"""Same-process evidence retrieval for a server-bound research scope."""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import date
from typing import TYPE_CHECKING

from fastapi import HTTPException
from pydantic import ValidationError

from app.rag.schemas import RetrieveRequest

if TYPE_CHECKING:
    from app.rag.service import RagService

LOGGER = logging.getLogger(__name__)


def _failure(code: str, message: str) -> dict:
    return {
        "ok": False,
        "found": False,
        "error": code,
        "message": message,
        "context": "",
        "citations": {},
    }


def search_fundamentals(
    query: str,
    *,
    symbol: str,
    cutoff_date: str | date | None = None,
    top_k: int = 5,
    service: RagService,
) -> dict:
    """Return actual RAG evidence; symbol and date are supplied by the server.

    The underlying RAG owns retrieval/context selection. This adapter adds no
    result character limit and does not call the application's HTTP endpoint.
    """
    try:
        request = RetrieveRequest(query=query, symbol=symbol, cutoff_date=cutoff_date, top_k=top_k)
        trace = service.retrieve(request)
        if trace.get("reranker_error"):
            return _failure("rerank_failed", "基本面资料重排失败，请检查后端配置与日志。")
        found = bool(trace["top_child_hits"])
        context, citations = trace["generation_context"], trace["citation_map"]
        if not isinstance(context, str) or not isinstance(citations, dict):
            return _failure("invalid_tool_response", "基本面检索返回了无效证据结构。")
        if found and (not context.strip() or not citations):
            return _failure("invalid_tool_response", "基本面检索结果缺少可核对的原文或引用。")
        return {
            "ok": True,
            "found": found,
            "query": request.query,
            "context": context if found else "",
            "citations": deepcopy(citations) if found else {},
            "scope": deepcopy(trace.get("metadata_filter", {})),
            "coverage": {
                "eligible_document_count": trace.get("eligible_document_count", 0),
                "eligible_child_count": trace.get("eligible_child_count", 0),
            },
        }
    except ValidationError:
        return _failure("invalid_arguments", "基本面查询参数无效。")
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = detail.get("code")
        if code == "ambiguous_symbol":
            return _failure(code, "证券代码存在歧义，请使用可核对的完整代码。")
        if code == "source_changed":
            return _failure(code, "代码映射源已变化，请重新发起研究。")
        return _failure("rag_unavailable", "基本面检索服务不可用，请检查后端日志。")
    except Exception as exc:
        LOGGER.warning("Research fundamentals retrieval failed (%s)", type(exc).__name__)
        return _failure("retrieval_failed", "基本面检索失败，请检查后端日志。")
