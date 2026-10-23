"""Cross-source (federated JOIN) findings on the DuckDB backend with synthetic rows (no network)."""

import re

import pytest

import because.agent.parse as parse
from because.agent.analyst import Analyst
from because.agent.federated import company_sql, cross_source, lead_sql
from because.harness.base import SourceSpec
from because.harness.duckdb_backend import DuckDBBackend
from because.meterless.hmem import HMEM

REV = {2020: 78, 2021: 79, 2022: 63, 2023: 54, 2024: 53}  # synthetic, $B
GP = {2020: 44, 2021: 44, 2022: 27, 2023: 21.7, 2024: 17.3}
PPI = {2019: 50.0, 2020: 50.0, 2021: 51.0, 2022: 53.0, 2023: 55.0, 2024: 57.2}  # flat within each year


def months(y0, y1):
    return [f"{y}-{m:02d}" for y in range(y0, y1 + 1) for m in range(1, 13)]


def edgar_rows(tag, data):
    rows = []
    for fy, v in data.items():
        s, e = f"{fy - 1}-12-31", f"{fy}-12-28"
        rows.append(("50863", "INTEL CORP", tag, s, e, v * 1e9, f"a{fy}", fy, "FY", "10-K", f"{fy + 1}-01-30"))
        # an older, superseded filing of the same period (must lose to the latest filed)
        rows.append(("50863", "INTEL CORP", tag, s, e, v * 0.5e9, f"old{fy}", fy, "FY", "10-K", f"{fy}-01-30"))
        # a quarter reported inside the 10-K (must be filtered out by the window length)
        rows.append(("50863", "INTEL CORP", tag, f"{fy}-09-29", e, v * 0.25e9, f"q{fy}", fy, "FY", "10-K", f"{fy + 1}-01-30"))
    return rows


def gas_level(i):
    return 100 + 30 * ((i % 24) - 12) ** 2 / 144 + 5 * (i % 7)


def loader(spec):
    if spec.kind == "edgar_concept":
        tag = spec.key.split(":")[1]
        data = {"RevenueFromContractWithCustomerExcludingAssessedTax": REV, "GrossProfit": GP}.get(tag, {})
        return edgar_rows(tag, data)
    if spec.kind == "fred_series" and spec.key == "PCU33443344":
        return [(spec.key, f"{m}-01", PPI[int(m[:4])]) for m in months(2019, 2024)]
    ms = months(2010, 2024)
    if spec.kind == "fred_series" and spec.key == "PNGASEUUSDM":
        return [(spec.key, f"{m}-01", gas_level(i)) for i, m in enumerate(ms)]
    if spec.kind == "ons_series" and spec.key == "D7G7":
        # target = 12m % change of gas, 6 months later (so the best lead must be 6)
        lv = {m: gas_level(i) for i, m in enumerate(ms)}
        out = []
        for i, m in enumerate(ms):
            if i >= 18:
                src, prev = ms[i - 6], ms[i - 18]
                out.append((spec.key, m, (lv[src] / lv[prev] - 1) * 100))
        return out
    return []


@pytest.fixture()
def harness():
    return DuckDBBackend(row_loader=loader)


def test_company_sql_joins_edgar_and_fred_on_fiscal_window(harness):
    e = SourceSpec("edgar_concept", "0000050863:RevenueFromContractWithCustomerExcludingAssessedTax")
    m = SourceSpec("fred_series", "PCU33443344")
    res = harness.query(company_sql("2020-01-01"), {"e": e, "m": m})
    assert res.error is None, res.error
    assert re.search(r"\bJOIN\b", res.sql) and {s["kind"] for s in res.sources} == {"edgar_concept", "fred_series"}
    recs = {r["period_end"][:4]: r for r in res.records()}
    assert set(recs) == {"2020", "2021", "2022", "2023", "2024"}
    assert recs["2024"]["value_usd"] == 53e9  # latest filing wins, the quarter is excluded
    # FY2024 window is 2023-12-31..2024-12-28 -> twelve 2024 months, so the average is the 2024 level
    assert recs["2024"]["index_months"] == 12 and recs["2024"]["index_avg"] == pytest.approx(57.2)


