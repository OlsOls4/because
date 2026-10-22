"""Data harness: Zetaris (federated, primary) or DuckDB (local fallback)."""

from __future__ import annotations

import os

from because.harness.base import DataHarness, HarnessError, QueryResult, SourceSpec

__all__ = ["DataHarness", "HarnessError", "QueryResult", "SourceSpec", "get_harness", "backend_choice"]


def backend_choice() -> str:
    """BECAUSE_BACKEND=zetaris|duckdb. SAMPLE_MODE=1 forces duckdb."""
    if os.environ.get("SAMPLE_MODE", "").lower() in ("1", "true", "yes"):
        return "duckdb"
    return os.environ.get("BECAUSE_BACKEND", "duckdb").lower()


def get_harness(name: str | None = None, fallback: bool = True) -> DataHarness:
    """Build the configured harness. If Zetaris is selected but unreachable and
    `fallback` is true, fall back to DuckDB and record why on `.fallback_reason`."""
    name = (name or backend_choice()).lower()
    if name.startswith("zetaris"):
        try:
            from because.harness.zetaris_backend import ZetarisBackend

            return ZetarisBackend()
        except Exception as e:
            if not fallback:
                raise
            from because.harness.duckdb_backend import DuckDBBackend

            h = DuckDBBackend()
            h.fallback_reason = f"Zetaris unavailable ({e}); using local DuckDB fallback."  # type: ignore[attr-defined]
            return h
    from because.harness.duckdb_backend import DuckDBBackend

    return DuckDBBackend()
