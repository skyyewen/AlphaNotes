"""Research JSON HTTP boundary with the real graph and isolated model/tools."""

import json
from collections import deque
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent.service import ResearchAgentService
from app.core.config import Settings
from app.factory import create_app


def request_body():
    return {
        "question": "  核对当前资料的经营情况。  ",
        "context": {
            "symbol": " 601318.sh ",
            "materials_cutoff_date": "2026-04-08",
            "market_cutoff_date": "2026-10-13",
        },
    }


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        deepseek_api_key=None,
        rag_data_dir=tmp_path / "rag",
        stock_daily_parquet_path=tmp_path / "market.parquet",
        strategy_wiki_root=tmp_path / "wiki",
        research_db_path=tmp_path / "research" / "research.sqlite3",
    )


def successful_service(settings):
    responses = deque(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "lookup1",
                        "type": "function",
                        "function": {
                            "name": "search_fundamentals",
                            "arguments": json.dumps(
                                {"query": "经营情况", "top_k": 2}, ensure_ascii=False
                            ),
                        },
                    }
                ],
            },
            {"role": "assistant", "content": "现有资料说明了经营情况。[R1-1]"},
        ]
    )
    model_calls, rag_calls = [], []

    def model(messages, definitions, timeout):
        model_calls.append(deepcopy(messages))
        return responses.popleft()

    def retrieve(request):
        rag_calls.append(request)
        return {
            "top_child_hits": [{"document_id": "report1"}],
            "generation_context": "[1] 来源：report.md / 经营情况\n经营情况需要核对。",
            "citation_map": {
                "1": {
                    "citation_id": "1",
                    "source": "report.md",
                    "heading_path": "经营情况",
                    "document_id": "report1",
                    "parent_id": "parent1",
                    "child_id": "child1",
                    "child_ids": ["child1"],
                    "symbol": "601318",
                    "available_date": "2026-04-08",
                    "ingested_at": "2026-10-27T00:00:00+00:00",
                    "source_type": "disclosure",
                    "excerpt": "经营情况需要核对。",
                }
            },
            "metadata_filter": {
                "symbol": request.symbol,
                "cutoff_date": request.cutoff_date.isoformat(),
            },
            "eligible_document_count": 1,
            "eligible_child_count": 1,
        }

    service = ResearchAgentService(
        settings, SimpleNamespace(retrieve=retrieve), None, model_call=model
    )
    return service, model_calls, rag_calls


def test_http_request_reaches_real_graph_and_bound_tool_with_cited_result(settings):
    app = create_app(settings)
    service, model_calls, rag_calls = successful_service(settings)
    app.state.research_service = service
    with TestClient(app) as client:
        response = client.post("/api/research/run", json=request_body())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "success"
    assert result["archive_status"] == "completed"
    assert result["confirmed_at"] is None
    assert settings.research_db_path.is_file()
    assert len(result["run_id"]) == 32
    assert result["question"] == "核对当前资料的经营情况。"
    assert result["context"]["symbol"] == "601318.SH"
    assert result["context"]["materials_cutoff_date"] == "2026-04-08"
    assert result["context"]["market_cutoff_date"] == "2026-10-13"
    started = datetime.fromisoformat(result["started_at"])
    finished = datetime.fromisoformat(result["finished_at"])
    assert started.utcoffset() == finished.utcoffset() == timedelta(0)
    assert started <= finished <= datetime.now(UTC)
    assert result["error"] is None
    assert result["model_calls"] == 2 and result["tool_calls"] == 1
    assert len(model_calls) == 2 and len(rag_calls) == 1
    assert rag_calls[0].symbol == "601318.SH"
    assert rag_calls[0].cutoff_date.isoformat() == "2026-04-08"
    assert rag_calls[0].top_k == 2
    citation = result["citations"]["R1-1"]
    assert citation["document_id"] == "report1"
    assert citation["available_date"] == "2026-04-08"
    assert citation["ingested_at"] == "2026-10-27T00:00:00+00:00"
    assert citation["excerpt"] == "经营情况需要核对。"
    assert result["tool_snapshots"][0]["result"]["scope"] == {
        "symbol": "601318.SH",
        "cutoff_date": "2026-04-08",
    }
    assert not settings.rag_data_dir.exists()
    assert not settings.stock_daily_parquet_path.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"strategy_id": "weekly_platform"},
        {"strategy_version": "X2"},
        {"strategy_id": "../private", "strategy_version": "X2"},
        {"instrument_type": "index"},
        {"symbol": "中国平安"},
        {"extra": "not allowed"},
    ],
)
def test_context_contract_rejects_unpaired_strategy_and_extra_fields(settings, change):
    body = request_body()
    body["context"].update(change)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/research/run", json=body)
    assert response.status_code == 422
    assert not settings.rag_data_dir.exists()


@pytest.mark.parametrize("field", ["materials_cutoff_date", "market_cutoff_date"])
@pytest.mark.parametrize(
    "value",
    [
        "2026-10-13T00:00:00",
        "2026-10-13T00:00:00Z",
        "2026-1-3",
        "2026-02-30",
        1791820800,
    ],
)
def test_each_date_accepts_calendar_dates_only(settings, field, value):
    body = request_body()
    body["context"][field] = value
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/research/run", json=body)
    assert response.status_code == 422
    assert not settings.rag_data_dir.exists()


