import hashlib
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import BACKEND_ROOT, Settings
from app.factory import create_app
from app.tools.strategy_wiki import (
    STRATEGY_WIKI_TOOL,
    StrategyWikiArgs,
    StrategyWikiService,
    query_strategy_wiki,
)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def page_sections(content):
    lines = content.splitlines()
    headings = []
    for number, line in enumerate(lines, 1):
        match = re.match(r"^(#{1,6}) (.+)$", line)
        if match:
            headings.append((number, len(match[1]), match[2]))
    sections = []
    for index, (start, level, title) in enumerate(headings):
        end = len(lines)
        for next_start, next_level, _ in headings[index + 1 :]:
            if next_level <= level:
                end = next_start - 1
                break
        sections.append({"title": title, "level": level, "line_start": start, "line_end": end})
    return sections


@pytest.fixture
def wiki(tmp_path):
    """Small independent copy of the real catalog/registry format, never live data."""
    repository = tmp_path / "repository"
    root = repository / "wiki"
    root.mkdir(parents=True)
    source = repository / "01_规则库" / "冻结规则.md"
    source.parent.mkdir()
    source.write_text("# 冻结原文\n规则来源，只读不执行。\n", encoding="utf-8")
    source_id = "src_test_rules"
    registry = {
        "schema_version": 1,
        "updated": "2026-10-26",
        "path_base": "gupiao_repository_root",
        "path_policy": "Resolve only registered paths under the real repository root.",
        "sources": [
            {
                "source_id": source_id,
                "role": "strategy_rule",
                "path": "01_规则库/冻结规则.md",
                "title": "冻结规则",
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "line_count": len(source.read_text(encoding="utf-8").splitlines()),
                "source_date": "2026-10-09",
                "date_basis": "冻结日期",
                "hash_checked_at": "2026-10-26",
                "allowed_for_tool_read": True,
            }
        ],
        "unresolved_references": [],
    }
    index = {
        "schema_version": 1,
        "created": "2026-10-26",
        "updated": "2026-10-26",
        "path_base": "wiki_root",
        "source_registry": "来源清单.json",
        "retrieval_policy": {
            "catalog": "metadata_first",
            "selected_strategy": "exact_id_and_version",
            "short_rule_page": "full_page_within_budget",
            "long_rule_page": "section_search_with_prerequisites_invalidations_and_execution",
            "research_evidence": "on_demand_only",
            "explicit_version": "validate_before_alias_or_reference_default",
            "ambiguity": "return_candidates",
            "source_change": "require_review",
            "scope": "registered_documents_only",
        },
        "families": [
            {
                "strategy_id": "weekly_platform",
                "name": "周线实体平台突破与回踩",
                "aliases": ["周线平台", "实体平台", "平台突破", "回踩转强"],
                "reference_version": "X0",
                "reference_basis": "原文主版本；读取选择不等于用户采用",
            },
            {
                "strategy_id": "breakout20",
                "name": "20日最高点突破",
                "aliases": ["20日突破", "20日最高点"],
                "reference_version": None,
                "reference_basis": "原文与指数适配不同，需明确版本",
            },
        ],
        "strategies": [],
        "components": [],
        "research_evidence": [],
    }
    pages = {}

    def add_page(document_id, strategy_id, version, title, aliases, kind, marker):
        folder = "研究证据" if kind == "research_evidence" else "策略"
        if kind == "component_rule":
            folder = "组件"
        relative = f"{folder}/{document_id}.md"
        content = (
            "---\n"
            f'document_id: "{document_id}"\n'
            f'type: "{kind}"\n'
            f'strategy_id: "{strategy_id}"\n'
            f'version: "{version}"\n'
            'created: "2026-10-26"\n'
            'updated: "2026-10-26"\n'
            'content_origin: "source_derived"\n'
            f"source_ids: {json.dumps([source_id])}\n"
            "---\n\n"
            f"# {title}\n\n"
            "## 前置条件\n\n"
            f"{marker}；至少200条真实有效报价，不补零。\n\n"
            "## 入场与止损\n\n"
            "完整收盘确认后下一可执行开盘尝试，初始风险不得超过15%。\n\n"
            "## 失效与执行约束\n\n"
            "失效必须撤销观察；缺失数据不能判为通过；T+1与不可成交顺延。\n"
        )
        path = root / relative
        path.parent.mkdir(exist_ok=True)
        path.write_text(content, encoding="utf-8")
        item = {
            "document_id": document_id,
            "type": kind,
            "strategy_id": strategy_id,
            "version": version,
            "file": relative,
            "title": title,
            "aliases": aliases,
            "tags": [strategy_id, "规则"],
            "status": "research_candidate",
            "summary": f"{title}的精简目录说明。",
            "source_ids": [source_id],
            "evidence_document_ids": [],
            "source_rule_version": "fixture_20261009",
            "source_rule_ids": [f"fixture_{document_id}"],
            "verification_scope": "独立测试夹具，不代表真实行情或策略验证。",
            "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "line_count": len(content.splitlines()),
            "sections": page_sections(content),
            "reading_mode": "on_demand" if kind != "strategy_rule" else "full_rule_page",
        }
        key = {
            "strategy_rule": "strategies",
            "component_rule": "components",
            "research_evidence": "research_evidence",
        }[kind]
        if kind == "component_rule":
            item["component_id"] = strategy_id
        index[key].append(item)
        pages[document_id] = {"path": path, "item": item, "content": content}
        return item

    weekly0 = add_page(
        "weekly_platform_x0",
        "weekly_platform",
        "X0",
        "周线实体平台 X0",
        ["周线平台", "实体平台", "平台突破", "回踩转强", "周线平台X0"],
        "strategy_rule",
        "ONLY_X0_RULE",
    )
    weekly2 = add_page(
        "weekly_platform_x2",
        "weekly_platform",
        "X2",
        "周线实体平台 X2",
        ["周线平台X2", "实体平台2R止盈", "平台突破X2"],
        "strategy_rule",
        "ONLY_X2_RULE；收盘达到2R后退出",
    )
    add_page(
        "breakout20_source",
        "breakout20",
        "source_snapshot_20261026",
        "20日突破原文基准",
        ["20日最高点买入", "20日股票突破", "20日突破原版"],
        "strategy_rule",
        "ONLY_ORIGINAL_RULE；同板块相似股票过滤保留",
    )
    add_page(
        "breakout20_index_v1",
        "breakout20",
        "index_adaptation_v1",
        "20日突破指数适配 v1",
        ["20日指数突破", "20日突破指数适配版", "指数20日最高点突破"],
        "strategy_rule",
        "ONLY_INDEX_RULE；省略同板块相似股票过滤",
    )
    add_page(
        "component_pe_market",
        "pe_market_filters",
        "comparison_20261010",
        "PE与大盘过滤",
        ["大盘PE窗口", "PE过滤组件"],
        "component_rule",
        "ONLY_COMPONENT_RULE",
    )
    add_page(
        "evidence_weekly_index",
        "weekly_platform",
        "experiment_20261012",
        "实体平台实验与证据",
        [],
        "research_evidence",
        "ONLY_RESEARCH_EVIDENCE；小样本，不能证明稳定有效",
    )
    weekly0["evidence_document_ids"] = ["evidence_weekly_index"]
    weekly2["evidence_document_ids"] = ["evidence_weekly_index"]
    write_json(root / "index.json", index)
    write_json(root / "来源清单.json", registry)
    settings = Settings(
        _env_file=None, strategy_wiki_root=root, rag_data_dir=tmp_path / "unused-rag"
    )
    return {
        "root": root,
        "repository": repository,
        "source": source,
        "index": index,
        "registry": registry,
        "pages": pages,
        "settings": settings,
    }


