# AGENTS.md - working on Because

Because is an analyst agent that answers *why* questions with evidence. Every claim in an answer cites a
finding (`F#`), every data finding cites a harness query (`query_id` + row indexes + source URL), and the
answer always carries counter-evidence and a confidence with its rationale. Keep it that way.

## Map

| Path | What |
|---|---|
| `because/harness/` | DataHarness contract. `zetaris_backend.py` (JDBC/REST, primary), `duckdb_backend.py` (fallback), `sources.py` (endpoints, local fetch, Zetaris DDL) |
| `because/agent/analyst.py` | Orchestration: Scout -> H-MEM recall -> playbook -> answer -> narrative -> H-MEM write |
| `because/agent/company.py`, `macro.py` | Playbooks: hypotheses with explicit, numeric tests |
| `because/meterless/hmem.py` | Meterless H-MEM implementation (memory with provenance) |
| `because/meterless/scout.py` | Meterless Scout Intent implementation (routing, guard, signed contracts) |
| `because/agent/intents.json` | Scout registry for Because |
| `because/llm.py` | NVIDIA NIM / Groq / Gemini, all OpenAI-compatible, optional |
| `because/web/` | FastAPI + single-file UI |
| `conformance/meterless/` | Adapters that run the official Meterless TS suites against the Python engines |
| `examples/outputs/` | Saved traces (regenerate with `because examples`) |

## Rules

1. **No uncited numbers.** A number reaches the answer only through a `Finding` built from a `QueryResult`.
   LLM output is prose only and must pass `validate_narrative` (every sentence cited, every number present in findings).
2. **Portable SQL only** in agent code: ANSI `SELECT/WHERE/GROUP BY/ORDER BY/LIMIT/CAST/CASE`, tables via `{t}` placeholders.
   Backend-specific SQL lives in `sources.zetaris_ddl` (Lightning) and `duckdb_backend.py`.
3. **Every tool call goes through the contract.** Call `self._enforce("<capability>")` (analyst) before using a tool; add new
   capabilities to `intents.json` first.
4. **Memory writes carry provenance** (`origin`, `label`, `refs.query_id`, `refs.sql`, `refs.source_url`). Records without a `source` are rejected.
5. **Engines follow their spec.** H-MEM must stay green on `pytest tests/test_hmem_conformance.py` and `conformance/meterless/run.sh`.
   Scout must clear the eval gates (`conformance/meterless/scout-eval-report.md`).
6. **Free only.** No paid APIs; keyless public sources by default. Never commit secrets; `.env.local` is gitignored.
7. Counter-evidence is a feature: when a test fails, say so in the answer rather than dropping the hypothesis.

## Commands

```bash
pip install -e ".[dev]"            # add ,zetaris for JDBC
pytest -q                          # offline tests (fast)
pytest -m live                     # hits SEC/ONS/FRED
because ask "Why did UK inflation spike in 2022?" --md /tmp/trace.md
because serve
bash conformance/meterless/run.sh  # official Meterless suites (needs node)
```

## Adding a playbook

Create `because/agent/<name>.py` with a class exposing `run() -> dict` (keys: `target_finding`, `target_fids`, `delta`,
`actual_dir`, `premise_ok`, `move`, `subject`, `period`, `coverage`), add an intent with examples to `intents.json`,
bind parameters in `analyst.ask`, and add an offline test with a synthetic `row_loader`.
