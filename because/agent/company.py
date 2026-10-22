"""Playbook: why did a company's financial metric change? (SEC EDGAR via the harness)

Method
------
1. Pull the target metric's annual 10-K values for Y-3..Y (one harness query per
   XBRL concept, same SQL on Zetaris and DuckDB).
2. Check the premise (did it actually move the way the question says?).
3. Build a bridge: express each observable driver as a change in
   "percentage points of revenue" (margins) or dollars (income), so hypotheses
   are tested against the size of the move, not just its sign.
4. Each hypothesis is supported / contradicted / inconclusive by explicit rules;
   whatever the bridge cannot explain is reported as an unexplained remainder.
5. Background context (Wikipedia) can corroborate a hypothesis but never
   supplies a number.
"""

from __future__ import annotations

from typing import Any

from because.agent.common import days_between, fiscal_label, fmt_pct, fmt_pts, fmt_usd, pct
from because.harness.base import SourceSpec

CONCEPTS: dict[str, list[str]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "rd": ["ResearchAndDevelopmentExpense"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "charges": ["RestructuringSettlementAndImpairmentProvisions", "RestructuringCharges", "AssetImpairmentCharges"],
    "goodwill_impairment": ["GoodwillImpairmentLoss"],
    "depreciation": ["Depreciation", "DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "inventory": ["InventoryNet"],
    "inventory_writedown": ["InventoryWriteDown"],
    "tax": ["IncomeTaxExpenseBenefit"],
}
LABEL = {
    "revenue": "revenue", "cogs": "cost of sales", "gross_profit": "gross profit", "operating_income": "operating income",
    "net_income": "net income", "rd": "R&D expense", "sga": "SG&A expense", "charges": "restructuring and impairment charges",
    "goodwill_impairment": "goodwill impairment", "depreciation": "depreciation", "capex": "capital expenditure",
    "inventory": "inventory", "inventory_writedown": "inventory write-downs", "tax": "income tax expense",
    "gross_margin": "gross margin", "operating_margin": "operating margin",
}
INSTANT = {"inventory"}

SQL_ANNUAL = (
    "SELECT period_start, period_end, value_usd, form_type, fiscal_period, filed_date, accession_number "
    "FROM {t} WHERE form_type = '10-K' AND fiscal_period = 'FY' ORDER BY period_end, filed_date"
)


class CompanyPlaybook:
    def __init__(self, ctx: Any, company: dict[str, Any], metric: str, year: int, direction: str | None) -> None:
        self.ctx = ctx
        self.t = ctx.trace
        self.co = company
        self.metric = metric
        self.year = year
        self.asked_dir = direction
        self.short = company["name"].split(" ")[0].title() if company["name"].isupper() else company["name"].split(",")[0]
        self.series: dict[str, dict[int, dict[str, Any]]] = {}

    # -- data -------------------------------------------------------------------
    def annual(self, key: str, years: range) -> dict[int, dict[str, Any]]:
        """Annual values by fiscal year, merged across alternative XBRL tags."""
        if key in self.series:
            return self.series[key]
        out: dict[int, dict[str, Any]] = {}
        for tag in CONCEPTS[key]:
            spec = SourceSpec("edgar_concept", f"{self.co['cik']}:{tag}", f"{self.co['name']} {tag}")
            res = self.ctx.query(SQL_ANNUAL, {"t": spec}, f"{LABEL[key]} ({tag}) annual 10-K facts")
            if res.error or not res.rows:
                continue
            for i, r in enumerate(res.records()):
                if r["value_usd"] is None or not r["period_end"]:
                    continue
                if key in INSTANT:
                    if r["period_start"]:
                        continue
                else:
                    d = days_between(r["period_start"], r["period_end"])
                    if d is None or not (340 <= d <= 380):
                        continue
                fy = fiscal_label(r["period_end"])
                if fy not in years or fy in out and out[fy]["concept"] != tag:
                    continue  # earlier tag in the preference list already covers this year
                prev = out.get(fy)
                if prev is None or (r["filed_date"] or "") >= (prev["filed"] or ""):  # latest filing = as restated
                    out[fy] = {"value": float(r["value_usd"]), "query_id": res.query_id, "row": i, "url": res.sources[0]["url"],
                               "concept": tag, "filed": r["filed_date"], "end": r["period_end"], "accn": r["accession_number"]}
            if all(y in out for y in years):
                break
        self.series[key] = out
        return out

    def fact(self, key: str, fy: int) -> str | None:
        """Register a data finding for one annual value; return its id."""
        d = self.series.get(key, {}).get(fy)
        if not d:
            return None
        cache = getattr(self, "_fact_ids", {})
        self._fact_ids = cache
        if (key, fy) in cache:
            return cache[(key, fy)]
        f = self.t.add_finding(
            statement=f"{self.short} {LABEL[key]} FY{fy}: {fmt_usd(d['value'])} (us-gaap:{d['concept']}, 10-K filed {d['filed']}, period ending {d['end']})",
            kind="data", query_id=d["query_id"], rows=[d["row"]], source_url=d["url"], value=d["value"], unit="USD",
            fact_key=f"{self.co['ticker']}:{key}:{fy}",
        )
        cache[(key, fy)] = f.id
        return f.id

    def v(self, key: str, fy: int) -> float | None:
        d = self.series.get(key, {}).get(fy)
        return d["value"] if d else None

    def ratio(self, key: str, fy: int) -> float | None:
        a, rev = self.v(key, fy), self.v("revenue", fy)
        return a / rev * 100 if a is not None and rev else None

    # -- run --------------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        Y, P = self.year, self.year - 1
        years = range(Y - 3, Y + 1)
        self.t.step("plan", f"Resolve {self.co['name']} (CIK {self.co['cik']}) and pull {LABEL.get(self.metric, self.metric)} for FY{Y - 3}-FY{Y} from SEC EDGAR")
        need = {"revenue", "gross_profit", "cogs", "operating_income", "net_income", "rd", "sga", "charges",
                "goodwill_impairment", "depreciation", "capex", "inventory", "inventory_writedown"}
        if self.metric == "net_income":
            need.add("tax")
        for k in sorted(need):
            self.annual(k, years)
        # derive gross profit if missing
        if not self.series["gross_profit"] and self.series["cogs"] and self.series["revenue"]:
            gp = {}
            for fy in years:
                r, c = self.v("revenue", fy), self.v("cogs", fy)
                if r is not None and c is not None:
                    gp[fy] = {**self.series["cogs"][fy], "value": r - c, "concept": "Revenue-CostOfRevenue"}
            self.series["gross_profit"] = gp
        self.t.step("queries", f"Ran {len(self.t.queries)} harness queries over {len(need)} XBRL concepts")

        target = self.target_values(years)
        if Y not in target or P not in target:
            return {"error": f"EDGAR has no FY{P}/FY{Y} values for {LABEL.get(self.metric, self.metric)} at {self.co['name']}. "
                             f"Years found: {sorted(target)}"}
        cur, prev = target[Y]["value"], target[P]["value"]
        is_ratio = self.metric in ("gross_margin", "operating_margin")
        delta = cur - prev
        actual_dir = "up" if delta > 0 else "down"
        if is_ratio:
            move = f"{prev:.1f}% -> {cur:.1f}% ({fmt_pts(delta)})"
        else:
            move = f"{fmt_usd(prev)} -> {fmt_usd(cur)} ({fmt_pct(pct(cur, prev))})"
        tf = self.t.add_finding(statement=f"{self.short} {LABEL.get(self.metric, self.metric)} FY{P} -> FY{Y}: {move}",
                                kind="derived", value=cur, unit="%" if is_ratio else "USD",
                                fact_key=f"{self.co['ticker']}:{self.metric}:{Y}",
                                query_id=target[Y].get("query_id"), rows=target[Y].get("rows", []))
        target_fids = [tf.id] + target[Y].get("evidence", []) + target[P].get("evidence", [])
        trend = ", ".join(f"FY{fy}: {target[fy]['value']:.1f}%" if is_ratio else f"FY{fy}: {fmt_usd(target[fy]['value'])}" for fy in sorted(target))
        self.t.add_finding(statement=f"{LABEL.get(self.metric, self.metric).capitalize()} trend: {trend}", kind="derived")
        premise_ok = self.asked_dir is None or self.asked_dir == actual_dir
        self.t.step("findings", f"Target moved {move}" + ("" if premise_ok else f" - the question assumed '{self.asked_dir}'"))

        # hypotheses
        if self.metric in ("gross_margin", "operating_margin"):
            self.margin_hypotheses(Y, P, delta, actual_dir)
        elif self.metric in ("net_income", "operating_income"):
            self.income_hypotheses(Y, P, delta, actual_dir)
        else:
            self.revenue_hypotheses(Y, P, delta, actual_dir)
        self.context(Y, actual_dir)
        return {"target_finding": tf.id, "target_fids": target_fids, "delta": delta, "is_ratio": is_ratio, "actual_dir": actual_dir,
                "premise_ok": premise_ok, "move": move, "subject": f"{self.short}'s {LABEL.get(self.metric, self.metric)}",
                "period": f"FY{Y}", "coverage": self.coverage}

    def target_values(self, years: range) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for fy in years:
            if self.metric == "gross_margin":
                num, den = self.v("gross_profit", fy), self.v("revenue", fy)
                if num is not None and den:
                    out[fy] = {"value": num / den * 100, "evidence": [x for x in (self.fact("gross_profit", fy), self.fact("revenue", fy)) if x] if fy >= self.year - 1 else []}
            elif self.metric == "operating_margin":
                num, den = self.v("operating_income", fy), self.v("revenue", fy)
                if num is not None and den:
                    out[fy] = {"value": num / den * 100, "evidence": [x for x in (self.fact("operating_income", fy), self.fact("revenue", fy)) if x] if fy >= self.year - 1 else []}
            else:
                key = {"rd": "rd"}.get(self.metric, self.metric)
                d = self.series.get(key, {}).get(fy)
                if d:
                    out[fy] = {"value": d["value"], "query_id": d["query_id"], "rows": [d["row"]],
                               "evidence": [self.fact(key, fy)] if fy >= self.year - 1 else []}
        return out

    # -- hypothesis families --------------------------------------------------------
    coverage: float = 0.0

    def _pts_driver(self, key: str, Y: int, P: int, hyp: str, test: str, sign: int, delta: float, threshold: float = 0.5) -> float:
        """Driver measured as change in % of revenue. `sign` = +1 if the driver rising lowers the target."""
        a, b = self.ratio(key, Y), self.ratio(key, P)
        h = self.t.add_hypothesis(statement=hyp, test=test)
        if a is None or b is None:
            h.verdict, h.detail = "inconclusive", f"EDGAR has no {LABEL[key]} for FY{P}/FY{Y} under the concepts tried ({', '.join(CONCEPTS[key])})."
            return 0.0
        d = a - b
        ev = [x for x in (self.fact(key, Y), self.fact(key, P), self.fact("revenue", Y), self.fact("revenue", P)) if x]
        f = self.t.add_finding(statement=f"{LABEL[key].capitalize()} was {a:.1f}% of revenue in FY{Y} vs {b:.1f}% in FY{P} ({fmt_pts(d)}; "
                                         f"{fmt_usd(self.v(key, P))} -> {fmt_usd(self.v(key, Y))})", kind="derived")
        impact = -sign * d  # effect on the target in pts
        cap_note = ""
        if self.metric == "gross_margin" and key != "rd" and key != "sga":
            # A driver can only move gross margin through cost of sales. If it grew by more
            # than cost of sales did, the excess must sit in operating expenses.
            dc = (self.v("cogs", Y) or (self.v("revenue", Y) or 0) - (self.v("gross_profit", Y) or 0)) - \
                 (self.v("cogs", P) or (self.v("revenue", P) or 0) - (self.v("gross_profit", P) or 0))
            dd = (self.v(key, Y) or 0) - (self.v(key, P) or 0)
            rev = self.v("revenue", Y) or 1
            if dd > 0 and dc >= 0 and dd > dc:
                capped = -dc / rev * 100
                cap_note = (f" It rose {fmt_usd(dd)} while total cost of sales rose only {fmt_usd(dc)}, so at most {fmt_pts(capped)} "
                            f"can sit in cost of sales; the rest is in operating expenses (XBRL does not say where).")
                impact = max(impact, capped) if impact < 0 else min(impact, -capped)
                f.statement += f" - but cost of sales rose only {fmt_usd(dc)}"
        h.weight = abs(impact)
        if abs(d) < threshold:
            h.verdict, h.counter = "inconclusive", [f.id] + ev
            h.detail = f"Moved only {fmt_pts(d)} of revenue - too small to matter."
            return 0.0
        if (impact < 0) == (delta < 0):
            h.verdict, h.evidence = "supported", [f.id] + ev
            h.detail = f"Accounts for up to {fmt_pts(impact)} of the {fmt_pts(delta)} move.{cap_note}"
            return impact
        h.verdict, h.counter = "contradicted", [f.id] + ev
        h.detail = f"Moved the other way ({fmt_pts(impact)} effect on the target)."
        return 0.0

    def _revenue_hyp(self, Y: int, P: int, delta: float, framing: str) -> None:
        r, rp = self.v("revenue", Y), self.v("revenue", P)
        h = self.t.add_hypothesis(statement=framing, test="Revenue change FY-1 -> FY (threshold +/-2%)")
        if r is None or rp is None:
            h.verdict = "inconclusive"
            return
        g = pct(r, rp)
        ev = [self.fact("revenue", Y), self.fact("revenue", P)]
        f = self.t.add_finding(statement=f"{self.short} revenue changed {fmt_pct(g)} in FY{Y} ({fmt_usd(rp)} -> {fmt_usd(r)})", kind="derived")
        h.weight = min(abs(g) / 10, 1.0) * max(abs(delta), 1.0) * 0.5
        want_down = delta < 0
        if abs(g) < 2:
            h.verdict, h.counter, h.detail = "inconclusive", [f.id] + ev, "Revenue was roughly flat."
        elif (g < 0) == want_down:
            h.verdict, h.evidence, h.detail = "supported", [f.id] + ev, f"Revenue moved {fmt_pct(g)}, in the same direction as the target."
        else:
            h.verdict, h.counter, h.detail = "contradicted", [f.id] + ev, f"Revenue moved {fmt_pct(g)}, the opposite way, so volume does not explain it."

    def margin_hypotheses(self, Y: int, P: int, delta: float, d: str) -> None:
        self.t.step("hypotheses", "Margin bridge: test each observable cost line as a change in % of revenue")
        self._revenue_hyp(Y, P, delta, "Revenue " + ("fell, so fixed manufacturing costs were spread over fewer sales (under-absorption)" if d == "down" else "grew, spreading fixed costs over more sales (operating leverage)"))
        explained = 0.0
        explained += self._pts_driver("depreciation", Y, P,
                                      "Depreciation from heavy capital spending " + ("rose" if d == "down" else "fell") + " as a share of revenue",
                                      "Change in depreciation / revenue (pts)", +1, delta)
        cx, cxp = self.ratio("capex", Y), self.ratio("capex", P)
        if cx is not None and cxp is not None:
            fid = self.t.add_finding(statement=f"Capital expenditure was {cx:.1f}% of revenue in FY{Y} vs {cxp:.1f}% in FY{P} "
                                               f"({fmt_usd(self.v('capex', Y))} vs {fmt_usd(self.v('capex', P))})", kind="derived").id
            dep_h = self.t.hypotheses[-1]
            if dep_h.verdict == "supported":
                dep_h.evidence += [fid] + [x for x in [self.fact("capex", Y)] if x]
        explained += self._pts_driver("charges", Y, P,
                                      "Restructuring and impairment charges " + ("increased" if d == "down" else "decreased"),
                                      "Change in restructuring+impairment / revenue (pts)", +1, delta)
        if self.v("goodwill_impairment", Y):
            fid = self.fact("goodwill_impairment", Y)
            if fid:
                self.t.hypotheses[-1].evidence.append(fid)
        explained += self._pts_driver("inventory_writedown", Y, P, "Inventory write-downs " + ("increased" if d == "down" else "decreased"),
                                      "Change in inventory write-downs / revenue (pts)", +1, delta, threshold=0.3)
        if self.metric == "operating_margin":
            explained += self._pts_driver("rd", Y, P, "R&D spending " + ("grew faster than revenue" if d == "down" else "grew slower than revenue"),
                                          "Change in R&D / revenue (pts)", +1, delta)
            explained += self._pts_driver("sga", Y, P, "SG&A " + ("rose" if d == "down" else "fell") + " relative to revenue",
                                          "Change in SG&A / revenue (pts)", +1, delta)
        inv, invp = self.ratio("inventory", Y), self.ratio("inventory", P)
        if inv is not None and invp is not None:
            self.t.add_finding(statement=f"Year-end inventory was {inv:.1f}% of revenue in FY{Y} vs {invp:.1f}% in FY{P}", kind="derived")
        rem = delta - explained
        if abs(explained) > abs(delta):
            self.t.add_finding(statement=f"Bridge: the identified lines add up to {fmt_pts(explained)}, more than the {fmt_pts(delta)} change - "
                                         f"they overlap or were partly offset by other cost improvements, so their individual sizes are upper bounds", kind="derived")
            self.coverage = 0.8
        else:
            self.t.add_finding(statement=f"Bridge: identified cost lines explain about {fmt_pts(explained)} of the {fmt_pts(delta)} change; "
                                         f"{fmt_pts(rem)} is unexplained by tagged line items (price/mix, yields, other cost of sales)", kind="derived")
            self.coverage = min(1.0, abs(explained) / abs(delta)) if delta else 0.0
        self.remainder = rem

    def income_hypotheses(self, Y: int, P: int, delta: float, d: str) -> None:
        self.t.step("hypotheses", "Profit bridge: dollar contribution of each line to the change")
        total = 0.0
        parts = [("gross_profit", +1, "Gross profit " + ("fell" if d == "down" else "rose")),
                 ("rd", -1, "R&D expense " + ("rose" if d == "down" else "fell")),
                 ("sga", -1, "SG&A expense " + ("rose" if d == "down" else "fell")),
                 ("charges", -1, "Restructuring and impairment charges " + ("rose" if d == "down" else "fell"))]
        if self.metric == "net_income":
            parts.append(("tax", -1, "Income tax expense " + ("rose" if d == "down" else "fell")))
        for key, sign, text in parts:
            a, b = self.v(key, Y), self.v(key, P)
            h = self.t.add_hypothesis(statement=text, test=f"Dollar contribution of {LABEL[key]} to the change (>=10% of the move)")
            if a is None or b is None:
                h.verdict, h.detail = "inconclusive", f"No {LABEL[key]} in EDGAR for both years."
                continue
            contrib = sign * (a - b)
            ev = [self.fact(key, Y), self.fact(key, P)]
            f = self.t.add_finding(statement=f"{LABEL[key].capitalize()}: {fmt_usd(b)} -> {fmt_usd(a)}, contributing {fmt_usd(contrib)} to the change", kind="derived")
            h.weight = abs(contrib) / 1e9
            if abs(contrib) < 0.1 * abs(delta):
                h.verdict, h.counter, h.detail = "inconclusive", [f.id] + ev, "Small relative to the move."
            elif (contrib < 0) == (delta < 0):
                h.verdict, h.evidence, h.detail = "supported", [f.id] + ev, f"{fmt_usd(contrib)} of the {fmt_usd(delta)} change."
                total += contrib
            else:
                h.verdict, h.counter, h.detail = "contradicted", [f.id] + ev, f"Offset the move by {fmt_usd(contrib)}."
        self._revenue_hyp(Y, P, delta, "Revenue " + ("fell" if d == "down" else "grew") + ", driving the profit change")
        self.coverage = min(1.0, abs(total) / abs(delta)) if delta else 0.0

    def revenue_hypotheses(self, Y: int, P: int, delta: float, d: str) -> None:
        self.t.step("hypotheses", "Revenue: test pricing power, demand vs supply signals and operating leverage")
        g, gp = self.ratio("gross_profit", Y), self.ratio("gross_profit", P)
        h = self.t.add_hypothesis(statement=("Demand outstripped supply and pricing power rose (gross margin expanded with revenue)" if d == "up"
                                             else "Pricing pressure accompanied lower volumes (gross margin contracted)"),
                                  test="Gross margin change alongside revenue (>= 2 pts)")
        if g is not None and gp is not None:
            f = self.t.add_finding(statement=f"Gross margin {gp:.1f}% -> {g:.1f}% ({fmt_pts(g - gp)}) while revenue moved {fmt_pct(pct(self.v('revenue', Y), self.v('revenue', P)))}", kind="derived")
            ev = [f.id] + [x for x in (self.fact("gross_profit", Y), self.fact("gross_profit", P)) if x]
            h.weight = abs(g - gp)
            if abs(g - gp) < 2:
                h.verdict, h.counter, h.detail = "inconclusive", ev, "Margin roughly unchanged: volume, not price, did the work."
            elif (g > gp) == (d == "up"):
                h.verdict, h.evidence, h.detail = "supported", ev, "Margin moved with revenue - consistent with pricing power / mix."
            else:
                h.verdict, h.counter, h.detail = "contradicted", ev, "Margin moved against revenue."
        else:
            h.verdict = "inconclusive"
        inv, invp = self.v("inventory", Y), self.v("inventory", P)
        h2 = self.t.add_hypothesis(statement=("Sales ran ahead of inventory (demand-led growth)" if d == "up" else "Inventory piled up as demand fell (demand-led decline)"),
                                   test="Inventory growth vs revenue growth (10-pt gap)")
        if inv is not None and invp is not None:
            ig, rg = pct(inv, invp), pct(self.v("revenue", Y), self.v("revenue", P))
            f = self.t.add_finding(statement=f"Year-end inventory {fmt_usd(invp)} -> {fmt_usd(inv)} ({fmt_pct(ig)}) vs revenue {fmt_pct(rg)}", kind="derived")
            ev = [f.id] + [x for x in (self.fact("inventory", Y), self.fact("inventory", P)) if x]
            gap = (rg or 0) - (ig or 0)
            h2.weight = min(abs(gap) / 20, 2)
            if abs(gap) < 10:
                h2.verdict, h2.counter = "inconclusive", ev
            elif (gap > 0) == (d == "up"):
                h2.verdict, h2.evidence = "supported", ev
            else:
                h2.verdict, h2.counter = "contradicted", ev
        else:
            h2.verdict = "inconclusive"
        oi, oip = self.v("operating_income", Y), self.v("operating_income", P)
        h3 = self.t.add_hypothesis(statement="Operating leverage amplified the move (operating income moved more than revenue)",
                                   test="Operating income % change vs revenue % change")
        if oi is not None and oip is not None:
            og, rg = pct(oi, oip), pct(self.v("revenue", Y), self.v("revenue", P))
            f = self.t.add_finding(statement=f"Operating income {fmt_usd(oip)} -> {fmt_usd(oi)} ({fmt_pct(og)}) vs revenue {fmt_pct(rg)}", kind="derived")
            ev = [f.id] + [x for x in (self.fact("operating_income", Y), self.fact("operating_income", P)) if x]
            h3.weight = 0.5
            if og is not None and rg is not None and abs(og) > abs(rg) and (og > 0) == (rg > 0):
                h3.verdict, h3.evidence = "supported", ev
            else:
                h3.verdict, h3.counter = "inconclusive", ev
        rd, rdp = self.v("rd", Y - 1), self.v("rd", Y - 3)
        if rd and rdp:
            self.t.add_finding(statement=f"R&D grew {fmt_pct(pct(rd, rdp))} from FY{Y - 3} to FY{Y - 1} ({fmt_usd(rdp)} -> {fmt_usd(rd)}), the investment base behind FY{Y} products", kind="derived")
        supported = sum(1 for h in self.t.hypotheses if h.verdict == "supported")
        self.coverage = min(1.0, supported / 3)

    # -- background ------------------------------------------------------------------
    KEYWORDS = {
        "depreciation": ["depreciation", "capital expenditure", "capex", "fab", "foundry", "factories", "manufacturing capacity"],
        "restructuring": ["restructuring", "layoff", "lay off", "job cuts", "impairment", "write-down", "writedown", "charges"],
        "revenue": ["demand", "slump", "decline in sales", "weak", "boom", "surge", "lost market share"],
        "pricing": ["pricing", "price", "margin", "ai", "data center", "gpu", "h100", "shortage", "supply"],
        "inventory": ["inventory", "glut", "excess"],
    }

    def context(self, Y: int, d: str) -> None:
        terms = [str(Y)]
        hits = self.ctx.background(f"{self.short} {Y}", terms, extra=[f"{self.short} {Y} layoffs restructuring" if d == "down" else f"{self.short} {Y} demand growth"])
        for f in hits:
            low = f.statement.lower()
            for h in self.t.hypotheses:
                hs = h.statement.lower()
                fam = ("depreciation" if "depreciation" in hs else "restructuring" if "restructuring" in hs or "impairment" in hs
                       else "inventory" if "inventory" in hs else "pricing" if "pricing" in hs or "margin" in hs else "revenue" if "revenue" in hs else None)
                if fam and any(k in low for k in self.KEYWORDS[fam]) and h.verdict == "supported":
                    h.evidence.append(f.id)
