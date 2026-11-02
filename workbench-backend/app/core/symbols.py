"""Resolve explicit security aliases from the configured local market source only.

Importing this module does not inspect files or load DuckDB. A bare code is not
an exchange identifier; only a unique code in the actual source can resolve it.
"""

import re
from functools import lru_cache
from pathlib import Path

BARE_CODE = re.compile(r"[0-9]{6}")
QUALIFIED_CODE = re.compile(r"[0-9]{6}\.(SH|SZ|BJ)")


class SymbolMappingError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _unavailable() -> SymbolMappingError:
    return SymbolMappingError(
        "symbol_mapping_unavailable", "证券代码映射源不可用，请检查本地日线文件。"
    )


@lru_cache(maxsize=8)
def _load_catalog(path: str, size: int, mtime_ns: int) -> dict[str, tuple[str, ...]]:
    try:
        import duckdb

        with duckdb.connect(":memory:", config={"threads": 2, "memory_limit": "256MB"}) as con:
            rows = con.execute("SELECT DISTINCT thscode FROM read_parquet(?)", [path]).fetchall()
        catalog: dict[str, set[str]] = {}
        for (value,) in rows:
            if isinstance(value, str) and QUALIFIED_CODE.fullmatch(value):
                catalog.setdefault(value[:6], set()).add(value)
        return {code: tuple(sorted(values)) for code, values in catalog.items()}
    except Exception as exc:
        # DuckDB is optional until use, including its error classes. Neither raw
        # engine errors nor absolute configured paths belong in the tool result.
        raise _unavailable() from exc


def resolve_symbol_aliases(symbol: str, path: Path) -> dict:
    """Return exact aliases, with no guessed exchange or arbitrary suffix removal.

    Legacy bare symbols are aliases for a uniquely resolved stock in this source.
    An explicit different exchange never inherits that stock's bare alias.
    """
    requested = symbol.strip().upper()
    result = {
        "requested_symbol": requested,
        "canonical_symbol": requested,
        "aliases": [requested],
        "mapping_status": "exact",
    }
    if not (BARE_CODE.fullmatch(requested) or QUALIFIED_CODE.fullmatch(requested)):
        return result
    try:
        resolved = Path(path).resolve()
        before = resolved.stat()
        if not resolved.is_file():
            raise _unavailable()
        catalog = _load_catalog(str(resolved), before.st_size, before.st_mtime_ns)
        after = resolved.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise SymbolMappingError("source_changed", "代码映射期间源文件发生变化，请重新查询。")
    except OSError as exc:
        raise _unavailable() from exc
    candidates = catalog.get(requested[:6], ())
    if BARE_CODE.fullmatch(requested):
        if len(candidates) > 1:
            raise SymbolMappingError(
                "ambiguous_symbol", "裸证券代码存在多个市场候选，请提供完整代码。"
            )
        if not candidates:
            return result | {"mapping_status": "unmapped"}
        return result | {
            "canonical_symbol": candidates[0],
            "aliases": [requested, candidates[0]],
            "mapping_status": "mapped",
        }
    if candidates == (requested,):
        return result | {"aliases": [requested, requested[:6]], "mapping_status": "mapped"}
    return result
