"""Cross-source findings: ONE federated SQL statement that JOINs two source families.

Every other query in a run reads a single registered table. This module adds one
query per investigation that joins two *different* upstream families inside the
harness, so on Zetaris Lightning the join itself runs federated across a REST
source and a file/REST source:

* company questions:  SEC EDGAR concept view  JOIN  FRED price-index view
  (non-equi join: every monthly index value that falls inside each fiscal-year
  window), giving nominal vs price-adjusted growth for the target year.
* UK macro questions: ONS series view  JOIN  FRED driver view on a computed month
  index with a lag table, giving the lead (months) at which the external driver
  co-moves most with the target.
* US inflation:       FRED CPI view  JOIN  ONS UK CPI view on month (was it global?).

Portability: plain ANSI SELECT / JOIN / GROUP BY / CASE / CAST / SUBSTR / ROUND /
AVG / CORR / ROW_NUMBER() OVER, subqueries and UNION ALL. All of these exist in
DuckDB and in Spark SQL (Zetaris Lightning). No CTEs, no VALUES lists, no date
functions: periods are ISO strings in both backends, so month arithmetic is done
on SUBSTR(...) casts.

The finding is labelled kind="cross-source" and points at the query (with its SQL)
like every other finding. A failure here never breaks the investigation: it is
recorded as a step and the run carries on.
"""

from __future__ import annotations

from typing import Any

from because.agent.common import fiscal_label, fmt_pct, fmt_usd
from because.harness.base import SourceSpec

# --- price index per company (FRED, monthly) --------------------------------
SEMIS = {"INTC", "NVDA", "AMD", "QCOM", "TXN", "MU", "AVGO", "ADI", "MCHP", "NXPI", "ON", "MRVL", "TSM", "ASML", "AMAT", "LRCX", "KLAC"}
PPI_SEMIS = ("PCU33443344", "US PPI: semiconductor and related device manufacturing (FRED PCU33443344)", "the US producer price index for semiconductors")
CPI_US = ("CPIAUCSL", "US CPI-U all items (FRED CPIAUCSL)", "US consumer prices (CPI-U)")
UK_CPI = ("D7G7", "UK CPI annual rate (ONS D7G7)", "UK CPI inflation")

LAGS = (0, 3, 6, 9, 12)


def _midx(col: str) -> str:
    """Month index of an ISO period string column: YYYY*12 + MM (portable)."""
    return f"(CAST(SUBSTR({col}, 1, 4) AS INT) * 12 + CAST(SUBSTR({col}, 6, 2) AS INT))"


def company_sql(since: str) -> str:
    """EDGAR annual facts JOIN FRED monthly index on the fiscal-year window. Placeholders {e}, {m}."""
    span = f"{_midx('period_end')} - {_midx('period_start')}"
    return (
        "SELECT e.period_start, e.period_end, e.value_usd, "
        "ROUND(AVG(m.value), 3) AS index_avg, COUNT(m.value) AS index_months "
        "FROM (SELECT period_start, period_end, value_usd, "
        "ROW_NUMBER() OVER (PARTITION BY period_end ORDER BY filed_date DESC) AS rn "
        "FROM {e} WHERE form_type = '10-K' AND fiscal_period = 'FY' AND period_start IS NOT NULL "
        f"AND {span} BETWEEN 11 AND 13) e "
        "JOIN {m} m ON m.period >= e.period_start AND m.period <= e.period_end "
        f"WHERE e.rn = 1 AND e.period_end >= '{since}' "
        "GROUP BY e.period_start, e.period_end, e.value_usd "
        "ORDER BY e.period_end"
    )


def _series_expr(alias: str, transform: str) -> str:
    if transform == "yoy":
        return f"({alias}.value / {alias}0.value - 1) * 100"
    return f"{alias}.value"


def _yoy_join(alias: str, placeholder: str, transform: str) -> str:
    if transform != "yoy":
        return ""
    return f" JOIN {{{placeholder}}} {alias}0 ON {_midx(alias + '0.period')} = {_midx(alias + '.period')} - 12"


