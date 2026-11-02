"""Migrated baseline acceptance with SQLite and deterministic external substitutes.

These checks exercise real indexing/retrieval logic, but do not validate the
semantic quality of an embedding model or the real SiliconFlow/DeepSeek APIs.
"""

import json
import re
import socket
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.factory import create_app
from app.rag import service as rag_module
from app.rag.schemas import ChatRequest, RetrieveRequest, UploadRequest
from app.rag.service import RagService, RerankError


class FakeEmbedder:
    def __init__(self):
        self.batches = []
        self.fail = False

    def __call__(self, texts):
        self.batches.append(list(texts))
        if self.fail:
            raise RuntimeError("offline embedding failure")
        return [[1.0, float(len(text) % 5)] for text in texts]


class FakeVectors:
    """Only the Chroma operations used by the baseline; no similarity claims."""

    def __init__(self):
        self.rows = {}
        self.queries = []

    @staticmethod
    def matches(metadata, where):
        if not where:
            return True
        if "$and" in where:
            return all(FakeVectors.matches(metadata, part) for part in where["$and"])
        return all(
            metadata.get(field) in value["$in"]
            if isinstance(value, dict) and "$in" in value
            else metadata.get(field) == value
            for field, value in where.items()
        )

    def upsert(self, *, ids, embeddings, documents, metadatas):
        for index, key in enumerate(ids):
            self.rows[key] = (embeddings[index], documents[index], metadatas[index])

    def get(self, *, where=None, include=None):
        return {
            "ids": [
                key for key, (_, _, metadata) in self.rows.items() if self.matches(metadata, where)
            ]
        }

    def count(self):
        return len(self.rows)

    def delete(self, *, ids):
        for key in ids:
            self.rows.pop(key, None)

    def query(self, *, query_embeddings, n_results, where, include):
        self.queries.append({"where": where, "n_results": n_results})
        ids = self.get(where=where)["ids"][:n_results]
        return {"ids": [ids], "distances": [[0.1] * len(ids)]}


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    real_connect = socket.socket.connect
    real_create_connection = socket.create_connection

    def connect(sock, address):
        # asyncio on Windows builds its wake-up pipe with loopback sockets.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}:
            return real_connect(sock, address)
        raise AssertionError("RAG baseline tests must not use external network.")

    def create_connection(address, *args, **kwargs):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}:
            return real_create_connection(address, *args, **kwargs)
        raise AssertionError("RAG baseline tests must not use external network.")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)


@pytest.fixture
def rag(tmp_path):
    settings = Settings(
        _env_file=None,
        app_env="test",
        rag_data_dir=tmp_path / "rag",
        stock_daily_parquet_path=tmp_path / "market.parquet",
    )
    return RagService(settings, embedder=FakeEmbedder(), vector_collection=FakeVectors())


@pytest.fixture
def ranked_rag(rag, monkeypatch):
    def rerank(query, candidates):
        return [(index, 1.0 - index / 1000) for index in range(len(candidates))], {
            "response_id": "offline-fixture",
            "trace_id": None,
        }

    monkeypatch.setattr(rag, "rerank", rerank)
    return rag


@pytest.fixture
def client(rag):
    application = create_app(rag.settings)
    application.state.rag_service = rag
    with TestClient(application) as test_client:
        yield test_client


def upload(
    rag,
    content="# 收入\n收入同比增长，新增订单。\n\n现金回款改善。",
    filename="sample.md",
    *,
    symbol="600001.SH",
    source_type="disclosure",
    available_date="2026-06-30",
):
    return rag.ingest(
        UploadRequest(
            filename=filename,
            content=content,
            symbol=symbol,
            source_type=source_type,
            available_date=available_date,
        )
    )


def events(chunks):
    result = []
    for block in "".join(chunks).strip().split("\n\n"):
        lines = block.splitlines()
        result.append((lines[0].removeprefix("event: "), json.loads(lines[1][6:])))
    return result


def assert_consistent(rag, children):
    state = rag.debug_state()
    assert state["counts"]["children"] == children
    assert state["bm25_index_children"] == children
    assert state["chroma_vector_count"] == children
    for key in (
        "child_ids_missing_in_bm25",
        "stale_bm25_ids",
        "child_ids_missing_in_chroma",
        "stale_chroma_ids",
    ):
        assert state[key] == []


