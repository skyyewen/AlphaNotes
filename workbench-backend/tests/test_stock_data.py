import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import duckdb
import pytest

from app.core.config import Settings
from app.tools import stock_data
from app.tools.stock_data import query_stock_data


@pytest.fixture
def daily_file(tmp_path):
    path = tmp_path / "daily.parquet"
    with duckdb.connect(":memory:") as con:
        con.execute(
            """
            CREATE TABLE daily (
                thscode VARCHAR, currency VARCHAR, interval VARCHAR, adjusted VARCHAR,
                date_ms BIGINT, open_price DOUBLE, high_price DOUBLE, low_price DOUBLE,
                close_price DOUBLE, volume DOUBLE, turnover DOUBLE
            )
            """
        )
        for day in (8, 9, 12, 13):
            timestamp = datetime(2026, 10, day, tzinfo=timezone(timedelta(hours=8)))
            con.execute(
                "INSERT INTO daily VALUES (?, 'CNY', '1d', 'none', ?, 1, 3, 1, 2, 100, 250)",
                ["000001.SZ", int(timestamp.timestamp() * 1000)],
            )
        con.execute(
            """
            INSERT INTO daily VALUES ('600519.SH', 'CNY', '1d', 'none',
                epoch_ms(TIMESTAMP '2026-10-13 16:00:00'), 10, 30, 10, 20, 200, 5000)
            """
        )
        con.execute("COPY daily TO ? (FORMAT PARQUET)", [str(path)])
    return path


@pytest.fixture
def data_settings(daily_file):
    return Settings(_env_file=None, stock_daily_parquet_path=daily_file)


def test_query_is_json_serializable_bounded_and_preserves_source_units(data_settings, daily_file):
    before = daily_file.stat()
    result = query_stock_data(" 000001.sz ", limit=2, settings=data_settings)
    assert result["ok"] is True
    assert result["found"] is True
    assert result["symbol"] == "000001.SZ"
    assert result["count"] == 2
    assert result["truncated"] is True
    assert result["order"] == "date_desc"
    assert [row["date"] for row in result["data"]] == ["2026-10-13", "2026-10-12"]
    assert result["data"][0] == {
        "thscode": "000001.SZ",
        "date": "2026-10-13",
        "open": 1.0,
        "high": 3.0,
        "low": 1.0,
        "close": 2.0,
        "volume": 100.0,
        "amount": 250.0,
    }
    assert result["adjustment"] == "none"
    assert result["units"] == {"price": "CNY", "volume": "share", "amount": "CNY"}
    assert result["actual_range"] == {"start_date": "2026-10-12", "end_date": "2026-10-13"}
    assert result["dataset_range"] == {"start_date": "2026-10-08", "end_date": "2026-10-14"}
    assert result["source_file"] == daily_file.name
    assert str(daily_file.parent) not in json.dumps(result)
    json.dumps(result, allow_nan=False)
    after = daily_file.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    assert result["source_id"] == f"{daily_file.name}:{before.st_size}:{before.st_mtime_ns}"


def test_shanghai_dates_are_inclusive_and_cutoff_takes_precedence(data_settings):
    result = query_stock_data(
        "000001.SZ",
        start_date="2026-10-09",
        end_date="2026-10-14",
        cutoff_date="2026-10-12",
        settings=data_settings,
    )
    assert result["ok"] is True
    assert [row["date"] for row in result["data"]] == ["2026-10-12", "2026-10-09"]
    assert result["effective_end_date"] == "2026-10-12"
    assert result["requested_range"]["end_date"] == "2026-10-14"
    assert result["truncated"] is False
    single = query_stock_data(
        "600519.SH",
        start_date=date(2026, 10, 14),
        end_date="2026-10-14",
        settings=data_settings,
    )
    assert [row["date"] for row in single["data"]] == ["2026-10-14"]


