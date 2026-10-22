# Running Because on Zetaris (local Lightning, JDBC)

Because's primary data harness is **Zetaris Lightning**. Every number in an answer comes from a SQL query
the harness ran, and every public source (SEC EDGAR, ONS, FRED) is registered in Zetaris as a
**Lightning REST table** and flattened with a **SchemaStore view**, exactly as the
[Zetaris getting-started REST cookbook](https://zetaris.github.io/getting-started/open_data/rest_apis/HOWTO.html) does it.
The DuckDB fallback (`SAMPLE_MODE=1`) runs the *same* agent SQL over the same endpoints, so the demo
also works with no Zetaris instance.

The free Hobby tier gives you a local Zetaris Lightning instance from a Docker Compose bundle; the
interface is **JDBC** (`jdbc:zetaris:lightning@localhost:10000`, driver class
`com.zetaris.lightning.jdbc.LightningDriver`, driver JAR shipped in the bundle).

## Two commands

```bash
./scripts/zetaris_setup.sh ~/Downloads/zetaris-platform.zip   # install, start, connect, register sources
.venv/bin/because serve                                       # UI at http://127.0.0.1:8000, backend = Zetaris
```

Before the first run:

1. Download `zetaris-platform.zip` from **cloud.enterprise.zetaris.com/dashboard/download** (Run locally page, signed in with the Hobby account).
2. Have the registry credentials for `zetarishackathon.azurecr.io` to hand (the script runs `docker login` once).
3. Docker Desktop running with at least 8 GB memory and ~15 GB free disk; a JDK 11+ (`brew install openjdk@17` on macOS).
   The images are amd64; Apple Silicon runs them under emulation and starts slower.

The script asks for an admin email and password for your local instance if the bundle's `.env` has none
(password policy: 8+ chars, lower, upper, digit and one of `$ @ ! % * ? &`). It never prints secrets.

## What the script does

| Step | Command it runs |
|---|---|
| Check prerequisites | `docker info`, `docker compose version`, `java -version` |
| Unpack + log in | `unzip`, `docker login zetarishackathon.azurecr.io` |
| Configure + start | set `ZETARIS_ADMIN_*` in the bundle `.env`, `./preflight.sh`, `docker compose pull && docker compose up -d`, wait for `./verify.sh` |
| Wire Because | find `jdbc/*driver*.jar`, write `.env.local` (`BECAUSE_BACKEND=zetaris`, `ZETARIS_JDBC_*`, credentials), `pip install -e ".[zetaris]"` |
| Verify | `because zetaris ping` -> `SELECT 1`, `SHOW LIGHTNING DATABASES` |
| Register sources | `because zetaris register` -> the statements in [`zetaris_ddl.sql`](zetaris_ddl.sql) |

Stop with `./scripts/zetaris_setup.sh --down` (data is kept). Re-running the script is safe: existing
views are detected with `SELECT 1 FROM because.<view> LIMIT 1` and reused, never dropped.

## How sources are registered

```sql
CREATE LIGHTNING DATABASE BECAUSE_REST DESCRIBE BY "Because agent public REST sources - SEC EDGAR, ONS, FRED";
CREATE SCHEMASTORE CONTAINER because;

-- one per (company, XBRL concept)
CREATE LIGHTNING REST TABLE edgar_concept_0000050863_grossprofit_raw FROM BECAUSE_REST REQUEST(
    endpoint "https://data.sec.gov/api/xbrl/companyconcept/CIK0000050863/us-gaap/GrossProfit.json",
    method "get", response_type "json", http_encoding "URLENCODED"
) HEADER ( user-agent "because-agent you@example.com" ) BODY ();

CREATE SCHEMASTORE VIEW edgar_concept_0000050863_grossprofit WITH CONTAINER because AS
SELECT CAST(cik AS STRING) AS cik, `entityName` AS entity_name, tag AS concept,
       fact.`start` AS period_start, fact.`end` AS period_end, CAST(fact.val AS DOUBLE) AS value_usd,
       fact.accn AS accession_number, CAST(fact.fy AS BIGINT) AS fiscal_year, fact.fp AS fiscal_period,
       fact.form AS form_type, fact.filed AS filed_date
FROM because_rest.edgar_concept_0000050863_grossprofit_raw
LATERAL VIEW explode(units.USD) AS fact;
```

ONS series (`.../timeseries/d7g7/mm23/data`) are exploded over `months`; FRED series use the FRED JSON API
(`explode(observations)`), which needs a free `FRED_API_KEY`. Without that key Because keeps FRED
queries on the local harness and labels them `duckdb (hybrid: FRED without key)` in the trace
(set `BECAUSE_ZETARIS_HYBRID=0` to make that an error instead).

The agent then runs the same portable SQL on either backend, for example:

```sql
SELECT period_start, period_end, value_usd, form_type, fiscal_period, filed_date, accession_number
FROM because.edgar_concept_0000050863_grossprofit
WHERE form_type = '10-K' AND fiscal_period = 'FY' ORDER BY period_end, filed_date
```

Sources are registered lazily too: asking about a new company creates its tables on first use.

## Status and known gaps

* Written against the getting-started docs and scripts (`scripts/zetaris_sql.py` JDBC channel, REST cookbook,
  EDGAR/PUDL/NOAA guide). It has **not yet been run against a live instance**: the build sandbox had no Docker.
  Unit tests cover the registration flow and SQL shape with a fake JDBC channel.
* If `because zetaris ping` cannot connect, check that the `driver` service publishes port 10000
  (`docker compose ps`), and that `ZETARIS_USERNAME`/`ZETARIS_PASSWORD` match the admin login.
* Statement-level gotchas from the Zetaris SQL companion apply: one statement per call; `DESCRIBE BY` accepts only
  letters, digits, spaces and `_ . - ,`; numeric-looking JSON strings are cast in the view.
* An optional REST channel (`ZETARIS_CHANNEL=rest`, `/api/v1.0/sql-editor/sqls/run`, API key) exists for instances that have one.

## Demo checklist (for the video)

1. `./scripts/zetaris_setup.sh` until `because zetaris register` reports the sources readable.
2. Open the Zetaris UI (http://localhost:3000) -> SQL Editor -> `SHOW LIGHTNING TABLES BECAUSE_REST` to show the registrations.
3. `.venv/bin/because serve`, ask "Why did Intel's gross margin fall in 2024?". The Queries tab shows backend `zetaris-jdbc` and the `because.*` views.
