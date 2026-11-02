import socket
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.factory import create_app


@pytest.fixture
def client(tmp_path):
    application = create_app(
        Settings(_env_file=None, app_env="test", research_db_path=tmp_path / "research.sqlite3")
    )
    with TestClient(application) as test_client:
        yield test_client


def test_health_returns_plain_data_without_network(client, monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("Health must not connect to a model or retrieval service.")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "workbench-backend",
        "version": "0.1.0",
        "environment": "test",
        "stage": "bootstrap",
    }
    assert "data" not in response.json()


def test_openapi_only_lists_implemented_endpoints(client):
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert set(schema.json()["paths"]) == {
        "/api/health",
        "/api/documents",
        "/api/documents/{document_id}",
        "/api/debug/state",
        "/api/retrieve",
        "/api/chat/stream",
        "/api/strategies",
        "/api/strategy-wiki/query",
        "/api/research/run",
        "/api/research/{run_id}",
        "/api/research/{run_id}/confirm",
        "/api/records",
        "/api/records/{run_id}",
    }
    assert client.get("/docs").status_code == 200


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("POST", "/api/research/stream", 405),
        ("POST", "/api/research/not-created/confirm", 404),
    ],
)
def test_unimplemented_business_does_not_fake_success(client, method, path, expected):
    assert client.request(method, path).status_code == expected


@pytest.mark.parametrize("origin", ["http://127.0.0.1:5173", "http://localhost:4173"])
def test_known_frontend_origin_is_allowed(client, origin):
    response = client.options(
        "/api/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers


def test_unknown_origin_is_not_allowed(client):
    response = client.options(
        "/api/health",
        headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_startup_and_debug_page_do_not_initialize_rag(tmp_path):
    data_dir = tmp_path / "unused-rag"
    research_db = tmp_path / "research" / "research.sqlite3"
    application = create_app(
        Settings(_env_file=None, rag_data_dir=data_dir, research_db_path=research_db)
    )
    with TestClient(application) as test_client:
        assert test_client.get("/api/health").status_code == 200
        assert test_client.get("/openapi.json").status_code == 200
        assert test_client.get("/rag/debug").status_code == 200
    assert not data_dir.exists()
    assert not research_db.parent.exists()


def test_health_never_returns_keys_or_source_paths():
    application = create_app(Settings(_env_file=None, deepseek_api_key="unit-test-not-a-real-key"))
    with TestClient(application) as test_client:
        text = test_client.get("/api/health").text
    assert "unit-test-not-a-real-key" not in text
    assert "rag_data_dir" not in text
    assert "F:/Desktop" not in text


def test_cold_factory_import_has_no_rag_storage_or_dependency_side_effects(tmp_path):
    script = """
import socket
import sys
from pathlib import Path

def reject_network(*args, **kwargs):
    raise AssertionError('Application construction must not connect to anything.')

socket.create_connection = reject_network
socket.socket.connect = reject_network
from app.core.config import Settings
from app.factory import create_app

data_dir = Path(sys.argv[1])
application = create_app(Settings(_env_file=None, rag_data_dir=data_dir))
assert not data_dir.exists()
for module in ('chromadb', 'jieba', 'onnxruntime', 'openai', 'requests'):
    assert module not in sys.modules, module
"""
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "cold-rag")],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
