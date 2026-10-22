"""Local fallback backend: DuckDB over the same public endpoints (SAMPLE_MODE)."""

from __future__ import annotations

import threading
from typing import Any

import duckdb

from because.harness.base import CANONICAL_SCHEMAS, DataHarness, SourceSpec
from because.harness.sources import fetch_rows


class DuckDBBackend(DataHarness):
    name = "duckdb"

    def __init__(self, path: str = ":memory:", row_loader=fetch_rows) -> None:
        super().__init__()
        self.con = duckdb.connect(path)
        self._lock = threading.Lock()
        self._row_loader = row_loader  # injectable for tests

    def _register(self, spec: SourceSpec) -> str:
        cols = CANONICAL_SCHEMAS[spec.schema]
        table = spec.table_name
        rows = self._row_loader(spec)
        ddl = ", ".join(f"{c} {t}" for c, t in cols)
        with self._lock:
            self.con.execute(f"CREATE OR REPLACE TABLE {table} ({ddl})")
            if rows:
                ph = ", ".join("?" for _ in cols)
                self.con.executemany(f"INSERT INTO {table} VALUES ({ph})", rows)
        return table

    def _execute(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        with self._lock:
            cur = self.con.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            return cols, [list(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.con.close()
