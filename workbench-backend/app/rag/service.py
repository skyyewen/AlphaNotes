"""Parent-child RAG with per-application resources and a small SQL-owned scope.

The source is F:/Desktop/rag/RAG/最小验证/app.py. Storage, settings and HTTP
assembly are isolated here. Symbol and calendar-date filters apply before both
retrieval branches; same-symbol filename updates do not retain history.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

from app.core.symbols import SymbolMappingError, resolve_symbol_aliases
from app.rag.schemas import ChatRequest, UploadRequest

if TYPE_CHECKING:
    from app.core.config import Settings

LOGGER = logging.getLogger(__name__)

SCHEMA = """
    CREATE TABLE IF NOT EXISTS documents (
        document_id TEXT PRIMARY KEY, filename TEXT NOT NULL,
        file_hash TEXT NOT NULL, status TEXT NOT NULL,
        symbol TEXT NOT NULL, source_type TEXT NOT NULL,
        available_date TEXT, ingested_at TEXT NOT NULL,
        UNIQUE(symbol, filename));
    CREATE INDEX IF NOT EXISTS idx_documents_scope
        ON documents(status, symbol, available_date);
    CREATE TABLE IF NOT EXISTS parents (
        parent_id TEXT PRIMARY KEY, document_id TEXT NOT NULL
        REFERENCES documents(document_id) ON DELETE CASCADE,
        heading_path TEXT NOT NULL, content TEXT NOT NULL, position INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS children (
        child_id TEXT PRIMARY KEY, parent_id TEXT NOT NULL
        REFERENCES parents(parent_id) ON DELETE CASCADE,
        document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
        content TEXT NOT NULL, retrieval_text TEXT NOT NULL, position INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_children_document ON children(document_id);
    CREATE TABLE IF NOT EXISTS bm25_docs (
        child_id TEXT PRIMARY KEY REFERENCES children(child_id) ON DELETE CASCADE,
        document_id TEXT NOT NULL, text_hash TEXT NOT NULL,
        token_count INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS bm25_postings (
        term TEXT NOT NULL, child_id TEXT NOT NULL
        REFERENCES bm25_docs(child_id) ON DELETE CASCADE,
        tf INTEGER NOT NULL, PRIMARY KEY(term, child_id));
    CREATE TABLE IF NOT EXISTS bm25_document_stats (
        document_id TEXT PRIMARY KEY REFERENCES documents(document_id) ON DELETE CASCADE,
        child_count INTEGER NOT NULL, token_total INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_bm25_docs_document ON bm25_docs(document_id);
    CREATE INDEX IF NOT EXISTS idx_bm25_postings_term ON bm25_postings(term);
"""


def stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def parse_markdown(document_id: str, content: str):
    """Each heading section is a Parent; each paragraph has stable Child IDs."""
    sections = []
    headings: list[str] = []
    body: list[str] = []

    def flush():
        text = "\n".join(body).strip()
        if text:
            sections.append((list(headings), text))

    for line in content.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            flush()
            level = len(match.group(1))
            headings[:] = headings[: level - 1] + [match.group(2).strip()]
            body = []
        else:
            body.append(line)
    flush()

    parents, children = [], []
    repeated: dict[str, int] = defaultdict(int)
    for section_index, (path, text) in enumerate(sections):
        heading = " > ".join(path) or "文档正文"
        repeated[heading] += 1
        parent_id = stable_id(document_id, heading, str(repeated[heading]))
        parents.append(
            dict(
                parent_id=parent_id,
                document_id=document_id,
                heading_path=heading,
                content=f"{heading}\n{text}",
                position=section_index,
            )
        )
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        for paragraph_index, paragraph in enumerate(paragraphs):
            parts = [paragraph[i : i + 300] for i in range(0, len(paragraph), 300)]
            for part_index, piece in enumerate(parts):
                children.append(
                    dict(
                        child_id=stable_id(parent_id, str(paragraph_index), str(part_index)),
                        parent_id=parent_id,
                        document_id=document_id,
                        content=piece,
                        retrieval_text=f"{heading}\n{piece}",
                        position=len(children),
                    )
                )
    return parents, children


def tokenize(text: str) -> list[str]:
    import jieba

    return [token.lower() for token in jieba.cut(text) if token.strip()]


def index_child(connection: sqlite3.Connection, child: dict):
    """Analyze a changed Child once and persist its term frequencies."""
    tokens = tokenize(child["retrieval_text"])
    text_hash = hashlib.sha256(child["retrieval_text"].encode("utf-8")).hexdigest()
    connection.execute(
        """
        INSERT INTO bm25_docs(child_id, document_id, text_hash, token_count)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(child_id) DO UPDATE SET
            document_id=excluded.document_id, text_hash=excluded.text_hash,
            token_count=excluded.token_count
        """,
        (child["child_id"], child["document_id"], text_hash, len(tokens)),
    )
    connection.execute("DELETE FROM bm25_postings WHERE child_id=?", (child["child_id"],))
    connection.executemany(
        "INSERT INTO bm25_postings(term, child_id, tf) VALUES (?, ?, ?)",
        [(term, child["child_id"], tf) for term, tf in Counter(tokens).items()],
    )


def refresh_bm25_stats(connection: sqlite3.Connection, document_id: str):
    count, total = connection.execute(
        "SELECT count(*), coalesce(sum(token_count), 0) FROM bm25_docs WHERE document_id=?",
        (document_id,),
    ).fetchone()
    connection.execute(
        """
        INSERT INTO bm25_document_stats(document_id, child_count, token_total)
        VALUES (?, ?, ?)
        ON CONFLICT(document_id) DO UPDATE SET
            child_count=excluded.child_count, token_total=excluded.token_total
        """,
        (document_id, count, total),
    )


def bm25_search(connection: sqlite3.Connection, query: str, document_ids: list[str], limit: int):
    """Use the exact same eligible document set for corpus, DF and postings."""
    query_terms = Counter(tokenize(query))
    if not document_ids:
        return [], dict(query_tokens=list(query_terms), indexed_children=0, average_child_tokens=0)
    scope_slots = ",".join("?" for _ in document_ids)
    stats = connection.execute(
        "SELECT coalesce(sum(s.child_count), 0), coalesce(sum(s.token_total), 0) "
        "FROM bm25_document_stats s WHERE s.document_id IN (" + scope_slots + ")",
        document_ids,
    ).fetchone()
    total, total_tokens = stats
    avg_length = total_tokens / total if total else 0
    info = dict(
        query_tokens=list(query_terms),
        indexed_children=total,
        average_child_tokens=round(avg_length, 3),
    )
    if not total or not query_terms:
        return [], info
    placeholders = ",".join("?" for _ in query_terms)
    rows = connection.execute(
        "SELECT p.term,p.child_id,p.tf,b.token_count FROM bm25_postings p "
        "JOIN bm25_docs b ON b.child_id=p.child_id "
        "WHERE b.document_id IN (" + scope_slots + ") AND p.term IN (" + placeholders + ")",
        [*document_ids, *query_terms],
    ).fetchall()
    document_frequency = Counter(row["term"] for row in rows)
    scores = defaultdict(float)
    k1, b = 1.5, 0.75
    for row in rows:
        df = document_frequency[row["term"]]
        idf = math.log1p((total - df + 0.5) / (df + 0.5))
        normalized_length = 1 - b + b * row["token_count"] / max(avg_length, 1)
        tf = row["tf"]
        scores[row["child_id"]] += (
            query_terms[row["term"]] * idf * tf * (k1 + 1) / (tf + k1 * normalized_length)
        )
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit], info


def hit(child: dict, score: float, rank: int):
    return dict(
        child_id=child["child_id"],
        parent_id=child["parent_id"],
        document_id=child["document_id"],
        child_content=child["content"],
        score=round(float(score), 6),
        rank=rank,
    )


class RerankError(Exception):
    """A configured dependency failed; never turn this into a normal no-hit result."""


def sse(event: str, payload):
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class RagService:
    """Single-process local RAG store, isolated for each FastAPI application.

    Constructor and imports perform no I/O. SQLite is initialized on the first
    store operation. Chroma/ONNX and API clients are initialized only when used.
    Injected embedder/collection support offline tests without a model download.
    """

    def __init__(self, settings: Settings, *, embedder=None, vector_collection=None):
        self.settings = settings
        self.data_dir = settings.rag_data_dir
        self.db_path = self.data_dir / "rag.sqlite3"
        self.lock = threading.RLock()
        self._store_ready = False
        self._embedder = embedder
        self._vectors = vector_collection
        self._chroma_client = None

    def close(self):
        """Connections/LLM streams are scoped; do not stop shared Chroma globals.

        Chroma's embedded runtime is process-managed. A library-private global
        shutdown would also stop other application/test instances in this process.
        """

    def _symbol_scope(self, symbol: str) -> dict:
        try:
            return resolve_symbol_aliases(symbol, self.settings.stock_daily_parquet_path)
        except SymbolMappingError as exc:
            if exc.code != "symbol_mapping_unavailable":
                raise HTTPException(409, detail={"code": exc.code, "message": str(exc)}) from exc
            # The document store remains usable without a market file. Unverified
            # aliases never widen the scope; the debug trace exposes exact-only use.
            return {
                "requested_symbol": symbol,
                "canonical_symbol": symbol,
                "aliases": [symbol],
                "mapping_status": "unavailable_exact_only",
            }

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _ensure_store(self):
        with self.lock:
            if self._store_ready:
                return
            self.data_dir.mkdir(parents=True, exist_ok=True)
            with self._connection() as connection:
                columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(documents)")
                }
                if columns and not {
                    "symbol",
                    "source_type",
                    "available_date",
                    "ingested_at",
                }.issubset(columns):
                    raise HTTPException(
                        503, "RAG 数据库结构不兼容；请停止服务并清空旧 RAG 数据目录后重试。"
                    )
                connection.executescript(SCHEMA)
                # Preserve the baseline repair for persistent BM25 postings.
                rows = connection.execute("""
                    SELECT c.child_id,c.document_id,c.retrieval_text,b.text_hash
                    FROM children c LEFT JOIN bm25_docs b ON b.child_id=c.child_id
                """).fetchall()
                for row in rows:
                    text_hash = hashlib.sha256(row["retrieval_text"].encode("utf-8")).hexdigest()
                    if row["text_hash"] != text_hash:
                        index_child(connection, dict(row))
                document_ids = [
                    row[0] for row in connection.execute("SELECT document_id FROM documents")
                ]
                for document_id in document_ids:
                    refresh_bm25_stats(connection, document_id)
            self._store_ready = True

    @contextmanager
    def db(self):
        self._ensure_store()
        with self._connection() as connection:
            yield connection

    @property
    def vectors(self):
        with self.lock:
            if self._vectors is None:
                import chromadb
                from chromadb.config import Settings as ChromaSettings

                self._ensure_store()
                client = chromadb.PersistentClient(
                    path=str(self.data_dir / "chroma"),
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
                vectors = client.get_or_create_collection(
                    name="minimal_rag_children", embedding_function=None
                )
                self._chroma_client = client
                self._vectors = vectors
            return self._vectors

    def embed(self, texts: list[str]):
        with self.lock:
            if self._embedder is None:
                from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

                self._embedder = DefaultEmbeddingFunction()
            return [
                vector.tolist() if hasattr(vector, "tolist") else list(vector)
                for vector in self._embedder(texts)
            ]

    def list_documents(self):
        with self.lock, self.db() as connection:
            rows = connection.execute("""
                SELECT d.*,
                       (SELECT count(*) FROM parents p
                        WHERE p.document_id=d.document_id) parent_count,
                       (SELECT count(*) FROM children c
                        WHERE c.document_id=d.document_id) child_count
                FROM documents d ORDER BY symbol, filename
            """).fetchall()
        return [dict(row) for row in rows]

    def ingest(self, request: UploadRequest):
        filename = Path(request.filename).name.strip()
        if not filename.lower().endswith(".md") or not request.content.strip():
            raise HTTPException(400, "请选择非空 Markdown 文件")
        file_hash = hashlib.sha256(request.content.encode("utf-8")).hexdigest()
        available_date = request.available_date.isoformat() if request.available_date else None
        ingested_at = datetime.now(UTC).isoformat()
        with self.lock:
            scope = self._symbol_scope(request.symbol)
            aliases = scope["aliases"]
            with self.db() as connection:
                slots = ",".join("?" for _ in aliases)
                existing = connection.execute(
                    f"SELECT * FROM documents WHERE symbol IN ({slots}) AND filename=?",
                    [*aliases, filename],
                ).fetchall()
                if len(existing) > 1:
                    raise HTTPException(
                        409,
                        detail={
                            "code": "duplicate_symbol_documents",
                            "message": "同一证券的裸码和完整代码下已有同名资料，请先核对重复文档。",
                        },
                    )
                old = existing[0] if existing else None
                # Keep legacy IDs, stored symbols and vector metadata intact.
                # A suffix-only spelling change must not create a second document.
                stored_symbol = old["symbol"] if old else scope["canonical_symbol"]
                metadata_changed = bool(
                    old
                    and (
                        old["source_type"] != request.source_type
                        or old["available_date"] != available_date
                    )
                )
                if (
                    old
                    and old["file_hash"] == file_hash
                    and not metadata_changed
                    and old["status"] == "READY"
                ):
                    return dict(
                        action="SKIP",
                        document_id=old["document_id"],
                        file_hash=file_hash,
                        diff=dict(CREATE=[], UPDATE=[], DELETE=[], UNCHANGED=[]),
                        embedded_children=0,
                        embedding_calls=0,
                        bm25_tokenized_children=0,
                    )
                document_id = old["document_id"] if old else uuid.uuid4().hex
                old_children = {
                    row["child_id"]: dict(row)
                    for row in connection.execute(
                        "SELECT * FROM children WHERE document_id=?", (document_id,)
                    )
                }
                old_parent_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT parent_id FROM parents WHERE document_id=?", (document_id,)
                    )
                }
                parents, children = parse_markdown(document_id, request.content)
                if not children:
                    raise HTTPException(400, "文档中没有可索引的正文")
                new_children = {child["child_id"]: child for child in children}
                created = [key for key in new_children if key not in old_children]
                updated = [
                    key
                    for key in new_children
                    if key in old_children
                    and (
                        new_children[key]["content"] != old_children[key]["content"]
                        or new_children[key]["retrieval_text"]
                        != old_children[key]["retrieval_text"]
                    )
                ]
                unchanged = [
                    key for key in new_children if key in old_children and key not in updated
                ]
                deleted = [key for key in old_children if key not in new_children]
                to_embed = [new_children[key] for key in created + updated]
                recovering = bool(old and old["status"] != "READY")
                if recovering:
                    to_embed = children
                to_index = (
                    children if recovering else [new_children[key] for key in created + updated]
                )
                connection.execute(
                    "INSERT INTO documents "
                    "(document_id,filename,file_hash,status,symbol,source_type,available_date,ingested_at) "
                    "VALUES (?, ?, ?, 'PROCESSING', ?, ?, ?, ?) "
                    "ON CONFLICT(symbol,filename) DO UPDATE SET status='PROCESSING'",
                    (
                        document_id,
                        filename,
                        file_hash,
                        stored_symbol,
                        request.source_type,
                        available_date,
                        ingested_at,
                    ),
                )
            try:
                if to_embed:
                    self.vectors.upsert(
                        ids=[child["child_id"] for child in to_embed],
                        embeddings=self.embed([child["retrieval_text"] for child in to_embed]),
                        documents=[child["retrieval_text"] for child in to_embed],
                        metadatas=[
                            {"document_id": document_id, "parent_id": child["parent_id"]}
                            for child in to_embed
                        ],
                    )
                vector_ids = set(
                    self.vectors.get(where={"document_id": document_id}, include=[])["ids"]
                )
                stale_vectors = sorted(vector_ids - set(new_children))
                if stale_vectors:
                    self.vectors.delete(ids=stale_vectors)
                with self.db() as connection:
                    connection.executemany(
                        """
                        INSERT INTO parents VALUES
                            (:parent_id,:document_id,:heading_path,:content,:position)
                        ON CONFLICT(parent_id) DO UPDATE SET
                            heading_path=excluded.heading_path, content=excluded.content,
                            position=excluded.position
                        """,
                        parents,
                    )
                    connection.executemany(
                        """
                        INSERT INTO children VALUES
                            (:child_id,:parent_id,:document_id,:content,:retrieval_text,:position)
                        ON CONFLICT(child_id) DO UPDATE SET
                            parent_id=excluded.parent_id, content=excluded.content,
                            retrieval_text=excluded.retrieval_text, position=excluded.position
                        """,
                        children,
                    )
                    connection.executemany(
                        "DELETE FROM children WHERE child_id=?", [(key,) for key in deleted]
                    )
                    new_parent_ids = {parent["parent_id"] for parent in parents}
                    connection.executemany(
                        "DELETE FROM parents WHERE parent_id=?",
                        [(key,) for key in old_parent_ids - new_parent_ids],
                    )
                    for child in to_index:
                        index_child(connection, child)
                    refresh_bm25_stats(connection, document_id)
                    connection.execute(
                        "UPDATE documents SET file_hash=?,status='READY',source_type=?,"
                        "available_date=?,ingested_at=? WHERE document_id=?",
                        (file_hash, request.source_type, available_date, ingested_at, document_id),
                    )
            except Exception as exc:
                with self.db() as connection:
                    connection.execute(
                        "UPDATE documents SET status='ERROR' WHERE document_id=?", (document_id,)
                    )
                # Exception strings from clients may contain URLs or request details.
                LOGGER.error("RAG ingestion failed (%s)", type(exc).__name__)
                raise HTTPException(500, "入库失败，请检查 RAG 服务配置或日志。") from exc
            return dict(
                action="CREATE" if not old else "UPDATE",
                document_id=document_id,
                file_hash=file_hash,
                diff=dict(CREATE=created, UPDATE=updated, DELETE=deleted, UNCHANGED=unchanged),
                embedded_children=len(to_embed),
                embedding_calls=1 if to_embed else 0,
                bm25_tokenized_children=len(to_index),
                recovery=recovering,
                metadata_updated=metadata_changed,
                parent_count=len(parents),
                child_count=len(children),
            )

    def delete_document(self, document_id: str):
        with self.lock, self.db() as connection:
            found = connection.execute(
                "SELECT 1 FROM documents WHERE document_id=?", (document_id,)
            ).fetchone()
            if not found:
                raise HTTPException(404, "文档不存在")
            ids = [
                row[0]
                for row in connection.execute(
                    "SELECT child_id FROM children WHERE document_id=?", (document_id,)
                )
            ]
            bm25_count = connection.execute(
                "SELECT count(*) FROM bm25_docs WHERE document_id=?", (document_id,)
            ).fetchone()[0]
            vector_ids = self.vectors.get(where={"document_id": document_id}, include=[])["ids"]
            if vector_ids:
                self.vectors.delete(ids=vector_ids)
            connection.execute("DELETE FROM documents WHERE document_id=?", (document_id,))
        return dict(
            deleted_document_id=document_id,
            deleted_children=len(ids),
            deleted_bm25_children=bm25_count,
            deleted_vectors=len(vector_ids),
            embedding_calls=0,
        )

    def debug_state(self):
        with self.lock, self.db() as connection:
            counts = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("documents", "parents", "children")
            }
            child_ids = {row[0] for row in connection.execute("SELECT child_id FROM children")}
            bm25_ids = {row[0] for row in connection.execute("SELECT child_id FROM bm25_docs")}
            posting_count = connection.execute("SELECT count(*) FROM bm25_postings").fetchone()[0]
            vector_ids = set(self.vectors.get(include=[])["ids"])
        return dict(
            counts=counts,
            bm25_index_children=len(bm25_ids),
            bm25_posting_count=posting_count,
            child_ids_missing_in_bm25=sorted(child_ids - bm25_ids),
            stale_bm25_ids=sorted(bm25_ids - child_ids),
            chroma_vector_count=len(vector_ids),
            child_ids_missing_in_chroma=sorted(child_ids - vector_ids),
            stale_chroma_ids=sorted(vector_ids - child_ids),
        )

    def rerank(self, query: str, candidates: list[dict]):
        """Return (candidate index, BGE score) pairs, without generation or fallback."""
        key = self.settings.siliconflow_api_key
        api_key = key.get_secret_value() if key else ""
        if not api_key.strip():
            raise RerankError("SILICONFLOW_API_KEY is not configured.")

        import requests

        payload = {
            "model": self.settings.rag_rerank_model,
            "query": query,
            "documents": [child["retrieval_text"] for child in candidates],
            "top_n": len(candidates),
            "return_documents": False,
        }
        try:
            response = requests.post(
                str(self.settings.rag_rerank_url),
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                timeout=(5, 30),
            )
        except requests.RequestException as exc:
            raise RerankError("BGE reranker request failed.") from exc
        trace_id = response.headers.get("x-siliconcloud-trace-id")
        if response.status_code != 200:
            raise RerankError(f"BGE reranker returned HTTP {response.status_code}.")
        try:
            data = response.json()
        except ValueError as exc:
            raise RerankError("BGE reranker returned invalid JSON.") from exc
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list) or len(results) != len(candidates):
            raise RerankError("BGE reranker result count does not match candidates.")
        ranking = []
        seen = set()
        for item in results:
            if not isinstance(item, dict):
                raise RerankError("BGE reranker returned an invalid result item.")
            index, score = item.get("index"), item.get("relevance_score")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(candidates)
                or index in seen
                or not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(score)
            ):
                raise RerankError("BGE reranker returned invalid index or score.")
            seen.add(index)
            ranking.append((index, float(score)))
        ranking.sort(key=lambda item: item[1], reverse=True)
        return ranking, {"response_id": data.get("id"), "trace_id": trace_id}

    def retrieve(self, request: ChatRequest):
        with self.lock, self.db() as connection:
            trace = dict(
                user_query=request.query,
                metadata_filter={
                    "symbol": request.symbol,
                    "cutoff_date": request.cutoff_date.isoformat() if request.cutoff_date else None,
                    "document_id": request.document_id,
                    "required_status": "READY",
                    "excluded_source_types": ["ai_generated"],
                    "unknown_date_policy": "exclude_when_cutoff_is_set",
                },
                eligible_document_count=0,
                eligible_child_count=0,
                bm25_hits=[],
                dense_hits=[],
                rrf_result=[],
                reranker_result=[],
                top_child_hits=[],
                expanded_parents=[],
                generation_context="",
                citation_map={},
                prompt="",
            )
            # SQLite is the metadata authority. Derive the allowed IDs once and
            # pass the same set into every retrieval and evidence expansion stage.
            condition = "WHERE status='READY' AND source_type!='ai_generated'"
            params = []
            if request.symbol:
                scope = self._symbol_scope(request.symbol)
                aliases = scope["aliases"]
                trace["metadata_filter"].update(
                    canonical_symbol=scope["canonical_symbol"],
                    symbol_aliases=aliases,
                    symbol_mapping_status=scope["mapping_status"],
                )
                condition += " AND symbol IN (" + ",".join("?" for _ in aliases) + ")"
                params.extend(aliases)
            if request.cutoff_date:
                condition += " AND available_date IS NOT NULL AND available_date<=?"
                params.append(request.cutoff_date.isoformat())
            if request.document_id:
                condition += " AND document_id=?"
                params.append(request.document_id)
            eligible_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT document_id FROM documents " + condition + " ORDER BY document_id",
                    params,
                )
            ]
            trace["eligible_document_count"] = len(eligible_ids)
            if not eligible_ids:
                return trace
            scope_slots = ",".join("?" for _ in eligible_ids)
            child_count = connection.execute(
                "SELECT count(*) FROM children WHERE document_id IN (" + scope_slots + ")",
                eligible_ids,
            ).fetchone()[0]
            trace["eligible_child_count"] = child_count
            if not child_count:
                return trace

            candidate_count = min(child_count, max(request.top_k * 4, 10))
            bm25_order, trace["bm25_index"] = bm25_search(
                connection, request.query, eligible_ids, candidate_count
            )
            dense_order = []
            if self.vectors.count():
                result = self.vectors.query(
                    query_embeddings=self.embed([request.query]),
                    n_results=min(child_count, max(candidate_count * 2, 20)),
                    where={"document_id": {"$in": eligible_ids}},
                    include=["distances"],
                )
                dense_order = list(zip(result["ids"][0], result["distances"][0]))

            candidate_ids = {key for key, _ in bm25_order}
            candidate_ids.update(key for key, _ in dense_order)
            by_id = {}
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                rows = connection.execute(
                    "SELECT c.* FROM children c WHERE c.child_id IN ("
                    + placeholders
                    + ") AND c.document_id IN ("
                    + scope_slots
                    + ")",
                    [*candidate_ids, *eligible_ids],
                ).fetchall()
                by_id = {row["child_id"]: dict(row) for row in rows}
            trace["bm25_hits"] = [
                hit(by_id[key], score, rank)
                for rank, (key, score) in enumerate(
                    [(key, score) for key, score in bm25_order if key in by_id], 1
                )
            ]
            dense_order = [(key, distance) for key, distance in dense_order if key in by_id][
                :candidate_count
            ]
            trace["dense_hits"] = [
                hit(by_id[key], 1 - distance, rank)
                for rank, (key, distance) in enumerate(dense_order, 1)
            ]
            scores: dict[str, float] = defaultdict(float)
            for ranking in (trace["bm25_hits"], trace["dense_hits"]):
                for item in ranking:
                    scores[item["child_id"]] += 1 / (60 + item["rank"])
            rrf_order = sorted(scores, key=scores.get, reverse=True)[:candidate_count]
            trace["rrf_result"] = [
                hit(by_id[key], scores[key], rank) for rank, key in enumerate(rrf_order, 1)
            ]
            trace["reranker_type"] = self.settings.rag_rerank_model
            if not rrf_order:
                return trace
            try:
                ranking, provider = self.rerank(request.query, [by_id[key] for key in rrf_order])
            except RerankError as exc:
                trace["reranker_error"] = str(exc)
                return trace
            trace["reranker_provider"] = provider
            trace["reranker_result"] = [
                hit(by_id[rrf_order[index]], score, rank)
                for rank, (index, score) in enumerate(ranking, 1)
            ]
            trace["top_child_hits"] = trace["reranker_result"][: request.top_k]

            grouped = {}
            for item in trace["top_child_hits"]:
                parent_id = item["parent_id"]
                if parent_id not in grouped:
                    grouped[parent_id] = dict(
                        parent_id=parent_id,
                        document_id=item["document_id"],
                        evidence_children=[],
                        score=item["score"],
                    )
                grouped[parent_id]["evidence_children"].append(item)
                grouped[parent_id]["score"] = max(grouped[parent_id]["score"], item["score"])
            for parent_id, item in grouped.items():
                parent = connection.execute(
                    """
                    SELECT p.content,p.heading_path,d.filename,d.symbol,d.source_type,
                           d.available_date,d.ingested_at FROM parents p
                    JOIN documents d ON d.document_id=p.document_id WHERE p.parent_id=?
                    AND p.document_id IN (
                    """
                    + scope_slots
                    + ")",
                    [parent_id, *eligible_ids],
                ).fetchone()
                if parent is None:
                    raise HTTPException(500, "引用原文与本次检索范围不一致，请检查索引。")
                item.update(
                    parent_content=parent["content"],
                    heading_path=parent["heading_path"],
                    source=parent["filename"],
                    symbol=parent["symbol"],
                    source_type=parent["source_type"],
                    available_date=parent["available_date"],
                    ingested_at=parent["ingested_at"],
                )
            trace["expanded_parents"] = sorted(
                grouped.values(), key=lambda item: item["score"], reverse=True
            )

            context = []
            context_size = 0
            for number, item in enumerate(trace["expanded_parents"], 1):
                citation_id = str(number)
                header = (
                    f"[{citation_id}] 来源：{item['source']} / {item['heading_path']}\n"
                    f"标的：{item['symbol']}；类型：{item['source_type']}；"
                    f"公开/形成日期：{item['available_date'] or '未知'}\n"
                )
                separator_size = len("\n\n---\n\n") if context else 0
                remaining = 8000 - context_size - separator_size
                if remaining <= len(header):
                    break
                excerpt = item["parent_content"][: remaining - len(header)]
                block = header + excerpt
                trace["citation_map"][citation_id] = dict(
                    citation_id=citation_id,
                    child_id=item["evidence_children"][0]["child_id"],
                    child_ids=[evidence["child_id"] for evidence in item["evidence_children"]],
                    parent_id=item["parent_id"],
                    document_id=item["document_id"],
                    source=item["source"],
                    heading_path=item["heading_path"],
                    symbol=item["symbol"],
                    source_type=item["source_type"],
                    available_date=item["available_date"],
                    ingested_at=item["ingested_at"],
                    excerpt=excerpt,
                )
                context.append(block)
                context_size += separator_size + len(block)
            trace["generation_context"] = "\n\n---\n\n".join(context)
            trace["prompt"] = (
                "请仅依据资料回答。使用资料时在相应句末标注 [编号]；不得编造编号。"
                "资料没有答案时回答‘资料中未提及’。\n\n"
                f"资料：\n{trace['generation_context']}\n\n问题：{request.query}"
            )
            return trace

    def stream_chat(self, request: ChatRequest):
        """Legacy debug SSE, intentionally separate from the research-run protocol."""
        try:
            trace = self.retrieve(request)
        except Exception as exc:
            LOGGER.error("RAG debug retrieval failed (%s)", type(exc).__name__)
            yield sse("citations", {})
            yield sse("error", {"message": "知识库检索失败，请检查配置或日志。", "trace": {}})
            return
        yield sse("citations", trace["citation_map"])
        if trace.get("reranker_error"):
            yield sse("error", {"message": trace["reranker_error"], "trace": trace})
            return
        if not trace["top_child_hits"]:
            answer = "知识库中未检索到相关内容。"
            yield sse("delta", {"text": answer})
            yield sse("done", {"answer": answer, "trace": trace})
            return
        key = self.settings.deepseek_api_key
        api_key = key.get_secret_value() if key else ""
        if not api_key.strip():
            yield sse(
                "error",
                {
                    "message": "未配置 DEEPSEEK_API_KEY；检索链路已完成，LLM 尚未验证。",
                    "trace": trace,
                },
            )
            return

        from openai import OpenAI

        answer_parts = []
        try:
            with OpenAI(api_key=api_key, base_url=str(self.settings.deepseek_base_url)) as client:
                stream = client.chat.completions.create(
                    model=self.settings.deepseek_model,
                    messages=[{"role": "user", "content": trace["prompt"]}],
                    temperature=0.1,
                    stream=True,
                )
                with stream:
                    for chunk in stream:
                        delta = chunk.choices[0].delta.content if chunk.choices else None
                        if delta:
                            answer_parts.append(delta)
                            yield sse("delta", {"text": delta})
            yield sse("done", {"answer": "".join(answer_parts), "trace": trace})
        except Exception as exc:
            LOGGER.error("RAG debug generation failed (%s)", type(exc).__name__)
            yield sse("error", {"message": "LLM 请求失败，请检查配置或日志。", "trace": trace})