def query(wiki, **kwargs):
    return StrategyWikiService(wiki["settings"]).query(StrategyWikiArgs(**kwargs))


def replace_registered_content(wiki, document_id, content):
    page = wiki["pages"][document_id]
    page["path"].write_text(content, encoding="utf-8")
    page["item"].update(
        content_sha256=hashlib.sha256(page["path"].read_bytes()).hexdigest(),
        line_count=len(content.splitlines()),
        sections=page_sections(content),
    )
    write_json(wiki["root"] / "index.json", wiki["index"])


def assert_error(result, code):
    assert result["ok"] is False
    assert result["error"] == code
    assert result.get("context", "") == ""
    assert result.get("citations", {}) == {}
    assert result.get("found") is not True


def document_ids(result):
    return {document["document_id"] for document in result["documents"]}


def test_catalog_is_metadata_only_and_has_no_model_or_rag_io(wiki, monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("Strategy lookup must not connect to a model or another service.")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    result = StrategyWikiService(wiki["settings"]).catalog()
    assert result["ok"] is True
    assert len(result["families"]) == 2
    assert len(result["strategies"]) == 4
    assert len(result["components"]) == 1
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert "ONLY_X0_RULE" not in payload
    assert "ONLY_RESEARCH_EVIDENCE" not in payload
    assert '"sections"' not in payload
    assert not wiki["settings"].rag_data_dir.exists()


def test_exact_selection_returns_real_rule_with_citations_without_evidence(wiki):
    result = query(wiki, strategy_id="weekly_platform", version="X2")
    assert result["ok"] is True
    assert result["found"] is True
    assert result["status"] == "found"
    assert result["candidates"] == []
    assert document_ids(result) == {"weekly_platform_x2"}
    assert "ONLY_X2_RULE" in result["context"]
    assert "失效必须撤销观察" in result["context"]
    assert "T+1" in result["context"]
    assert "ONLY_X0_RULE" not in result["context"]
    assert "ONLY_RESEARCH_EVIDENCE" not in result["context"]
    assert result["citations"]
    assert result["truncated"] is False
    json.dumps(result, ensure_ascii=False, allow_nan=False)


def test_family_reference_version_is_explicit_and_is_not_adoption(wiki):
    result = query(wiki, strategy_id="weekly_platform")
    assert document_ids(result) == {"weekly_platform_x0"}
    selection = json.dumps(result["selection"], ensure_ascii=False)
    assert "X0" in selection
    assert "原文主版本" in selection or "reference" in selection
    assert "采用" in selection or "note" in selection


@pytest.mark.parametrize("text", ["周线平台X2到2R怎么退出？", "实体平台 X2 怎么止盈？"])
def test_explicit_natural_language_version_takes_priority_over_reference(wiki, text):
    result = query(wiki, query=text)
    assert result["status"] == "found"
    assert document_ids(result) == {"weekly_platform_x2"}
    assert "ONLY_X0_RULE" not in result["context"]


@pytest.mark.parametrize(
    "arguments",
    [
        {"strategy_id": "weekly_platform", "version": "X3"},
        {"query": "周线平台X3怎么退出？"},
        {"query": "实体平台 X3 怎么退出？"},
    ],
)
def test_unknown_explicit_version_never_falls_back(wiki, arguments):
    assert_error(query(wiki, **arguments), "version_not_found")


def test_generic_twenty_day_query_returns_candidates_not_a_silent_choice(wiki):
    result = query(wiki, query="20日突破怎么止损？")
    assert result["ok"] is True
    assert result["found"] is False
    assert result["status"] == "ambiguous"
    assert {item["document_id"] for item in result["candidates"]} == {
        "breakout20_source",
        "breakout20_index_v1",
    }
    assert result.get("context", "") == ""
    assert result.get("citations", {}) == {}
    assert "ONLY_" not in json.dumps(result["candidates"], ensure_ascii=False)


def test_index_alias_selects_the_adaptation_without_original_stock_filter(wiki):
    result = query(wiki, query="20日指数突破怎么止损？")
    assert result["status"] == "found"
    assert document_ids(result) == {"breakout20_index_v1"}
    assert "ONLY_INDEX_RULE" in result["context"]
    assert "ONLY_ORIGINAL_RULE" not in result["context"]


def test_evidence_is_opt_in_or_can_be_selected_by_document_id(wiki):
    result = query(wiki, strategy_id="weekly_platform", version="X0", include_evidence=True)
    assert document_ids(result) == {"weekly_platform_x0", "evidence_weekly_index"}
    assert "ONLY_RESEARCH_EVIDENCE" in result["context"]
    direct = query(wiki, document_id="evidence_weekly_index")
    assert direct["status"] == "found"
    assert document_ids(direct) == {"evidence_weekly_index"}


def test_unknown_query_is_normal_empty_but_unknown_document_is_an_error(wiki):
    result = query(wiki, query="有海龟交易法吗？")
    assert result["ok"] is True
    assert result["found"] is False
    assert result["status"] == "not_found"
    assert result.get("context", "") == ""
    assert result.get("citations", {}) == {}
    assert_error(query(wiki, document_id="not_registered"), "document_not_found")


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": "   "},
        {"query": "x" * 2001},
        {"query": "规则", "unregistered": True},
        {"document_id": "../private.md"},
        {"strategy_id": "../weekly_platform"},
        {"strategy_id": "weekly_platform", "version": "../../X0"},
        {"document_id": r"C:\private\file.md"},
        {"query": "规则", "max_chars": 0},
        {"query": "规则", "max_chars": True},
        {"query": "规则", "max_chars": "24000"},
        {"query": "规则", "max_chars": 24000.0},
    ],
)
def test_arguments_reject_invalid_input_before_catalog_access(arguments):
    with pytest.raises(ValidationError):
        StrategyWikiArgs(**arguments)