def lead_sql(x_tr: str, y_tr: str, start: str, end: str) -> str:
    """Target {x} JOIN driver {y} (driver leads by lag_m months) -> CORR per lag. Placeholders {x}, {y}."""
    lags = " UNION ALL ".join(f"SELECT {k} AS lag_m" for k in LAGS)
    return (
        f"SELECT l.lag_m, ROUND(CORR({_series_expr('x', x_tr)}, {_series_expr('y', y_tr)}), 3) AS r, COUNT(*) AS n "
        "FROM {x} x"
        + _yoy_join("x", "x", x_tr)
        + f" CROSS JOIN ({lags}) l"
        + f" JOIN {{y}} y ON {_midx('y.period')} + l.lag_m = {_midx('x.period')}"
        + _yoy_join("y", "y", y_tr)
        + f" WHERE SUBSTR(x.period, 1, 7) >= '{start}' AND SUBSTR(x.period, 1, 7) <= '{end}'"
        " GROUP BY l.lag_m ORDER BY l.lag_m"
    )


# --- entry point -------------------------------------------------------------

def cross_source(ctx: Any, playbook: Any) -> Any:
    """Add one cross-source finding to ctx.trace (or a step saying why not). Never raises."""
    t = ctx.trace
    try:
        if hasattr(playbook, "co"):
            f = _company(ctx, playbook)
        elif getattr(playbook, "cfg", None):
            f = _macro(ctx, playbook)
        else:
            f = None
    except Exception as e:  # cross-source is additive evidence: never fail the investigation
        if type(e).__name__ == "ContractRefused":
            raise
        t.step("cross-source", f"Cross-source join skipped: {type(e).__name__}: {str(e)[:160]}")
        return None
    if f is not None:
        t.answer.setdefault("cross_source", []).append({"finding": f.id, "statement": f.statement, "query_id": f.query_id})
    return f


def _q(ctx: Any, sql: str, tables: dict[str, SourceSpec], purpose: str):
    res = ctx.query(sql, tables, purpose)
    if ctx.trace.queries and ctx.trace.queries[-1].get("query_id") == res.query_id:
        ctx.trace.queries[-1]["federated"] = sorted({s.kind for s in tables.values()})
    return res


def _company(ctx: Any, pb: Any):
    t = ctx.trace
    Y, P = pb.year, pb.year - 1
    rev = pb.series.get("revenue", {})
    tag = (rev.get(Y) or rev.get(P) or {}).get("concept")
    if not tag:
        t.step("cross-source", "Cross-source join skipped: no annual revenue concept resolved")
        return None
    key, label, noun = PPI_SEMIS if (pb.co.get("ticker") or "").upper() in SEMIS else CPI_US
    e_spec = SourceSpec("edgar_concept", f"{pb.co['cik']}:{tag}", f"{pb.co['name']} {tag}")
    m_spec = SourceSpec("fred_series", key, label)
    res = _q(ctx, company_sql(f"{Y - 4}-01-01"), {"e": e_spec, "m": m_spec},
             f"cross-source: EDGAR {tag} JOIN FRED {key} on each fiscal-year window")
    if res.error or not res.rows:
        t.step("cross-source", f"Cross-source join returned no rows{': ' + res.error[:160] if res.error else ''}")
        return None
    by_fy: dict[int, tuple[int, dict[str, Any]]] = {}
    for i, r in enumerate(res.records()):
        if r["index_avg"] is None or r["value_usd"] is None or int(r["index_months"] or 0) < 10:
            continue
        by_fy[fiscal_label(str(r["period_end"]))] = (i, r)
    if Y not in by_fy or P not in by_fy:
        t.step("cross-source", f"Cross-source join has no FY{P}/FY{Y} pair with a full index window")
        return None
    (iy, ry), (ip, rp) = by_fy[Y], by_fy[P]
    g = (ry["value_usd"] / rp["value_usd"] - 1) * 100
    p = (ry["index_avg"] / rp["index_avg"] - 1) * 100
    real = ((1 + g / 100) / (1 + p / 100) - 1) * 100
    if abs(p) < 1:
        read = "sector prices were roughly flat, so the move was volume or mix, not industry pricing"
    elif (g >= 0) == (p >= 0) and abs(real) < abs(g) / 2:
        read = "most of the nominal move tracks industry prices"
    elif (g >= 0) != (p >= 0):
        read = "revenue moved against industry prices, so the move was volume, mix or share, not industry pricing"
    else:
        read = "the move was larger than industry price changes alone would give"
    stmt = (f"Cross-source (SEC EDGAR x FRED, one federated SQL JOIN): {pb.short} revenue FY{P} -> FY{Y} "
            f"{fmt_usd(rp['value_usd'])} -> {fmt_usd(ry['value_usd'])} ({fmt_pct(g)}); {noun} averaged "
            f"{rp['index_avg']:.1f} over FY{P} and {ry['index_avg']:.1f} over FY{Y} ({fmt_pct(p)}), "
            f"so price-adjusted revenue changed {fmt_pct(real)}: {read}")
    f = t.add_finding(statement=stmt, kind="cross-source", query_id=res.query_id, rows=[ip, iy],
                      source_url=res.sources[0]["url"], value=round(real, 2), unit="%",
                      fact_key=f"{pb.co.get('ticker')}:real_revenue_growth:{Y}:{key}")
    t.step("cross-source", f"Federated JOIN EDGAR x FRED ({key}) on fiscal-year windows: {len(res.rows)} fiscal years")
    return f