def test_explicit_range_beyond_file_reports_coverage_without_fabricated_rows(data_settings):
    result = query_stock_data("000001.SZ", end_date="2026-10-26", settings=data_settings)
    assert result["ok"] is True
    assert result["coverage_limited"] is True
    assert result["effective_end_date"] == "2026-10-26"
    assert result["actual_range"]["end_date"] == "2026-10-13"
    assert result["count"] == 4


@pytest.mark.parametrize(
    "kwargs",
    [{"symbol": "999999.BJ"}, {"symbol": "000001.SZ", "start_date": "2026-10-20"}],
)
def test_normal_empty_result_is_distinct_from_read_failure(data_settings, kwargs):
    result = query_stock_data(**kwargs, settings=data_settings)
    assert result["ok"] is True
    assert result["found"] is False
    assert result["data"] == []
    assert result["actual_range"] == {"start_date": None, "end_date": None}
    assert result["truncated"] is False


@pytest.mark.parametrize("dates", [{"start_date": "2026-10-20"}, {"end_date": "2026-10-01"}])
def test_window_outside_dataset_is_explicitly_coverage_limited(data_settings, dates):
    result = query_stock_data("000001.SZ", **dates, settings=data_settings)
    assert result["ok"] is True
    assert result["found"] is False
    assert result["coverage_limited"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"symbol": "上证指数"},
        {"symbol": "000001.SZ' OR 1=1 --"},
        {"symbol": "１２３４５６.SH"},
        {"symbol": "000001.US"},
        {"symbol": None},
        {"limit": 0},
        {"limit": 501},
        {"limit": True},
        {"limit": "60"},
        {"start_date": "2026-02-30"},
        {"start_date": "2026-1-01"},
        {"end_date": "2026-10-13T00:00:00"},
        {"cutoff_date": 1791820800000},
        {"start_date": "2026-10-13", "end_date": "2026-10-12"},
        {"start_date": "2026-10-13", "cutoff_date": "2026-10-12"},
    ],
)
def test_invalid_arguments_are_rejected_before_file_access(tmp_path, kwargs):
    settings = Settings(_env_file=None, stock_daily_parquet_path=tmp_path / "missing.parquet")
    result = query_stock_data(**({"symbol": "000001.SZ"} | kwargs), settings=settings)
    assert result["ok"] is False
    assert result["error"] == "invalid_arguments"


def test_missing_and_corrupt_files_are_failures_not_empty_data(tmp_path):
    path = tmp_path / "missing.parquet"
    settings = Settings(_env_file=None, stock_daily_parquet_path=path)
    missing = query_stock_data("000001.SZ", settings=settings)
    assert missing["ok"] is False
    assert missing["error"] == "file_not_found"
    assert not path.exists()
    path.write_bytes(b"this is not parquet")
    broken = query_stock_data("000001.SZ", settings=settings)
    assert broken["ok"] is False
    assert broken["error"] == "read_failed"
    assert str(tmp_path) not in broken["message"]


@pytest.mark.parametrize("unsupported", ["schema", "adjustment", "null", "null_date", "nan"])
def test_unsupported_data_is_not_mislabeled_as_valid_quotes(tmp_path, unsupported):
    path = tmp_path / "unsupported.parquet"
    with duckdb.connect(":memory:") as con:
        if unsupported == "schema":
            con.execute("CREATE TABLE data AS SELECT '000001.SZ' AS thscode")
        else:
            con.execute(
                """
                CREATE TABLE data AS SELECT '000001.SZ' AS thscode, 'CNY' AS currency,
                    '1d' AS interval, 'none' AS adjusted,
                    epoch_ms(TIMESTAMP '2026-10-12 16:00:00') AS date_ms,
                    1.0::DOUBLE AS open_price, 3.0::DOUBLE AS high_price,
                    1.0::DOUBLE AS low_price, 2.0::DOUBLE AS close_price,
                    100.0::DOUBLE AS volume, 250.0::DOUBLE AS turnover
                """
            )
            if unsupported == "adjustment":
                con.execute("UPDATE data SET adjusted='forward'")
            elif unsupported == "null":
                con.execute("UPDATE data SET close_price=NULL")
            elif unsupported == "null_date":
                con.execute("UPDATE data SET date_ms=NULL")
            elif unsupported == "nan":
                con.execute("UPDATE data SET close_price='NaN'::DOUBLE")
        con.execute("COPY data TO ? (FORMAT PARQUET)", [str(path)])
    settings = Settings(_env_file=None, stock_daily_parquet_path=path)
    result = query_stock_data("000001.SZ", settings=settings)
    assert result["ok"] is False
    assert result["error"] == "invalid_dataset"