def test_public_function_validates_arguments_and_uses_supplied_settings(wiki, tmp_path):
    success = query_strategy_wiki(
        strategy_id="weekly_platform", version="X0", settings=wiki["settings"]
    )
    assert success["status"] == "found"
    assert document_ids(success) == {"weekly_platform_x0"}
    missing = Settings(_env_file=None, strategy_wiki_root=tmp_path / "does-not-exist")
    assert_error(query_strategy_wiki("   ", settings=missing), "invalid_arguments")
    assert_error(query_strategy_wiki("周线平台", settings=missing), "wiki_not_ready")
    assert not missing.strategy_wiki_root.exists()


@pytest.mark.parametrize("budget", [{}, {"max_chars": None}, {"max_chars": 100000}])
def test_large_rule_and_evidence_are_complete_without_an_implicit_budget(wiki, budget):
    required_rule = "必须保留的失效前提。" * 3000 + "失效必须撤销观察"
    content = wiki["pages"]["weekly_platform_x0"]["content"].replace(
        "失效必须撤销观察", required_rule
    )
    replace_registered_content(wiki, "weekly_platform_x0", content)
    result = query_strategy_wiki(
        strategy_id="weekly_platform",
        version="X0",
        include_evidence=True,
        settings=wiki["settings"],
        **budget,
    )
    assert result.get("status") == "found", result
    assert len(json.dumps(result, ensure_ascii=False)) > 32000
    assert result["truncated"] is False
    assert document_ids(result) == {"weekly_platform_x0", "evidence_weekly_index"}
    assert required_rule in result["context"]
    assert "ONLY_RESEARCH_EVIDENCE" in result["context"]
    assert all(item["reading_mode"] == "full_page" for item in result["documents"])
    assert any(required_rule in item["excerpt"] for item in result["citations"].values())


