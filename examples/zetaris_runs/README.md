# Runs recorded on a live Zetaris Lightning instance

Produced by the [`zetaris-e2e`](../../.github/workflows/zetaris-e2e.yml) GitHub Actions workflow: the Zetaris Hobby
Docker Compose bundle (Lightning 2.4.3.1) started on `ubuntu-latest`, sources registered as Lightning REST / FILESTORE
tables with SchemaStore views, and `because ask ... --backend zetaris --strict` over JDBC.

Each question has a Markdown trace and a JSON trace (every query with its SQL, backend label and rows).
`SUMMARY.md` lists, per question, how many queries ran on `zetaris-jdbc` and how many returned rows.
The web UI shows these under "Recorded on a live Zetaris instance".