def _pick_driver(pb: Any, target_kind: str) -> tuple[str, str, str, str] | None:
    """First external monthly driver from another source family than the target."""
    for d in pb.cfg.get("drivers", []):
        if d["kind"] != target_kind and d["group"] == "external" and d["key"] not in ("DEXUSUK",):
            return d["kind"], d["key"], d["label"], d["transform"]
    return None


def _macro(ctx: Any, pb: Any):
    t = ctx.trace
    Y = pb.year
    kind, key, label, tr = pb.cfg["target"]
    other = _pick_driver(pb, kind)
    if other is None and kind == "fred_series" and pb.indicator == "inflation":
        other = ("ons_series", UK_CPI[0], UK_CPI[1], "level")  # US inflation vs UK: global or domestic?
    if other is None:
        t.step("cross-source", f"Cross-source join skipped: no second source family configured for {pb.indicator}/{pb.country}")
        return None
    okind, okey, olabel, otr = other
    x, y = SourceSpec(kind, key, label), SourceSpec(okind, okey, olabel)
    start, end = f"{Y - 7}-01", f"{Y}-12"
    fam = {"ons_series": "ONS", "fred_series": "FRED", "edgar_concept": "SEC EDGAR"}
    res = _q(ctx, lead_sql(tr, otr, start, end), {"x": x, "y": y},
             f"cross-source: {fam[kind]} {key} JOIN {fam[okind]} {okey} on month, driver leading 0-12 months")
    rows = [(i, r) for i, r in enumerate(res.records()) if r.get("r") is not None and int(r.get("n") or 0) >= 24]
    if res.error or not rows:
        t.step("cross-source", f"Cross-source join returned no usable rows{': ' + res.error[:160] if res.error else ''}")
        return None
    ib, best = max(rows, key=lambda ir: abs(float(ir[1]["r"])))
    i0 = next((i for i, r in rows if int(r["lag_m"]) == 0), None)
    r0 = next((r for _, r in rows if int(r["lag_m"]) == 0), None)
    bl, br, bn = int(best["lag_m"]), float(best["r"]), int(best["n"])
    strength = "strong" if abs(br) >= 0.7 else "moderate" if abs(br) >= 0.4 else "weak"
    def _name(lbl: str, transform: str) -> str:
        base = lbl.split(" (")[0].replace(", USD", " (USD)").replace(", YoY", "")
        return f"the 12-month change in {base}" if transform == "yoy" else base

    tname, oname = _name(label, tr), _name(olabel, otr)
    when = "in the same month" if bl == 0 else f"{bl} months earlier"
    stmt = (f"Cross-source ({fam[kind]} x {fam[okind]}, one federated SQL JOIN on month): over {start} to {end}, "
            f"{tname[0].upper() + tname[1:]} tracks {oname} {when} most closely "
            f"(r = {br:.2f}, n = {bn} months, {strength})"
            + (f"; same-month r = {float(r0['r']):.2f}" if r0 is not None and bl != 0 else ""))
    f = t.add_finding(statement=stmt, kind="cross-source", query_id=res.query_id,
                      rows=sorted({ib} | ({i0} if i0 is not None else set())), source_url=res.sources[1]["url"],
                      value=round(br, 3), unit="r", fact_key=f"{pb.country}:{pb.indicator}:{Y}:lead:{okey}")
    t.step("cross-source", f"Federated JOIN {fam[kind]} x {fam[okind]} ({key} x {okey}): best lead {bl} months, r={br:.2f}")
    return f