def test_default_budget_does_not_drop_large_ambiguous_candidates(wiki):
    for document in wiki["index"]["strategies"]:
        if document["strategy_id"] == "breakout20":
            document["summary"] = "不能静默丢弃的候选摘要。" * 2000
    write_json(wiki["root"] / "index.json", wiki["index"])
    result = query(wiki, query="20日突破")
    assert result["status"] == "ambiguous"
    assert {item["document_id"] for item in result["candidates"]} == {
        "breakout20_source",
        "breakout20_index_v1",
    }
    assert len(json.dumps(result, ensure_ascii=False)) > 32000
    assert result["truncated"] is False


def test_tool_schema_has_no_default_or_maximum_character_budget():
    budget = STRATEGY_WIKI_TOOL["function"]["parameters"]["properties"]["max_chars"]
    assert budget["default"] is None
    assert "maximum" not in json.dumps(budget)


def test_budget_does_not_return_an_incomplete_rule_as_success(wiki):
    content = wiki["pages"]["weekly_platform_x0"]["content"].replace(
        "失效必须撤销观察", "必须保留的失效前提。" * 400 + "失效必须撤销观察"
    )
    replace_registered_content(wiki, "weekly_platform_x0", content)
    result = query(wiki, strategy_id="weekly_platform", version="X0", max_chars=2000)
    assert_error(result, "budget_exceeded")


