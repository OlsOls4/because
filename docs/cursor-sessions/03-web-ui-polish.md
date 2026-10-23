# Cursor Agent CLI session: 03-web-ui-polish

- date: 2026-10-22T12:32:20Z
- cli: cursor-agent 2026.10.19-6bbf2c4
- model: auto
- command: cursor-agent -p --force --trust --model auto --output-format stream-json "<prompt>"

## Prompt

```
Because repo (read AGENTS.md and .cursor/rules). Polish the web UI for hackathon judges. ONLY edit because/web/static/index.html, because/web/app.py and add tests in tests/test_web.py. Another agent is editing because/harness/, because/cli.py, sources and docs at the same time: do not touch those files. No build step, no new dependencies (FastAPI TestClient needs httpx, already installed). Keep the existing look (system font, indigo accent, cards) and every existing feature; make it clearly better:

1. Pipeline strip under the question box once a result is shown: five clickable steps "Plan → Zetaris SQL → Evidence → Hypothesis tests → Answer", each with a count (plan steps, queries with the backend name e.g. "15 queries on zetaris-jdbc", findings, hypotheses with supported/contradicted counts, confidence). Clicking a step opens the matching tab (trace, queries, evidence, hyp) or scrolls to the answer.
2. Backend badge in the header area: "Zetaris Lightning (JDBC)" in teal when trace.backend starts with "zetaris", "DuckDB fallback (SAMPLE_MODE)" in grey otherwise; if any query's backend contains "hybrid", add a small note "N queries answered locally (FRED without key)". Show trace.fallback_reason prominently (amber box) when present.
3. "Meterless" card in the right column, above Run: Scout intent, band, risk level, contract id (first 12 chars), capabilities as pills, TTL/expiresAt, signature algorithm (HMAC-SHA256) and "verified" if trace.scout.contract has a signature; H-MEM: recalled count, written count (trace.memory_written length), conflicts count (trace.conflicts). Link "details" to the Scout contract / Memory tabs. Tolerate missing fields.
4. Citations: hovering a [F#] chip shows a tooltip (title attribute is fine) with the finding statement, query_id and source URL; clicking keeps the current jump-to-row behaviour; in the evidence table, the query_id cell links to the Queries tab and highlights that query (give each query block an id).
5. Queries tab: group a header per query with backend pill coloured teal for zetaris*, grey for duckdb; add a "copy SQL" button per query.
6. Saved runs: the API should also list saved Zetaris runs from examples/zetaris_runs/*.json (same format as examples/outputs) if the directory exists. Add GET /api/zetaris_runs (list of {question, slug}) and GET /api/zetaris_runs/{slug}; in the UI add a second chip row "Recorded on a live Zetaris instance:" shown only when the list is non-empty, chips marked with a small "Z" badge.
7. Loading state: while /api/ask runs, show an animated status that cycles through the stage names (Scout → H-MEM recall → Zetaris queries → hypothesis tests → answer) and disable chips.
8. Mobile: single column below 900 px still works; no horizontal overflow except inside pre/table wrappers (wrap tables in a div with overflow:auto).
9. Accessibility: buttons have aria-labels where icon-only; tabs are buttons or have role=tab.

Add tests/test_web.py using fastapi.testclient: GET / returns 200 and contains "Because"; GET /api/examples returns a list; GET /api/examples/<first slug> returns trace+mermaid; GET /api/zetaris_runs returns a list (possibly empty) and 404 for an unknown slug. Do not call /api/ask in tests (network). Run `.venv/bin/pytest -q` until green and print a short summary.
```

## Run

- session model: Auto
- duration: 196 s
- status: success
- tool calls: 30

### Tool calls (in order)

- `read: <repo>/AGENTS.md`
- `read: <repo>/because/web/app.py`
- `read: <repo>/because/web/static/index.html`
- `glob: `
- `glob: `
- `glob: `
- `read: <repo>/.cursor/rules/zetaris.mdc`
- `glob: `
- `grep: EXAMPLES|slug|to_dict|fallback_reason|memory_written|conflicts`
- `shell: ls -la examples/ 2>/dev/null; ls examples/outputs/ 2>/dev/null | head -20; ls examples/zetaris_runs/ 2>/dev/null | head -10; .venv/bin/pytho`
- `shell: .venv/bin/python -c "
import json
from pathlib import Path
p = Path('examples/outputs/why-did-uk-inflation-spike-in-2022.json')
t = json.loa`
- `read: <repo>/because/agent/trace.py`
- `grep: signature|expiresAt|capabilities|contract`
- `glob: `
- `shell: .venv/bin/python -c "
import json
from pathlib import Path
t = json.loads(Path('examples/outputs/why-did-uk-inflation-spike-in-2022.json').r`
- `read: <repo>/tests/test_analyst_offline.py`
- `grep: zetaris_runs|SAMPLE_MODE|hybrid|pipeline`
- `getMcpTools: `
- `read: <repo>/docs/cursor-sessions/03-web-ui-polish.md`
- `shell: .venv/bin/python -c "
import json
from pathlib import Path
t = json.loads(Path('examples/outputs/why-did-intel-s-gross-margin-fall-in-2024.j`
- `updateTodos: `
- `edit: <repo>/because/web/app.py`
- `edit: <repo>/because/web/app.py`
- `edit: <repo>/because/web/static/index.html`
- `edit: <repo>/tests/test_web.py`
- `shell: .venv/bin/pytest -q 2>&1`
- `updateTodos: `
- `shell: .venv/bin/pytest -q tests/test_web.py 2>&1; echo "---"; .venv/bin/pytest -q --ignore=tests/test_harness.py 2>&1; echo "---"; git status -sb;`
- `updateTodos: `
- `read: <repo>/because/web/app.py`

## Agent's final message

I'll read AGENTS.md and the current web UI files, then polish only those paths plus `tests/test_web.py`.Implementing API endpoints first, then the full UI polish and tests.Harness failures look like concurrent work elsewhere. Confirming web tests pass and that our changes didn't cause them.Web UI polish is in place (only `because/web/app.py`, `because/web/static/index.html`, `tests/test_web.py`).

**API:** `GET /api/zetaris_runs` and `GET /api/zetaris_runs/{slug}` read `examples/zetaris_runs/*.json` when present (empty list / 404 otherwise).

**UI:** Pipeline strip, backend badge + amber fallback, Meterless card, citation tooltips + query deep-links, Queries with teal/grey backend pills + Copy SQL, Zetaris chip row with **Z** badge, cycling load status, table overflow wrappers, tab/button a11y.

**Tests:** `tests/test_web.py` — 3 passed. Full suite excluding harness: **33 passed**. Full `pytest -q` still has **3 failures in `tests/test_harness.py`** from the parallel FRED/Zetaris agent (`cli` / harness WIP); left those files alone per your scope.

Raw event stream: `03-web-ui-polish.jsonl` (Cursor `--output-format stream-json`).
