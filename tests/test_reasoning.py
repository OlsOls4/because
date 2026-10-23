"""Regression tests for the reasoning layer (offline, synthetic fixture series; not real data).

Covers the judges' findings: start-of-year move measurement, direction from data, driver sign checks over the
same interval, neutral hypothesis statements, honest/monotone confidence, number-token validation, scope messages.
"""

import itertools

import pytest

import because.agent.parse as parse
from because.agent.analyst import (MACRO_CONFIDENCE_CAP, SINGLE_DRIVER_CAP, Analyst, confidence_score, lower_first,
                                   number_tokens, seed_memory_from_trace, validate_narrative)
from because.agent.macro import CONFIG, locate_move
from because.harness.duckdb_backend import DuckDBBackend
from because.meterless.hmem import HMEM


def months(y0=2017, y1=2024):
    return [f"{y}-{m:02d}" for y in range(y0, y1 + 1) for m in range(1, 13)]


def interp(points: dict[str, float]) -> dict[str, float]:
    """Piecewise-linear monthly series through the given (month -> value) anchor points."""
    ks = sorted(points)
    idx = {m: i for i, m in enumerate(months())}
    out = {}
    for a, b in zip(ks, ks[1:]):
        ia, ib = idx[a], idx[b]
        for i in range(ia, ib + 1):
            out[months()[i]] = points[a] + (points[b] - points[a]) * (i - ia) / (ib - ia)
    return out


# synthetic UK CPI annual rate: ~0.8 in 2020, 5.4 at Dec 2021, 11.1 peak Oct 2022, 10.5 Dec 2022, 3.9 low Nov 2023
CPI = interp({"2017-01": 2.0, "2019-12": 1.5, "2020-06": 0.6, "2020-12": 0.6, "2021-03": 0.7, "2021-12": 5.4,
              "2022-10": 11.1, "2022-12": 10.5, "2023-11": 3.9, "2023-12": 4.0, "2024-12": 2.5})
ENERGY = {m: 2.4 * v - 1.0 for m, v in CPI.items()}  # household energy component tracks headline
SERVICES = {m: 3.0 for m in CPI}  # flat: should not be supported


def macro_loader(series: dict[str, dict[str, float]]):
    def loader(spec):
        s = series.get(spec.key.split(":")[0])
        return [(spec.key, m, v) for m, v in sorted(s.items())] if s else []
    return loader