def test_budget_never_returns_ambiguity_without_a_selectable_candidate(wiki):
    for document in wiki["index"]["strategies"]:
        if document["strategy_id"] == "breakout20":
            document["summary"] = "不能静默丢弃的候选摘要。" * 1000
    write_json(wiki["root"] / "index.json", wiki["index"])
    assert_error(query(wiki, query="20日突破", max_chars=2000), "budget_exceeded")


def test_catalog_path_permission_error_is_a_safe_read_failure(wiki, monkeypatch):
    original_resolve = Path.resolve

    def deny_root(path, *args, **kwargs):
        if path == wiki["root"]:
            raise PermissionError("private internal path must not leak")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", deny_root)
    result = StrategyWikiService(wiki["settings"]).catalog()
    assert_error(result, "read_failed")
    assert "private internal path" not in result["message"]


@pytest.mark.parametrize(
    "text",
    ["周线平台 X0 和 X3 怎么退出？", "周线平台X0与X3比较", "实体平台 X3，按X0说明"],
)
def test_known_and_unknown_versions_in_one_query_do_not_hide_the_unknown(wiki, text):
    assert_error(query(wiki, query=text), "version_not_found")


def test_two_registered_versions_in_a_question_return_ambiguity_not_missing_version(wiki):
    result = query(wiki, query="周线平台X0和X2有什么区别？")
    assert result.get("status") == "ambiguous", result
    assert result["ok"] is True
    assert result["found"] is False
    assert {item["document_id"] for item in result["candidates"]} == {
        "weekly_platform_x0",
        "weekly_platform_x2",
    }
    assert result["context"] == ""
    assert result["citations"] == {}


def test_citation_lines_hash_and_excerpt_match_the_actual_registered_page(wiki):
    result = query(wiki, strategy_id="weekly_platform", version="X2")
    assert result["status"] == "found"
    for citation_id, citation in result["citations"].items():
        page = wiki["pages"][citation["document_id"]]
        lines = page["path"].read_bytes().decode("utf-8-sig").splitlines(keepends=True)
        assert citation["citation_id"] == citation_id
        assert 1 <= citation["line_start"] <= citation["line_end"] <= len(lines)
        assert citation["excerpt"] == "".join(
            lines[citation["line_start"] - 1 : citation["line_end"]]
        )
        assert citation["content_sha256"] == page["item"]["content_sha256"]
        assert citation["source_ids"] == page["item"]["source_ids"]
        assert f"[{citation_id}]" in result["context"]
        assert "parent_id" not in citation
        assert "child_id" not in citation


def test_section_mode_keeps_observation_confirmation_and_execution_prerequisites(wiki):
    original = wiki["pages"]["weekly_platform_x0"]["content"]
    frontmatter = original.split("# ", 1)[0]
    content = frontmatter + (
        "# 周线实体平台 X0\n\n规则解释，包含不可省略的入场链路。\n\n"
        "## 规则身份与适用边界\n\n固定来源版本。\n\n"
        "## 价格、指标与计数时钟\n\n使用真实有效报价。\n\n"
        "## 观察前置条件\n\nENTRY_TREND_GATE：趋势、数据资格和PE门槛同时通过。\n\n"
        "## 观察、等待与确认\n\nENTRY_WAIT_CONFIRM：观察后第1至5个交易日首次转强，过期失效。\n\n"
        "## 确认后的买入尝试\n\nENTRY_OPEN_RISK：下一开盘检查实际风险，不得按信号收盘成交。\n\n"
        "## X0持仓、止损与退出\n\nEXIT_CLOSE_ONLY：有效收盘触及止损才排卖单。\n\n"
        "## 成交与执行约定\n\nEXECUTION_T_PLUS_ONE：停牌顺延，保留T+1。\n\n"
        "## 原始来源\n\nUNRELATED_APPENDIX：" + "另见登记原始资料的导航说明。" * 2000 + "\n"
    )
    replace_registered_content(wiki, "weekly_platform_x0", content)
    result = query(wiki, query="周线平台X0怎么退出？", max_chars=24000)
    assert result.get("status") == "found", result
    assert result["truncated"] is True
    for marker in (
        "ENTRY_TREND_GATE",
        "ENTRY_WAIT_CONFIRM",
        "ENTRY_OPEN_RISK",
        "EXIT_CLOSE_ONLY",
        "EXECUTION_T_PLUS_ONE",
    ):
        assert marker in result["context"], marker
    assert "UNRELATED_APPENDIX" not in result["context"]


