"""The DataHarness contract.

Every number the agent cites comes out of a `DataHarness.query()` call. A query
returns a `QueryResult` with a stable `query_id`, the exact SQL that ran, the
backend that ran it, and the upstream source URL(s) the table was registered
from. The agent cites `query_id` + row index, so any claim can be replayed.

Two backends implement the contract:

* `ZetarisBackend`  - federated SQL through Zetaris Lightning (JDBC, optional
  UI-proxy REST). Sources are registered as Lightning REST tables and
  flattened with SchemaStore views, exactly as the Zetaris cookbooks do.
* `DuckDBBackend`   - local fallback ("SAMPLE_MODE"). Fetches the same public
  endpoints, flattens them to the same canonical columns, and runs the same SQL.

Portability rule: agent SQL only uses ANSI SELECT / WHERE / GROUP BY /
ORDER BY / LIMIT / CAST / CASE / ROUND over the canonical columns below, and
refers to tables through `{t}` placeholders that the backend resolves. No
backend-specific functions in agent SQL.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

# Canonical logical schemas. Both backends expose exactly these columns.
CANONICAL_SCHEMAS: dict[str, list[tuple[str, str]]] = {
    # SEC EDGAR companyconcept: one row per reported fact.
    "edgar_concept": [
        ("cik", "VARCHAR"),
        ("entity_name", "VARCHAR"),
        ("concept", "VARCHAR"),
        ("period_start", "VARCHAR"),
        ("period_end", "VARCHAR"),
        ("value_usd", "DOUBLE"),
        ("accession_number", "VARCHAR"),
        ("fiscal_year", "BIGINT"),
        ("fiscal_period", "VARCHAR"),
        ("form_type", "VARCHAR"),
        ("filed_date", "VARCHAR"),
    ],
    # Any time series (FRED, ONS, World Bank): one row per observation.
    "series": [
        ("series_id", "VARCHAR"),
        ("period", "VARCHAR"),  # ISO-ish sortable: YYYY, YYYY-MM or YYYY-MM-DD
        ("value", "DOUBLE"),
    ],
}


@dataclass(frozen=True)
class SourceSpec:
    """One upstream public endpoint registered as one harness table."""

    kind: str  # edgar_concept | fred_series | ons_series | worldbank_indicator
    key: str  # e.g. "0000050863:GrossProfit", "CPIAUCSL", "d7g7", "GB:FP.CPI.TOTL.ZG"
    label: str = ""

    @property
    def schema(self) -> str:
        return "edgar_concept" if self.kind == "edgar_concept" else "series"

    @property
    def table_name(self) -> str:
        """Backend-neutral physical table name (lowercase, [a-z0-9_])."""
        safe = "".join(ch if ch.isalnum() else "_" for ch in self.key.lower())
        return f"{self.kind}_{safe}"[:120]

    @property
    def url(self) -> str:
        from because.harness.sources import source_url

        return source_url(self)


@dataclass
class QueryResult:
    query_id: str
    sql: str
    backend: str
    columns: list[str]
    rows: list[list[Any]]
    sources: list[dict[str, str]] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str | None = None

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_query_id(sql: str, backend: str) -> str:
    h = hashlib.sha1(f"{backend}\n{sql}".encode()).hexdigest()[:10]
    return f"Q-{h}"


class HarnessError(RuntimeError):
    pass


class DataHarness(ABC):
    """Federated SQL over registered public sources."""

    name: str = "abstract"

    def __init__(self) -> None:
        self._registered: dict[SourceSpec, str] = {}
        self.log: list[dict[str, Any]] = []

    # -- registration -------------------------------------------------------
    @abstractmethod
    def _register(self, spec: SourceSpec) -> str:
        """Make `spec` queryable; return the fully qualified table name."""

    def ensure_source(self, spec: SourceSpec) -> str:
        if spec not in self._registered:
            self._registered[spec] = self._register(spec)
        return self._registered[spec]

    # -- querying -----------------------------------------------------------
    @abstractmethod
    def _execute(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        """Run one SQL statement and return (columns, rows)."""

    def query(self, sql_template: str, tables: dict[str, SourceSpec]) -> QueryResult:
        """Run portable SQL. `{name}` placeholders resolve to registered tables."""
        t0 = time.perf_counter()
        try:
            resolved = {name: self.ensure_source(spec) for name, spec in tables.items()}
        except Exception as e:  # registration failure: surfaced in the trace
            resolved = {name: spec.table_name for name, spec in tables.items()}
            sql = sql_template.format(**resolved)
            return self._result(sql, resolved, tables, [], [], f"register: {type(e).__name__}: {e}", t0)
        sql = sql_template.format(**resolved)
        try:
            cols, rows = self._execute(sql)
            err = None
        except Exception as e:  # surfaced in the trace, never swallowed silently
            cols, rows, err = [], [], f"{type(e).__name__}: {e}"
        return self._result(sql, resolved, tables, cols, rows, err, t0)

    def _result(self, sql: str, resolved: dict, tables: dict, cols: list, rows: list, err: str | None, t0: float) -> QueryResult:
        qid = make_query_id(sql, self.name)
        res = QueryResult(
            query_id=qid,
            sql=" ".join(sql.split()),
            backend=self.name,
            columns=cols,
            rows=[[_jsonable(v) for v in r] for r in rows],
            sources=[{"table": resolved[n], "kind": s.kind, "key": s.key, "url": s.url} for n, s in tables.items()],
            elapsed_ms=round((time.perf_counter() - t0) * 1000, 1),
            error=err,
        )
        self.log.append({"query_id": qid, "sql": res.sql, "rows": len(res.rows), "error": err})
        return res

    def describe(self) -> dict[str, Any]:
        return {"backend": self.name, "tables": {s.key: t for s, t in self._registered.items()}}

    def close(self) -> None:  # pragma: no cover - optional
        pass


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        return float(v)
    except Exception:
        return json.loads(json.dumps(v, default=str))
