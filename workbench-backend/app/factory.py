"""Application assembly without storage or network work on module import."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.agent.service import ResearchAgentService
from app.api.health import router as health_router
from app.api.rag import router as rag_router
from app.api.research import router as research_router
from app.api.strategy_wiki import router as strategy_wiki_router
from app.core.config import Settings, get_settings
from app.rag.service import RagService
from app.storage.research import ResearchStore
from app.tools.strategy_wiki import StrategyWikiService


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            application.state.rag_service.close()

    application = FastAPI(
        title=config.app_name,
        version=__version__,
        description="Local research Agent with synchronous JSON, saved snapshots and confirmation archives.",
        lifespan=lifespan,
    )
    application.state.settings = config
    application.state.rag_service = RagService(config)
    application.state.strategy_wiki_service = StrategyWikiService(config)
    application.state.research_store = ResearchStore(config.research_db_path)
    application.state.research_service = ResearchAgentService(
        config, application.state.rag_service, application.state.strategy_wiki_service
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Request-ID"],
    )
    application.include_router(health_router, prefix="/api")
    application.include_router(rag_router)
    application.include_router(strategy_wiki_router)
    application.include_router(research_router)
    return application