def test_missing_source_remains_a_failure_for_a_match_and_not_a_fabricated_empty(wiki):
    wiki["source"].unlink()
    absent = query(wiki, query="有海龟交易法吗？")
    assert absent["ok"] is True
    assert absent["status"] == "not_found"
    assert_error(query(wiki, strategy_id="weekly_platform", version="X0"), "source_missing")


def test_unreadable_registered_source_has_read_failed_semantics(wiki, monkeypatch):
    original_open = Path.open

    def fail_source_read(path, *args, **kwargs):
        if path == wiki["source"]:
            raise PermissionError("internal test path must not leak")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_source_read)
    result = query(wiki, strategy_id="weekly_platform", version="X0")
    assert_error(result, "read_failed")
    assert "internal test path" not in json.dumps(result)


def test_successful_lookup_preserves_wiki_and_source_files(wiki):
    before = {
        path.relative_to(wiki["repository"]): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in wiki["repository"].rglob("*")
        if path.is_file()
    }
    assert query(wiki, strategy_id="weekly_platform", version="X0")["status"] == "found"
    after = {
        path.relative_to(wiki["repository"]): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in wiki["repository"].rglob("*")
        if path.is_file()
    }
    assert before == after


@pytest.mark.parametrize(
    "change,code", [("delete", "source_missing"), ("modify", "source_changed")]
)
def test_registered_source_failure_is_not_treated_as_no_evidence(wiki, change, code):
    if change == "delete":
        wiki["source"].unlink()
    else:
        wiki["source"].write_text("# 原文已变化\n不应继续返回旧摘要。\n", encoding="utf-8")
    assert_error(query(wiki, strategy_id="weekly_platform", version="X0"), code)


def test_changed_rule_page_is_rejected_before_returning_old_hash(wiki):
    page = wiki["pages"]["weekly_platform_x0"]["path"]
    page.write_text(page.read_text(encoding="utf-8") + "\n未经登记的新条件。\n", encoding="utf-8")
    assert_error(query(wiki, strategy_id="weekly_platform", version="X0"), "document_changed")


@pytest.mark.parametrize("target", ["document", "source", "registry"])
def test_registered_paths_cannot_escape_their_allowed_root(wiki, tmp_path, target):
    outside = tmp_path / "outside.md"
    outside.write_text("PRIVATE_OUTSIDE_CONTENT\n", encoding="utf-8")
    if target == "document":
        wiki["pages"]["weekly_platform_x0"]["item"]["file"] = "../../outside.md"
        write_json(wiki["root"] / "index.json", wiki["index"])
    elif target == "source":
        wiki["registry"]["sources"][0]["path"] = "../outside.md"
        write_json(wiki["root"] / "来源清单.json", wiki["registry"])
    else:
        wiki["index"]["source_registry"] = "../../outside.md"
        write_json(wiki["root"] / "index.json", wiki["index"])
    result = query(wiki, strategy_id="weekly_platform", version="X0")
    assert_error(result, "invalid_path")
    assert "PRIVATE_OUTSIDE_CONTENT" not in json.dumps(result)


