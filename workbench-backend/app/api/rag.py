"""Compatibility API for the migrated RAG; research SSE is a separate future API."""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.rag.schemas import ChatRequest, RetrieveRequest, UploadRequest
from app.rag.service import RagService

LOGGER = logging.getLogger(__name__)
router = APIRouter(tags=["rag"])
DEBUG_PAGE = Path(__file__).resolve().parents[1] / "rag" / "assets" / "index.html"


def get_rag_service(request: Request) -> RagService:
    return request.app.state.rag_service


RagDependency = Annotated[RagService, Depends(get_rag_service)]


@router.get("/rag/debug", include_in_schema=False)
def debug_page():
    return FileResponse(DEBUG_PAGE)


@router.get("/api/documents")
def list_documents(service: RagDependency):
    return service.list_documents()


@router.post("/api/documents")
def ingest(body: UploadRequest, service: RagDependency):
    return service.ingest(body)


@router.delete("/api/documents/{document_id}")
def delete_document(document_id: str, service: RagDependency):
    return service.delete_document(document_id)


@router.get("/api/debug/state")
def debug_state(service: RagDependency):
    return service.debug_state()


@router.post("/api/retrieve")
def retrieve(body: RetrieveRequest, service: RagDependency):
    """Return evidence only. This path never calls a generation model."""
    trace = service.retrieve(body)
    if trace.get("reranker_error"):
        LOGGER.error("RAG reranking failed: %s", trace["reranker_error"])
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "error": "rerank_failed",
                "message": "知识库重排序失败，请检查 RAG 服务配置或日志。",
            },
        )
    found = bool(trace["top_child_hits"])
    return {
        "ok": True,
        "query": body.query,
        "found": found,
        "context": trace["generation_context"] if found else "",
        "citations": trace["citation_map"] if found else {},
    }


@router.post("/api/chat/stream")
def debug_chat(body: ChatRequest, service: RagDependency):
    """Legacy debug protocol; do not connect it to the research run adapter."""
    return StreamingResponse(
        service.stream_chat(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
