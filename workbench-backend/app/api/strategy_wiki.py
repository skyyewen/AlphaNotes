"""Small catalog/query API for the same read-only Python Wiki tool."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.tools.strategy_wiki import StrategyWikiArgs, StrategyWikiService

router = APIRouter(tags=["strategy-wiki"])
ERROR_STATUS = {
    "invalid_arguments": 422,
    "invalid_path": 400,
    "version_not_found": 404,
    "document_not_found": 404,
    "budget_exceeded": 413,
    "source_changed": 409,
    "document_changed": 409,
}


def get_strategy_wiki_service(request: Request) -> StrategyWikiService:
    return request.app.state.strategy_wiki_service


WikiDependency = Annotated[StrategyWikiService, Depends(get_strategy_wiki_service)]


def _response(result: dict):
    if result["ok"]:
        return result
    return JSONResponse(status_code=ERROR_STATUS.get(result["error"], 503), content=result)


@router.get("/api/strategies")
def list_strategies(service: WikiDependency):
    return _response(service.catalog())


@router.post("/api/strategy-wiki/query")
def query_strategy(body: StrategyWikiArgs, service: WikiDependency):
    return _response(service.query(body))
