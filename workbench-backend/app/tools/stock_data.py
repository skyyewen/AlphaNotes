"""Bounded, read-only access to an unadjusted CNY daily Parquet market dump.

This is a callable Python tool, not an Agent runner, HTTP endpoint or MCP server.
DuckDB and the configured file are opened only when a query is actually requested.
"""

import logging
import math
import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.config import Settings, get_settings
from app.core.symbols import SymbolMappingError, resolve_symbol_aliases

LOGGER = logging.getLogger(__name__)
DAY_MS = 86_400_000
EPOCH_DATE = date(1970, 1, 1)
REQUIRED_COLUMNS = {
    "thscode",
    "currency",
    "interval",
    "adjusted",
    "date_ms",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "volume",
    "turnover",
}


class StockDataArgs(BaseModel):
    """Only data selection is accepted; SQL and file paths are server-owned."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    limit: int = Field(default=60, ge=1, le=500, strict=True)
    start_date: date | None = None
    end_date: date | None = None
    cutoff_date: date | None = None

    @field_validator("symbol", mode="before")
    @classmethod
    def validate_symbol(cls, value):
        if not isinstance(value, str):
            raise ValueError("symbol must be a six-digit code, optionally with .SH, .SZ or .BJ")
        value = value.strip().upper()
        if not re.fullmatch(r"[0-9]{6}(\.(SH|SZ|BJ))?", value):
            raise ValueError("symbol must be a six-digit code, optionally with .SH, .SZ or .BJ")
        return value

    @field_validator("start_date", "end_date", "cutoff_date", mode="before")
    @classmethod
    def validate_date(cls, value):
        if value is None or type(value) is date:
            return value
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("date must use YYYY-MM-DD")
        return date.fromisoformat(value)

    @model_validator(mode="after")
    def validate_range(self):
        if self.start_date:
            for end in (self.end_date, self.cutoff_date):
                if end is not None and self.start_date > end:
                    raise ValueError("start_date must not be after end_date or cutoff_date")
        return self


class InvalidDatasetError(ValueError):
    """The file does not have the supported daily/none/CNY contract."""


def _failure(code: str, message: str) -> dict:
    return {"ok": False, "error": code, "message": message}


def _midnight_ms(value: date) -> int:
    # Asia/Shanghai midnight for this modern A-share dataset (UTC+08:00).
    return (value - EPOCH_DATE).days * DAY_MS - 28_800_000


def query_stock_data(
    symbol: str,
    limit: int = 60,
    *,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    cutoff_date: str | date | None = None,
    settings: Settings | None = None,
) -> dict:
    """Return latest rows in the inclusive Shanghai date window, newest first.

    A future research service must inject its bound symbol/cutoff_date here;
    this standalone function does not supply research authorization by itself.
    """
    try:
        args = StockDataArgs(
            symbol=symbol,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
            cutoff_date=cutoff_date,
        )
    except ValidationError:
        return _failure(
            "invalid_arguments",
            "股票代码须为六位数字，可带 .SH/.SZ/.BJ 后缀；limit 为 1～500 的整数，日期须为有效 YYYY-MM-DD 且范围有序。",
        )

    config = settings if settings is not None else get_settings()
    path = config.stock_daily_parquet_path
    try:
        before = path.stat()
        if not path.is_file():
            return _failure("file_not_found", "日线数据文件不存在，请检查后端数据路径配置。")
    except FileNotFoundError:
        return _failure("file_not_found", "日线数据文件不存在，请检查后端数据路径配置。")
    except OSError:
        LOGGER.exception("Cannot access configured daily data")
        return _failure("read_failed", "无法读取日线数据文件，请检查后端日志。")

    try:
        import duckdb
    except ImportError:
        return _failure("dependency_missing", "后端缺少 DuckDB，请安装项目锁定依赖。")

    ends = [value for value in (args.end_date, args.cutoff_date) if value is not None]
    effective_end = min(ends) if ends else None
    predicates = ["thscode = ?"]
    parameters = [str(path), args.symbol]
    if args.start_date:
        predicates.append("date_ms >= ?")
        parameters.append(_midnight_ms(args.start_date))
    if effective_end:
        predicates.append("date_ms < ?")
        parameters.append(_midnight_ms(effective_end) + DAY_MS)
    parameters.append(args.limit + 1)

    try:
        with duckdb.connect(":memory:", config={"threads": 2, "memory_limit": "256MB"}) as con:
            columns = {
                row[0]
                for row in con.execute(
                    "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
                ).fetchall()
            }
            if not REQUIRED_COLUMNS.issubset(columns):
                raise InvalidDatasetError("日线数据文件缺少必要字段。")
            mapping = resolve_symbol_aliases(args.symbol, path)
            if mapping["mapping_status"] == "unmapped":
                return _failure(
                    "symbol_not_found", "本地日线源中没有该裸代码，请提供可核对的完整代码。"
                )
            canonical_symbol = mapping["canonical_symbol"]
            parameters[1] = canonical_symbol
            first, last, currencies, intervals, adjustments = con.execute(
                """
                SELECT
                    strftime(epoch_ms(min(date_ms)) + INTERVAL '8 hours', '%Y-%m-%d'),
                    strftime(epoch_ms(max(date_ms)) + INTERVAL '8 hours', '%Y-%m-%d'),
                    list(DISTINCT currency), list(DISTINCT "interval"), list(DISTINCT adjusted)
                FROM read_parquet(?)
                """,
                [str(path)],
            ).fetchone()
            for values, expected in ((currencies, "CNY"), (intervals, "1d"), (adjustments, "none")):
                if values and values != [expected]:
                    raise InvalidDatasetError("仅支持 CNY、1d、none 未复权日线文件。")

            result = con.execute(
                """
                SELECT thscode,
                    strftime(epoch_ms(date_ms) + INTERVAL '8 hours', '%Y-%m-%d') AS date,
                    open_price AS open, high_price AS high,
                    low_price AS low, close_price AS close,
                    volume, turnover AS amount
                FROM read_parquet(?) WHERE """
                + " AND ".join(predicates)
                + """
                ORDER BY date_ms DESC LIMIT ?
                """,
                parameters,
            )
            names = [column[0] for column in result.description]
            fetched = [dict(zip(names, row)) for row in result.fetchall()]
            rows = fetched[: args.limit]
            for row in rows:
                if row["date"] is None:
                    raise InvalidDatasetError("查询结果缺少交易日期，不能作为正常行情返回。")
                if any(
                    row[key] is None or not math.isfinite(row[key])
                    for key in ("open", "high", "low", "close", "volume", "amount")
                ):
                    raise InvalidDatasetError(
                        "查询结果包含缺失或非有限数值，不能作为正常行情返回。"
                    )
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return _failure("source_changed", "查询期间数据文件发生变化，请重新查询。")
    except SymbolMappingError as exc:
        return _failure(exc.code, exc.message)
    except InvalidDatasetError as exc:
        return _failure("invalid_dataset", str(exc))
    except (duckdb.Error, OSError, ValueError, TypeError, OverflowError):
        LOGGER.exception("Cannot query configured daily data")
        return _failure("read_failed", "日线数据读取失败，请检查文件与后端日志。")

    return {
        "ok": True,
        "found": bool(rows),
        "symbol": canonical_symbol,
        "symbol_mapping": mapping,
        "count": len(rows),
        "order": "date_desc",
        "adjustment": "none",
        "units": {"price": "CNY", "volume": "share", "amount": "CNY"},
        "timezone": "Asia/Shanghai",
        "requested_range": {
            "start_date": args.start_date.isoformat() if args.start_date else None,
            "end_date": args.end_date.isoformat() if args.end_date else None,
            "cutoff_date": args.cutoff_date.isoformat() if args.cutoff_date else None,
        },
        "effective_end_date": effective_end.isoformat() if effective_end else None,
        "actual_range": {
            "start_date": rows[-1]["date"] if rows else None,
            "end_date": rows[0]["date"] if rows else None,
        },
        "dataset_range": {"start_date": first, "end_date": last},
        "coverage_limited": any(
            boundary is not None
            and (first is None or last is None or not first <= boundary.isoformat() <= last)
            for boundary in (args.start_date, effective_end)
        ),
        "truncated": len(fetched) > args.limit,
        "source_file": path.name,
        # File version marker, not a content hash or historical/PIT snapshot.
        "source_id": f"{path.name}:{before.st_size}:{before.st_mtime_ns}",
        "data": rows,
    }
