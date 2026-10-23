"""Playbook: why did a macro indicator move? (ONS + FRED via the harness)

Method
------
1. Pull the target series (e.g. ONS D7G7, UK CPI annual rate) for Y-4..Y+1.
   The move is measured from the START of year Y (December of Y-1) to the
   in-year extreme that is furthest from that start. The direction of the
   move is decided from the data, never from the question. The Y-2 average is
   reported as context only.
2. Each candidate driver is a neutral, data-level statement ("Wholesale gas
   prices rose in the run-up to the 2022 peak"). It is tested on the SAME
   start->extreme interval (optionally shifted by its best lead of 0-9 months):
     a. did the driver change in the direction the statement needs (sign check
        on the change, not on a window high)?
     b. was the change material for that kind of series?
     c. does it co-move with the target (Pearson r >= 0.5 in the expected sign)?
3. supported = right sign + material + correlated; contradicted = material move
   the wrong way; otherwise inconclusive. Narrative context (a mechanism) is a
   separate field shown only next to supported statements, labelled as context.
4. Drivers that measure the same cause (e.g. gas, oil and the household-energy
   CPI component) share a cause group; evidence weight is de-duplicated by group.
"""

from __future__ import annotations

from typing import Any

from because.agent.common import month_key, pearson, shift_month
from because.harness.base import SourceSpec

SQL_SERIES = "SELECT period, value FROM {t} WHERE period >= '{start}' AND period <= '{end}' ORDER BY period"


class Driver(dict):
    """A driver spec. Also indexable like the old tuple (group, kind, key, label, transform, sign, statement)
    so existing callers such as `because zetaris ddl` keep working."""

    _ORDER = ("group", "kind", "key", "label", "transform", "sign", "noun")

    def __getitem__(self, k: Any) -> Any:
        if isinstance(k, int):
            return dict.__getitem__(self, self._ORDER[k])
        return dict.__getitem__(self, k)


def D(group: str, kind: str, key: str, label: str, transform: str, sign: int, noun: str, cause: str,
      context: Any = "", min_move: float | None = None, condition: str | None = None) -> dict[str, Any]:
    """One candidate driver.

    group: component | external | policy | labour | sector | activity (how it is tested)
    transform: level (already a rate; change in pts), yoy (12m % change; change in pts of that rate),
               level_pct (an index or count; change measured in % of its level)
    sign: +1 if the driver rising pushes the target up, -1 if it pushes it down
    noun: what the tested statement talks about (neutral, data-level)
    cause: cause group for de-duplicating correlated drivers
    context: a note shown only when the statement is supported (never tested): a mechanism string, or a list of
             (first_year, last_year, text) so a historical note only appears for the years it is about
    min_move: materiality threshold on the change over the interval (series units)
    condition: for inflation spikes, 'low_rate' / 'tight_labour' test a pre-peak level instead of a change
    """
    return Driver(group=group, kind=kind, key=key, label=label, transform=transform, sign=sign,
                  noun=noun, cause=cause, context=context, min_move=min_move, condition=condition)