def test_startup_health_and_debug_page_do_not_create_storage(rag, client):
    assert not rag.settings.rag_data_dir.exists()
    assert client.get("/api/health").status_code == 200
    page = client.get("/rag/debug")
    assert page.status_code == 200
    assert "RAG" in page.text
    assert "/api/documents" in page.text
    assert client.get("/").status_code == 404
    assert not rag.settings.rag_data_dir.exists()


@pytest.mark.parametrize("document_id", [None, "missing"])
def test_empty_retrieve_is_normal_not_a_model_failure(client, rag, document_id, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("An empty store must not embed or rerank.")

    monkeypatch.setattr(rag, "embed", fail)
    monkeypatch.setattr(rag, "rerank", fail)
    response = client.post("/api/retrieve", json={"query": "  订单  ", "document_id": document_id})
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "query": "订单",
        "found": False,
        "context": "",
        "citations": {},
    }


def test_ingest_skip_and_incremental_revision_preserve_identity(rag):
    created = upload(rag)
    assert created["action"] == "CREATE"
    assert created["child_count"] == 2
    assert created["embedded_children"] == 2
    assert_consistent(rag, 2)
    calls = len(rag._embedder.batches)

    skipped = upload(rag)
    assert skipped["action"] == "SKIP"
    assert skipped["document_id"] == created["document_id"]
    assert skipped["embedded_children"] == skipped["bm25_tokenized_children"] == 0
    assert len(rag._embedder.batches) == calls

    revised = upload(rag, "# 收入\n收入同比下降，订单减少。\n\n现金回款改善。")
    assert revised["action"] == "UPDATE"
    assert revised["document_id"] == created["document_id"]
    assert revised["file_hash"] != created["file_hash"]
    assert len(revised["diff"]["UPDATE"]) == 1
    assert len(revised["diff"]["UNCHANGED"]) == 1
    assert revised["embedded_children"] == revised["bm25_tokenized_children"] == 1
    assert len(rag.list_documents()) == 1
    assert_consistent(rag, 2)


def test_revision_removes_deleted_sections_and_postings(rag):
    created = upload(rag, "# 保留\n收入增长。\n\n# 删除\n风险处置。")
    updated = upload(rag, "# 保留\n收入增长。")
    assert updated["document_id"] == created["document_id"]
    assert len(updated["diff"]["DELETE"]) == 1
    assert updated["embedded_children"] == 0
    assert_consistent(rag, 1)
    with rag.db() as connection:
        assert connection.execute("SELECT count(*) FROM parents").fetchone()[0] == 1
        assert (
            connection.execute("SELECT count(*) FROM bm25_postings WHERE term='风险'").fetchone()[0]
            == 0
        )


def test_delete_cascades_all_indexes_without_removing_another_document(rag):
    first = upload(rag)
    second = upload(rag, filename="other.md")
    calls = len(rag._embedder.batches)
    removed = rag.delete_document(first["document_id"])
    assert removed["deleted_children"] == removed["deleted_bm25_children"] == 2
    assert removed["deleted_vectors"] == 2
    assert len(rag._embedder.batches) == calls
    assert [row["document_id"] for row in rag.list_documents()] == [second["document_id"]]
    assert_consistent(rag, 2)
    rag.delete_document(second["document_id"])
    assert_consistent(rag, 0)
    with rag.db() as connection:
        assert connection.execute("SELECT count(*) FROM bm25_postings").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM bm25_document_stats").fetchone()[0] == 0


def test_failed_ingestion_is_error_and_same_content_retry_recovers(rag):
    rag._embedder.fail = True
    with pytest.raises(HTTPException) as error:
        upload(rag)
    assert error.value.status_code == 500
    document = rag.list_documents()[0]
    assert document["status"] == "ERROR"
    assert rag.retrieve(RetrieveRequest(query="收入"))["top_child_hits"] == []

    rag._embedder.fail = False
    recovered = upload(rag)
    assert recovered["action"] == "UPDATE"
    assert recovered["document_id"] == document["document_id"]
    assert recovered["recovery"] is True
    assert recovered["embedded_children"] == recovered["bm25_tokenized_children"] == 2
    assert rag.list_documents()[0]["status"] == "READY"
    assert_consistent(rag, 2)


