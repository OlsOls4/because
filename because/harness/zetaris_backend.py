"""Zetaris Lightning backend (the primary, federated data harness).

Channels
--------
* ``jdbc`` (default) - Zetaris Lightning JDBC driver via ``jaydebeapi`` + JPype.
  This is the interface of the free Hobby tier: a local Zetaris Lightning
  instance from the Docker Compose bundle, reached at
  ``jdbc:zetaris:lightning@localhost:10000``.
* ``ui`` - login to the Lightning UI (``ZETARIS_UI_URL``, default
  ``http://localhost:3000``) and run SQL through the UI proxy
  (``/api/proxy/sql-editor/sqls/run-query``). Useful when JDBC is unavailable.
* ``rest`` (optional) - the documented REST API (``/api/v1.0/sql-editor/sqls/run``)
  for instances where you have an API key.
* ``auto`` - try JDBC, then fall back to the UI proxy.

Env vars (see .env.example and docs/ZETARIS.md)::

    ZETARIS_CHANNEL=jdbc|ui|rest|auto
    ZETARIS_JDBC_URL=jdbc:zetaris:lightning@localhost:10000
    ZETARIS_JDBC_JAR=/abs/path/zetaris-platform/jdbc/ndp-jdbc-driver-...-driver.jar
    ZETARIS_USERNAME=...   ZETARIS_PASSWORD=...
    ZETARIS_UI_URL=http://localhost:3000
    ZETARIS_REST_URL=http://localhost:8888/api/v1.0  ZETARIS_API_KEY=...  ZETARIS_ORG_ID=1
    BECAUSE_ZETARIS_CACHE=1   # CACHE TABLE after each REST table; 0 disables
    BECAUSE_ZETARIS_FILE_DIR=  # host dir bind-mounted into the driver for FRED CSV
    BECAUSE_ZETARIS_FILE_URI=file:///home/zetaris/customer-data/shared/because

How sources are registered (from the getting-started REST cookbook):

    CREATE LIGHTNING DATABASE BECAUSE_REST DESCRIBE BY "...";
    CREATE SCHEMASTORE CONTAINER because;
    CREATE LIGHTNING REST TABLE <t>_raw FROM BECAUSE_REST REQUEST(endpoint "...") ...;
    CACHE TABLE because_rest.<t>_raw;
    CREATE SCHEMASTORE VIEW <t> WITH CONTAINER because AS SELECT ... LATERAL VIEW explode(...);

FRED without ``FRED_API_KEY`` can instead use FILESTORE CSV tables under
``BECAUSE_FILES`` when ``BECAUSE_ZETARIS_FILE_DIR`` is set (see ``zetaris_file_ddl``).

Agent SQL then reads ``because.<t>`` with the same canonical columns the DuckDB
backend exposes, so the analyst code is identical on both backends.
One statement per call, as the Zetaris docs require.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from because.harness.base import CANONICAL_SCHEMAS, DataHarness, HarnessError, SourceSpec
from because.harness.sources import (
    ZETARIS_CONTAINER,
    ZETARIS_DATABASE,
    ZETARIS_FILE_DATABASE,
    fetch_rows,
    fred_file_store_active,
    redact,
    zetaris_ddl,
    zetaris_file_ddl,
    zetaris_file_uri_prefix,
)

DRIVER_CLASS = "com.zetaris.lightning.jdbc.LightningDriver"
DEFAULT_JDBC_URL = "jdbc:zetaris:lightning@localhost:10000"
DEFAULT_REST_URL = "http://localhost:8888/api/v1.0"
DEFAULT_UI_URL = "http://localhost:3000"
ALREADY_EXISTS = re.compile(r"already exist|AlreadyExists|duplicate", re.I)
JWT_RE = re.compile(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_UI_ROW_KEYS = ("data", "rows", "records", "result", "results")


def _norm_col(name: Any) -> str:
    """Hive-style JDBC labels may be ``view.col`` or ``because.view.col``."""
    s = str(name)
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    return s.lower()


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
            cols = [_norm_col(d[0]) for d in cur.description]
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
            return [_norm_col(c) for c in cols], [list(r.values()) for r in data]
        if cols and isinstance(cols[0], dict):
            cols = [c.get("name") or c.get("label") for c in cols]
        return [_norm_col(c) for c in (cols or [])], [list(r) for r in (data or [])]

    def close(self) -> None:
        pass


class _UiChannel:
    """Lightning UI login + proxy SQL (Hobby-tier path when JDBC is down)."""

    name = "ui"

    def __init__(self) -> None:
        self.base = os.environ.get("ZETARIS_UI_URL", DEFAULT_UI_URL).rstrip("/")
        user = os.environ.get("ZETARIS_USERNAME", "")
        pwd = os.environ.get("ZETARIS_PASSWORD", "")
        if not user or not pwd:
            raise HarnessError("Set ZETARIS_USERNAME and ZETARIS_PASSWORD for ZETARIS_CHANNEL=ui.")
        body = {"username": user, "password": pwd}
        req = Request(
            self.base + "/api/auth/login",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "X-Request-ID": str(uuid.uuid4())},
        )
        try:
            with urlopen(req, timeout=60) as resp:
                out = json.loads(resp.read().decode() or "null")
        except HTTPError as e:
            raise HarnessError(f"UI login HTTP {e.code}: {e.read(400).decode(errors='replace')}") from e
        except (URLError, OSError) as e:
            raise HarnessError(f"Cannot reach UI at {self.base}: {getattr(e, 'reason', e)}") from e
        token = _ui_token(out)
        if not token:
            raise HarnessError("UI login response had no token (idToken/token/accessToken or JWT).")
        self._token = token
        org = _ui_org_id(out)
        self.org = str(org if org is not None else os.environ.get("ZETARIS_ORG_ID", "1"))

    def run(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        body = {"sql": sql, "limit": 10000}
        req = Request(
            self.base + "/api/proxy/sql-editor/sqls/run-query",
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "X-Org-ID": self.org,
                "X-Request-ID": str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=600) as resp:
                raw = resp.read().decode() or "null"
                out = json.loads(raw)
        except HTTPError as e:
            raise HarnessError(f"HTTP {e.code}: {e.read(400).decode(errors='replace')}") from e
        except (URLError, OSError) as e:
            raise HarnessError(f"Cannot reach {self.base}: {getattr(e, 'reason', e)}") from e
        cols, rows = _parse_ui_result(out)
        return [_norm_col(c) for c in cols], [[_py(v) for v in r] for r in rows]

    def close(self) -> None:
        pass


def _ui_token(obj: Any) -> str | None:
    named = _find_key_str(obj, ("idToken", "token", "accessToken"))
    if named:
        return named
    return _find_jwt(obj)


def _find_key_str(obj: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if isinstance(v, str) and v:
                return v
        for v in obj.values():
            found = _find_key_str(v, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key_str(v, keys)
            if found:
                return found
    return None


def _find_jwt(obj: Any) -> str | None:
    if isinstance(obj, str):
        m = JWT_RE.search(obj)
        return m.group(0) if m else None
    if isinstance(obj, dict):
        for v in obj.values():
            found = _find_jwt(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_jwt(v)
            if found:
                return found
    return None


def _ui_org_id(obj: Any) -> int | None:
    if isinstance(obj, dict):
        if "orgId" in obj:
            v = obj["orgId"]
            if isinstance(v, bool):
                pass
            elif isinstance(v, int):
                return v
            elif isinstance(v, str) and v.isdigit():
                return int(v)
        for v in obj.values():
            found = _ui_org_id(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _ui_org_id(v)
            if found is not None:
                return found
    return None


def _parse_ui_result(out: Any) -> tuple[list[str], list[list[Any]]]:
    """Tolerant parse of UI proxy SQL responses."""
    dict_rows = _find_dict_rows(out)
    if dict_rows is not None:
        if not dict_rows:
            return [], []
        cols = list(dict_rows[0].keys())
        return cols, [list(r.values()) for r in dict_rows]
    meta = _find_cols_and_matrix(out)
    if meta is not None:
        cols_meta, matrix = meta
        cols: list[str] = []
        for c in cols_meta:
            if isinstance(c, dict):
                cols.append(str(c.get("name") or c.get("label") or c))
            else:
                cols.append(str(c))
        return cols, [list(r) for r in matrix]
    return [], []


def _find_dict_rows(obj: Any) -> list[dict] | None:
    if isinstance(obj, dict):
        for k in _UI_ROW_KEYS:
            v = obj.get(k)
            if isinstance(v, list):
                if not v:
                    return []
                if isinstance(v[0], dict):
                    return v  # type: ignore[return-value]
        for v in obj.values():
            found = _find_dict_rows(v)
            if found is not None:
                return found
    return None


def _find_cols_and_matrix(obj: Any) -> tuple[list[Any], list[list[Any]]] | None:
    if isinstance(obj, dict):
        cols = obj.get("columns")
        if isinstance(cols, list):
            for k in _UI_ROW_KEYS:
                v = obj.get(k)
                if isinstance(v, list) and (not v or isinstance(v[0], (list, tuple))):
                    return cols, [list(r) for r in v]
        for v in obj.values():
            found = _find_cols_and_matrix(v)
            if found is not None:
                return found
    return None


def _open_channel(ch: str) -> Any:
    if ch == "rest":
        return _RestChannel()
    if ch == "ui":
        return _UiChannel()
    if ch == "auto":
        errors: list[str] = []
        try:
            return _JdbcChannel()
        except Exception as e:
            errors.append(f"jdbc: {e}")
        try:
            return _UiChannel()
        except Exception as e:
            errors.append(f"ui: {e}")
        detail = "; ".join(errors)
        raise HarnessError(f"ZETARIS_CHANNEL=auto: both channels failed ({detail})")
    return _JdbcChannel()


class ZetarisBackend(DataHarness):
    name = "zetaris"

    def __init__(self, channel: str | None = None) -> None:
        super().__init__()
        ch = (channel or os.environ.get("ZETARIS_CHANNEL", "jdbc")).lower()
        self.channel = _open_channel(ch)
        self.name = f"zetaris-{self.channel.name}"
        self.ddl_log: list[str] = []
        self._bootstrapped = False
        self._file_db_bootstrapped = False
        self._local = None
        self._absent: set[SourceSpec] = set()
        self._file_store_failed: set[SourceSpec] = set()

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

    def _ensure_file_database(self) -> None:
        if self._file_db_bootstrapped:
            return
        self._run_ddl(
            f'CREATE LIGHTNING DATABASE {ZETARIS_FILE_DATABASE} DESCRIBE BY '
            f'"Because agent public files - FRED CSV"'
        )
        self._file_db_bootstrapped = True

    def _exists(self, qualified: str) -> bool:
        try:
            self.channel.run(f"SELECT 1 FROM {qualified} LIMIT 1")
            return True
        except Exception:
            return False

    def _cache_raw(self, raw: str) -> None:
        if os.environ.get("BECAUSE_ZETARIS_CACHE", "1") == "0":
            return
        cache_sql = f"CACHE TABLE {ZETARIS_DATABASE.lower()}.{raw}"
        self.ddl_log.append(redact(cache_sql))
        try:
            self.channel.run(cache_sql)
        except Exception as e:
            self.ddl_log.append(f"-- cache skipped: {e}")

    def _write_fred_csv(self, spec: SourceSpec) -> Path:
        """Download FRED via keyless CSV and write a stable observation_date,value file."""
        root = Path(os.environ["BECAUSE_ZETARIS_FILE_DIR"])
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o755)
        fred_dir = root / "fred"
        fred_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(fred_dir, 0o755)
        path = fred_dir / f"{spec.key}.csv"
        rows = fetch_rows(spec)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["observation_date", "value"])
            for _sid, period, value in rows:
                w.writerow([period, value])
        os.chmod(path, 0o644)
        return path

    def _register_fred_filestore(self, spec: SourceSpec, qualified: str) -> str:
        self.bootstrap()
        self._ensure_file_database()
        self._write_fred_csv(spec)
        uri = f"{zetaris_file_uri_prefix()}/fred/{spec.key}.csv"
        for stmt in zetaris_file_ddl(spec, uri):
            self._run_ddl(stmt)
        return qualified

    def _register(self, spec: SourceSpec) -> str:
        qualified = f"{ZETARIS_CONTAINER}.{spec.table_name}"
        if self._exists(qualified):  # registered on an earlier run: reuse, never drop
            return qualified
        if spec.kind == "edgar_concept":
            try:
                from because.harness.sources import _get

                _get(spec.url)
            except HarnessError as e:
                if "404" in str(e):
                    self._absent.add(spec)
                    return qualified
            except Exception:
                pass  # other probe errors: register normally
        if fred_file_store_active() and spec.kind == "fred_series":
            try:
                return self._register_fred_filestore(spec, qualified)
            except HarnessError as e:
                self._file_store_failed.add(spec)
                self.ddl_log.append(f"-- file-store failed for {spec.key}: {e}")
                raise
        self.bootstrap()
        stmts = zetaris_ddl(spec)
        # [CREATE LIGHTNING REST TABLE ..., CREATE SCHEMASTORE VIEW ...]
        self._run_ddl(stmts[0])
        raw = f"{spec.table_name}_raw"
        self._cache_raw(raw)
        for stmt in stmts[1:]:
            self._run_ddl(stmt)
        return qualified

    def _execute(self, sql: str) -> tuple[list[str], list[list[Any]]]:
        return self.channel.run(sql)

    def _fred_needs_hybrid(self, spec: SourceSpec) -> bool:
        """True when this FRED series should be answered by local DuckDB."""
        if spec.kind != "fred_series" or os.environ.get("FRED_API_KEY"):
            return False
        if fred_file_store_active() and spec not in self._file_store_failed:
            return False
        return True

    def _query_local_hybrid(self, sql_template: str, tables: dict[str, SourceSpec]):
        if self._local is None:
            from because.harness.duckdb_backend import DuckDBBackend

            self._local = DuckDBBackend()
            self._local.name = "duckdb (hybrid: FRED without key)"
        res = self._local.query(sql_template, tables)
        self.log.append(
            {"query_id": res.query_id, "sql": res.sql, "rows": len(res.rows), "error": res.error, "routed": "local"}
        )
        return res

    def query(self, sql_template: str, tables: dict[str, SourceSpec]):  # type: ignore[override]
        """Federated query through Zetaris. Sources Zetaris cannot register here (FRED with no
        FRED_API_KEY and no FILESTORE dir, or FILESTORE registration failed) are answered by the
        local DuckDB harness and labelled as such in the trace, unless BECAUSE_ZETARIS_HYBRID=0.
        EDGAR concepts that 404 become empty tables."""
        hybrid_ok = os.environ.get("BECAUSE_ZETARIS_HYBRID", "1") != "0"
        if hybrid_ok and any(self._fred_needs_hybrid(s) for s in tables.values()):
            return self._query_local_hybrid(sql_template, tables)

        t0 = time.perf_counter()
        try:
            resolved = {name: self.ensure_source(spec) for name, spec in tables.items()}
        except Exception as e:
            if hybrid_ok and any(self._fred_needs_hybrid(s) for s in tables.values()):
                return self._query_local_hybrid(sql_template, tables)
            resolved = {name: spec.table_name for name, spec in tables.items()}
            sql = sql_template.format(**resolved)
            return self._result(sql, resolved, tables, [], [], f"register: {type(e).__name__}: {e}", t0)

        if any(spec in self._absent for spec in tables.values()):
            sql = sql_template.format(**resolved)
            absent = next(s for s in tables.values() if s in self._absent)
            cols = [c for c, _ in CANONICAL_SCHEMAS[absent.schema]]
            res = self._result(sql, resolved, tables, cols, [], None, t0)
            self.log[-1]["note"] = "source returned 404 (concept not reported); empty table"
            return res

        sql = sql_template.format(**resolved)
        try:
            cols, rows = self._execute(sql)
            err = None
        except Exception as e:
            cols, rows, err = [], [], f"{type(e).__name__}: {e}"
        return self._result(sql, resolved, tables, cols, rows, err, t0)

    def ping(self) -> dict[str, Any]:
        cols, rows = self.channel.run("SELECT 1 AS one")
        _, dbs = self.channel.run("SHOW LIGHTNING DATABASES")
        return {
            "select_1": rows[0][0] if rows else None,
            "lightning_databases": [r[0] for r in dbs],
            "channel": self.channel.name,
        }

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
