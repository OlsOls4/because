"""Playbook: why did a macro indicator move? (ONS + FRED via the harness)

Method
------
1. Pull the target series (e.g. ONS D7G7, UK CPI annual rate) for Y-3..Y+1 and
   find the peak (or trough) in year Y versus the Y-2 baseline.
2. For each candidate driver (components, energy, food, currency, policy,
   labour market) pull its series through the same harness and test two things:
     a. did it move in the direction the hypothesis needs, by enough?
     b. does it lead/co-move with the target (best Pearson r at lag 0-9 months)?
3. supported = right direction + material move + r >= 0.5 in the expected sign;
   contradicted = moved the wrong way; otherwise inconclusive.
"""

from __future__ import annotations

from typing import Any

from because.agent.common import fmt_pct, month_key, pearson, shift_month
from because.harness.base import SourceSpec

SQL_SERIES = "SELECT period, value FROM {t} WHERE period >= '{start}' AND period <= '{end}' ORDER BY period"

# transform: level (already a rate), yoy (12m % change), level_change (pp change)
CONFIG: dict[tuple[str, str], dict[str, Any]] = {
    ("inflation", "GB"): {
        "target": ("ons_series", "D7G7", "UK CPI annual rate (ONS D7G7)", "level"),
        "drivers": [
            ("component", "ons_series", "D7GB", "CPI housing, water & fuels annual rate (ONS D7GB)", "level", +1, "Household energy (housing, water and fuels) prices surged"),
            ("component", "ons_series", "D7G8", "CPI food & non-alcoholic drinks annual rate (ONS D7G8)", "level", +1, "Food prices surged"),
            ("component", "ons_series", "D7GE", "CPI transport annual rate (ONS D7GE)", "level", +1, "Transport costs (motor fuel) surged"),
            ("component", "ons_series", "D7NN", "CPI services annual rate (ONS D7NN)", "level", +1, "Domestic services inflation (wage-driven) took off"),
            ("external", "fred_series", "PNGASEUUSDM", "European natural gas price, USD (FRED PNGASEUUSDM)", "yoy", +1, "Wholesale gas prices spiked (Russia-Ukraine energy shock)"),
            ("external", "fred_series", "POILBREUSDM", "Brent crude oil price, USD (FRED POILBREUSDM)", "yoy", +1, "Oil prices rose"),
            ("external", "fred_series", "PFOODINDEXM", "Global food price index (IMF via FRED PFOODINDEXM)", "yoy", +1, "Global food commodity prices rose"),
            ("external", "fred_series", "DEXUSUK", "US dollars per pound (FRED DEXUSUK)", "yoy", -1, "A weaker pound made imports dearer"),
            ("policy", "fred_series", "IRSTCI01GBM156N", "UK short-term interest rate (FRED IRSTCI01GBM156N)", "level", -1, "Monetary policy was loose (low rates) going into the spike"),
            ("labour", "fred_series", "LRHUTTTTGBM156S", "UK unemployment rate (FRED LRHUTTTTGBM156S)", "level", -1, "A tight labour market (low unemployment) pushed wages up"),
        ],
        "wiki": "2021–2023 inflation surge United Kingdom cost of living crisis",
    },
    ("inflation", "US"): {
        "target": ("fred_series", "CPIAUCSL", "US CPI-U all items, YoY (FRED CPIAUCSL)", "yoy"),
        "drivers": [
            ("component", "fred_series", "CPIENGSL", "US CPI energy, YoY (FRED CPIENGSL)", "yoy", +1, "Energy prices surged"),
            ("component", "fred_series", "CPIUFDSL", "US CPI food, YoY (FRED CPIUFDSL)", "yoy", +1, "Food prices surged"),
            ("component", "fred_series", "CUSR0000SETA02", "US CPI used cars, YoY (FRED CUSR0000SETA02)", "yoy", +1, "Supply-chain shortages pushed up goods prices (used cars)"),
            ("component", "fred_series", "CUSR0000SAH1", "US CPI shelter, YoY (FRED CUSR0000SAH1)", "yoy", +1, "Shelter (rents) inflation rose"),
            ("external", "fred_series", "POILBREUSDM", "Brent crude oil price, USD (FRED POILBREUSDM)", "yoy", +1, "Oil prices rose"),
            ("policy", "fred_series", "M2SL", "US M2 money supply, YoY (FRED M2SL)", "yoy", +1, "Rapid money growth (pandemic stimulus) fed demand"),
            ("policy", "fred_series", "FEDFUNDS", "Federal funds rate (FRED FEDFUNDS)", "level", -1, "Monetary policy was loose (near-zero rates) going into the spike"),
            ("labour", "fred_series", "UNRATE", "US unemployment rate (FRED UNRATE)", "level", -1, "A tight labour market pushed wages up"),
        ],
        "wiki": "2021–2023 inflation surge United States",
    },
    ("unemployment", "US"): {
        "target": ("fred_series", "UNRATE", "US unemployment rate (FRED UNRATE)", "level"),
        "drivers": [
            ("labour", "fred_series", "ICSA", "Initial jobless claims (FRED ICSA)", "level", +1, "Mass layoffs (initial jobless claims exploded)"),
            ("labour", "fred_series", "PAYEMS", "Total nonfarm payrolls, YoY (FRED PAYEMS)", "yoy", -1, "Payroll employment collapsed"),
            ("sector", "fred_series", "USLAH", "Leisure & hospitality employment, YoY (FRED USLAH)", "yoy", -1, "Shutdowns hit leisure & hospitality hardest"),
            ("activity", "fred_series", "INDPRO", "Industrial production, YoY (FRED INDPRO)", "yoy", -1, "Industrial output fell"),
            ("policy", "fred_series", "FEDFUNDS", "Federal funds rate (FRED FEDFUNDS)", "level", +1, "Tight monetary policy caused it"),
        ],
        "wiki": "COVID-19 recession United States unemployment",
    },
    ("unemployment", "GB"): {
        "target": ("fred_series", "LRHUTTTTGBM156S", "UK unemployment rate (FRED LRHUTTTTGBM156S)", "level"),
        "drivers": [
            ("activity", "fred_series", "GBRPROINDMISMEI", "UK industrial production index (FRED GBRPROINDMISMEI)", "yoy", -1, "Output fell (recession)"),
            ("policy", "fred_series", "IRSTCI01GBM156N", "UK short-term interest rate (FRED IRSTCI01GBM156N)", "level", +1, "Tight monetary policy caused it"),
            ("external", "fred_series", "POILBREUSDM", "Brent crude oil price, USD (FRED POILBREUSDM)", "yoy", +1, "An oil price shock squeezed firms"),
        ],
        "wiki": "United Kingdom unemployment recession",
    },
}
SUPPORTED = sorted(f"{i} ({c})" for i, c in CONFIG)


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
            return {"error": f"No playbook for {self.indicator} in {self.country} yet. Supported: {', '.join(SUPPORTED)}"}
        Y = self.year
        kind, key, label, tr = self.cfg["target"]
        self.t.step("plan", f"Pull {label} for {Y - 4}-{Y + 1}, find the {Y} extreme, then test {len(self.cfg['drivers'])} candidate drivers")
        tgt, trows, tres = self.series(kind, key, label, tr)
        in_year = {m: v for m, v in tgt.items() if m.startswith(str(Y))}
        base = [v for m, v in tgt.items() if m.startswith(str(Y - 2))]
        if not in_year or not base:
            return {"error": f"No {label} data for {Y} and {Y - 2} (got {min(tgt) if tgt else '-'}..{max(tgt) if tgt else '-'})."}
        baseline = sum(base) / len(base)
        peak_m = max(in_year, key=in_year.get) if (self.asked_dir or "up") == "up" else min(in_year, key=in_year.get)
        peak = in_year[peak_m]
        delta = peak - baseline
        actual_dir = "up" if delta > 0 else "down"
        unit = "%" if tr == "yoy" or self.indicator in ("inflation", "unemployment") else ""
        fp = self.t.add_finding(statement=f"{label}: {peak:.1f}{unit} in {peak_m} (the {Y} {'peak' if actual_dir == 'up' else 'low'}), "
                                          f"vs a {Y - 2} average of {baseline:.1f}{unit} ({delta:+.1f} pts)",
                                kind="data" if tr == "level" else "derived", query_id=tres.query_id,
                                rows=[trows.get(peak_m, 0)] + ([trows.get(shift_month(peak_m, -12), 0)] if tr == "yoy" else []),
                                source_url=tres.sources[0]["url"], value=peak, unit=unit, fact_key=f"{self.country}:{self.indicator}:{Y}:peak")
        premise_ok = self.asked_dir is None or self.asked_dir == actual_dir
        self.t.step("findings", f"Target {baseline:.1f} -> {peak:.1f} ({delta:+.1f} pts, peak {peak_m})")

        months = sorted(m for m in tgt if f"{Y - 3}-01" <= m <= peak_m)
        supported_w = 0.0
        total_w = 0.0
        for group, dkind, dkey, dlabel, dtr, sign, hyp in self.cfg["drivers"]:
            h = self.t.add_hypothesis(statement=hyp, test=f"{dlabel}: direction & size of move vs {Y - 2}, and best lead correlation (lag 0-9m)")
            ser, rows, res = self.series(dkind, dkey, dlabel, dtr)
            if res.error or not ser:
                h.verdict, h.detail = "inconclusive", f"No data ({res.error or 'empty series'})."
                continue
            b = [v for m, v in ser.items() if m.startswith(str(Y - 2))]
            if not b:
                h.verdict, h.detail = "inconclusive", f"No {Y - 2} baseline."
                continue
            # value at peak month (or latest available up to it), and window max for leads
            avail = sorted(m for m in ser if m <= peak_m)
            if not avail:
                h.verdict = "inconclusive"
                continue
            at = ser[avail[-1]]
            window = [ser[m] for m in avail if m >= shift_month(peak_m, -12)]
            extreme = max(window) if sign * (1 if actual_dir == "up" else -1) > 0 else min(window)
            if self.indicator == "inflation" and group in ("policy", "labour") and dtr == "level":
                # condition hypotheses: level in the year before the spike
                pre = [ser[m] for m in avail if shift_month(peak_m, -18) <= m <= shift_month(peak_m, -6)]
                lvl = sum(pre) / len(pre) if pre else at
                bl = sum(b) / len(b)
                f = self.t.add_finding(statement=f"{dlabel}: averaged {lvl:.2f} in the 6-18 months before the {peak_m} peak (vs {bl:.2f} in {Y - 2}); {at:.2f} at the peak",
                                       kind="data" if dtr == "level" else "derived", query_id=res.query_id,
                                       rows=[rows.get(avail[-1], 0)], source_url=res.sources[0]["url"], value=at)
                cond = (lvl < 1.0) if (group == "policy" and sign < 0) else (lvl < bl - 0.3) if group == "labour" else (lvl > bl)
                h.weight = 0.3
                total_w += 0.3
                if cond:
                    h.verdict, h.evidence, h.detail = "supported", [f.id], "Pre-spike conditions match the hypothesis (enabling factor, not a trigger)."
                    supported_w += 0.3
                else:
                    h.verdict, h.counter, h.detail = "contradicted", [f.id], "Pre-spike conditions do not match."
                continue
            bl = sum(b) / len(b)
            move = extreme - bl
            if group == "policy":  # a policy stance is judged where it stood at the turning point
                move, extreme = at - bl, at
            # lead correlation
            best_r, best_lag = None, 0
            for lag in range(0, 10):
                xs, ys = [], []
                for m in months:
                    lm = shift_month(m, -lag)
                    if lm in ser:
                        xs.append(ser[lm])
                        ys.append(tgt[m])
                r = pearson(xs, ys)
                if r is not None and (best_r is None or sign * r > sign * best_r):
                    best_r, best_lag = r, lag
            u = "%" if dtr in ("yoy",) or "rate" in dlabel.lower() else ""
            fn = lambda x: f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.1f}"
            what = "YoY change" if dtr == "yoy" else "level"
            f = self.t.add_finding(
                statement=f"{dlabel} ({what}): {fn(bl)}{u} average in {Y - 2}; " + (f"{fn(at)}{u} at {avail[-1]}" if group == "policy" else f"reached {fn(extreme)}{u} in the 12 months to {peak_m}")
                          + (f"; best correlation with the target r={best_r:+.2f} at a {best_lag}-month lead" if best_r is not None else ""),
                kind="derived" if dtr == "yoy" else "data", query_id=res.query_id,
                rows=sorted({rows.get(m, 0) for m in (avail[-1], shift_month(avail[-1], -12)) if m in rows}),
                source_url=res.sources[0]["url"], value=extreme,
            )
            want = sign * (1 if actual_dir == "up" else -1)  # +1: driver should rise
            material = abs(move) >= (5.0 if dtr == "yoy" and group == "external" else 1.0)
            if group == "component" and self.indicator == "inflation":
                material = extreme > peak * 0.8 and move * want > 0  # component running near/above headline
            corr_ok = best_r is not None and best_r * sign >= 0.5
            mag = min(abs(move) / max(abs(delta), 1.0), 3.0)
            h.weight = round(mag * (abs(best_r) if best_r is not None else 0.3), 3)
            total_w += h.weight
            if move * want > 0 and material and corr_ok:
                h.verdict, h.evidence = "supported", [f.id]
                h.detail = f"Moved {fn(move)} the expected way and co-moved with the target (r={best_r:+.2f}, lead {best_lag}m)."
                supported_w += h.weight
            elif move * want < 0 and abs(move) >= (0.25 if group == "policy" else 1.0):
                h.verdict, h.counter = "contradicted", [f.id]
                h.detail = f"Moved the other way ({fn(move)})."
            else:
                h.verdict, h.counter = "inconclusive", [f.id]
                h.detail = "Moved the right way but weakly or without a consistent lead." if move * want > 0 else "No material move."
        self.coverage = supported_w / total_w if total_w else 0.0
        self.t.step("hypotheses", f"Tested {len(self.cfg['drivers'])} drivers; {sum(1 for h in self.t.hypotheses if h.verdict == 'supported')} supported")
        self.context(Y)
        name = {"inflation": "inflation", "unemployment": "unemployment"}[self.indicator]
        cname = {"GB": "UK", "US": "US"}[self.country]
        return {"target_finding": fp.id, "target_fids": [fp.id], "delta": delta, "is_ratio": True, "actual_dir": actual_dir,
                "premise_ok": premise_ok, "move": f"{baseline:.1f} -> {peak:.1f} ({delta:+.1f} pts, peak {peak_m})",
                "subject": f"{cname} {name}", "period": str(Y), "coverage": self.coverage}

    KEYWORDS = {
        "energy": ["energy", "gas", "electricity", "fuel", "ofgem", "price cap", "ukraine", "russia"],
        "food": ["food"],
        "pound": ["sterling", "pound", "exchange rate", "currency", "dollar"],
        "supply": ["supply chain", "shortage", "chips", "semiconductor"],
        "money": ["stimulus", "money supply", "quantitative", "fiscal"],
        "labour": ["labour", "labor", "wage", "unemployment", "jobs"],
        "layoff": ["layoff", "lockdown", "covid", "pandemic", "furlough", "claims"],
    }

    def context(self, Y: int) -> None:
        hits = self.ctx.background(self.cfg["wiki"], [str(Y)], extra=[])
        fam_of = lambda s: next((k for k, ws in (("energy", ["energy", "gas", "oil", "fuel", "transport"]), ("food", ["food"]),
                                                 ("pound", ["pound", "imports"]), ("supply", ["supply", "shortage"]),
                                                 ("money", ["money"]), ("labour", ["labour", "wage"]), ("layoff", ["layoff", "shutdown", "payroll", "claims"]))
                                 if any(w in s for w in ws)), None)
        for f in hits:
            low = f.statement.lower()
            for h in self.t.hypotheses:
                fam = fam_of(h.statement.lower())
                if fam and h.verdict == "supported" and any(k in low for k in self.KEYWORDS[fam]):
                    h.evidence.append(f.id)
