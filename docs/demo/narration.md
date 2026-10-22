# Demo video narration (edge-tts en-GB-RyanNeural)

1. This is Because, an analyst agent for the track: The Agent That Can Explain Why. Ask it why something happened, and it investigates public data, tests competing explanations, and shows its work.

2. Language models answer why questions fluently, but you can't check them. Dashboards show what changed, never why. Because closes that gap with an audit trail that a sceptical reader can follow, line by line.

3. Let's ask: why did Intel's gross margin fall in 2024? Meterless Scout classifies the question, runs its guard, and signs a contract that limits which tools the agent may call. Then the agent pulls Intel's filings through the Zetaris data harness.

4. The margin fell from forty to thirty-two point seven percent. The answer names three drivers: restructuring and impairment charges, higher depreciation from heavy capital spending, and lower revenue. Every claim carries finding numbers, and each finding is the exact figure, the SEC concept, and the filing it came from. Confidence is high, at point seven four, and the rationale says why.

5. The evidence graph links the question to hypotheses, findings, and the queries behind them. Counter-evidence is shown too: the drivers overlap, so each size is an upper bound, and the inventory write-down hypothesis stayed inconclusive.

6. The queries tab shows every SQL statement and its source URL. The same portable SQL runs on Zetaris Lightning, or on a local DuckDB fallback in sample mode, which is what this public demo uses.

7. A macro question works the same way. For UK inflation in 2022, wholesale gas led consumer prices by nine months, with a correlation of point nine two. Loose money and a tight labour market are tested as enabling conditions, not triggers.

8. Every finding is written to Meterless H-MEM with its provenance: the query, the rows, and the source. So when I ask what it already knows about Intel, it answers from memory, and says exactly where each belief came from.

9. And a prompt injection is refused by Scout's guard, before any tool runs.

10. Under the hood, Scout routes and gates, the analyst runs a playbook of numeric hypothesis tests, the data harness federates SEC, ONS and FRED, and H-MEM keeps beliefs with lineage. An NVIDIA NIM model can optionally write the narrative, and it's rejected unless every sentence cites a finding.

11. On Zetaris, sources are registered as Lightning REST tables, FRED as file-store CSV tables, and SchemaStore views flatten them into clean columns. A GitHub Actions workflow boots the Zetaris Hobby bundle on a runner, and runs the agent strictly over JDBC.

12. Our H-MEM and Scout implementations pass Meterless's official conformance suites: thirteen of thirteen memory checks, and a passing Scout evaluation, backed by fifty-eight offline tests.

13. Because was built in Cursor, with project rules steering every agent run, and three headless Cursor Agent sessions logged in the repo.

14. Because. Try it at because-agent dot vercel dot app, and read the trace.

Recorded with headless Chromium (CDP screencast) against the local app in SAMPLE_MODE (DuckDB fallback), assembled with ffmpeg.