def test_reopen_reuses_persistent_bm25_and_repairs_missing_entry(rag, monkeypatch):
    created = upload(rag)
    with rag.db() as connection:
        child_id = connection.execute("SELECT child_id FROM bm25_docs LIMIT 1").fetchone()[0]
        connection.execute("DELETE FROM bm25_docs WHERE child_id=?", (child_id,))
    reopened = RagService(rag.settings, embedder=rag._embedder, vector_collection=rag.vectors)
    analyzed = []
    original = rag_module.index_child

    def record(connection, child):
        analyzed.append(child["child_id"])
        return original(connection, child)

    monkeypatch.setattr(rag_module, "index_child", record)
    assert len(reopened.list_documents()) == 1
    assert analyzed == [child_id]
    assert_consistent(reopened, 2)
    with reopened.db() as connection:
        hits, info = rag_module.bm25_search(connection, "收入", [created["document_id"]], 5)
    assert hits
    assert info["indexed_children"] == 2


def test_document_scope_and_ready_filter_survive_dense_candidates(ranked_rag):
    rag = ranked_rag
    first = upload(rag, "# 收入\n收入增长。", "first.md")
    second = upload(rag, "# 收入\n收入下降。", "second.md")
    scoped = rag.retrieve(RetrieveRequest(query="收入", document_id=first["document_id"]))
    assert scoped["bm25_index"]["indexed_children"] == 1
    assert scoped["bm25_hits"] and scoped["dense_hits"]
    for branch in ("bm25_hits", "dense_hits", "rrf_result", "top_child_hits", "expanded_parents"):
        assert {hit["document_id"] for hit in scoped[branch]} == {first["document_id"]}
    with rag.db() as connection:
        connection.execute(
            "UPDATE documents SET status='ERROR' WHERE document_id=?", (second["document_id"],)
        )
    global_trace = rag.retrieve(RetrieveRequest(query="收入"))
    assert global_trace["bm25_index"]["indexed_children"] == 1
    assert {hit["document_id"] for hit in global_trace["dense_hits"]} == {first["document_id"]}
    assert "second.md" not in global_trace["generation_context"]


def test_parent_expansion_deduplicates_citations_and_preserves_source(ranked_rag):
    created = upload(ranked_rag)
    trace = ranked_rag.retrieve(RetrieveRequest(query="收入", top_k=5))
    assert len(trace["top_child_hits"]) == 2
    assert len(trace["expanded_parents"]) == len(trace["citation_map"]) == 1
    citation = trace["citation_map"]["1"]
    assert citation["document_id"] == created["document_id"]
    assert citation["source"] == "sample.md"
    assert citation["symbol"] == "600001.SH"
    assert citation["source_type"] == "disclosure"
    assert citation["available_date"] == "2026-06-30"
    assert datetime.fromisoformat(citation["ingested_at"]).tzinfo is not None
    assert citation["excerpt"] in trace["generation_context"]
    assert len(citation["child_ids"]) == 2
    assert "[1] 来源：sample.md / 收入" in trace["generation_context"]
    assert "现金回款改善" in trace["generation_context"]


def test_context_budget_does_not_leave_orphan_citations(ranked_rag, monkeypatch):
    upload(ranked_rag, "# 大段\n共同证据" + "甲" * 9000 + "\n\n# 另一段\n共同证据" + "乙" * 9000)

    def spread(query, candidates):
        seen = set()
        first, rest = [], []
        for index, child in enumerate(candidates):
            (rest if child["parent_id"] in seen else first).append(index)
            seen.add(child["parent_id"])
        return [(index, 1 - rank / 1000) for rank, index in enumerate(first + rest)], {}

    monkeypatch.setattr(ranked_rag, "rerank", spread)
    trace = ranked_rag.retrieve(RetrieveRequest(query="共同证据", top_k=20))
    assert len(trace["expanded_parents"]) == 2
    assert len(trace["generation_context"]) == 8000
    ids = re.findall(r"^\[(\d+)\] 来源：", trace["generation_context"], re.MULTILINE)
    assert ids == list(trace["citation_map"])
    assert len(ids) == 1
    assert trace["citation_map"]["1"]["excerpt"] in trace["generation_context"]
    assert len(trace["citation_map"]["1"]["excerpt"]) < 9000


