# Cursor Agent CLI session: 02-fred-filestore-and-sql-cli

- date: 2026-10-22T12:30:58Z
- cli: cursor-agent 2026.10.19-6bbf2c4
- model: auto
- command: cursor-agent -p --force --trust --model auto --output-format stream-json "<prompt>"

## Prompt

```
Because repo (read AGENTS.md and .cursor/rules). Two changes; keep `.venv/bin/pytest -q` green and add offline tests. Do NOT edit .github/, README.md or because/web/.

A) FRED through Zetaris without an API key, as Lightning FILESTORE (CSV) tables.
Today, FRED series need FRED_API_KEY to be registered in Zetaris (REST JSON API); without it ZetarisBackend.query routes them to local DuckDB ("hybrid"). Add a third path, used when FRED_API_KEY is unset AND env BECAUSE_ZETARIS_FILE_DIR is set (a host directory that the Zetaris driver container can read, bind-mounted at the URI prefix in env BECAUSE_ZETARIS_FILE_URI, default "file:///home/zetaris/customer-data/shared/because"):
  1. In because/harness/sources.py add `zetaris_file_ddl(spec, uri: str) -> list[str]` for kind fred_series, following the Lightning SQL reference syntax
     `CREATE LIGHTNING FILESTORE TABLE <table> FROM <database> FORMAT CSV OPTIONS (path "<uri>", header "true", inferSchema "false")`.
     Database: new constant ZETARIS_FILE_DATABASE = "BECAUSE_FILES" (created with `CREATE LIGHTNING DATABASE BECAUSE_FILES DESCRIBE BY "Because agent public files - FRED CSV"`).
     Raw table name `<spec.table_name>_raw`; then `CREATE SCHEMASTORE VIEW <spec.table_name> WITH CONTAINER because AS SELECT '<KEY>' AS series_id, CAST(<date col> AS STRING) AS period, CAST(<value col> AS DOUBLE) AS value FROM because_files.<raw> WHERE <value col> <> '.'`.
     FRED's fredgraph.csv header is `observation_date,<SERIES_ID>` (older files used `DATE`). Write the CSV yourself so the header is stable: columns `observation_date,value` (lower-case), so the view uses `observation_date` and `value` (backtick them).
  2. In because/harness/zetaris_backend.py: when that mode is active, `_register` for a fred_series spec downloads the series with the existing local fetch (`sources.fetch_rows(spec)` gives (series_id, date, value) tuples), writes `<BECAUSE_ZETARIS_FILE_DIR>/fred/<KEY>.csv` (mode 0644, dir 0755) with header `observation_date,value`, then runs `CREATE LIGHTNING DATABASE BECAUSE_FILES ...` once (ignore already-exists, same as bootstrap) and the statements from zetaris_file_ddl with uri f"{prefix}/fred/{KEY}.csv". The hybrid routing in `query()` must then NOT route these to DuckDB. If the file-store registration fails (any HarnessError), fall back to the current hybrid behaviour for that spec (remember it in a set, log `-- file-store failed for <KEY>: <err>` to ddl_log) unless BECAUSE_ZETARIS_HYBRID=0, in which case surface the error.
  3. `because zetaris register` (cli.warm_specs) currently drops FRED specs when FRED_API_KEY is unset; keep them when BECAUSE_ZETARIS_FILE_DIR is set.
  4. `because zetaris ddl` should print the file-store DDL for FRED when BECAUSE_ZETARIS_FILE_DIR is set (path placeholder ok).

B) CLI: add `because zetaris sql "<statement>"` that runs one statement on the configured Zetaris channel and prints JSON {"columns": [...], "rows": [...]} (rows capped at 200). Add `query` positional nargs="?" to the zetaris subparser. Redact with `redact()` before printing the SQL anywhere.

Also update docs/ZETARIS.md with a short "FRED as CSV file-store tables" section. Print a summary and the pytest result at the end.
```

## Run

- status: **stopped by Cursor** after ~9 minutes and 38 tool calls: `ActionRequiredError: You've hit your usage limit` (free Hobby plan's included agent usage).
- By then the agent had implemented `zetaris_file_ddl` + `fred_file_store_active` (sources.py), the FILESTORE registration path with CSV writing and per-spec hybrid fallback (zetaris_backend.py), `because zetaris sql` and the `warm_specs` change (cli.py), docs/ZETARIS.md and .env.example updates, and new tests.
- Finished by hand (outside Cursor): one test (`test_zetaris_absent_edgar_404`) installed its 404 stub before the helper's default stub, so the stub was overwritten; reordered. Suite: 58 passed.

Raw event stream: `02-fred-filestore-and-sql-cli.jsonl` (Cursor `--output-format stream-json`, truncated where the CLI stopped).
