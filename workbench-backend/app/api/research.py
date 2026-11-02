"""Synchronous research with server-owned snapshots and simple confirmation."""

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.agent.schemas import ResearchRequest
from app.agent.service import ResearchAgentService
from app.storage.research import ResearchStorageError, ResearchStore

LOGGER = logging.getLogger(__name__)
router = APIRouter(tags=["research"])


def get_research_service(request: Request) -> ResearchAgentService:
    return request.app.state.research_service


def get_research_store(request: Request) -> ResearchStore:
    return request.app.state.research_store


ServiceDependency = Annotated[ResearchAgentService, Depends(get_research_service)]
StoreDependency = Annotated[ResearchStore, Depends(get_research_store)]


def storage_error(exc: ResearchStorageError, result: dict | None = None):
    LOGGER.warning("Research snapshot storage rejected (%s)", exc.code)
    body = {
        **(result or {}),
        "status": "failed",
        "archive_status": None,
        "error": {"code": exc.code, "message": exc.message},
    }
    return JSONResponse(status_code=exc.status_code, content=body)


@router.post("/api/research/run")
def run_research(body: ResearchRequest, service: ServiceDependency, store: StoreDependency):
    result = service.run(body)
    code = (result.get("error") or {}).get("code")
    if code != "research_busy":
        try:
            result = store.save(result)
        except ResearchStorageError as exc:
            return storage_error(exc, result)
    if result["status"] == "success":
        return result
    if code == "research_busy":
        status = 409
    elif code == "model_not_configured":
        status = 503
    elif code in {"model_timeout", "tool_timeout", "run_timeout"}:
        status = 504
    else:
        status = 502
    return JSONResponse(status_code=status, content=result)


@router.get("/api/research/{run_id}")
def research_detail(run_id: str, store: StoreDependency):
    try:
        return store.get(run_id)
    except ResearchStorageError as exc:
        return storage_error(exc)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post("/api/research/{run_id}/confirm")
def confirm_research(
    run_id: str, store: StoreDependency, body: ConfirmRequest | None = Body(default=None)
):
    try:
        return store.confirm(run_id)
    except ResearchStorageError as exc:
        return storage_error(exc)


@router.get("/api/records")
def list_records(store: StoreDependency, limit: Annotated[int, Query(ge=1, le=100)] = 50):
    try:
        return store.list_records(limit)
    except ResearchStorageError as exc:
        return storage_error(exc)


@router.get("/api/records/{run_id}")
def record_detail(run_id: str, store: StoreDependency):
    try:
        return store.get(run_id, archived_only=True)
    except ResearchStorageError as exc:
        return storage_error(exc)