def test_application_instances_use_separate_stores(rag, tmp_path):
    other = RagService(
        Settings(_env_file=None, rag_data_dir=tmp_path / "another"),
        embedder=FakeEmbedder(),
        vector_collection=FakeVectors(),
    )
    upload(rag)
    assert other.list_documents() == []
    upload(other, filename="other.md")
    assert [row["filename"] for row in rag.list_documents()] == ["sample.md"]
    assert [row["filename"] for row in other.list_documents()] == ["other.md"]


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "  "},
        {"query": "a" * 2001},
        {"query": "x", "top_k": 0},
        {"query": "x", "top_k": 21},
    ],
)
def test_retrieve_http_validation(client, payload):
    assert client.post("/api/retrieve", json=payload).status_code == 422


@pytest.mark.parametrize(
    "filename,content", [("file.txt", "正文"), ("file.md", " "), ("file.md", "# 仅标题")]
)
def test_bad_upload_is_rejected_without_partial_document(client, filename, content):
    response = client.post(
        "/api/documents",
        json={
            "filename": filename,
            "content": content,
            "symbol": "600001.SH",
            "source_type": "disclosure",
        },
    )
    assert response.status_code == 400
    assert client.get("/api/documents").json() == []


def test_unknown_document_delete_is_404(client):
    assert client.delete("/api/documents/missing").status_code == 404


def test_nonempty_missing_rerank_key_is_failure_not_no_hit(client, rag):
    upload(rag)
    response = client.post("/api/retrieve", json={"query": "收入"})
    assert response.status_code == 502
    assert response.json()["ok"] is False
    assert response.json()["error"] == "rerank_failed"
    assert "found" not in response.json()


def test_retrieve_endpoint_returns_evidence_without_generation(client, ranked_rag, monkeypatch):
    def reject_generation(*args, **kwargs):
        raise AssertionError("Pure retrieval must not create an LLM client.")

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=reject_generation))
    upload(ranked_rag)
    response = client.post("/api/retrieve", json={"query": "收入"})
    assert response.status_code == 200
    assert set(response.json()) == {"ok", "query", "found", "context", "citations"}
    assert response.json()["found"] is True
    assert response.json()["citations"]["1"]["source"] == "sample.md"


def test_debug_chat_empty_store_finishes_without_model_key(client):
    response = client.post("/api/chat/stream", json={"query": "收入"})
    assert response.headers["content-type"].startswith("text/event-stream")
    stream = events([response.text])
    assert [kind for kind, _ in stream] == ["citations", "delta", "done"]
    assert stream[0][1] == {}
    assert stream[-1][1]["answer"] == "知识库中未检索到相关内容。"


def test_debug_chat_missing_llm_key_reports_error_after_retrieval(ranked_rag):
    upload(ranked_rag)
    stream = events(ranked_rag.stream_chat(ChatRequest(query="收入")))
    assert [kind for kind, _ in stream] == ["citations", "error"]
    assert stream[0][1]
    assert "DEEPSEEK_API_KEY" in stream[-1][1]["message"]


@pytest.mark.parametrize("generation_fails", [False, True])
def test_debug_chat_stream_closes_clients_and_distinguishes_failure(
    ranked_rag, monkeypatch, generation_fails
):
    upload(ranked_rag)
    ranked_rag.settings.deepseek_api_key = Settings(
        _env_file=None, deepseek_api_key="offline-generation-key"
    ).deepseek_api_key
    closed = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append("stream")

        def __iter__(self):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="收入"))])
            if generation_fails:
                raise RuntimeError("private-provider-request-details")
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="增长[1]"))]
            )

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "offline-generation-key"
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            assert kwargs["stream"] is True
            assert "[1] 来源：sample.md" in kwargs["messages"][0]["content"]
            return Stream()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append("client")

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=Client))
    stream = events(ranked_rag.stream_chat(ChatRequest(query="收入")))
    assert closed == ["stream", "client"]
    assert stream[0][0] == "citations"
    if generation_fails:
        assert stream[-1][0] == "error"
        assert all(kind != "done" for kind, _ in stream)
        assert "private-provider-request-details" not in str(stream)
    else:
        assert [kind for kind, _ in stream] == ["citations", "delta", "delta", "done"]
        assert stream[-1][1]["answer"] == "收入增长[1]"


