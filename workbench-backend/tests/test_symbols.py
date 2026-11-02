import os
import subprocess
import sys

import duckdb
import pytest

from app.core import symbols
from app.core.symbols import SymbolMappingError, resolve_symbol_aliases


def write_codes(path, codes):
    with duckdb.connect(":memory:") as con:
        con.execute("CREATE TABLE codes (thscode VARCHAR)")
        if codes:
            con.executemany("INSERT INTO codes VALUES (?)", [(code,) for code in codes])
        con.execute("COPY codes TO ? (FORMAT PARQUET)", [str(path)])


def test_verified_bare_and_qualified_codes_share_exact_aliases(tmp_path):
    path = tmp_path / "codes.parquet"
    write_codes(path, ["601318.SH", "601318.SH", "000001.SZ"])
    bare = resolve_symbol_aliases(" 601318 ", path)
    full = resolve_symbol_aliases("601318.sh", path)
    assert bare == {
        "requested_symbol": "601318",
        "canonical_symbol": "601318.SH",
        "aliases": ["601318", "601318.SH"],
        "mapping_status": "mapped",
    }
    assert full["canonical_symbol"] == bare["canonical_symbol"]
    assert set(full["aliases"]) == set(bare["aliases"])
    assert resolve_symbol_aliases("000001.SH", path)["aliases"] == ["000001.SH"]
    assert resolve_symbol_aliases("000001.SZ", path)["aliases"] == ["000001.SZ", "000001"]


def test_ambiguous_bare_code_is_rejected_without_merging_explicit_markets(tmp_path):
    path = tmp_path / "codes.parquet"
    write_codes(path, ["000001.SH", "000001.SZ"])
    with pytest.raises(SymbolMappingError) as error:
        resolve_symbol_aliases("000001", path)
    assert error.value.code == "ambiguous_symbol"
    for code in ("000001.SH", "000001.SZ"):
        assert resolve_symbol_aliases(code, path)["aliases"] == [code]


def test_unknown_bare_code_is_not_inferred_from_prefix(tmp_path):
    path = tmp_path / "codes.parquet"
    write_codes(path, ["601318.SH"])
    result = resolve_symbol_aliases("600000", path)
    assert result["mapping_status"] == "unmapped"
    assert result["canonical_symbol"] == "600000"
    assert result["aliases"] == ["600000"]
    assert resolve_symbol_aliases("600000.SH", path)["aliases"] == ["600000.SH"]


def test_symbol_catalog_is_cached_per_source_and_invalidated_on_change(tmp_path, monkeypatch):
    path = tmp_path / "codes.parquet"
    write_codes(path, ["601318.SH"])
    real_connect = duckdb.connect
    calls = []

    def connect(*args, **kwargs):
        calls.append(True)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", connect)
    resolve_symbol_aliases("601318", path)
    resolve_symbol_aliases("601318.SH", path)
    assert len(calls) == 1
    old = path.stat()
    write_codes(path, ["601318.SH", "600000.SH"])
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
    assert resolve_symbol_aliases("600000", path)["canonical_symbol"] == "600000.SH"
    assert len(calls) == 3  # One write connection, then a fresh directory read.
    other = tmp_path / "other.parquet"
    write_codes(other, ["600000.SZ"])
    assert resolve_symbol_aliases("600000", other)["canonical_symbol"] == "600000.SZ"


@pytest.mark.parametrize("kind", ["missing", "directory", "corrupt", "missing_column"])
def test_unavailable_mapping_source_has_safe_explicit_error(tmp_path, kind):
    path = tmp_path / "codes.parquet"
    if kind == "directory":
        path.mkdir()
    elif kind == "corrupt":
        path.write_bytes(b"not parquet")
    elif kind == "missing_column":
        with duckdb.connect(":memory:") as con:
            con.execute("COPY (SELECT 1 AS wrong) TO ? (FORMAT PARQUET)", [str(path)])
    with pytest.raises(SymbolMappingError) as error:
        resolve_symbol_aliases("601318", path)
    assert error.value.code == "symbol_mapping_unavailable"
    assert str(path) not in error.value.message


def test_catalog_change_during_resolution_is_not_accepted(tmp_path, monkeypatch):
    path = tmp_path / "codes.parquet"
    write_codes(path, ["601318.SH"])
    real_load = symbols._load_catalog

    def changing_load(*args):
        result = real_load(*args)
        current = path.stat()
        os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000))
        return result

    monkeypatch.setattr(symbols, "_load_catalog", changing_load)
    with pytest.raises(SymbolMappingError) as error:
        resolve_symbol_aliases("601318", path)
    assert error.value.code == "source_changed"


def test_import_and_non_market_symbols_do_not_load_duckdb_or_access_source():
    script = """
import sys
from pathlib import Path
from unittest.mock import patch
with patch.object(Path, 'stat', side_effect=AssertionError('unexpected stat')):
    from app.core.symbols import resolve_symbol_aliases
    assert resolve_symbol_aliases(' abc.sh ', Path('missing.parquet')) == {
        'requested_symbol': 'ABC.SH', 'canonical_symbol': 'ABC.SH',
        'aliases': ['ABC.SH'], 'mapping_status': 'exact'}
assert 'duckdb' not in sys.modules
assert '_duckdb' not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-B", "-c", script], check=True, capture_output=True, timeout=20
    )
