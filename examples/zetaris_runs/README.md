# Runs recorded on a live Zetaris Lightning instance

Produced by the [`zetaris-e2e`](../../.github/workflows/zetaris-e2e.yml) GitHub Actions workflow: the Zetaris Hobby
Docker Compose bundle (Lightning 2.4.3.1) started on `ubuntu-latest`, sources registered as Lightning REST / FILESTORE
tables with SchemaStore views, and `because ask ... --backend zetaris --strict` over JDBC.

Each question has a Markdown trace and a JSON trace (every query with its SQL, backend label and rows).
`SUMMARY.md` lists, per question, how many queries ran on `zetaris-jdbc` and how many returned rows.
The web UI shows these under "Recorded on a live Zetaris instance".

Latest recorded run: [Actions run 39949595787](https://github.com/OlsOls4/because/actions/runs/39949595787) (23 Oct 2026):
all five questions on `zetaris-jdbc`, 0 query errors. Intel gross margin Medium 0.65 (14/16 queries with rows),
Nvidia revenue High 0.70 (15/17), UK inflation High 0.85 (12/12), US unemployment High 0.85 (8/8), plus H-MEM recall.
Each company and UK-inflation trace includes one federated cross-source SQL JOIN (EDGAR × FRED PPI; ONS CPI × FRED European gas,
best lead 9 months, r = 0.90); US unemployment has no second source, so it has none. `usl.log` records the optional
Unified Semantic Layer step (9/9 statements). The first green run was [39928173953](https://github.com/OlsOls4/because/actions/runs/39928173953).
`compose/` holds the logs of the bundle's services (tokens are redacted by Zetaris itself).