@pytest.mark.parametrize(
    "results",
    [
        None,
        [],
        [{"index": True, "relevance_score": 0.9}],
        [{"index": 2, "relevance_score": 0.9}],
        [{"index": 0, "relevance_score": float("nan")}],
        [{"index": 0, "relevance_score": True}],
    ],
)
def test_malformed_reranker_response_is_rejected(rag, monkeypatch, results):
    import requests

    rag.settings.siliconflow_api_key = Settings(
        _env_file=None, siliconflow_api_key="offline-key"
    ).siliconflow_api_key
    response = SimpleNamespace(status_code=200, headers={}, json=lambda: {"results": results})
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response)
    with pytest.raises(RerankError):
        rag.rerank("收入", [{"retrieval_text": "收入增长"}])


def test_reranker_uses_settings_key_and_returns_provider_order(rag, monkeypatch):
    import requests

    rag.settings.siliconflow_api_key = Settings(
        _env_file=None, siliconflow_api_key="offline-key"
    ).siliconflow_api_key
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=200,
            headers={"x-siliconcloud-trace-id": "offline-trace"},
            json=lambda: {
                "id": "offline-id",
                "results": [
                    {"index": 0, "relevance_score": 0.2},
                    {"index": 1, "relevance_score": 0.9},
                ],
            },
        )

    monkeypatch.setattr(requests, "post", post)
    ranked, provider = rag.rerank("收入", [{"retrieval_text": "a"}, {"retrieval_text": "b"}])
    assert ranked == [(1, 0.9), (0, 0.2)]
    assert provider == {"response_id": "offline-id", "trace_id": "offline-trace"}
    assert calls[0][1]["headers"]["Authorization"] == "Bearer offline-key"
    assert calls[0][1]["timeout"] == (5, 30)
    assert calls[0][1]["json"]["documents"] == ["a", "b"]


def test_reranker_timeout_is_error_and_does_not_leak_exception_details(rag, monkeypatch):
    import requests

    rag.settings.siliconflow_api_key = Settings(
        _env_file=None, siliconflow_api_key="offline-key"
    ).siliconflow_api_key

    def fail(*args, **kwargs):
        raise requests.Timeout("sensitive-upstream-request-details")

    monkeypatch.setattr(requests, "post", fail)
    with pytest.raises(RerankError) as error:
        rag.rerank("收入", [{"retrieval_text": "a"}])
    assert "sensitive-upstream-request-details" not in str(error.value)


def test_same_filename_for_different_symbols_does_not_overwrite(rag):
    first = upload(rag, "# 公司甲\n收入增长。", symbol="600001.SH")
    second = upload(rag, "# 公司乙\n收入下降。", symbol="600002.SH")
    assert first["document_id"] != second["document_id"]
    assert first["action"] == second["action"] == "CREATE"
    documents = rag.list_documents()
    assert {row["symbol"] for row in documents} == {"600001.SH", "600002.SH"}
    assert len(documents) == 2
    assert_consistent(rag, 2)


def test_symbol_cutoff_and_source_filter_apply_before_bm25_and_dense(ranked_rag):
    rag = ranked_rag
    allowed = upload(rag, "# 收入\n收入增长，允许证据。", filename="allowed.md")
    request = RetrieveRequest(query="收入", symbol="600001.SH", cutoff_date="2026-06-30")
    baseline = rag.retrieve(request)
    other = upload(rag, "# 收入\n收入增长，其他公司。", "other.md", symbol="600002.SH")
    future = upload(rag, "# 收入\n收入增长，未来资料。", "future.md", available_date="2026-07-01")
    unknown = upload(rag, "# 收入\n收入增长，日期未知。", "unknown.md", available_date=None)
    ai = upload(rag, "# 收入\n收入增长，AI生成。", "ai.md", source_type="ai_generated")
    trace = rag.retrieve(request)
    assert trace["bm25_index"] == baseline["bm25_index"]
    assert trace["bm25_hits"] == baseline["bm25_hits"]
    for branch in ("bm25_hits", "dense_hits", "rrf_result", "top_child_hits", "expanded_parents"):
        assert {hit["document_id"] for hit in trace[branch]} == {allowed["document_id"]}
    assert {row["document_id"] for row in trace["citation_map"].values()} == {
        allowed["document_id"]
    }
    where = rag.vectors.queries[-1]["where"]
    assert where is not None
    vector_ids = set(rag.vectors.get(where=where)["ids"])
    assert vector_ids == set(allowed["diff"]["CREATE"])
    assert not vector_ids.intersection(
        child_id for row in (other, future, unknown, ai) for child_id in row["diff"]["CREATE"]
    )
    assert trace["metadata_filter"]["symbol"] == "600001.SH"
    assert trace["metadata_filter"]["cutoff_date"] == "2026-06-30"