CONFIG: dict[tuple[str, str], dict[str, Any]] = {
    ("inflation", "GB"): {
        "target": ("ons_series", "D7G7", "UK CPI annual rate (ONS D7G7)", "level"),
        "drivers": [
            D("component", "ons_series", "D7GB", "CPI housing, water & fuels annual rate (ONS D7GB)", "level", +1,
              "Household energy inflation (CPI housing, water & fuels)", "energy",
              "Household gas and electricity bills follow wholesale prices with a lag (in the UK via the Ofgem price cap)."),
            D("component", "ons_series", "D7G8", "CPI food & non-alcoholic drinks annual rate (ONS D7G8)", "level", +1,
              "Food inflation (CPI food & non-alcoholic drinks)", "food",
              "Retail food prices pass through farm, fertiliser and energy costs over several months."),
            D("component", "ons_series", "D7GE", "CPI transport annual rate (ONS D7GE)", "level", +1,
              "Transport inflation (CPI transport, incl. motor fuel)", "energy",
              "Motor fuel prices track crude oil quickly."),
            D("component", "ons_series", "D7NN", "CPI services annual rate (ONS D7NN)", "level", +1,
              "Services inflation (CPI services)", "domestic",
              "Services prices are labour-intensive, so they tend to reflect wage growth."),
            D("external", "fred_series", "PNGASEUUSDM", "European natural gas price, USD (FRED PNGASEUUSDM)", "yoy", +1,
              "The 12-month change in wholesale European gas prices", "energy",
              [(2021, 2023, "Context: the 2021-22 European gas crisis and Russia's invasion of Ukraine (Feb 2022) drove wholesale gas "
                            "to record highs; prices fell back through 2023."),
               (None, None, "Wholesale gas feeds household energy bills and producer costs with a lag.")]),
            D("external", "fred_series", "POILBREUSDM", "Brent crude oil price, USD (FRED POILBREUSDM)", "yoy", +1,
              "The 12-month change in Brent crude oil prices", "energy",
              "Oil feeds into motor fuel and transport costs."),
            D("external", "fred_series", "PFOODINDEXM", "Global food price index (IMF via FRED PFOODINDEXM)", "yoy", +1,
              "The 12-month change in global food commodity prices", "food",
              "World commodity prices feed into UK retail food prices with a lag."),
            D("external", "fred_series", "DEXUSUK", "US dollars per pound (FRED DEXUSUK)", "yoy", -1,
              "The 12-month change in the pound's value against the dollar", "currency",
              "A weaker pound raises the sterling cost of dollar-priced imports such as energy and food."),
            D("policy", "fred_series", "IRSTCI01GBM156N", "UK short-term interest rate (FRED IRSTCI01GBM156N)", "level", -1,
              "UK short-term interest rates", "policy",
              "Low rates are an enabling condition for demand, not a trigger.", min_move=0.25, condition="low_rate"),
            D("labour", "fred_series", "LRHUTTTTGBM156S", "UK unemployment rate (FRED LRHUTTTTGBM156S)", "level", -1,
              "UK unemployment", "labour",
              "A tight labour market tends to push wages, and then services prices, up.", min_move=0.3, condition="tight_labour"),
        ],
        "wiki": "2021–2023 inflation surge United Kingdom cost of living crisis",
    },
    ("inflation", "US"): {
        "target": ("fred_series", "CPIAUCSL", "US CPI-U all items, YoY (FRED CPIAUCSL)", "yoy"),
        "drivers": [
            D("component", "fred_series", "CPIENGSL", "US CPI energy, YoY (FRED CPIENGSL)", "yoy", +1,
              "Energy inflation (US CPI energy)", "energy", "Gasoline and utility prices track crude oil and natural gas."),
            D("component", "fred_series", "CPIUFDSL", "US CPI food, YoY (FRED CPIUFDSL)", "yoy", +1,
              "Food inflation (US CPI food)", "food", "Retail food prices pass through commodity and transport costs."),
            D("component", "fred_series", "CUSR0000SETA02", "US CPI used cars, YoY (FRED CUSR0000SETA02)", "yoy", +1,
              "Used-car price inflation (US CPI used cars)", "goods",
              [(2021, 2023, "Context: used-car prices are a common gauge of the 2021-22 chip and supply-chain shortages."),
               (None, None, "Used-car prices respond to new-vehicle supply and demand.")]),
            D("component", "fred_series", "CUSR0000SAH1", "US CPI shelter, YoY (FRED CUSR0000SAH1)", "yoy", +1,
              "Shelter inflation (US CPI shelter)", "domestic", "Shelter reflects market rents with a lag of about a year."),
            D("external", "fred_series", "POILBREUSDM", "Brent crude oil price, USD (FRED POILBREUSDM)", "yoy", +1,
              "The 12-month change in Brent crude oil prices", "energy", "Oil feeds into gasoline and transport costs."),
            D("policy", "fred_series", "M2SL", "US M2 money supply, YoY (FRED M2SL)", "yoy", +1,
              "M2 money supply growth (12-month %)", "policy",
              [(2020, 2023, "Context: pandemic-era fiscal and monetary stimulus expanded M2 rapidly in 2020-21."),
               (None, None, "Faster money growth can support demand with a lag.")], min_move=2.0),
            D("policy", "fred_series", "FEDFUNDS", "Federal funds rate (FRED FEDFUNDS)", "level", -1,
              "The federal funds rate", "policy",
              "Low rates are an enabling condition for demand, not a trigger.", min_move=0.25, condition="low_rate"),
            D("labour", "fred_series", "UNRATE", "US unemployment rate (FRED UNRATE)", "level", -1,
              "US unemployment", "labour",
              "A tight labour market tends to push wages, and then services prices, up.", min_move=0.3, condition="tight_labour"),
        ],
        "wiki": "2021–2023 inflation surge United States",
    },
    ("unemployment", "US"): {
        "target": ("fred_series", "UNRATE", "US unemployment rate (FRED UNRATE)", "level"),
        "drivers": [
            D("labour", "fred_series", "ICSA", "Initial jobless claims (FRED ICSA)", "level", +1,
              "Initial jobless claims", "layoffs", "Claims count new layoffs filing for benefits."),
            D("labour", "fred_series", "PAYEMS", "Total nonfarm payrolls (FRED PAYEMS)", "level_pct", -1,
              "Total payroll employment", "layoffs", "", min_move=1.0),
            D("sector", "fred_series", "USLAH", "Leisure & hospitality employment (FRED USLAH)", "level_pct", -1,
              "Leisure & hospitality employment", "sector:leisure",
              [(2020, 2021, "Context: in 2020 COVID-19 shutdowns closed restaurants, hotels and venues."),
               (None, None, "Leisure & hospitality depends on in-person spending, so it is among the first sectors hit by demand shocks.")], min_move=2.0),
            D("sector", "fred_series", "USCONS", "Construction employment (FRED USCONS)", "level_pct", -1,
              "Construction employment", "sector:construction",
              [(2007, 2010, "Context: in 2007-09 the US housing bust cut construction jobs."),
               (None, None, "Construction is cyclical and sensitive to housing demand and credit.")], min_move=2.0),
            D("sector", "fred_series", "MANEMP", "Manufacturing employment (FRED MANEMP)", "level_pct", -1,
              "Manufacturing employment", "sector:manufacturing", "", min_move=2.0),
            D("activity", "fred_series", "INDPRO", "Industrial production (FRED INDPRO)", "level_pct", -1,
              "Industrial production", "activity", "", min_move=2.0),
            D("policy", "fred_series", "FEDFUNDS", "Federal funds rate (FRED FEDFUNDS)", "level", +1,
              "The federal funds rate", "policy", "Higher rates cool demand with a lag.", min_move=0.25),
        ],
        "wiki": "COVID-19 recession United States unemployment",
        "wiki_by_year": {(2007, 2010): "Great Recession in the United States"},
    },
    ("unemployment", "GB"): {
        "target": ("fred_series", "LRHUTTTTGBM156S", "UK unemployment rate (FRED LRHUTTTTGBM156S)", "level"),
        "drivers": [
            D("activity", "fred_series", "GBRPROINDMISMEI", "UK industrial production index (FRED GBRPROINDMISMEI)", "level_pct", -1,
              "UK industrial production", "activity",
              [(2008, 2010, "Context: the 2008-09 UK recession followed the global financial crisis."),
               (None, None, "Firms shed jobs when output falls.")], min_move=2.0),
            D("policy", "fred_series", "IRSTCI01GBM156N", "UK short-term interest rate (FRED IRSTCI01GBM156N)", "level", +1,
              "UK short-term interest rates", "policy", "Higher rates cool demand with a lag.", min_move=0.25),
            D("external", "fred_series", "POILBREUSDM", "Brent crude oil price, USD (FRED POILBREUSDM)", "yoy", +1,
              "The 12-month change in Brent crude oil prices", "external", "An oil price rise squeezes firms' costs.", min_move=10.0),
        ],
        "wiki": "United Kingdom unemployment recession",
        "wiki_by_year": {(2008, 2010): "Great Recession in the United Kingdom"},
    },
}
SUPPORTED = sorted(f"{i} ({c})" for i, c in CONFIG)
COUNTRY_NAMES = {"GB": "UK", "US": "US"}


