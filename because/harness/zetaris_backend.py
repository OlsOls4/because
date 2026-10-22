"""Zetaris Lightning backend (the primary, federated data harness).

Channels
--------
* ``jdbc`` (default) - Zetaris Lightning JDBC driver via ``jaydebeapi`` + JPype.
  This is the interface of the free Hobby tier: a local Zetaris Lightning
  instance from the Docker Compose bundle, reached at
  ``jdbc:zetaris:lightning@localhost:10000``.
* ``rest`` (optional) - the documented REST API (``/api/v1.0/sql-editor/sqls/run``)
  for instances where you have an API key.

Env vars (see .env.example and docs/ZETARIS.md)::

    ZETARIS_CHANNEL=jdbc|rest
    ZETARIS_JDBC_URL=jdbc:zetaris:lightning@localhost:10000
    ZETARIS_JDBC_JAR=/abs/path/zetaris-platform/jdbc/ndp-jdbc-driver-...-driver.jar
    ZETARIS_USERNAME=...   ZETARIS_PASSWORD=...
    ZETARIS_REST_URL=http://localhost:8888/api/v1.0  ZETARIS_API_KEY=...  ZETARIS_ORG_ID=1

How sources are registered (from the getting-started REST cookbook):

    CREATE LIGHTNING DATABASE BECAUSE_REST DESCRIBE BY "...";
    CREATE SCHEMASTORE CONTAINER because;
    CREATE LIGHTNING REST TABLE <t>_raw FROM BECAUSE_REST REQUEST(endpoint "...") ...;
    CREATE SCHEMASTORE VIEW <t> WITH CONTAINER because AS SELECT ... LATERAL VIEW explode(...);

Agent SQL then reads ``because.<t>`` with the same canonical columns the DuckDB
backend exposes, so the analyst code is identical on both backends.
One statement per call, as the Zetaris docs require.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from because.harness.base import DataHarness, HarnessError, SourceSpec
from because.harness.sources import ZETARIS_CONTAINER, ZETARIS_DATABASE, redact, zetaris_ddl

DRIVER_CLASS = "com.zetaris.lightning.jdbc.LightningDriver"
DEFAULT_JDBC_URL = "jdbc:zetaris:lightning@localhost:10000"
DEFAULT_REST_URL = "http://localhost:8888/api/v1.0"
ALREADY_EXISTS = re.compile(r"already exist|AlreadyExists|duplicate", re.I)


class _JdbcChannel:
    name = "jdbc"

    def __init__(self) -> None:
        jar = os.environ.get("ZETARIS_JDBC_JAR", "")
        user = os.environ.get("ZETARIS_USERNAME", "")
        pwd = os.environ.get("ZETARIS_PASSWORD", "")
        if not jar or not Path(jar).is_file():
            raise HarnessError(
                "ZETARIS_JDBC_JAR must point at the Zetaris driver JAR shipped in the platform bundle "
                "(zetaris-platform/jdbc/*driver*.jar). Run scripts/zetaris_setup.sh to fill it in."
            )
        if not user or not pwd:
            raise HarnessError("Set ZETARIS_USERNAME and ZETARIS_PASSWORD (your local Zetaris admin login).")
        try:
            import jaydebeapi  # type: ignore
        except ImportError as e:
            raise HarnessError("pip install 'because[zetaris]' (jaydebeapi + JPype1) and a JDK 11+.") from e
        url = os.environ.get("ZETARIS_JDBC_URL", DEFAULT_JDBC_URL)
        try:
            self._conn = jaydebeapi.connect(DRIVER_CLASS, url, [user, pwd], jar)
        except Exception as e:
            raise HarnessError(f"JDBC connect to {url} failed: {str(e)[:300]}") from e

    def run(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        cur = self._conn.cursor()
        try:
            cur.execute(sql)
            if not cur.description:
                return [], []
            cols = [d[0] for d in cur.description]
            return cols, [[_py(v) for v in r] for r in cur.fetchall()]
        finally:
            cur.close()

    def close(self) -> None:
        self._conn.close()


class _RestChannel:
    name = "rest"

    def __init__(self) -> None:
        self.base = os.environ.get("ZETARIS_REST_URL", DEFAULT_REST_URL).rstrip("/")
        self.key = os.environ.get("ZETARIS_API_KEY", "")
        self.org = os.environ.get("ZETARIS_ORG_ID", "1")
        if not self.key:
            raise HarnessError("ZETARIS_API_KEY is required for ZETARIS_CHANNEL=rest.")

    def run(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        body = {"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": 10000}
        req = Request(
            self.base + "/sql-editor/sqls/run",
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.key}",
                "X-Org-ID": self.org,
                "X-Request-ID": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=600) as resp:
                out = json.loads(resp.read().decode() or "null")
        except HTTPError as e:
            raise HarnessError(f"HTTP {e.code}: {e.read(400).decode(errors='replace')}") from e
        except (URLError, OSError) as e:
            raise HarnessError(f"Cannot reach {self.base}: {getattr(e, 'reason', e)}") from e
        data = out.get("data") if isinstance(out, dict) else None
        cols = out.get("columns") if isinstance(out, dict) else None
        if data and isinstance(data[0], dict):
            cols = list(data[0].keys())
            return cols, [list(r.values()) for r in data]
        if cols and isinstance(cols[0], dict):
            cols = [c.get("name") or c.get("label") for c in cols]
        return list(cols or []), [list(r) for r in (data or [])]

    def close(self) -> None:
        pass


class ZetarisBackend(DataHarness):
    name = "zetaris"

    def __init__(self, channel: str | None = None) -> None:
        super().__init__()
        ch = (channel or os.environ.get("ZETARIS_CHANNEL", "jdbc")).lower()
        self.channel = _RestChannel() if ch == "rest" else _JdbcChannel()
        self.name = f"zetaris-{self.channel.name}"
        self.ddl_log: list[str] = []
        self._bootstrapped = False
        self._local = None

    # -- bootstrap -----------------------------------------------------------
    def _run_ddl(self, sql: str) -> None:
        self.ddl_log.append(redact(sql))
        try:
            self.channel.run(sql)
        except Exception as e:
            if ALREADY_EXISTS.search(str(e)):
                return
            raise HarnessError(f"Zetaris DDL failed: {str(e)[:400]}\nSQL: {redact(sql)[:400]}") from e

    def bootstrap(self) -> None:
        if self._bootstrapped:
            return
        self._run_ddl(f'CREATE LIGHTNING DATABASE {ZETARIS_DATABASE} DESCRIBE BY "Because agent public REST sources - SEC EDGAR, ONS, FRED"')
        self._run_ddl(f"CREATE SCHEMASTORE CONTAINER {ZETARIS_CONTAINER}")
        self._bootstrapped = True

    def _exists(self, qualified: str) -> bool:
        try:
            self.channel.run(f"SELECT 1 FROM {qualified} LIMIT 1")
            return True
        except Exception:
            return False

    def _register(self, spec: SourceSpec) -> str:
        qualified = f"{ZETARIS_CONTAINER}.{spec.table_name}"
        if self._exists(qualified):  # registered on an earlier run: reuse, never drop
            return qualified
        self.bootstrap()
        for stmt in zetaris_ddl(spec):
            self._run_ddl(stmt)
        return qualified

    def _execute(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        return self.channel.run(sql)

    def query(self, sql_template: str, tables: dict[str, SourceSpec]):  # type: ignore[override]
        """Federated query through Zetaris. Sources Zetaris cannot register here (FRED with no
        FRED_API_KEY) are answered by the local DuckDB harness and labelled as such in the trace,
        unless BECAUSE_ZETARIS_HYBRID=0."""
        if os.environ.get("BECAUSE_ZETARIS_HYBRID", "1") != "0" and any(
            s.kind == "fred_series" and not os.environ.get("FRED_API_KEY") for s in tables.values()
        ):
            if self._local is None:
                from because.harness.duckdb_backend import DuckDBBackend

                self._local = DuckDBBackend()
                self._local.name = "duckdb (hybrid: FRED without key)"
            res = self._local.query(sql_template, tables)
            self.log.append({"query_id": res.query_id, "sql": res.sql, "rows": len(res.rows), "error": res.error, "routed": "local"})
            return res
        return super().query(sql_template, tables)

    def ping(self) -> dict[str, Any]:
        cols, rows = self.channel.run("SELECT 1 AS one")
        _, dbs = self.channel.run("SHOW LIGHTNING DATABASES")
        return {"select_1": rows[0][0] if rows else None, "lightning_databases": [r[0] for r in dbs]}

    def close(self) -> None:
        self.channel.close()


def _py(v: Any) -> Any:
    """Coerce JPype/Java values to plain Python."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        return float(str(v)) if re.fullmatch(r"-?\d+(\.\d+)?([eE]-?\d+)?", str(v)) else str(v)
    except Exception:
        return str(v)
