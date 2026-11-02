"""Agent adapters bind frozen scope while preserving real evidence metadata."""

import json
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.agent import tools as tool_module
from app.agent.tools import ResearchTools
from app.core.config import Settings
from app.tools.fundamentals import search_fundamentals


@pytest.fixture
def context():
    return SimpleNamespace(
        symbol="601318.SH",
        instrument_type="stock",
        materials_cutoff_date=date(2026, 4, 8),
        market_cutoff_date=date(2026, 10, 13),
        strategy_id=None,
        strategy_version=None,
    )


@pytest.fixture
def adapters(tmp_path, monkeypatch):
    citation = {
        "citation_id": "1",
        "source": "report.md",
        "heading_path": "经营情况",
        "document_id": "doc1",
        "parent_id": "p1",
        "child_id": "c1",
        "child_ids": ["c1"],
        "symbol": "601318",
        "source_type": "disclosure",
        "available_date": "2026-04-08",
        "ingested_at": "2026-10-27T00:00:00+00:00",
        "excerpt": "收入情况，指标尚未披露。",
        "optional_metric": None,
    }
    trace = {
        "top_child_hits": [{"child_id": "c1"}],
        "generation_context": "[1] 来源：report.md / 经营情况\n收入情况，指标尚未披露。",
        "citation_map": {"1": citation},
        "metadata_filter": {
            "symbol": "601318.SH",
            "symbol_aliases": ["601318.SH", "601318"],
            "cutoff_date": "2026-04-08",
            "excluded_source_types": ["ai_generated"],
        },
        "eligible_document_count": 1,
        "eligible_child_count": 2,
    }
    rag = SimpleNamespace(calls=[], trace=trace)

    def retrieve(request):
        rag.calls.append(request)
        return rag.trace

    rag.retrieve = retrieve
    wiki = SimpleNamespace(
        calls=[],
        result={
            "ok": True,
            "found": True,
            "status": "found",
            "selection": {"strategy_id": "weekly_platform", "version": "X2"},
            "context": "[W1] 来源：wiki/rules.md / 规则\n" + "规则原文。" * 7000,
            "citations": {
                "W1": {
                    "citation_id": "W1",
                    "source": "wiki/rules.md",
                    "heading_path": "规则",
                    "version": "X2",
                    "excerpt": "规则原文。" * 7000,
                    "content_sha256": "abc",
                }
            },
            "documents": [{"version": "X2", "reading_mode": "full_page"}],
            "truncated": False,
        },
    )

    def query(args):
        wiki.calls.append(args)
        return wiki.result

    wiki.query = query

    def reject_market(*args, **kwargs):
        raise AssertionError("market reader must be explicitly supplied by each test")

    monkeypatch.setattr(tool_module, "query_stock_data", reject_market)
    settings = Settings(
        _env_file=None,
        rag_data_dir=tmp_path / "rag",
        stock_daily_parquet_path=tmp_path / "market.parquet",
        strategy_wiki_root=tmp_path / "wiki",
    )
    return ResearchTools(settings, rag, wiki)


def test_definitions_never_expose_server_owned_scope_or_file_access(adapters, context):
    definitions = {
        entry["function"]["name"]: entry["function"] for entry in adapters.definitions(context)
    }
    assert set(definitions) == {"search_fundamentals", "query_stock_data"}
    assert set(definitions["search_fundamentals"]["parameters"]["properties"]) == {"query", "top_k"}
    assert set(definitions["query_stock_data"]["parameters"]["properties"]) == {
        "limit",
        "start_date",
        "end_date",
    }
    context.strategy_id, context.strategy_version = "weekly_platform", "X2"
    wiki = {item["function"]["name"]: item["function"] for item in adapters.definitions(context)}[
        "query_strategy_wiki"
    ]
    assert set(wiki["parameters"]["properties"]) == {"include_evidence"}
    assert wiki["parameters"]["additionalProperties"] is False