def supported_scope() -> list[str]:
    """Plain-words list of the macro questions with a playbook, e.g. 'UK inflation'."""
    return sorted(f"{COUNTRY_NAMES.get(c, c)} {i}" for i, c in CONFIG)


def pick_context(context: Any, year: int) -> str:
    """context is a plain mechanism string, or a list of (first_year, last_year, text) with an optional
    (None, None, text) fallback; a year-specific note is only shown for the years it is about."""
    if isinstance(context, str):
        return context
    fallback = ""
    for lo, hi, text in context:
        if lo is None:
            fallback = text
        elif lo <= year <= hi:
            return text
    return fallback


def default_min_move(d: dict[str, Any], start_value: float | None) -> float:
    if d["min_move"] is not None:
        return d["min_move"]
    if d["transform"] == "yoy":
        return 5.0 if d["group"] == "external" else 1.0
    if d["transform"] == "level_pct":
        return 1.0
    if start_value is not None and abs(start_value) > 100:  # counts (e.g. claims): 20% relative change
        return abs(start_value) * 0.2
    return 1.0


def value_at(ser: dict[str, float], m: str) -> tuple[str, float] | None:
    """Latest observation at or before month m."""
    avail = [k for k in ser if k <= m]
    if not avail:
        return None
    k = max(avail)
    return k, ser[k]