@pytest.mark.parametrize(
    "scope",
    [
        {"symbol": "600002.SH"},
        {"cutoff_date": "2026-06-29"},
        {"symbol": "600002.SH", "document_id": "missing"},
    ],
)
def test_empty_eligible_set_never_falls_back_to_global_vectors(rag, monkeypatch, scope):
    upload(rag)

    def reject(*args, **kwargs):
        raise AssertionError("No eligible documents must not call vectors, embedding or reranking.")

    monkeypatch.setattr(rag, "embed", reject)
    monkeypatch.setattr(rag, "rerank", reject)
    monkeypatch.setattr(rag.vectors, "query", reject)
    trace = rag.retrieve(RetrieveRequest(query="收入", **scope))
    assert trace["bm25_hits"] == trace["dense_hits"] == trace["top_child_hits"] == []
    assert trace["generation_context"] == ""
    assert trace["citation_map"] == {}


def test_unknown_date_is_usable_only_when_no_cutoff_is_requested(ranked_rag):
    upload(ranked_rag, available_date=None)
    assert ranked_rag.retrieve(RetrieveRequest(query="收入"))["top_child_hits"]
    trace = ranked_rag.retrieve(RetrieveRequest(query="收入", cutoff_date="2026-12-31"))
    assert trace["top_child_hits"] == []
    assert trace["citation_map"] == {}


def test_ai_generated_source_is_excluded_even_by_explicit_document_id(rag, monkeypatch):
    document = upload(rag, source_type="ai_generated")

    def reject(*args, **kwargs):
        raise AssertionError("AI sources are not fact retrieval candidates.")

    monkeypatch.setattr(rag, "embed", reject)
    monkeypatch.setattr(rag, "rerank", reject)
    trace = rag.retrieve(RetrieveRequest(query="收入", document_id=document["document_id"]))
    assert trace["top_child_hits"] == []
    assert rag.list_documents()[0]["status"] == "READY"


def test_metadata_updates_change_scope_without_embedding_again(ranked_rag):
    rag = ranked_rag
    original = upload(rag)
    batches = len(rag._embedder.batches)
    changed_date = upload(rag, available_date="2026-07-01")
    assert changed_date["action"] == "UPDATE"
    assert changed_date["document_id"] == original["document_id"]
    assert changed_date["metadata_updated"] is True
    assert changed_date["embedded_children"] == changed_date["bm25_tokenized_children"] == 0
    assert len(rag._embedder.batches) == batches
    assert (
        rag.retrieve(RetrieveRequest(query="收入", cutoff_date="2026-06-30"))["top_child_hits"]
        == []
    )
    assert rag.retrieve(RetrieveRequest(query="收入", cutoff_date="2026-07-01"))["top_child_hits"]
    batches = len(rag._embedder.batches)
    changed_type = upload(rag, available_date="2026-07-01", source_type="ai_generated")
    assert changed_type["action"] == "UPDATE"
    assert changed_type["embedded_children"] == changed_type["bm25_tokenized_children"] == 0
    assert len(rag._embedder.batches) == batches
    assert rag.retrieve(RetrieveRequest(query="收入"))["top_child_hits"] == []
    assert upload(rag, available_date="2026-07-01", source_type="ai_generated")["action"] == "SKIP"
    cleared_date = upload(rag, source_type="personal_note", available_date=None)
    assert cleared_date["action"] == "UPDATE" and cleared_date["embedded_children"] == 0
    assert rag.list_documents()[0]["available_date"] is None
    assert rag.retrieve(RetrieveRequest(query="收入"))["top_child_hits"]


