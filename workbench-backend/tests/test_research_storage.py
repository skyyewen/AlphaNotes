"""Real SQLite snapshots, immutable payloads and idempotent confirmation."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime

import pytest

from app.storage.research import (
    ResearchRunConflict,
    ResearchRunNotFound,
    ResearchStorageError,
    ResearchStore,
)


def payload(run_id="run-1", *, status="success"):
    return {
        "run_id": run_id,
        "status": status,
        "question": "核对资料",
        "context": {
            "symbol": "601318.SH",
            "strategy_id": "weekly_platform",
            "strategy_version": "X2",
            "materials_cutoff_date": "2026-10-26",
            "market_cutoff_date": "2026-10-13",
        },
        "started_at": "2026-10-27T01:00:00+00:00",
        "finished_at": "2026-10-27T01:00:01+00:00",
        "answer": "实际引用[R1-1]",
        "citations": {
            "R1-1": {"excerpt": "原文" * 20000, "available_date": "2026-04-08", "source": "财报.md"}
        },
        "tool_snapshots": [
            {
                "tool": "query_stock_data",
                "result": {"data": [{"date": "2026-10-13", "close": 51.69}]},
            }
        ],
        "steps": [{"step": "model", "status": "completed"}],
        "model": "configured-model",
        "prompt_version": "research-v1.3",
        "error": None if status == "success" else {"code": "tool_failed"},
    }


def test_constructor_is_lazy_and_only_one_business_table_is_created(tmp_path):
    path = tmp_path / "new" / "research.sqlite3"
    store = ResearchStore(path)
    assert not path.parent.exists()
    assert store.list_records() == []
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("research_runs",)
        ]


def test_complete_snapshot_survives_reopening_and_source_mutation(tmp_path):
    path = tmp_path / "research.sqlite3"
    original = payload()
    expected = deepcopy(original)
    saved = ResearchStore(path).save(original)
    assert saved["status"] == "success"
    assert saved["archive_status"] == "completed"
    assert saved["confirmed_at"] is None
    original["citations"]["R1-1"]["excerpt"] = "updated source"
    original["tool_snapshots"][0]["result"]["data"][0]["close"] = 0
    reopened = ResearchStore(path)
    result = reopened.get("run-1")
    assert {key: result[key] for key in expected} == expected
    assert reopened.list_records() == []
    with pytest.raises(ResearchRunNotFound):
        reopened.get("run-1", archived_only=True)
    confirmation = reopened.confirm("run-1")
    assert confirmation["record_id"] == "run-1"
    assert datetime.fromisoformat(confirmation["confirmed_at"]).utcoffset().total_seconds() == 0
    archived = ResearchStore(path).get("run-1", archived_only=True)
    assert archived["citations"] == expected["citations"]
    assert archived["tool_snapshots"] == expected["tool_snapshots"]
    assert archived["status"] == "success" and archived["archive_status"] == "archived"


def test_confirm_is_atomic_idempotent_and_preserves_first_time(tmp_path):
    path = tmp_path / "research.sqlite3"
    ResearchStore(path).save(payload())
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: ResearchStore(path).confirm("run-1"), range(12)))
    assert all(result == results[0] for result in results)
    assert len(ResearchStore(path).list_records()) == 1
    with sqlite3.connect(path) as con:
        before = con.execute("SELECT payload_json FROM research_runs").fetchone()[0]
    assert ResearchStore(path).confirm("run-1") == results[0]
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT payload_json FROM research_runs").fetchone()[0] == before


def test_same_id_cannot_replace_a_draft_or_an_archived_snapshot(tmp_path):
    store = ResearchStore(tmp_path / "research.sqlite3")
    original = payload()
    store.save(original)
    assert store.save(original)["archive_status"] == "completed"
    altered = deepcopy(original)
    altered["answer"] = "different answer"
    for confirmed in (False, True):
        if confirmed:
            store.confirm("run-1")
        with pytest.raises(ResearchRunConflict):
            store.save(altered)
        assert store.get("run-1")["answer"] == original["answer"]
    assert store.save(original)["archive_status"] == "archived"


def test_failed_and_unknown_runs_cannot_be_archived(tmp_path):
    store = ResearchStore(tmp_path / "research.sqlite3")
    store.save(payload(status="failed"))
    with pytest.raises(ResearchRunConflict):
        store.confirm("run-1")
    with pytest.raises(ResearchRunNotFound):
        store.confirm("missing")
    assert store.list_records() == []


def test_records_only_include_archived_summaries_and_respect_limit(tmp_path):
    store = ResearchStore(tmp_path / "research.sqlite3")
    for run_id in ("draft", "first", "second"):
        store.save(payload(run_id))
    store.confirm("first")
    store.confirm("second")
    records = store.list_records(limit=1)
    assert [row["run_id"] for row in records] == ["second"]
    assert set(records[0]) == {
        "run_id",
        "record_id",
        "symbol",
        "strategy_id",
        "strategy_version",
        "question",
        "started_at",
        "confirmed_at",
    }


def test_invalid_or_corrupt_storage_is_a_safe_error(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ResearchStorageError):
        ResearchStore(blocked / "research.sqlite3").save(payload())
    store = ResearchStore(tmp_path / "research.sqlite3")
    store.save(payload())
    with sqlite3.connect(store.path) as con:
        con.execute("UPDATE research_runs SET payload_json=?", (json.dumps([]),))
    with pytest.raises(ResearchStorageError):
        store.get("run-1")