def test_fundamentals_injects_only_materials_date_and_preserves_all_metadata(adapters, context):
    result = adapters.execute("search_fundamentals", {"query": " 经营情况 ", "top_k": 3}, context)
    assert result["ok"] is True and result["found"] is True
    request = adapters.rag_service.calls[0]
    assert request.symbol == "601318.SH"
    assert request.cutoff_date == date(2026, 4, 8)
    assert request.top_k == 3 and request.query == "经营情况"
    citation = result["citations"]["1"]
    assert citation["available_date"] == "2026-04-08"
    assert citation["ingested_at"] == "2026-10-27T00:00:00+00:00"
    assert citation["optional_metric"] is None
    assert citation["excerpt"] == "收入情况，指标尚未披露。"
    result["citations"]["1"]["excerpt"] = "consumer edit"
    assert adapters.rag_service.trace["citation_map"]["1"]["excerpt"] != "consumer edit"


@pytest.mark.parametrize(
    "extra",
    [
        {"symbol": "000001.SH"},
        {"cutoff_date": "2030-01-01"},
        {"document_id": "other"},
        {"settings": {}},
        {"top_k": True},
        {"top_k": 21},
        {"query": " "},
    ],
)
def test_fundamentals_rejects_injected_scope_and_invalid_selection(adapters, context, extra):
    result = adapters.execute("search_fundamentals", {"query": "经营情况"} | extra, context)
    assert result["ok"] is False and result["error"] == "invalid_arguments"
    assert adapters.rag_service.calls == []


def test_fundamentals_no_hit_is_not_a_reranker_failure(adapters, context):
    adapters.rag_service.trace.update(top_child_hits=[], generation_context="", citation_map={})
    empty = adapters.execute("search_fundamentals", {"query": "没有资料"}, context)
    assert empty["ok"] is True and empty["found"] is False
    assert empty["context"] == "" and empty["citations"] == {}
    adapters.rag_service.trace["reranker_error"] = "secret provider body"
    failed = adapters.execute("search_fundamentals", {"query": "经营情况"}, context)
    assert failed["ok"] is False and failed["error"] == "rerank_failed"
    assert "secret" not in json.dumps(failed)


def test_fundamentals_service_errors_are_safely_classified(adapters, context):
    def fail(request):
        raise HTTPException(409, detail={"code": "ambiguous_symbol", "message": "private details"})

    adapters.rag_service.retrieve = fail
    result = search_fundamentals("经营情况", symbol=context.symbol, service=adapters.rag_service)
    assert result["error"] == "ambiguous_symbol"
    assert "private" not in json.dumps(result)


def test_wiki_requires_bound_version_and_does_not_truncate(adapters, context):
    assert adapters.execute("query_strategy_wiki", {}, context)["error"] == "tool_unavailable"
    context.strategy_id, context.strategy_version = "weekly_platform", "X2"
    result = adapters.execute("query_strategy_wiki", {"include_evidence": True}, context)
    args = adapters.wiki_service.calls[0]
    assert args.strategy_id == "weekly_platform" and args.version == "X2"
    assert args.query is None and args.document_id is None and args.max_chars is None
    assert args.include_evidence is True
    assert len(result["context"]) > 32000
    assert result["truncated"] is False
    assert result["citations"]["W1"]["version"] == "X2"
    assert result["scope"] == {"strategy_id": "weekly_platform", "strategy_version": "X2"}


@pytest.mark.parametrize(
    "arguments",
    [
        {"version": "X0"},
        {"strategy_id": "other"},
        {"query": "另一套策略"},
        {"document_id": "evidence"},
        {"max_chars": 2000},
        {"include_evidence": "true"},
    ],
)
def test_wiki_cannot_switch_strategy_or_change_server_budget(adapters, context, arguments):
    context.strategy_id, context.strategy_version = "weekly_platform", "X2"
    result = adapters.execute("query_strategy_wiki", arguments, context)
    assert result["error"] == "invalid_arguments"
    assert adapters.wiki_service.calls == []