def test_unscoped_dense_results_cannot_escape_candidate_and_parent_rechecks(
    ranked_rag, monkeypatch
):
    rag = ranked_rag
    allowed = upload(rag, "# 正文\n允许证据收入。", "allowed.md")
    denied = upload(rag, "# 正文\n禁止证据收入。", "denied.md", symbol="600002.SH")
    ai = upload(rag, "# 正文\nAI证据收入。", "ai.md", source_type="ai_generated")

    def broken_query(**kwargs):
        # Simulate an adapter returning candidates outside the requested vector filter.
        keys = [*denied["diff"]["CREATE"], *ai["diff"]["CREATE"], *allowed["diff"]["CREATE"]]
        return {"ids": [keys], "distances": [[0.0] * len(keys)]}

    monkeypatch.setattr(rag.vectors, "query", broken_query)
    trace = rag.retrieve(
        RetrieveRequest(query="收入", symbol="600001.SH", cutoff_date="2026-06-30")
    )
    assert {hit["document_id"] for hit in trace["dense_hits"]} == {allowed["document_id"]}
    assert {hit["document_id"] for hit in trace["expanded_parents"]} == {allowed["document_id"]}
    assert (
        "禁止证据" not in trace["generation_context"]
        and "AI证据" not in trace["generation_context"]
    )
    assert {row["document_id"] for row in trace["citation_map"].values()} == {
        allowed["document_id"]
    }


@pytest.mark.parametrize(
    "value",
    ["2026-6-30", "2026-02-30", "2026-06-30T00:00:00", "2026-06-30T00:00:00Z", 1782777600, True],
)
def test_date_fields_reject_ambiguous_or_noncalendar_values(client, value):
    query = client.post("/api/retrieve", json={"query": "收入", "cutoff_date": value})
    upload_response = client.post(
        "/api/documents",
        json={
            "filename": "date.md",
            "content": "# 正文\n收入。",
            "symbol": "600001.SH",
            "source_type": "disclosure",
            "available_date": value,
        },
    )
    assert query.status_code == upload_response.status_code == 422


@pytest.mark.parametrize("symbol", ["", " ", "股票名称", "../secret", "F:/private", "A" * 33])
def test_symbol_validation_is_shared_by_ingest_and_query(client, symbol):
    query = client.post("/api/retrieve", json={"query": "收入", "symbol": symbol})
    upload_response = client.post(
        "/api/documents",
        json={
            "filename": "date.md",
            "content": "# 正文\n收入。",
            "symbol": symbol,
            "source_type": "disclosure",
        },
    )
    assert query.status_code == upload_response.status_code == 422


def test_symbol_normalization_preserves_document_identity_and_query_scope(ranked_rag):
    first = upload(ranked_rag, symbol="  abc.sh ")
    assert upload(ranked_rag, symbol="ABC.SH")["action"] == "SKIP"
    assert ranked_rag.list_documents()[0]["symbol"] == "ABC.SH"
    trace = ranked_rag.retrieve(RetrieveRequest(query="收入", symbol=" abc.sh "))
    assert {row["document_id"] for row in trace["citation_map"].values()} == {first["document_id"]}


def write_symbol_catalog(rag, symbols):
    import duckdb

    with duckdb.connect(":memory:") as connection:
        connection.execute("CREATE TABLE codes(thscode VARCHAR)")
        connection.executemany("INSERT INTO codes VALUES (?)", [(symbol,) for symbol in symbols])
        connection.execute(
            "COPY codes TO ? (FORMAT PARQUET)", [str(rag.settings.stock_daily_parquet_path)]
        )


def test_verified_suffix_alias_reads_and_updates_legacy_document_without_reembedding(ranked_rag):
    rag = ranked_rag
    # Existing uploads stay intact; no data migration or forced re-upload.
    original = upload(rag, symbol="601318")
    before = rag.list_documents()[0]
    write_symbol_catalog(rag, ["601318.SH"])

    trace = rag.retrieve(RetrieveRequest(query="收入", symbol="601318.SH"))
    assert trace["metadata_filter"]["canonical_symbol"] == "601318.SH"
    assert set(trace["metadata_filter"]["symbol_aliases"]) == {"601318", "601318.SH"}
    assert {item["document_id"] for item in trace["citation_map"].values()} == {
        original["document_id"]
    }
    assert rag.list_documents()[0] == before
    result = upload(rag, symbol="601318.SH")
    assert result["action"] == "SKIP"
    assert result["document_id"] == original["document_id"]
    assert result["embedded_children"] == 0

    changed = upload(rag, symbol="601318.SH", available_date="2026-07-01")
    assert changed["document_id"] == original["document_id"]
    assert changed["metadata_updated"] is True
    assert changed["embedded_children"] == 0
    assert len(rag.list_documents()) == 1
    assert rag.list_documents()[0]["symbol"] == "601318"
    assert not rag.retrieve(
        RetrieveRequest(query="收入", symbol="601318.SH", cutoff_date="2026-06-30")
    )["top_child_hits"]
    assert_consistent(rag, original["child_count"])