@pytest.fixture()
def tickers(monkeypatch):
    monkeypatch.setattr(parse, "sec_tickers", lambda: [{"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
                                                       {"cik_str": 50863, "ticker": "INTC", "title": "INTEL CORP"}])


def mk(tmp_path, loader):
    return Analyst(harness=DuckDBBackend(row_loader=loader), memory=HMEM(path=tmp_path / "mem.json"), llm=None, background=False)


@pytest.fixture()
def uk_cpi(tmp_path, tickers):
    return mk(tmp_path, macro_loader({"D7G7": CPI, "D7GB": ENERGY, "D7NN": SERVICES}))


# -- 1. move measurement ------------------------------------------------------------------------

def test_locate_move_uses_start_of_year_not_y_minus_2():
    up = locate_move(CPI, 2022)
    assert up["start_m"] == "2021-12" and up["ext_m"] == "2022-10" and up["dir"] == "up"
    assert round(up["start"], 1) == 5.4 and round(up["ext"], 1) == 11.1
    down = locate_move(CPI, 2023)
    assert down["start_m"] == "2022-12" and down["ext_m"] == "2023-11" and down["dir"] == "down"


def test_uk_inflation_fall_2023_true_premise_is_not_contradicted(uk_cpi):
    t = uk_cpi.ask("Why did UK inflation fall in 2023?")
    a = t.answer
    assert a["actual_direction"] == "down" and a["premise_ok"], a["summary"]
    assert a["summary"].startswith("UK inflation fell in 2023: 10.5% (2022-12) -> 3.9% (2023-11 low)"), a["summary"]
    assert not any(c["claim"].startswith("Premise check") for c in a["counter_evidence"])
    energy = next(h for h in t.hypotheses if "Household energy" in h.statement)
    assert "fell" in energy.statement and energy.verdict == "supported"
    services = next(h for h in t.hypotheses if "Services inflation" in h.statement)
    assert services.verdict != "supported"


def test_uk_inflation_rise_2022_headline_uses_dec_2021_start(uk_cpi):
    t = uk_cpi.ask("Why did UK inflation spike in 2022?")
    s = t.answer["summary"]
    assert s.startswith("UK inflation rose in 2022: 5.4% (2021-12) -> 11.1% (2022-10 peak), +5.7 pts"), s
    assert "0.6 ->" not in s and "0.8 ->" not in s  # the Y-2 average is context only
    assert "2020 average" in s


def test_wrong_premise_is_flagged_and_not_remembered(uk_cpi):
    t = uk_cpi.ask("Why did UK inflation rise in 2023?")
    assert not t.answer["premise_ok"]
    assert any(c["claim"].startswith("Premise check") for c in t.answer["counter_evidence"])
    assert not t.memory_written and t.answer.get("memory_skipped")


# -- 2. driver sign check over the same interval ------------------------------------------------

def test_driver_window_high_is_not_read_as_a_cause(tmp_path, tickers):
    # synthetic 2004-2011: unemployment rises into 2009; oil peaks mid-2008 then collapses; output falls
    ms = [f"{y}-{m:02d}" for y in range(2004, 2012) for m in range(1, 13)]
    def path(anchors):
        ks = sorted(anchors)
        out = {}
        for a, b in zip(ks, ks[1:]):
            ia, ib = ms.index(a), ms.index(b)
            for i in range(ia, ib + 1):
                out[ms[i]] = anchors[a] + (anchors[b] - anchors[a]) * (i - ia) / (ib - ia)
        return out
    unemp = path({"2004-01": 5.0, "2007-12": 5.2, "2008-12": 6.5, "2009-06": 7.9, "2009-12": 7.8, "2011-12": 8.0})
    oil = path({"2004-01": 40, "2007-01": 55, "2008-07": 133, "2008-12": 40, "2009-06": 68, "2009-12": 75, "2011-12": 110})
    ip = path({"2004-01": 100, "2008-01": 101, "2008-12": 93, "2009-06": 88, "2009-12": 89, "2011-12": 92})
    a = mk(tmp_path, macro_loader({"LRHUTTTTGBM156S": unemp, "POILBREUSDM": oil, "GBRPROINDMISMEI": ip}))
    t = a.ask("Why did UK unemployment rise in 2009?")
    oil_h = next(h for h in t.hypotheses if "Brent" in h.statement)
    assert oil_h.verdict != "supported", oil_h.detail  # 12-month window high, but its change over the interval is down
    ip_h = next(h for h in t.hypotheses if "industrial production" in h.statement)
    assert ip_h.verdict == "supported", ip_h.detail


# -- 3. neutral statements, context separate --------------------------------------------------------

def test_hypothesis_statements_are_neutral_and_context_is_labelled(uk_cpi):
    for cfg in CONFIG.values():
        for d in cfg["drivers"]:
            for word in ("Russia", "Ukraine", "pandemic", "COVID", "stimulus", "Shutdown", "caused"):
                assert word.lower() not in d["noun"].lower(), d["noun"]
    t = uk_cpi.ask("Why did UK inflation spike in 2022?")
    for h in t.hypotheses:
        assert "Russia" not in h.statement and "shock" not in h.statement
    for b in t.answer["because"]:
        if b.get("context"):
            assert "Context (not tested):" in b["detail"]
    assert "points mainly to" not in t.answer["summary"]
    assert "consistent with" in t.answer["summary"]
    assert any(c["claim"].startswith("Correlation is not causation") for c in t.answer["counter_evidence"])
    assert t.answer["confidence"]["score"] <= MACRO_CONFIDENCE_CAP


# -- 4. confidence ----------------------------------------------------------------------------------

def test_confidence_is_monotone_and_capped():
    grid = [0.0, 0.3, 0.6, 1.0]
    for comp, ctx, prem, ncon, ng, pb in itertools.product(grid, (0, 1), (True, False), (0, 1, 3), (0, 1, 2, 4), ("macro", "company")):
        scores = [confidence_score(c, comp, ctx, prem, ncon, ng, pb) for c in grid]
        assert scores == sorted(scores)  # more coverage never lowers confidence
        assert confidence_score(0.8, comp, ctx, prem, ncon + 1, ng, pb) <= confidence_score(0.8, comp, ctx, prem, ncon, ng, pb)
        assert confidence_score(0.8, comp, ctx, prem, ncon, ng + 1, pb) >= confidence_score(0.8, comp, ctx, prem, ncon, ng, pb)
        assert confidence_score(0.8, comp, ctx, False, ncon, ng, pb) <= confidence_score(0.8, comp, ctx, True, ncon, ng, pb)
        if pb == "macro":
            assert confidence_score(1.0, 1.0, 1, True, 0, 5, pb) <= MACRO_CONFIDENCE_CAP
    assert confidence_score(1.0, 1.0, 1, True, 0, 1, "company") <= SINGLE_DRIVER_CAP
    assert confidence_score(1.0, 1.0, 1, True, 0, 3, "company", one_off_top=True) <= 0.65


TESLA = {  # synthetic Tesla-like annual values ($B): net income halves because the prior year had a tax benefit
    "Revenues": {2021: 54, 2022: 81, 2023: 97, 2024: 98},
    "GrossProfit": {2021: 13.6, 2022: 20.9, 2023: 17.7, 2024: 17.5},
    "ResearchAndDevelopmentExpense": {2021: 2.6, 2022: 3.1, 2023: 4.0, 2024: 4.5},
    "SellingGeneralAndAdministrativeExpense": {2021: 4.5, 2022: 3.9, 2023: 4.8, 2024: 5.2},
    "OperatingIncomeLoss": {2021: 6.5, 2022: 13.7, 2023: 8.9, 2024: 7.1},
    "NetIncomeLoss": {2021: 5.5, 2022: 12.6, 2023: 15.0, 2024: 7.1},
    "IncomeTaxExpenseBenefit": {2021: 0.7, 2022: 1.1, 2023: -5.0, 2024: 1.8},
}


def edgar_loader(data, cik="1318605", name="Tesla, Inc."):
    def loader(spec):
        if spec.kind != "edgar_concept":
            return []
        tag = spec.key.split(":")[1]
        return [(cik, name, tag, f"{fy}-01-01", f"{fy}-12-31", v * 1e9, f"a{fy}", fy, "FY", "10-K", f"{fy + 1}-01-30")
                for fy, v in data.get(tag, {}).items()]
    return loader


def test_single_supported_driver_and_one_off_tax_item_are_not_high_confidence(tmp_path, tickers):
    a = mk(tmp_path, edgar_loader(TESLA))
    t = a.ask("Why did Tesla's net income fall in 2024?")
    ans = t.answer
    tax = next(h for h in t.hypotheses if h.statement.startswith("Income tax"))
    assert tax.verdict == "supported" and "tax benefit" in tax.one_off
    assert ans["confidence"]["independent_drivers"] == 1
    assert ans["confidence"]["score"] <= SINGLE_DRIVER_CAP and ans["confidence"]["label"] != "High"
    assert any(c["claim"].startswith("One-off item") for c in ans["counter_evidence"])
    assert "cover ~" not in ans["confidence"]["rationale"]  # coverage is described honestly


# -- 5. scope ------------------------------------------------------------------------------------

def test_unsupported_country_gets_plain_scope_message(tmp_path, tickers):
    a = mk(tmp_path, lambda spec: [])
    t = a.ask("Why did German inflation spike in 2022?")
    s = t.answer["summary"]
    assert t.answer.get("unsupported_scope") and "German inflation" in s and "UK inflation" in s and "US unemployment" in s
    assert not t.queries and "country" not in (t.answer.get("clarification") or {}).get("missing", [])


def test_unsupported_topic_has_no_raw_intent_ids(tmp_path, tickers):
    a = mk(tmp_path, lambda spec: [])
    for q in ("Why did UK house prices fall in 2023?", "Why did inflation rise in 2022?"):
        t = a.ask(q)
        assert "explain." not in t.answer["summary"] and "memory.explain_belief" not in t.answer["summary"], t.answer["summary"]
        assert not t.queries


# -- 6. number tokens + acronym-safe lowercasing -------------------------------------------------

class _F:
    def __init__(self, fid, statement):
        self.id, self.statement = fid, statement


class _T:
    def __init__(self, statements, summary=""):
        self.findings = [_F(f"F{i + 1}", s) for i, s in enumerate(statements)]
        self.answer = {"summary": summary}


def test_number_check_uses_tokens_not_substrings():
    t = _T(["UK CPI annual rate: 11.1% in 2022-10", "Revenue was $1,234.5B"])
    assert not validate_narrative("Inflation hit 11 percent that year [F1].", t)[0]  # '11' is inside '11.1' only
    assert not validate_narrative("Inflation was 1.1 points higher than before [F1].", t)[0]
    assert validate_narrative("Inflation peaked at 11.1% in October 2022 [F1].", t)[0]
    assert validate_narrative("Revenue reached $1,234.5B in that period [F2].", t)[0]
    assert number_tokens("F12 Q-7278ba1cb2 D7G7 11.1% $60.92B -6.6 pts") == ["11.1", "60.92", "6.6"]


def test_lower_first_keeps_acronyms():
    assert lower_first("R&D expense rose") == "R&D expense rose"
    assert lower_first("UK CPI rose") == "UK CPI rose"
    assert lower_first("Food prices surged") == "food prices surged"
    assert lower_first("A") == "a" and lower_first("") == ""


# -- 7. memory seeding (public demo chip) -----------------------------------------------------------

def test_seed_memory_from_saved_intel_run_answers_memory_chip(tmp_path, tickers):
    import json
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "examples" / "outputs" / "why-did-intel-s-gross-margin-fall-in-2024.json"
    trace = json.loads(p.read_text())
    mem = HMEM(path=tmp_path / "m.json")
    n = seed_memory_from_trace(mem, trace, "intel-example")
    assert n > 0 and seed_memory_from_trace(mem, trace, "intel-example") == 0  # idempotent
    a = Analyst(harness=DuckDBBackend(row_loader=lambda s: []), memory=mem, llm=None, background=False)
    t = a.ask("What do you already know about Intel's margins?")
    assert t.scout["intent"] == "memory.explain_belief" and t.memory_recall
    assert any("Intel" in m["content"] for m in t.memory_recall)