def test_import_does_not_load_duckdb_or_open_a_data_file():
    script = """
import sys
from app.core.config import Settings
from app.tools.stock_data import StockDataArgs, query_stock_data
assert 'duckdb' not in sys.modules
assert '_duckdb' not in sys.modules
assert StockDataArgs(symbol='000001.SZ').limit == 60
assert StockDataArgs(symbol='000001').symbol == '000001'
"""
    subprocess.run(
        [sys.executable, "-B", "-c", script], check=True, capture_output=True, timeout=20
    )


def test_bare_code_uses_verified_market_symbol_with_identical_rows(data_settings):
    bare = query_stock_data("000001", limit=2, cutoff_date="2026-10-12", settings=data_settings)
    full = query_stock_data("000001.SZ", limit=2, cutoff_date="2026-10-12", settings=data_settings)
    assert bare["ok"] is True
    assert bare["symbol"] == full["symbol"] == "000001.SZ"
    assert bare["data"] == full["data"]
    assert bare["symbol_mapping"]["requested_symbol"] == "000001"
    assert bare["symbol_mapping"]["mapping_status"] == "mapped"
    assert bare["requested_range"] == full["requested_range"]


def test_unknown_bare_code_and_explicit_other_exchange_are_not_inferred(data_settings):
    unknown = query_stock_data("601318", settings=data_settings)
    assert unknown["ok"] is False
    assert unknown["error"] == "symbol_not_found"
    index = query_stock_data("000001.SH", settings=data_settings)
    assert index["ok"] is True
    assert index["found"] is False
    assert index["symbol_mapping"]["aliases"] == ["000001.SH"]


def test_ambiguous_bare_code_does_not_query_either_market(data_settings, daily_file, tmp_path):
    path = tmp_path / "ambiguous.parquet"
    with duckdb.connect(":memory:") as con:
        con.execute("CREATE TABLE quotes AS SELECT * FROM read_parquet(?)", [str(daily_file)])
        con.execute(
            "INSERT INTO quotes SELECT '000001.SH', currency, interval, adjusted, date_ms, "
            "open_price, high_price, low_price, close_price, volume, turnover "
            "FROM quotes WHERE thscode='000001.SZ' LIMIT 1"
        )
        con.execute("COPY quotes TO ? (FORMAT PARQUET)", [str(path)])
    settings = data_settings.model_copy(update={"stock_daily_parquet_path": path})
    ambiguous = query_stock_data("000001", settings=settings)
    assert ambiguous["ok"] is False
    assert ambiguous["error"] == "ambiguous_symbol"
    explicit = query_stock_data("000001.SZ", settings=settings)
    assert explicit["ok"] is True
    assert {row["thscode"] for row in explicit["data"]} == {"000001.SZ"}
    assert explicit["symbol_mapping"]["aliases"] == ["000001.SZ"]


def test_source_change_after_mapping_is_not_reported_as_success(
    data_settings, daily_file, monkeypatch
):
    real_resolve = stock_data.resolve_symbol_aliases

    def changing_resolve(*args):
        result = real_resolve(*args)
        current = daily_file.stat()
        os.utime(daily_file, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000))
        return result

    monkeypatch.setattr(stock_data, "resolve_symbol_aliases", changing_resolve)
    result = query_stock_data("000001", settings=data_settings)
    assert result["ok"] is False
    assert result["error"] == "source_changed"