def test_market_scope_and_snapshot_preserve_actual_dates_and_nulls(adapters, context, monkeypatch):
    snapshot = {
        "ok": True,
        "found": True,
        "symbol": "601318.SH",
        "source_file": "daily.parquet",
        "source_id": "daily.parquet:123:456",
        "adjustment": "none",
        "units": {"price": "CNY", "volume": "share", "amount": "CNY"},
        "actual_range": {"start_date": "2026-10-12", "end_date": "2026-10-13"},
        "requested_range": {
            "start_date": "2026-10-01",
            "end_date": "2030-01-01",
            "cutoff_date": "2026-10-13",
        },
        "data": [{"date": "2026-10-13", "close": 10.0}],
        "pe": None,
    }
    calls = []

    def market(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return deepcopy(snapshot)

    monkeypatch.setattr(tool_module, "query_stock_data", market)
    result = adapters.execute(
        "query_stock_data",
        {
            "limit": 2,
            "start_date": "2026-10-01",
            "end_date": "2030-01-01",
        },
        context,
    )
    symbol, args = calls[0]
    assert symbol == "601318.SH" and args["cutoff_date"] == date(2026, 10, 13)
    assert args["cutoff_date"] != context.materials_cutoff_date
    assert args["settings"] is adapters.settings
    assert result["snapshot"] == snapshot
    assert result["snapshot"]["pe"] is None
    citation = result["citations"]["D1"]
    assert citation["actual_range"]["end_date"] == "2026-10-13"
    assert json.loads(citation["excerpt"]) == snapshot
    assert citation["adjustment"] == "none"
    result["snapshot"]["data"][0]["close"] = 100
    assert snapshot["data"][0]["close"] == 10.0


@pytest.mark.parametrize(
    "arguments",
    [
        {"symbol": "600519.SH"},
        {"cutoff_date": "2030-01-01"},
        {"path": "F:/private"},
        {"sql": "SELECT *"},
        {"limit": True},
        {"limit": 501},
        {"start_date": "2026-10-13T00:00:00"},
        {"start_date": "2026-10-13", "end_date": "2026-10-12"},
    ],
)
def test_market_rejects_scope_injection_and_invalid_dates(adapters, context, arguments):
    result = adapters.execute("query_stock_data", arguments, context)
    assert result["error"] == "invalid_arguments"


def test_market_empty_and_failed_read_stay_distinct(adapters, context, monkeypatch):
    monkeypatch.setattr(
        tool_module,
        "query_stock_data",
        lambda *args, **kwargs: {
            "ok": True,
            "found": False,
            "data": [],
            "actual_range": {"start_date": None, "end_date": None},
        },
    )
    empty = adapters.execute("query_stock_data", {}, context)
    assert empty["ok"] is True and empty["found"] is False
    assert empty["citations"] == {} and empty["snapshot"]["data"] == []
    monkeypatch.setattr(
        tool_module,
        "query_stock_data",
        lambda *args, **kwargs: {
            "ok": False,
            "error": "file_not_found",
            "message": "数据文件不存在。",
        },
    )
    failed = adapters.execute("query_stock_data", {}, context)
    assert failed["ok"] is False and failed["error"] == "file_not_found"
    assert failed["found"] is False and failed["citations"] == {}


def test_unknown_tool_nonobject_arguments_and_exceptions_are_safe(adapters, context):
    assert adapters.execute("get_ticket", {}, context)["error"] == "tool_not_found"
    assert adapters.execute("search_fundamentals", [], context)["error"] == "invalid_arguments"

    def fail(request):
        raise RuntimeError("secret token")

    adapters.rag_service.retrieve = fail
    result = adapters.execute("search_fundamentals", {"query": "经营情况"}, context)
    assert result["error"] == "retrieval_failed"
    assert "secret" not in json.dumps(result)