def test_lead_sql_recovers_the_lag(harness):
    x, y = SourceSpec("ons_series", "D7G7"), SourceSpec("fred_series", "PNGASEUUSDM")
    res = harness.query(lead_sql("level", "yoy", "2013-01", "2024-12"), {"x": x, "y": y})
    assert res.error is None, res.error
    by = {r["lag_m"]: r["r"] for r in res.records()}
    assert set(by) == {0, 3, 6, 9, 12}
    assert max(by, key=by.get) == 6 and by[6] == pytest.approx(1.0, abs=1e-3)


def test_sql_is_portable_subset():
    for sql in (company_sql("2020-01-01"), lead_sql("yoy", "yoy", "2015-01", "2022-12")):
        up = sql.upper()
        assert not re.search(r"\bWITH\b|\bVALUES\b|::|DATE_|DATEDIFF|STRFTIME|INTERVAL|QUALIFY|ILIKE", up), sql
        assert up.startswith("SELECT ")


@pytest.fixture()
def analyst(tmp_path, monkeypatch, harness):
    monkeypatch.setattr(parse, "sec_tickers", lambda: [{"cik_str": 50863, "ticker": "INTC", "title": "INTEL CORP"}])
    return Analyst(harness=harness, memory=HMEM(path=tmp_path / "mem.json"), llm=None, background=False)


def test_company_run_logs_cross_source_finding_with_sql(analyst):
    t = analyst.ask("Why did Intel's gross margin fall in 2024?")
    xs = [f for f in t.findings if f.kind == "cross-source"]
    assert len(xs) == 1, [s["message"] for s in t.steps if s["stage"] == "cross-source"]
    f = xs[0]
    assert "SEC EDGAR x FRED" in f.statement and "FY2023 -> FY2024" in f.statement
    assert "+4.0%" in f.statement  # PPI 55.0 -> 57.2
    q = next(q for q in t.queries if q["query_id"] == f.query_id)
    assert " JOIN " in q["sql"] and q["federated"] == ["edgar_concept", "fred_series"]
    assert all(r < len(q["rows"]) for r in f.rows)
    assert t.answer["cross_source"][0]["finding"] == f.id


def test_macro_cross_source_on_uk_cpi(tmp_path, harness):
    from because.agent.macro import CONFIG, MacroPlaybook

    a = Analyst(harness=harness, memory=HMEM(path=tmp_path / "m.json"), llm=None, background=False, scout=None)
    a.contract = {"contractId": "test"}
    a._enforce = lambda cap: None
    a.trace.question = "UK CPI test"
    pb = MacroPlaybook(a, "inflation", "GB", 2022, "up")
    assert pb.cfg is CONFIG[("inflation", "GB")]
    f = cross_source(a, pb)
    assert f is not None and f.kind == "cross-source"
    assert "ONS x FRED" in f.statement and "6 months earlier" in f.statement
    q = a.trace.queries[-1]
    assert q["federated"] == ["fred_series", "ons_series"] and " JOIN " in q["sql"] and "LAG(value, 12)" in q["sql"]


def test_cross_source_failure_never_breaks_the_run(tmp_path, monkeypatch, harness):
    import because.agent.federated as fed

    monkeypatch.setattr(parse, "sec_tickers", lambda: [{"cik_str": 50863, "ticker": "INTC", "title": "INTEL CORP"}])
    monkeypatch.setattr(fed, "_company", lambda ctx, pb: (_ for _ in ()).throw(RuntimeError("boom")))
    a = Analyst(harness=harness, memory=HMEM(path=tmp_path / "m.json"), llm=None, background=False)
    t = a.ask("Why did Intel's gross margin fall in 2024?")
    assert "fell" in t.answer["summary"] and not [f for f in t.findings if f.kind == "cross-source"]
    assert any("Cross-source join skipped: RuntimeError: boom" in s["message"] for s in t.steps)


def test_no_self_join_of_one_view():
    """Spark/Zetaris cannot resolve a self-join of one SchemaStore view: each placeholder is scanned once."""
    for sql in (company_sql("2020-01-01"), lead_sql("yoy", "yoy", "2015-01", "2022-12"), lead_sql("level", "yoy", "2015-01", "2022-12")):
        assert sql.count("{x}") <= 1 and sql.count("{y}") <= 1 and sql.count("{e}") <= 1 and sql.count("{m}") <= 1, sql