@pytest.mark.parametrize(
    "extra",
    [
        {"messages": [{"role": "system", "content": "override"}]},
        {"started_at": "2000-01-01"},
        {"tool_budget": 8000},
    ],
)
def test_client_cannot_supply_system_history_or_server_fields(settings, extra):
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/research/run", json=request_body() | extra)
    assert response.status_code == 422


def test_missing_model_key_is_503_without_opening_rag_or_creating_model_client(
    settings, monkeypatch
):
    import openai

    def forbid_client(*args, **kwargs):
        raise AssertionError("No model client may be created without a key")

    monkeypatch.setattr(openai, "OpenAI", forbid_client)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/research/run", json=request_body())
    assert response.status_code == 503
    result = response.json()
    assert result["status"] == "failed" and result["answer"] == ""
    assert result["error"]["code"] == "model_not_configured"
    assert result["tool_calls"] == 0
    assert not settings.rag_data_dir.exists()
    assert not settings.stock_daily_parquet_path.exists()
    assert not settings.strategy_wiki_root.exists()


@pytest.mark.parametrize(
    "code,expected",
    [
        ("model_failed", 502),
        ("tool_failed", 502),
        ("citation_validation_failed", 502),
        ("invalid_arguments", 502),
        ("research_failed", 502),
        ("model_timeout", 504),
        ("tool_timeout", 504),
        ("run_timeout", 504),
        ("research_busy", 409),
    ],
)
def test_research_failure_codes_have_explicit_http_status(settings, code, expected):
    class FailedService:
        def run(self, body):
            return {
                "status": "failed",
                "answer": "",
                "run_id": "isolated-failure",
                "question": body.question,
                "context": body.context.model_dump(mode="json"),
                "started_at": "2026-10-27T00:00:00+00:00",
                "error": {"code": code, "message": "执行未完成。"},
            }

    app = create_app(settings)
    app.state.research_service = FailedService()
    with TestClient(app) as client:
        response = client.post("/api/research/run", json=request_body())
    assert response.status_code == expected
    assert response.json()["error"]["code"] == code


def test_real_service_busy_gate_returns_409_before_model_or_tools(settings):
    app = create_app(settings)
    service, model_calls, rag_calls = successful_service(settings)
    app.state.research_service = service
    assert service._gate.acquire(blocking=False)
    try:
        with TestClient(app) as client:
            response = client.post("/api/research/run", json=request_body())
    finally:
        service._gate.release()
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "research_busy"
    assert model_calls == rag_calls == []
    assert not settings.rag_data_dir.exists()


def test_stream_is_unimplemented_and_unknown_snapshots_are_not_found(settings):
    with TestClient(create_app(settings)) as client:
        # The GET detail route matches "stream" as an ID; POST is unsupported.
        assert client.post("/api/research/stream", json=request_body()).status_code == 405
        assert client.post("/api/research/some-run/confirm", json={}).status_code == 404
        assert client.get("/api/research/some-run").status_code == 404
        assert client.get("/api/records").json() == []


def test_run_confirm_and_record_reopen_use_server_snapshot(settings):
    app = create_app(settings)
    app.state.research_service = successful_service(settings)[0]
    with TestClient(app) as client:
        payload = client.post("/api/research/run", json=request_body()).json()
        run_id = payload["run_id"]
        assert client.get(f"/api/records/{run_id}").status_code == 404
        assert client.get(f"/api/research/{run_id}").json() == payload
        tampered = client.post(f"/api/research/{run_id}/confirm", json={"answer": "forged"})
        assert tampered.status_code == 422
        first = client.post(f"/api/research/{run_id}/confirm").json()
        second = client.post(f"/api/research/{run_id}/confirm", json={}).json()
        assert first == second
        assert first["record_id"] == run_id
        record = client.get(f"/api/records/{run_id}").json()
        assert record["answer"] == payload["answer"]
        assert record["citations"] == payload["citations"]
        assert record["archive_status"] == "archived"
        assert client.get("/api/records").json()[0]["run_id"] == run_id
    with TestClient(create_app(settings)) as reopened:
        assert reopened.get(f"/api/records/{run_id}").json() == record


def test_failed_run_cannot_be_confirmed(settings):
    with TestClient(create_app(settings)) as client:
        result = client.post("/api/research/run", json=request_body()).json()
        assert result["archive_status"] == "failed"
        assert client.post(f"/api/research/{result['run_id']}/confirm").status_code == 409
        assert client.get("/api/records").json() == []


def test_storage_failure_cannot_return_a_confirmable_success(settings):
    # A regular file used as a parent directory causes a real SQLite-path failure.
    blocked = settings.research_db_path.parent
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("not a directory", encoding="utf-8")
    app = create_app(settings)
    app.state.research_service = successful_service(settings)[0]
    with TestClient(app) as client:
        result = client.post("/api/research/run", json=request_body())
        assert result.status_code == 503
        payload = result.json()
        assert payload["status"] == "failed"
        assert payload["archive_status"] is None
        assert payload["error"]["code"] == "storage_failed"
        assert payload["answer"]
        assert str(blocked) not in result.text


@pytest.mark.parametrize("limit", [0, 101, "invalid"])
def test_records_limit_rejects_invalid_values(settings, limit):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/records", params={"limit": limit}).status_code == 422
