# Runs recorded on a live Zetaris Lightning instance

Produced by the [`zetaris-e2e`](../../.github/workflows/zetaris-e2e.yml) GitHub Actions workflow: the Zetaris Hobby
Docker Compose bundle (Lightning 2.4.3.1) started on `ubuntu-latest`, sources registered as Lightning REST / FILESTORE
tables with SchemaStore views, and `because ask ... --backend zetaris --strict` over JDBC.

Each question has a Markdown trace and a JSON trace (every query with its SQL, backend label and rows).
`SUMMARY.md` lists, per question, how many queries ran on `zetaris-jdbc` and how many returned rows.
The web UI shows these under "Recorded on a live Zetaris instance".

Recorded run: [Actions run 39928173953](https://github.com/OlsOls4/because/actions/runs/39928173953) (23 Oct 2026):
all five questions on `zetaris-jdbc`, 0 query errors. Intel gross margin High 0.74 (13/15 queries with rows),
Nvidia revenue High 0.70 (14/16), UK inflation High 0.94 (11/11), US unemployment High 0.91 (6/6), plus H-MEM recall.
`compose/` holds the logs of the bundle's services (tokens are redacted by Zetaris itself).
