"""Generate tiny, fully synthetic OHLCV data for AlphaNotes demonstrations.

No market service, real quote file, credential, or personal research is read.
Dates are simulated Monday-Friday sessions, not an exchange trading calendar.
Run from the repository root:
    python demo-data/market/generate_synthetic_daily.py
Requires the backend's locked DuckDB dependency.
"""

import json
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import duckdb

OUTPUT_DIR = Path(__file__).resolve().parent
END_DATE = date(2026, 10, 28)
ROWS_PER_SYMBOL = 120
SYMBOLS = ("601318.SH", "000001.SZ", "600519.SH")
NOTICE = "SYNTHETIC DEMO ONLY - fabricated prices, volumes and sessions; no investment meaning"


def simulated_sessions():
    sessions = []
    current = END_DATE
    while len(sessions) < ROWS_PER_SYMBOL:
        if current.weekday() < 5:
            sessions.append(current)
        current -= timedelta(days=1)
    return list(reversed(sessions))


def generate():
    sessions = simulated_sessions()
    rows = []
    for symbol_index, symbol in enumerate(SYMBOLS):
        for day_index, day in enumerate(sessions):
            # Integer formulas intentionally fabricate smooth, bounded examples.
            close_cents = (
                1000 * (symbol_index + 1)
                + day_index * (symbol_index + 2)
                + ((day_index * 17 + symbol_index * 11) % 61 - 30)
            )
            open_cents = close_cents + ((day_index * 7 + symbol_index) % 19 - 9)
            high_cents = max(open_cents, close_cents) + 8 + day_index % 13
            low_cents = min(open_cents, close_cents) - 8 - day_index % 11
            volume = 100_000 + (day_index * 7919 + symbol_index * 1009) % 900_000
            turnover_cents = ((open_cents + close_cents) * volume) // 2
            timestamp = datetime.combine(
                day, datetime.min.time(), timezone(timedelta(hours=8))
            )
            rows.append(
                (
                    symbol,
                    "CNY",
                    "1d",
                    "none",
                    int(timestamp.timestamp() * 1000),
                    open_cents / 100,
                    high_cents / 100,
                    low_cents / 100,
                    close_cents / 100,
                    float(volume),
                    turnover_cents / 100,
                    True,
                    NOTICE,
                )
            )
    output = OUTPUT_DIR / "daily.parquet"
    with duckdb.connect(":memory:", config={"threads": 1}) as connection:
        connection.execute(
            """
            CREATE TABLE synthetic_daily (
                thscode VARCHAR, currency VARCHAR, interval VARCHAR, adjusted VARCHAR,
                date_ms BIGINT, open_price DOUBLE, high_price DOUBLE, low_price DOUBLE,
                close_price DOUBLE, volume DOUBLE, turnover DOUBLE,
                is_synthetic BOOLEAN, dataset_notice VARCHAR
            )
            """
        )
        connection.executemany(
            "INSERT INTO synthetic_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.execute(
            "COPY (SELECT * FROM synthetic_daily ORDER BY thscode, date_ms) "
            "TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(output)],
        )
    metadata = {
        "dataset_kind": "fully_synthetic_demo",
        "notice": NOTICE,
        "source": "Deterministic integer formulas in generate_synthetic_daily.py; no real data used",
        "symbols": list(SYMBOLS),
        "symbol_note": "Security codes are routing examples only; every quote and volume is fabricated",
        "rows_per_symbol": ROWS_PER_SYMBOL,
        "total_rows": len(rows),
        "start_date": sessions[0].isoformat(),
        "end_date": sessions[-1].isoformat(),
        "calendar": "Simulated Monday-Friday sessions; public holidays are NOT excluded",
        "timezone": "Asia/Shanghai (UTC+08:00)",
        "currency": "CNY",
        "interval": "1d",
        "adjusted": "none",
        "units": {"price": "CNY", "volume": "share", "turnover": "CNY"},
        "file": output.name,
        "bytes": output.stat().st_size,
        "sha256": sha256(output.read_bytes()).hexdigest(),
    }
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    generate()
