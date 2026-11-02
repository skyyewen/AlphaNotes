"""One-table immutable research snapshots and idempotent confirmation."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy_id TEXT,
    strategy_version TEXT,
    question TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('completed','archived','failed')),
    payload_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    confirmed_at TEXT
);
"""


class ResearchStorageError(Exception):
    code = "storage_failed"
    status_code = 503
    message = "研究结果存储不可用，请检查后端配置或日志。"


class ResearchRunNotFound(ResearchStorageError):
    code = "research_not_found"
    status_code = 404
    message = "研究记录不存在或尚未归档。"


class ResearchRunConflict(ResearchStorageError):
    code = "research_conflict"
    status_code = 409
    message = "研究结果已存在且不能覆盖，或当前结果不能归档。"


class ResearchStore:
    def __init__(self, path: Path):
        # No directory, database or connection is created at application startup.
        self.path = Path(path)

    @contextmanager
    def connection(self):
        con = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            con = sqlite3.connect(self.path, timeout=10)
            con.row_factory = sqlite3.Row
            con.executescript(SCHEMA)
            with con:
                yield con
        except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
            raise ResearchStorageError() from exc
        finally:
            if con is not None:
                con.close()

    @staticmethod
    def _payload(row) -> dict:
        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, dict):
                raise ValueError("Invalid stored snapshot")
            return {
                **payload,
                "archive_status": row["status"],
                "confirmed_at": row["confirmed_at"],
            }
        except (ValueError, TypeError) as exc:
            raise ResearchStorageError() from exc

    def save(self, payload: dict) -> dict:
        """An existing run is never replaced, including after confirmation."""
        try:
            content = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
            context = payload["context"]
            status = {"success": "completed", "failed": "failed"}[payload["status"]]
            values = (
                payload["run_id"],
                context["symbol"],
                context.get("strategy_id"),
                context.get("strategy_version"),
                payload["question"],
                status,
                content,
                payload["started_at"],
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ResearchStorageError() from exc
        with self.connection() as con:
            try:
                con.execute(
                    "INSERT INTO research_runs "
                    "(run_id,symbol,strategy_id,strategy_version,question,status,payload_json,started_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    values,
                )
            except sqlite3.IntegrityError:
                row = con.execute(
                    "SELECT * FROM research_runs WHERE run_id=?", (payload["run_id"],)
                ).fetchone()
                if row is None or row["payload_json"] != content:
                    raise ResearchRunConflict() from None
            row = con.execute(
                "SELECT * FROM research_runs WHERE run_id=?", (payload["run_id"],)
            ).fetchone()
            return self._payload(row)

    def get(self, run_id: str, *, archived_only: bool = False) -> dict:
        with self.connection() as con:
            row = con.execute("SELECT * FROM research_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or (archived_only and row["status"] != "archived"):
                raise ResearchRunNotFound()
            return self._payload(row)

    def confirm(self, run_id: str) -> dict:
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT status FROM research_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise ResearchRunNotFound()
            if row["status"] == "failed":
                raise ResearchRunConflict()
            con.execute(
                "UPDATE research_runs SET status='archived',confirmed_at=? "
                "WHERE run_id=? AND status='completed'",
                (datetime.now(UTC).isoformat(), run_id),
            )
            row = con.execute(
                "SELECT confirmed_at FROM research_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            return {
                "record_id": run_id,
                "run_id": run_id,
                "status": "archived",
                "confirmed_at": row["confirmed_at"],
            }

    def list_records(self, limit: int = 50) -> list[dict]:
        with self.connection() as con:
            rows = con.execute(
                "SELECT run_id,run_id AS record_id,symbol,strategy_id,strategy_version,"
                "question,started_at,confirmed_at FROM research_runs "
                "WHERE status='archived' ORDER BY confirmed_at DESC,run_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
