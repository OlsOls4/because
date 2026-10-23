# Cursor Agent CLI session: 01-zetaris-backend-hardening

- date: 2026-10-22T12:24:22Z
- cli: cursor-agent 2026.10.19-6bbf2c4
- model: auto
- command: cursor-agent -p --force --trust --model auto --output-format stream-json "<prompt>"

## Prompt

```
You are working in the Because repo (read AGENTS.md and .cursor/rules first). Task: harden the Zetaris backend (because/harness/zetaris_backend.py) for its first run against a real Zetaris Lightning 2.4.3.1 instance (local Docker bundle in CI). Do NOT touch .github/, scripts/, README.md or the web UI; another developer owns those. Make these changes, with offline tests in tests/test_harness.py, and run `.venv/bin/pytest -q` until green:

1. Column labels: the Lightning JDBC driver (Hive-style) may return labels like `view_name.col` or `because.view.col`. In every channel's run(), normalise each column name to the part after the last '.', lower-cased.

2. Caching: the Zetaris REST cookbook says a Lightning REST table re-fetches its endpoint on every query unless cached. In `_register`, right after the `CREATE LIGHTNING REST TABLE <raw>` statement succeeds, run `CACHE TABLE because_rest.<raw>` (use ZETARIS_DATABASE.lower()). A failure of CACHE TABLE must not fail registration: append a line `-- cache skipped: <error>` to ddl_log and continue. Easiest: have `sources.zetaris_ddl` keep returning [rest_table, view], and do the CACHE in the backend between them (keep `zetaris_ddl` signature unchanged; the existing test checks it). Controlled by env BECAUSE_ZETARIS_CACHE (default "1"; "0" disables).

3. Absent sources: SEC EDGAR returns HTTP 404 for a company that never reported a concept. The DuckDB backend treats that as an empty table. Mirror that: before registering an `edgar_concept` spec, probe its URL with `because.harness.sources._get` (it caches; raises HarnessError containing "404" on 404). If 404, remember the spec in `self._absent` and do not register it. In `query()`, if any spec in `tables` is absent, return a QueryResult with zero rows and error=None (use `self._result(...)`, columns from CANONICAL_SCHEMAS for that spec's schema is fine or empty list), and add `"note": "source returned 404 (concept not reported); empty table"` to the self.log entry. Other probe errors: ignore and register normally.

4. New UI-proxy channel `_UiChannel` (ZETARIS_CHANNEL=ui), modelled on zetaris getting-started scripts/zetaris_api.py and the bundle's verify.sh:
   - base = ZETARIS_UI_URL (default http://localhost:3000). Login: POST {base}/api/auth/login JSON {"username": ZETARIS_USERNAME, "password": ZETARIS_PASSWORD}, headers Content-Type application/json and X-Request-ID uuid4. Token = idToken/token/accessToken key, else first JWT-looking string (regex [A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}) anywhere in the JSON. orgId = first integer value of key "orgId" anywhere in the response (recursive), else env ZETARIS_ORG_ID, else 1.
   - Query: POST {base}/api/proxy/sql-editor/sqls/run-query JSON {"sql": sql, "limit": 10000}, headers Authorization Bearer <token>, X-Org-ID, X-Request-ID, Content-Type. Timeout 600 s. On HTTP error raise HarnessError with code and first 400 chars of body.
   - Parse tolerantly: find the first list of dicts under keys data / rows / records / result / results (search nested dicts too). Columns = keys of the first row; rows = values. If a list of lists plus a "columns" list (of str or {name}) is present, use that. Coerce values with `_py`. Empty or DDL responses return ([], []).
   - Never log the password or token.
5. ZETARIS_CHANNEL=auto: try _JdbcChannel; on any exception try _UiChannel; if both fail raise HarnessError mentioning both errors. Set self.name to f"zetaris-{channel.name}". Keep default "jdbc" when the env var is unset.
6. ping() should also return {"channel": self.channel.name}.

Keep code style, type hints and the module docstring updated (mention the ui channel). Do not add dependencies (stdlib urllib only). When done, print a short summary of what changed and the pytest result.
```

## Run

- session model: Auto
- duration: 281 s
- status: success
- tool calls: 22

### Tool calls (in order)

- `read: <repo>/AGENTS.md`
- `read: <repo>/because/harness/zetaris_backend.py`
- `read: <repo>/.cursor/rules/because.mdc`
- `grep: <repo>`
- `read: <repo>/tests/test_harness.py`
- `grep: <repo>/because/harness`
- `read: <repo>/because/harness/base.py`
- `glob: `
- `read: <repo>/docs/cursor-sessions/01-zetaris-backend-hardening.md`
- `read: <repo>/because/harness/sources.py`
- `read: <repo>/because/harness/duckdb_backend.py`
- `grep: <repo>`
- `getMcpTools: `
- `updateTodos: `
- `edit: <repo>/because/harness/zetaris_backend.py`
- `edit: <repo>/tests/test_harness.py`
- `edit: <repo>/tests/test_harness.py`
- `updateTodos: `
- `shell: .venv/bin/pytest -q 2>&1`
- `edit: <repo>/because/harness/zetaris_backend.py`
- `shell: .venv/bin/pytest -q 2>&1`
- `updateTodos: `

## Agent's final message

Hardened `because/harness/zetaris_backend.py` for Lightning 2.4.3.1; offline tests extended in `tests/test_harness.py`.

**Changes**
1. **Column labels** — `_norm_col()` strips Hive-style qualifiers (`view.col` / `because.view.col`) and lower-cases; applied in jdbc/rest/ui `run()`.
2. **CACHE TABLE** — After `CREATE LIGHTNING REST TABLE`, runs `CACHE TABLE because_rest.<raw>`; failures log `-- cache skipped: …` and continue. Gated by `BECAUSE_ZETARIS_CACHE` (default `1`).
3. **Absent EDGAR** — Probes with `sources._get` before register; 404 → `_absent`, empty `QueryResult` + log note; other probe errors still register.
4. **`_UiChannel`** — UI login + `/api/proxy/sql-editor/sqls/run-query` with tolerant parse; no password/token logging.
5. **`ZETARIS_CHANNEL=auto`** — JDBC then UI; default remains `jdbc`.
6. **`ping()`** — Includes `"channel"`.

**pytest:** `47 passed, 2 deselected`

Raw event stream: `01-zetaris-backend-hardening.jsonl` (Cursor `--output-format stream-json`).
