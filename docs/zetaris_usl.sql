-- =============================================================================
-- Because on Zetaris: an optional Unified Semantic Layer (USL) model.
--
-- EXTRA, NOT ON THE AGENT'S PATH. The agent's federated cross-source JOIN runs over
-- SchemaStore views (see because/agent/federated.py and docs/zetaris_ddl.sql). This
-- file models the same two sources as a USL package so the join can also be expressed
-- against governed semantic tables, following the lifecycle in
-- https://github.com/zetaris/getting-started/tree/main/open_data/usl (HOWTO.md):
-- namespace -> COMPILE USL ... DDL -> ACTIVATE USL TABLE ... AS SELECT (from the RAW
-- Lightning tables; activating from a SchemaStore view is untested upstream) -> query.
--
-- The zetaris-e2e workflow tries it after the agent run (which has already registered
-- the raw tables) in a continue-on-error step and logs each statement's result to
-- zetaris_runs/usl.log. A failure there never fails the job.
--
-- NOT NULL columns must be COALESCEd in ACTIVATE (Lightning rejects nullable -> NOT NULL; CI run 39948332772).
--
-- Statements are separated by lines that read exactly `-- @@` (not by semicolons:
-- the multi-table COMPILE payload must be sent as ONE statement).
--
-- Prerequisites (created by `because ask ... --backend zetaris` on that instance):
--   because_rest.edgar_concept_0000050863_revenuefromcontractwithcustomerexcludingassessedtax_raw
--       (Lightning REST table: SEC EDGAR companyconcept JSON, Intel revenue)
--   because_files.fred_series_pcu33443344_raw
--       (Lightning FILESTORE CSV table: FRED PPI, semiconductor manufacturing)
-- =============================================================================

CREATE NAMESPACE IF NOT EXISTS lightning.metastore
-- @@
CREATE NAMESPACE IF NOT EXISTS lightning.metastore.because
-- @@
COMPILE USL IF NOT EXISTS because_usl DEPLOY NAMESPACE lightning.metastore.because DDL
CREATE TABLE annual_fact (
    accession_number varchar(25) NOT NULL,
    concept varchar(200),
    period_start varchar(10),
    period_end varchar(10) NOT NULL,
    value_usd double,
    fiscal_period varchar(4),
    form_type varchar(10),
    filed_date varchar(10)
);

CREATE TABLE price_index (
    period varchar(10) NOT NULL PRIMARY KEY,
    value double
);
-- @@
UPDATE USL lightning.metastore.because.because_usl SET DESCRIPTION 'Because agent: SEC EDGAR annual 10-K facts (Intel revenue) and a FRED producer price index, joined on the fiscal-year window to separate price from volume.'
-- @@
ACTIVATE USL TABLE lightning.metastore.because.because_usl.annual_fact AS
SELECT
    COALESCE(fact.accn, '') AS accession_number,
    tag AS concept,
    fact.`start` AS period_start,
    COALESCE(fact.`end`, '') AS period_end,
    CAST(fact.val AS DOUBLE) AS value_usd,
    fact.fp AS fiscal_period,
    fact.form AS form_type,
    fact.filed AS filed_date
FROM because_rest.edgar_concept_0000050863_revenuefromcontractwithcustomerexcludingassessedtax_raw
LATERAL VIEW explode(units.USD) AS fact
WHERE fact.form = '10-K' AND fact.fp = 'FY'
-- @@
ACTIVATE USL TABLE lightning.metastore.because.because_usl.price_index AS
SELECT
    COALESCE(CAST(`observation_date` AS STRING), '') AS period,
    CAST(`value` AS DOUBLE) AS value
FROM because_files.fred_series_pcu33443344_raw
WHERE `value` <> '.'
-- @@
SELECT COUNT(*) AS n FROM lightning.metastore.because.because_usl.annual_fact
-- @@
SELECT COUNT(*) AS n FROM lightning.metastore.because.because_usl.price_index
-- @@
-- The agent's cross-source query, against the USL tables instead of SchemaStore views.
SELECT e.period_start, e.period_end, e.value_usd,
       ROUND(AVG(m.value), 3) AS index_avg, COUNT(m.value) AS index_months
FROM (SELECT period_start, period_end, value_usd,
             ROW_NUMBER() OVER (PARTITION BY period_end ORDER BY filed_date DESC) AS rn
      FROM lightning.metastore.because.because_usl.annual_fact
      WHERE period_start IS NOT NULL
        AND (CAST(SUBSTR(period_end, 1, 4) AS INT) * 12 + CAST(SUBSTR(period_end, 6, 2) AS INT))
          - (CAST(SUBSTR(period_start, 1, 4) AS INT) * 12 + CAST(SUBSTR(period_start, 6, 2) AS INT)) BETWEEN 11 AND 13) e
JOIN lightning.metastore.because.because_usl.price_index m
  ON m.period >= e.period_start AND m.period <= e.period_end
WHERE e.rn = 1 AND e.period_end >= '2020-01-01'
GROUP BY e.period_start, e.period_end, e.value_usd
ORDER BY e.period_end

-- Teardown (USL User Guide; not run by CI):
-- REMOVE USL lightning.metastore.because.because_usl
-- DROP NAMESPACE lightning.metastore.because CASCADE
