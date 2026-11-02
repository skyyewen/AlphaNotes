import pytest
from pydantic import ValidationError

from app.core.config import BACKEND_ROOT, Settings


def test_defaults_do_not_require_a_key():
    settings = Settings(_env_file=None)
    assert settings.deepseek_api_key is None
    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 8000
    assert settings.siliconflow_api_key is None
    assert settings.rag_data_dir == BACKEND_ROOT / "data" / "rag"
    assert settings.research_db_path == BACKEND_ROOT / "data" / "research" / "research.sqlite3"


def test_env_path_is_anchored_to_backend():
    assert Settings.model_config["env_file"] == BACKEND_ROOT / ".env"
    assert (BACKEND_ROOT / "app" / "main.py").is_file()


def test_public_env_example_can_be_loaded_without_a_key():
    settings = Settings(_env_file=BACKEND_ROOT / ".env.example")
    assert settings.app_port == 8000
    assert settings.deepseek_api_key.get_secret_value() == ""
    assert settings.siliconflow_api_key.get_secret_value() == ""
    assert "http://127.0.0.1:5173" in settings.cors_origins


def test_environment_overrides_are_applied(monkeypatch):
    monkeypatch.setenv("APP_PORT", "8010")
    monkeypatch.setenv("CORS_ORIGINS", '["http://localhost:5173"]')
    settings = Settings(_env_file=None)
    assert settings.app_port == 8010
    assert settings.cors_origins == ["http://localhost:5173"]


@pytest.mark.parametrize("port", [0, 65536, "not-a-port"])
def test_invalid_ports_are_rejected(port):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_port=port)


@pytest.mark.parametrize(
    "origin",
    ["*", "http://*.example.com", "file:///tmp/ui", "http://localhost:5173/private"],
)
def test_cors_does_not_accept_wildcards_or_paths(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins=[origin])


def test_origins_are_normalized_and_deduplicated():
    settings = Settings(
        _env_file=None, cors_origins=[" http://localhost:5173/ ", "http://localhost:5173"]
    )
    assert settings.cors_origins == ["http://localhost:5173"]


def test_invalid_rerank_protocol_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_rerank_url="file:///private")


def test_relative_data_dir_uses_backend_root():
    assert Settings(_env_file=None, rag_data_dir="data/isolated").rag_data_dir == (
        BACKEND_ROOT / "data" / "isolated"
    )
    assert Settings(
        _env_file=None, research_db_path="data/test/research.sqlite3"
    ).research_db_path == (BACKEND_ROOT / "data" / "test" / "research.sqlite3")


def test_stock_data_path_uses_backend_root_without_opening_file(tmp_path):
    relative = "data/market/daily.parquet"
    assert (
        Settings(_env_file=None, stock_daily_parquet_path=relative).stock_daily_parquet_path
        == (BACKEND_ROOT / relative).resolve()
    )
    missing = tmp_path / "not-created" / "daily.parquet"
    settings = Settings(_env_file=None, stock_daily_parquet_path=missing)
    assert settings.stock_daily_parquet_path == missing.resolve()
    assert not missing.parent.exists()


def test_dotenv_secret_is_available_without_modifying_process_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    env_file = tmp_path / "settings.env"
    env_file.write_text("SILICONFLOW_API_KEY=unit-test-placeholder\n", encoding="utf-8")
    settings = Settings(_env_file=env_file)
    assert settings.siliconflow_api_key.get_secret_value() == "unit-test-placeholder"
    import os

    assert "SILICONFLOW_API_KEY" not in os.environ


def test_secret_representation_is_masked():
    settings = Settings(
        _env_file=None,
        deepseek_api_key="unit-test-not-a-real-key",
        siliconflow_api_key="unit-test-not-a-real-key",
    )
    assert "unit-test-not-a-real-key" not in repr(settings)
