"""Opt-in real SQLite/Chroma/ONNX acceptance; remote reranking remains substituted.

Requires an existing default embedding cache. Two separate Python processes
prove persisted vector/BM25 reuse rather than sharing in-memory test state.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(
    os.environ.get("RUN_LOCAL_RAG_TESTS") != "1",
    reason="Set RUN_LOCAL_RAG_TESTS=1 to use the cached real ONNX embedding model.",
)
def test_real_local_index_survives_process_restart(tmp_path):
    cache = Path.home() / ".cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx"
    if not all((cache / name).is_file() for name in ("model.onnx", "tokenizer.json")):
        pytest.skip("Cached MiniLM model unavailable; this check must not download models.")

    script = """
import socket
import sys
from pathlib import Path

def reject_network(*args, **kwargs):
    raise AssertionError('Local acceptance must not call external services or download models.')

socket.create_connection = reject_network
socket.socket.connect = reject_network

from app.core.config import Settings
from app.rag.schemas import RetrieveRequest, UploadRequest
from app.rag.service import RagService

data_dir = Path(sys.argv[1])
phase = sys.argv[2]
service = RagService(Settings(_env_file=None, rag_data_dir=data_dir,
                             siliconflow_api_key=None, deepseek_api_key=None))
# Only the remote reranker is substituted. Dense vectors and tokenization are real.
service.rerank = lambda query, candidates: (
    [(index, 1.0 - index / 1000) for index in range(len(candidates))],
    {'response_id': 'local-acceptance-substitute', 'trace_id': None},
)
initial = '# 收入\\n公司收入增长，新增订单。\\n\\n现金回款改善。\\n\\n# 风险\\n新增订单仍需要确认交付。'
revised = '# 收入\\n公司收入增长，新增订单。\\n\\n现金回款保持稳定。'
metadata = dict(symbol='600001.SH', source_type='disclosure', available_date='2026-06-30')

if phase == 'create':
    assert not data_dir.exists()
    assert service.retrieve(RetrieveRequest(query='收入'))['top_child_hits'] == []
    assert not (data_dir / 'chroma').exists()
    created = service.ingest(UploadRequest(filename='local-check.md', content=initial, **metadata))
    assert created['action'] == 'CREATE'
    assert service.ingest(UploadRequest(filename='local-check.md', content=initial, **metadata))['action'] == 'SKIP'
    updated = service.ingest(UploadRequest(filename='local-check.md', content=revised, **metadata))
    assert updated['document_id'] == created['document_id']
    assert updated['diff']['UPDATE'] and updated['diff']['DELETE']
    for filename, symbol, source_type, available_date in (
        ('local-check.md', '600002.SH', 'disclosure', '2026-06-30'),
        ('future.md', '600001.SH', 'disclosure', '2026-07-01'),
        ('unknown.md', '600001.SH', 'disclosure', None),
        ('ai.md', '600001.SH', 'ai_generated', '2026-06-30'),
    ):
        service.ingest(UploadRequest(filename=filename, content='# 收入\\n这是范围外收入证据。',
                                    symbol=symbol, source_type=source_type,
                                    available_date=available_date))
else:
    documents = service.list_documents()
    assert len(documents) == 5 and all(row['status'] == 'READY' for row in documents)
    skipped = service.ingest(UploadRequest(filename='local-check.md', content=revised, **metadata))
    assert skipped['action'] == 'SKIP' and skipped['embedding_calls'] == 0

main = next(row for row in service.list_documents()
            if row['filename'] == 'local-check.md' and row['symbol'] == '600001.SH')
request = RetrieveRequest(query='收入增长', top_k=2, symbol='600001.SH', cutoff_date='2026-06-30')
trace = service.retrieve(request)
assert trace['bm25_hits'] and trace['dense_hits'] and trace['reranker_result']
assert trace['bm25_index']['indexed_children'] == 2
assert {hit['document_id'] for hit in trace['dense_hits']} == {main['document_id']}
assert trace['citation_map']['1']['source'] == 'local-check.md'
assert '现金回款保持稳定' in trace['generation_context']
assert '范围外' not in trace['generation_context']
assert trace['citation_map']['1']['document_id'] == main['document_id']
assert trace['citation_map']['1']['available_date'] == '2026-06-30'
assert trace['citation_map']['1']['excerpt'] in trace['generation_context']
assert not service.retrieve(RetrieveRequest(query='收入', symbol='600001.SH',
                                           cutoff_date='2026-06-29'))['top_child_hits']
state = service.debug_state()
assert state['counts']['children'] == state['chroma_vector_count'] == state['bm25_index_children']
for key in ('child_ids_missing_in_bm25', 'stale_bm25_ids',
            'child_ids_missing_in_chroma', 'stale_chroma_ids'):
    assert state[key] == [], (key, state[key])

if phase == 'reopen':
    changed = service.ingest(UploadRequest(filename='local-check.md', content=revised,
                                          symbol='600001.SH', source_type='disclosure',
                                          available_date='2026-07-01'))
    assert changed['action'] == 'UPDATE' and changed['embedded_children'] == 0
    assert not service.retrieve(request)['top_child_hits']
    for document in service.list_documents():
        service.delete_document(document['document_id'])
    state = service.debug_state()
    assert state['counts'] == {'documents': 0, 'parents': 0, 'children': 0}
    assert state['chroma_vector_count'] == state['bm25_index_children'] == state['bm25_posting_count'] == 0
service.close()
print('local-' + phase + '-ok')
"""
    for phase in ("create", "reopen"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path / "local-index"), phase],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"local-{phase}-ok" in result.stdout