def locate_move(tgt: dict[str, float], year: int) -> dict[str, Any] | None:
    """Start of year (Dec Y-1) -> in-year extreme furthest from it. Direction comes from the data."""
    in_year = {m: v for m, v in tgt.items() if m.startswith(str(year))}
    start = value_at(tgt, f"{year - 1}-12")
    if not in_year or not start:
        return None
    start_m, start_v = start
    hi_m = max(in_year, key=in_year.get)
    lo_m = min(in_year, key=in_year.get)
    up, down = in_year[hi_m] - start_v, start_v - in_year[lo_m]
    ext_m = hi_m if up >= down else lo_m
    ext_v = in_year[ext_m]
    delta = ext_v - start_v
    return {"start_m": start_m, "start": start_v, "ext_m": ext_m, "ext": ext_v, "delta": delta,
            "dir": "up" if delta > 0 else "down"}


class MacroPlaybook:
    def __init__(self, ctx: Any, indicator: str, country: str, year: int, direction: str | None) -> None:
        self.ctx, self.t = ctx, ctx.trace
        self.indicator, self.country, self.year, self.asked_dir = indicator, country, year, direction
        self.cfg = CONFIG.get((indicator, country))
        self.coverage = 0.0

    def series(self, kind: str, key: str, label: str, transform: str) -> tuple[dict[str, float], dict[str, int], Any]:
        start, end = f"{self.year - 4}-01", f"{self.year + 1}-12-31"
        spec = SourceSpec(kind, key, label)
        res = self.ctx.query(SQL_SERIES.replace("{start}", start).replace("{end}", end), {"t": spec}, label)
        monthly: dict[str, list[float]] = {}
        rowmap: dict[str, int] = {}
        for i, r in enumerate(res.records()):
            if r["value"] is None:
                continue
            m = month_key(str(r["period"]))
            monthly.setdefault(m, []).append(float(r["value"]))
            rowmap.setdefault(m, i)
        lvl = {m: sum(v) / len(v) for m, v in monthly.items()}  # daily/weekly -> monthly mean
        if transform == "yoy":
            out = {m: (v / lvl[shift_month(m, -12)] - 1) * 100 for m, v in lvl.items() if lvl.get(shift_month(m, -12))}
        else:
            out = lvl
        return out, rowmap, res

    def run(self) -> dict[str, Any]:
        if not self.cfg:
            return {"error": f"No playbook for {self.indicator} in {self.country} yet. Supported: {', '.join(supported_scope())}",
                    "unsupported": True}
        Y = self.year
        kind, key, label, tr = self.cfg["target"]
        self.t.step("plan", f"Pull {label} for {Y - 4}-{Y + 1}; measure the move from Dec {Y - 1} to the {Y} extreme; "
                            f"test {len(self.cfg['drivers'])} candidate drivers over the same interval")
        tgt, trows, tres = self.series(kind, key, label, tr)
        mv = locate_move(tgt, Y)
        if not mv:
            return {"error": f"No {label} data for {Y} (got {min(tgt) if tgt else '-'}..{max(tgt) if tgt else '-'})."}
        start_m, start_v, peak_m, peak, delta, actual_dir = mv["start_m"], mv["start"], mv["ext_m"], mv["ext"], mv["delta"], mv["dir"]
        base = [v for m, v in tgt.items() if m.startswith(str(Y - 2))]
        baseline = sum(base) / len(base) if base else None
        unit = "%" if tr == "yoy" or self.indicator in ("inflation", "unemployment") else ""
        what = "peak" if actual_dir == "up" else "low"
        fp = self.t.add_finding(
            statement=f"{label}: {start_v:.1f}{unit} in {start_m} (start of {Y}) -> {peak:.1f}{unit} in {peak_m} (the {Y} {what}), "
                      f"{delta:+.1f} pts" + (f"; for context, the {Y - 2} average was {baseline:.1f}{unit}" if baseline is not None else ""),
            kind="data" if tr == "level" else "derived", query_id=tres.query_id,
            rows=sorted({trows.get(m, 0) for m in (start_m, peak_m)} | ({trows.get(shift_month(peak_m, -12), 0)} if tr == "yoy" else set())),
            source_url=tres.sources[0]["url"], value=peak, unit=unit, fact_key=f"{self.country}:{self.indicator}:{Y}:{what}")
        premise_ok = self.asked_dir is None or self.asked_dir == actual_dir
        self.t.step("findings", f"Target {start_v:.1f} ({start_m}) -> {peak:.1f} ({peak_m}), {delta:+.1f} pts: {actual_dir}"
                    + ("" if premise_ok else f" - the question assumed '{self.asked_dir}'"))

        months = sorted(m for m in tgt if f"{Y - 3}-01" <= m <= peak_m)
        dirsign = 1 if actual_dir == "up" else -1
        run_up = f"the run-up to the {Y} {what}" if actual_dir == "up" else f"the {Y} decline"
        group_w: dict[str, float] = {}
        group_sup: dict[str, float] = {}
        for d in self.cfg["drivers"]:
            want = d["sign"] * dirsign  # +1: the driver should rise
            cond = d["condition"] if (self.indicator == "inflation" and actual_dir == "up") else None
            if cond == "low_rate":
                stmt = f"{d['noun']} {'were' if d['noun'].endswith('s') else 'was'} low (average below 1%) in the 6-18 months before the {Y} peak"
            elif cond == "tight_labour":
                stmt = f"{d['noun']} was at least 0.3 pts below its {Y - 2} average in the 6-18 months before the {Y} peak"
            else:
                stmt = f"{d['noun']} {'rose' if want > 0 else 'fell'} during {run_up}"
            h = self.t.add_hypothesis(statement=stmt, test=(
                f"{d['label']}: level in the 6-18 months before the peak" if cond else
                f"{d['label']}: sign and size of its change over the target's {start_m} -> {peak_m} interval "
                f"(shifted by its best lead of 0-9 months), and lead correlation r >= 0.5"))
            h.group, h.context = d["cause"], pick_context(d["context"], Y)
            ser, rows, res = self.series(d["kind"], d["key"], d["label"], d["transform"])
            if res.error or not ser:
                h.verdict, h.detail = "inconclusive", f"No data ({res.error or 'empty series'})."
                continue
            avail = sorted(m for m in ser if m <= peak_m)
            if not avail:
                h.verdict, h.detail = "inconclusive", f"No data up to {peak_m}."
                continue
            fn = (lambda x: f"{x:,.0f}") if max(abs(v) for v in ser.values()) >= 1000 else (lambda x: f"{x:.1f}")
            u = "%" if d["transform"] == "yoy" or "rate" in d["label"].lower() else ""

            if cond:
                pre = [ser[m] for m in avail if shift_month(peak_m, -18) <= m <= shift_month(peak_m, -6)]
                b = [v for m, v in ser.items() if m.startswith(str(Y - 2))]
                if not pre or not b:
                    h.verdict, h.detail = "inconclusive", "Not enough pre-peak data."
                    continue
                lvl, bl, at = sum(pre) / len(pre), sum(b) / len(b), ser[avail[-1]]
                f = self.t.add_finding(statement=f"{d['label']}: averaged {lvl:.2f} in the 6-18 months before the {peak_m} peak "
                                                 f"(vs {bl:.2f} in {Y - 2}); {at:.2f} at {avail[-1]}",
                                       kind="data", query_id=res.query_id, rows=[rows.get(avail[-1], 0)], source_url=res.sources[0]["url"], value=at)
                ok = (lvl < 1.0) if cond == "low_rate" else (lvl <= bl - 0.3)
                h.weight = 0.3
                group_w[d["cause"]] = max(group_w.get(d["cause"], 0.0), 0.3)
                if ok:
                    h.verdict, h.evidence, h.detail = "supported", [f.id], "Pre-peak conditions match (an enabling condition, not a trigger)."
                    group_sup[d["cause"]] = max(group_sup.get(d["cause"], 0.0), 0.3)
                else:
                    h.verdict, h.counter, h.detail = "contradicted", [f.id], "Pre-peak conditions do not match."
                continue

            # best lead (0-9 months) by correlation in the expected sign
            best_r, best_lag = None, 0
            for lag in range(0, 10):
                xs, ys = [], []
                for m in months:
                    lm = shift_month(m, -lag)
                    if lm in ser:
                        xs.append(ser[lm])
                        ys.append(tgt[m])
                r = pearson(xs, ys)
                if r is not None and (best_r is None or d["sign"] * r > d["sign"] * best_r + 0.02):
                    best_r, best_lag = r, lag
            a = value_at(ser, shift_month(start_m, -best_lag))
            z = value_at(ser, shift_month(peak_m, -best_lag))
            if not a or not z or a[0] == z[0]:
                h.verdict, h.detail = "inconclusive", "Not enough observations over the interval."
                continue
            move = (z[1] / a[1] - 1) * 100 if d["transform"] == "level_pct" and a[1] else z[1] - a[1]
            thr = default_min_move(d, a[1])
            what_s = {"yoy": "12-month % change", "level_pct": "level"}.get(d["transform"], "level")
            mu = "%" if d["transform"] == "level_pct" else (" pts" if u == "%" else "")
            lead = f" (its best lead, {best_lag} month{'s' if best_lag > 1 else ''} earlier)" if best_lag else ""
            fm = (lambda x: f"{x:+,.0f}") if abs(move) >= 1000 else (lambda x: f"{x:+,.1f}")
            f = self.t.add_finding(
                statement=f"{d['label']} ({what_s}): {fn(a[1])}{u} in {a[0]} -> {fn(z[1])}{u} in {z[0]}{lead}, a change of {fm(move)}{mu}"
                          + (f"; correlation with the target r={best_r:+.2f} at a {best_lag}-month lead" if best_r is not None else ""),
                kind="derived" if d["transform"] == "yoy" else "data", query_id=res.query_id,
                rows=sorted({rows.get(m, 0) for m in (a[0], z[0], shift_month(z[0], -12)) if m in rows}),
                source_url=res.sources[0]["url"], value=z[1])
            material = abs(move) >= thr
            corr_ok = best_r is not None and best_r * d["sign"] >= 0.5
            # size of the move: components (same units as the target) relative to the target's move, others to their threshold
            scale = min(abs(move) / max(abs(delta), 0.1), 1.0) if d["group"] == "component" else min(abs(move) / thr, 3.0) / 3.0
            h.weight = round(scale * (abs(best_r) if best_r is not None else 0.3), 3)
            group_w[d["cause"]] = max(group_w.get(d["cause"], 0.0), h.weight)
            if move * want > 0 and material and corr_ok:
                h.verdict, h.evidence = "supported", [f.id]
                h.detail = (f"Changed {fm(move)}{mu} over the same interval{lead}, the direction needed, and co-moved with the target "
                            f"(r={best_r:+.2f}). Co-movement, not proof of cause.")
                group_sup[d["cause"]] = max(group_sup.get(d["cause"], 0.0), h.weight)
            elif move * want < 0 and material:
                h.verdict, h.counter = "contradicted", [f.id]
                h.detail = f"Changed {fm(move)}{mu} over the same interval{lead}: the opposite direction."
            else:
                h.verdict, h.counter = "inconclusive", [f.id]
                if move * want > 0 and material:
                    h.detail = f"Changed {fm(move)}{mu} the needed way, but without a consistent co-movement (r={best_r:+.2f})." if best_r is not None \
                        else f"Changed {fm(move)}{mu} the needed way, but too few points to test co-movement."
                else:
                    h.detail = f"Change of {fm(move)}{mu} is below the materiality threshold ({fn(thr) if abs(thr) >= 1000 else f'{thr:g}'}{mu})."
        total_w = sum(group_w.values())
        self.coverage = sum(group_sup.values()) / total_w if total_w else 0.0
        n_sup = sum(1 for h in self.t.hypotheses if h.verdict == "supported")
        self.t.step("hypotheses", f"Tested {len(self.cfg['drivers'])} drivers; {n_sup} supported across {len(group_sup)} cause groups")
        self.context(Y)
        name = {"inflation": "inflation", "unemployment": "unemployment"}[self.indicator]
        cname = COUNTRY_NAMES.get(self.country, self.country)
        bl_note = f"; {Y - 2} average {baseline:.1f}{unit}" if baseline is not None else ""
        return {"target_finding": fp.id, "target_fids": [fp.id], "delta": delta, "is_ratio": True, "actual_dir": actual_dir,
                "premise_ok": premise_ok,
                "move": f"{start_v:.1f}{unit} ({start_m}) -> {peak:.1f}{unit} ({peak_m} {what}), {delta:+.1f} pts{bl_note}",
                "subject": f"{cname} {name}", "period": str(Y), "coverage": self.coverage, "coverage_kind": "weight",
                "supported_groups": len(group_sup), "playbook": "macro"}

    KEYWORDS = {
        "energy": ["energy", "gas", "electricity", "fuel", "ofgem", "price cap", "oil"],
        "food": ["food"],
        "currency": ["sterling", "pound", "exchange rate", "currency", "dollar"],
        "goods": ["supply chain", "shortage", "chips", "semiconductor", "used car"],
        "policy": ["stimulus", "money supply", "quantitative", "interest rate", "bank rate"],
        "labour": ["labour", "labor", "wage", "unemployment", "jobs"],
        "layoffs": ["layoff", "lockdown", "covid", "pandemic", "furlough", "claims", "job losses"],
        "activity": ["recession", "output", "gdp", "production"],
    }

    def context(self, Y: int) -> None:
        q = self.cfg["wiki"]
        for (lo, hi), alt in (self.cfg.get("wiki_by_year") or {}).items():
            if lo <= Y <= hi:
                q = alt
        hits = self.ctx.background(q, [str(Y)], extra=[])
        for f in hits:
            low = f.statement.lower()
            for h in self.t.hypotheses:
                fam = getattr(h, "group", "").split(":")[0]
                kws = self.KEYWORDS.get(fam, []) + ([fam] if fam not in self.KEYWORDS else [])
                if h.verdict == "supported" and fam and any(k in low for k in kws):
                    h.evidence.append(f.id)