def test_alias_scope_keeps_date_source_and_vector_restrictions(ranked_rag):
    rag = ranked_rag
    legacy = upload(rag, symbol="601318", filename="allowed.md")
    write_symbol_catalog(rag, ["601318.SH", "600519.SH"])
    upload(rag, symbol="601318.SH", filename="future.md", available_date="2026-07-01")
    upload(rag, symbol="601318", filename="ai.md", source_type="ai_generated")
    upload(rag, symbol="600519.SH", filename="other.md")
    trace = rag.retrieve(
        RetrieveRequest(query="收入", symbol="601318.SH", cutoff_date="2026-06-30")
    )
    assert trace["eligible_document_count"] == 1
    for branch in ("bm25_hits", "dense_hits", "top_child_hits", "expanded_parents"):
        assert {hit["document_id"] for hit in trace[branch]} == {legacy["document_id"]}
    assert set(rag.vectors.get(where=rag.vectors.queries[-1]["where"])["ids"]) == set(
        legacy["diff"]["CREATE"]
    )
    citation = next(iter(trace["citation_map"].values()))
    assert citation["available_date"] == "2026-06-30"
    assert citation["symbol"] == "601318"


def test_stock_alias_never_matches_same_code_index(ranked_rag):
    rag = ranked_rag
    stock = upload(rag, symbol="000001")
    write_symbol_catalog(rag, ["000001.SZ"])
    index = upload(rag, symbol="000001.SH")
    for symbol, expected in (("000001.SH", index), ("000001.SZ", stock), ("000001", stock)):
        trace = rag.retrieve(RetrieveRequest(query="收入", symbol=symbol))
        assert {item["document_id"] for item in trace["citation_map"].values()} == {
            expected["document_id"]
        }
    assert_consistent(rag, stock["child_count"] + index["child_count"])


def test_ambiguous_bare_code_is_rejected_without_querying_vectors(ranked_rag):
    rag = ranked_rag
    upload(rag, symbol="000001")
    write_symbol_catalog(rag, ["000001.SZ", "000001.SH"])
    with pytest.raises(HTTPException) as result:
        rag.retrieve(RetrieveRequest(query="收入", symbol="000001"))
    assert result.value.status_code == 409
    assert result.value.detail["code"] == "ambiguous_symbol"
    assert not rag.vectors.queries


def test_missing_market_catalog_keeps_exact_rag_scope(ranked_rag):
    rag = ranked_rag
    original = upload(rag, symbol="601318")
    trace = rag.retrieve(RetrieveRequest(query="收入", symbol="601318"))
    assert trace["metadata_filter"]["symbol_mapping_status"] == "unavailable_exact_only"
    assert {item["document_id"] for item in trace["citation_map"].values()} == {
        original["document_id"]
    }
    assert not rag.retrieve(RetrieveRequest(query="收入", symbol="601318.SH"))["top_child_hits"]


def test_preexisting_duplicate_alias_documents_require_explicit_cleanup(ranked_rag):
    rag = ranked_rag
    upload(rag, symbol="601318")
    upload(rag, symbol="601318.SH")
    write_symbol_catalog(rag, ["601318.SH"])
    before = rag.list_documents()
    with pytest.raises(HTTPException) as result:
        upload(rag, symbol="601318.SH")
    assert result.value.detail["code"] == "duplicate_symbol_documents"
    assert rag.list_documents() == before


def test_new_bare_code_upload_uses_verified_canonical_symbol(ranked_rag):
    rag = ranked_rag
    write_symbol_catalog(rag, ["601318.SH"])
    original = upload(rag, symbol="601318")
    assert rag.list_documents()[0]["symbol"] == "601318.SH"
    assert upload(rag, symbol="601318.SH")["document_id"] == original["document_id"]