def test_symlink_source_cannot_escape_repository_when_supported(wiki, tmp_path):
    outside = tmp_path / "outside-source.md"
    outside.write_text("PRIVATE_SYMLINK_CONTENT\n", encoding="utf-8")
    wiki["source"].unlink()
    try:
        wiki["source"].symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("This Windows account cannot create file symlinks.")
    assert_error(query(wiki, strategy_id="weekly_platform", version="X0"), "invalid_path")


def test_invalid_catalog_json_is_an_explicit_failure(wiki):
    (wiki["root"] / "index.json").write_text("{broken", encoding="utf-8")
    assert_error(query(wiki, strategy_id="weekly_platform"), "invalid_catalog")


def test_relative_wiki_setting_is_anchored_without_creating_directories():
    relative = "data/strategy-wiki-unused"
    assert (
        Settings(_env_file=None, strategy_wiki_root=relative).strategy_wiki_root
        == (BACKEND_ROOT / relative).resolve()
    )


def test_strategy_api_uses_app_service_and_preserves_plain_result(wiki):
    application = create_app(wiki["settings"])
    assert isinstance(application.state.strategy_wiki_service, StrategyWikiService)
    with TestClient(application) as client:
        catalog = client.get("/api/strategies")
        assert catalog.status_code == 200
        assert catalog.json()["ok"] is True
        response = client.post(
            "/api/strategy-wiki/query", json={"strategy_id": "weekly_platform", "version": "X2"}
        )
        assert response.status_code == 200
        assert document_ids(response.json()) == {"weekly_platform_x2"}
        assert "data" not in response.json()
        assert client.post("/api/strategy-wiki/query", json={"query": " "}).status_code == 422
        assert (
            client.post(
                "/api/strategy-wiki/query", json={"query": "规则", "path": "../../outside.md"}
            ).status_code
            == 422
        )


@pytest.mark.parametrize(
    "code,status",
    [
        ("version_not_found", 404),
        ("document_not_found", 404),
        ("budget_exceeded", 413),
        ("invalid_path", 400),
        ("wiki_not_ready", 503),
        ("source_missing", 503),
        ("read_failed", 503),
        ("invalid_catalog", 503),
        ("source_changed", 409),
        ("document_changed", 409),
    ],
)
def test_api_does_not_turn_service_errors_into_http_success(wiki, monkeypatch, code, status):
    application = create_app(wiki["settings"])
    result = {"ok": False, "error": code, "context": "", "citations": {}}
    monkeypatch.setattr(application.state.strategy_wiki_service, "query", lambda args: result)
    with TestClient(application) as client:
        response = client.post("/api/strategy-wiki/query", json={"query": "周线平台"})
    assert response.status_code == status
    assert response.json() == result


def test_cold_start_never_reads_wiki_or_initializes_model_dependencies(tmp_path):
    script = r"""
import socket
import sys
from pathlib import Path

def reject_network(*args, **kwargs):
    raise AssertionError('Wiki construction must not connect to anything.')

socket.create_connection = reject_network
socket.socket.connect = reject_network
from app.core.config import Settings
from app.factory import create_app

root = Path(sys.argv[1])
original_text = Path.read_text
original_bytes = Path.read_bytes
def forbid_wiki_read(path, *args, **kwargs):
    if path == root or root in path.parents:
        raise AssertionError('Wiki catalog must be loaded only when queried.')
    return original_text(path, *args, **kwargs)
def forbid_wiki_bytes(path, *args, **kwargs):
    if path == root or root in path.parents:
        raise AssertionError('Wiki files must be loaded only when queried.')
    return original_bytes(path, *args, **kwargs)
Path.read_text = forbid_wiki_read
Path.read_bytes = forbid_wiki_bytes
application = create_app(Settings(
    _env_file=None, strategy_wiki_root=root, rag_data_dir=root.parent / 'unused-rag'
))
assert hasattr(application.state, 'strategy_wiki_service')
assert not root.exists()
assert not (root.parent / 'unused-rag').exists()
for module in ('chromadb', 'onnxruntime', 'openai', 'requests'):
    assert module not in sys.modules, module
"""
    subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path / "not-created" / "wiki")],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
