"""Configuration loads only this backend's .env; it never loads old project secrets."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEMO_DATA_ROOT = BACKEND_ROOT.parent / "demo-data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="workbench-backend", min_length=1)
    app_env: Literal["development", "test", "production"] = "development"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_reload: bool = False
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
        ]
    )

    # Local RAG is in this process. The old Agent HTTP adapter is not used here.
    rag_data_dir: Path = BACKEND_ROOT / "data" / "rag"
    research_db_path: Path = BACKEND_ROOT / "data" / "research" / "research.sqlite3"
    stock_daily_parquet_path: Path = DEMO_DATA_ROOT / "market" / "daily.parquet"
    strategy_wiki_root: Path = DEMO_DATA_ROOT / "strategy-wiki"
    siliconflow_api_key: SecretStr | None = None
    rag_rerank_url: AnyHttpUrl = "https://api.siliconflow.cn/v1/rerank"
    rag_rerank_model: str = Field(default="BAAI/bge-reranker-v2-m3", min_length=1)
    deepseek_base_url: AnyHttpUrl = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_api_key: SecretStr | None = None
    agent_max_model_calls: int = Field(default=6, ge=1, le=30)
    agent_max_tool_calls: int = Field(default=8, ge=1, le=60)
    agent_model_timeout_seconds: float = Field(default=45, gt=0, le=300)
    agent_tool_timeout_seconds: float = Field(default=30, gt=0, le=300)
    agent_total_timeout_seconds: float = Field(default=180, gt=0, le=1800)

    @field_validator(
        "rag_data_dir", "research_db_path", "stock_daily_parquet_path", "strategy_wiki_root"
    )
    @classmethod
    def resolve_rag_data_dir(cls, value: Path) -> Path:
        # Relative paths must not depend on the shell's working directory.
        path = value if value.is_absolute() else BACKEND_ROOT / value
        return path.resolve()

    @field_validator("cors_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        origins = []
        for value in values:
            origin = value.strip().rstrip("/")
            parts = urlsplit(origin)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or parts.path
                or parts.query
                or parts.fragment
                or "*" in origin
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins without paths.")
            origins.append(origin)
        return list(dict.fromkeys(origins))


@lru_cache
def get_settings() -> Settings:
    return Settings()
