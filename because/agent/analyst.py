"""The Because analyst: Scout -> recall -> investigate -> test -> answer -> remember.

    question
      -> Scout (intent, guard, signed contract)          because/meterless/scout.py
      -> H-MEM recall (what do I already believe, why?)    because/meterless/hmem.py
      -> playbook: harness queries -> findings -> hypotheses (company.py / macro.py)
      -> answer: claims with [F#] citations, counter-evidence, confidence
      -> optional LLM narrative (NIM / Groq / Gemini), validated against findings
      -> H-MEM write (findings with provenance) + conflict scan
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from because.agent.common import clamp, confidence_label
from because.agent.company import CompanyPlaybook
from because.agent.macro import CONFIG as MACRO_CONFIG
from because.agent.macro import MacroPlaybook, supported_scope
from because.agent.parse import COUNTRY_NAMES, METRICS, llm_parse, parse_question
from because.agent.trace import Trace
from because.harness import DataHarness, get_harness
from because.harness.base import QueryResult, SourceSpec
from because.llm import get_llm
from because.meterless.hmem import HMEM
from because.meterless.scout import ContractRefused, Scout
from because.research.background import gdelt, sentences_mentioning, wikipedia

HERE = Path(__file__).parent

# Conclusions below this confidence (or whose premise failed) are not written to memory.
REMEMBER_MIN_CONFIDENCE = 0.6
MACRO_CONFIDENCE_CAP = 0.85  # component weights are not applied, so a macro answer is never "certain"
REVENUE_CONFIDENCE_CAP = 0.70  # no segment data in companyfacts
SINGLE_DRIVER_CAP = 0.55  # only one independent driver supported: at most Medium
NO_DRIVER_CAP = 0.35
ONE_OFF_TOP_CAP = 0.65  # the largest supported driver is a one-off item

INTENT_WORDS = {
    "explain.company_metric": "why a company's financial metric changed (e.g. 'Why did Intel's gross margin fall in 2024?')",
    "explain.macro_series": "why a macro indicator moved (e.g. 'Why did UK inflation fall in 2023?')",
    "memory.explain_belief": "what I already remember about a topic (e.g. 'What do you already know about Intel's margins?')",
    "GENERAL": "something else",
}
PARAM_WORDS = {"company": "the company", "metric": "the metric (revenue, gross margin, operating margin, operating income, net income or R&D)",
               "year": "the year", "country": "the country", "indicator": "the indicator (inflation or unemployment)", "topic": "the topic"}
METRIC_WORDS = {"gross_margin": "gross margin", "operating_margin": "operating margin", "operating_income": "operating income",
                "net_income": "net income", "rd": "R&D expense", "revenue": "revenue"}


def scope_text() -> str:
    """What Because can investigate, derived from the macro CONFIG and the metric parser."""
    macro = ", ".join(supported_scope())
    metrics = ", ".join(METRIC_WORDS[m] for m, _ in METRICS)
    return (f"Macro indicators: {macro} (official ONS and FRED data). "
            f"Companies: {metrics} for US-listed companies (SEC 10-K filings).")


def lower_first(s: str) -> str:
    """Lowercase the first letter for use mid-sentence, unless it starts an acronym ('R&D', 'UK CPI')."""
    if not s:
        return s
    if len(s) == 1 or s[1].islower():
        return s[0].lower() + s[1:]
    return s


def confidence_score(coverage: float, completeness: float, context_hits: int, premise_ok: bool, n_contradicted: int,
                     n_groups: int, playbook: str = "macro", metric: str | None = None, one_off_top: bool = False) -> float:
    """Monotone confidence: never decreases with coverage, completeness, context or independent drivers;
    never increases with contradictions or a failed premise. Caps keep it honest about what was not measured."""
    score = 0.25 + 0.45 * clamp(coverage) + 0.15 * clamp(completeness) + (0.1 if context_hits else 0.0) \
        - (0.1 if not premise_ok else 0.0) - 0.04 * min(n_contradicted, 3)
    if n_groups <= 0:
        score = min(score, NO_DRIVER_CAP)
    elif n_groups == 1:
        score = min(score, SINGLE_DRIVER_CAP)
    if playbook == "macro":
        score = min(score, MACRO_CONFIDENCE_CAP)
    elif metric == "revenue":
        score = min(score, REVENUE_CONFIDENCE_CAP)
    if one_off_top:
        score = min(score, ONE_OFF_TOP_CAP)
    return round(clamp(score, 0.05, 0.95), 2)


def _home() -> Path:
    p = Path(os.environ.get("BECAUSE_HOME", ".because"))
    p.mkdir(parents=True, exist_ok=True)
    return p


class Analyst:
    def __init__(self, harness: DataHarness | None = None, memory: HMEM | None = None, llm: Any = "auto",
                 background: bool = True, scout: Scout | None = None, memory_lock: Any = None) -> None:
        self.harness = harness or get_harness()
        self.memory = memory if memory is not None else HMEM(path=_home() / "memory.json")
        self.llm = get_llm() if llm == "auto" else llm
        self.use_background = background and os.environ.get("BECAUSE_BACKGROUND", "1") != "0"
        self.scout = scout or Scout(intentRegistry=str(HERE / "intents.json"), policyPack="because-default")
        self.trace: Trace = Trace(question="")
        self.contract: dict | None = None
        self.memory_lock = memory_lock or threading.RLock()  # shared when several analysts share one memory

    # -- executor boundary ---------------------------------------------------------
    def _enforce(self, capability: str) -> None:
        if self.contract is None:
            raise ContractRefused("no execution contract")
        self.scout.enforce(self.contract, capability, engine="because-analyst")

    def query(self, sql: str, tables: dict[str, SourceSpec], purpose: str) -> QueryResult:
        self._enforce("harness.query")
        res = self.harness.query(sql, tables)
        d = res.to_dict()
        d["purpose"] = purpose
        if len(d["rows"]) > 400:  # keep traces readable; full rows are replayable from the SQL
            d["rows_truncated"] = len(d["rows"])
        self.trace.queries.append(d)
        return res

    def background(self, query: str, terms: list[str], extra: list[str] | None = None) -> list:
        if not self.use_background:
            return []
        self._enforce("background.search")
        out = []
        seen = set()
        for q in [query] + (extra or []):
            for page in wikipedia(q, pages=1):
                if page["url"] in seen:
                    continue
                seen.add(page["url"])
                for s in sentences_mentioning(page["text"], terms, limit=4):
                    out.append(self.trace.add_finding(statement=f"Context (Wikipedia, '{page['title']}'): {s}", kind="context", source_url=page["url"]))
        if os.environ.get("BECAUSE_GDELT", "0") == "1":  # recent news only; off by default (shared rate limit)
            for a in gdelt(query, max_records=5):
                out.append(self.trace.add_finding(statement=f"News (GDELT, {a.get('domain')}): {a.get('title')}", kind="context", source_url=a.get("url")))
        self.trace.step("context", f"Background search found {len(out)} context snippets (context corroborates, never supplies numbers)")
        return out

    # -- main entry -------------------------------------------------------------------
    def ask(self, question: str, user: dict | None = None, surface: str = "cli") -> Trace:
        t0 = time.time()
        self.trace = t = Trace(question=question, backend=self.harness.name,
                               fallback_reason=getattr(self.harness, "fallback_reason", None))
        t.llm = self.llm.describe() if self.llm else {"provider": "none", "model": "deterministic"}
        user = user or {"id": "local", "role": "analyst"}

        parsed = parse_question(question)
        if self.llm and not (parsed.get("company") or parsed.get("indicator")):
            parsed.update({k: v for k, v in llm_parse(self.llm, question).items() if k not in parsed})

        def binder(intent: dict, prompt: str) -> dict:
            if intent["id"] == "explain.company_metric":
                co = parsed.get("company")
                return {"company": co["name"] if co else None, "metric": parsed.get("metric"), "year": parsed.get("year")}
            if intent["id"] == "explain.macro_series":
                return {"indicator": parsed.get("indicator"), "country": parsed.get("country"), "year": parsed.get("year")}
            if intent["id"] == "memory.explain_belief":
                return {"topic": prompt}
            return {}

        decision = self.scout.decide_sync({"prompt": question, "user": user, "surface": surface}, binder=binder)
        self.contract = decision.get("executionContract")
        t.scout = {k: decision.get(k) for k in ("intent", "band", "candidates", "risk", "clarification", "parameters", "plan")}
        t.scout["contract"] = self.contract
        t.scout["telemetry"] = self.scout.telemetry[-4:]
        t.step("scout", f"Intent {decision['intent']} (band {decision['band']}, risk {decision['risk']['level']})"
               + (f"; signed contract {self.contract['contractId']}" if self.contract else ""))

        if decision["risk"]["level"] == "block":
            t.answer = {"summary": f"Refused: {decision['risk'].get('reason') or 'blocked by the guard stack'}.", "blocked": True,
                        "confidence": {"score": 0, "label": "n/a", "rationale": "Guard blocked the request before any tool ran."}}
            return self._finish(t, t0)
        unsupported = self._unsupported_scope(parsed)
        if unsupported:
            t.answer = {"summary": unsupported, "unsupported_scope": True, "supported": scope_text(),
                        "confidence": {"score": 0, "label": "n/a", "rationale": "Outside the supported scope; nothing was investigated."}}
            t.step("scope", "Question is outside the supported scope; no tools ran")
            return self._finish(t, t0)
        if decision.get("clarification"):
            c = decision["clarification"]
            t.answer = {"summary": self._clarify_text(c), "clarification": {**c, "options_plain": [INTENT_WORDS.get(o, o) for o in c.get("options") or []]},
                        "confidence": {"score": 0, "label": "n/a", "rationale": "Scout asked for clarification instead of guessing."}}
            return self._finish(t, t0)
        intent = decision["intent"]
        if intent == "GENERAL":
            t.answer = {"summary": "I answer *why* questions about public-company financials (SEC EDGAR) and macro indicators (ONS, FRED), "
                                   "e.g. 'Why did Intel's gross margin fall in 2024?' or 'Why did UK inflation spike in 2022?'.",
                        "confidence": {"score": 0, "label": "n/a", "rationale": "No investigation intent matched."}}
            return self._finish(t, t0)
        if intent == "memory.explain_belief":
            return self._finish(self._explain_belief(question), t0)

        # recall
        self._enforce("memory.recall")
        with self.memory_lock:
            rec = self.memory.query(question)
        t.memory_recall = [{"id": m["memory"]["id"], "content": m["memory"]["content"], "score": m["score"],
                            "confidence": m["memory"]["confidence"], "provenance": m["memory"].get("provenance"),
                            "learnedAt": m["memory"].get("timestamp"), "layer": m["memory"]["layer"]} for m in rec["memories"]]
        t.step("recall", f"H-MEM recalled {len(t.memory_recall)} prior beliefs ({rec['strategy']})")

        # plan + investigate
        p = decision["parameters"]
        year = parsed.get("year")
        if intent == "explain.company_metric":
            co, metric = parsed.get("company"), parsed.get("metric") or "revenue"
            t.plan = {"playbook": "company_metric", "company": co, "metric": metric, "year": year, "asked_direction": parsed.get("direction"),
                      "sources": ["SEC EDGAR companyconcept (XBRL)", "Wikipedia (context)"], "steps": [
                          "pull annual 10-K values for the metric and its drivers (FY-3..FY)", "check the premise",
                          "bridge the change across observable cost/revenue lines", "test each hypothesis with explicit thresholds",
                          "gather context that can corroborate (never supply numbers)", "score confidence, list counter-evidence"]}
            pb = CompanyPlaybook(self, co, metric, year, parsed.get("direction"))
        else:
            t.plan = {"playbook": "macro_series", "indicator": parsed.get("indicator"), "country": parsed.get("country"), "year": year,
                      "asked_direction": parsed.get("direction"), "sources": ["ONS", "FRED", "Wikipedia (context)"], "steps": [
                          "pull the target series and locate the extreme vs a baseline", "pull each candidate driver series",
                          "test direction, size and lead correlation per driver", "gather context", "score confidence, list counter-evidence"]}
            pb = MacroPlaybook(self, parsed.get("indicator"), parsed.get("country"), year, parsed.get("direction"))
        try:
            outcome = pb.run()
        except ContractRefused:
            raise
        except Exception as e:
            outcome = {"error": f"{type(e).__name__}: {e}"}
        if outcome.get("error"):
            t.answer = {"summary": f"I couldn't complete the investigation: {outcome['error']}",
                        "confidence": {"score": 0, "label": "n/a", "rationale": "Investigation failed."}}
            return self._finish(t, t0)
        t.answer = self._compose(outcome)
        self._narrate()
        self._remember(outcome)
        return self._finish(t, t0)

    # -- scope ---------------------------------------------------------------------------
    @staticmethod
    def _unsupported_scope(parsed: dict[str, Any]) -> str | None:
        """Plain message when the question is about something there is no playbook for."""
        ind, ctry = parsed.get("indicator"), parsed.get("country")
        what = None
        if ind and ctry and (ind, ctry) not in MACRO_CONFIG:
            what = f"{COUNTRY_NAMES.get(ctry, ctry)} {ind}"
        elif parsed.get("unsupported_topic") and not parsed.get("company"):
            what = (f"{COUNTRY_NAMES.get(ctry, ctry)} " if ctry else "") + parsed["unsupported_topic"]
        if not what:
            return None
        return (f"I can't investigate {what} yet, so I haven't guessed. What I can explain today: {scope_text()} "
                "For example: 'Why did UK inflation fall in 2023?' or 'Why did Meta's net income drop in 2022?'")

    @staticmethod
    def _clarify_text(c: dict[str, Any]) -> str:
        missing = [PARAM_WORDS.get(m, m) for m in c.get("missing") or []]
        if c.get("trigger") == "params" and missing:
            msg = "I need " + " and ".join(missing) + " to investigate this."
            if "the country" in missing:
                msg += " Countries I cover: " + ", ".join(sorted({x.split(' ')[0] for x in supported_scope()})) + "."
            return msg + " For example: 'Why did Intel's gross margin fall in 2024?' or 'Why did UK inflation spike in 2022?'"
        opts = [INTENT_WORDS.get(o, o) for o in c.get("options") or [] if o != "GENERAL"]
        if opts:
            return "I wasn't sure what you're asking. Did you mean " + ", or ".join(opts) + "?"
        return "Could you rephrase that as a why-question? " + scope_text()

    # -- answer --------------------------------------------------------------------------
    def _compose(self, o: dict[str, Any]) -> dict[str, Any]:
        t = self.trace
        macro = t.plan.get("playbook") == "macro_series"
        sup = sorted([h for h in t.hypotheses if h.verdict == "supported"], key=lambda h: -h.weight)
        con = [h for h in t.hypotheses if h.verdict == "contradicted"]
        inc = [h for h in t.hypotheses if h.verdict == "inconclusive"]
        verb = "rose" if o["actual_dir"] == "up" else "fell"
        summary = f"{o['subject']} {verb} in {o['period']}: {o['move']}."
        # one representative per cause group (correlated drivers are not counted twice)
        reps, seen = [], set()
        for h in sup:
            g = h.group or h.id
            if g not in seen:
                seen.add(g)
                reps.append(h)
        n_groups = len(reps)
        if reps:
            lead = "The evidence is consistent with " + ("these drivers: " if len(reps) > 1 else "one driver: ")
            summary += " " + lead + "; ".join((f"({i + 1}) " if len(reps) > 1 else "") + lower_first(h.statement) for i, h in enumerate(reps[:3])) + "."
            if n_groups == 1:
                summary += " Only one independent driver was supported, so treat this as a partial explanation."
        else:
            summary += " None of the tested drivers is consistent with the move."
        because = []
        for h in sup:
            detail = h.detail
            if h.one_off:
                detail += f" One-off: {h.one_off}"
            if h.context:
                detail += " Context (not tested): " + re.sub(r"^Context:\s*", "", h.context)
            because.append({"claim": h.statement, "detail": detail, "evidence": h.evidence, "hypothesis": h.id, "weight": h.weight,
                            "group": h.group or h.id, **({"context": h.context} if h.context else {}), **({"one_off": h.one_off} if h.one_off else {})})
        counter = []
        if not o["premise_ok"]:
            counter.append({"claim": f"Premise check: the question assumes the metric went {t.plan.get('asked_direction')}, but it {verb}.",
                            "evidence": o["target_fids"][:1]})
        if macro and sup:
            counter.append({"claim": "Correlation is not causation: each supported driver moved the needed way over the same interval and "
                                     "co-moved with the target, which is consistent with it contributing but does not prove it did; "
                                     "component weights are not applied.", "evidence": o["target_fids"][:1]})
        for h in sup:
            if h.one_off:
                counter.append({"claim": f"One-off item: {h.statement}. {h.one_off}", "evidence": h.evidence[:3], "hypothesis": h.id})
        for h in con:
            counter.append({"claim": f"Not supported: {h.statement}. {h.detail}", "evidence": h.counter, "hypothesis": h.id})
        for h in inc:
            if h.counter or h.evidence:
                counter.append({"claim": f"Weak or no support: {h.statement}. {h.detail}", "evidence": h.counter or h.evidence, "hypothesis": h.id})
        rem = next((f for f in t.findings if f.statement.startswith("Bridge:")), None)
        if rem and "unexplained" in rem.statement:
            counter.append({"claim": "Part of the move is not explained by tagged line items.", "evidence": [rem.id]})
        elif rem:
            counter.append({"claim": "Driver sizes overlap: treat each one's contribution as an upper bound.", "evidence": [rem.id]})
        coverage = o.get("coverage", 0.0)
        kind = o.get("coverage_kind", "weight")
        completeness = len([q for q in t.queries if not q.get("error") and q["rows"]]) / max(1, len(t.queries))
        context_hits = sum(1 for h in sup for e in h.evidence if (t.finding(e) and t.finding(e).kind == "context"))
        one_off_top = bool(reps and reps[0].one_off)
        score = confidence_score(coverage, completeness, context_hits, o["premise_ok"], len(con), n_groups,
                                 playbook="macro" if macro else "company", metric=t.plan.get("metric"), one_off_top=one_off_top)
        if kind == "move":
            cov_txt = f"supported line items add up to ~{coverage * 100:.0f}% of the change (bridge)"
        elif kind == "signals":
            cov_txt = f"{coverage * 100:.0f}% of the indirect revenue signals agree"
        else:
            cov_txt = (f"supported drivers carry ~{coverage * 100:.0f}% of the tested evidence weight (de-duplicated by cause; "
                       "this is not a share of the move)")
        rationale = (f"{len(sup)} of {len(t.hypotheses)} hypotheses supported across {n_groups} independent cause group(s); {cov_txt}; "
                     f"{completeness * 100:.0f}% of queries returned data; {context_hits} context corroborations"
                     + ("; premise contradicted" if not o["premise_ok"] else "") + (f"; {len(con)} hypotheses contradicted" if con else "")
                     + ("; only one independent driver, capped at Medium" if n_groups == 1 else "")
                     + ("; largest driver is a one-off item" if one_off_top else "")
                     + (f"; macro answers are capped at {MACRO_CONFIDENCE_CAP:.2f} (no CPI weights applied)" if macro else "") + ".")
        if t.plan.get("playbook") == "company_metric":
            caveats = ["Values are the latest filed (as-restated) figures per period; XBRL tags vary by company and year."]
            if t.plan.get("metric") == "revenue":
                caveats.append("SEC companyfacts has no segment breakdown, so which products or customers drove revenue is not established here; "
                               f"confidence is capped at {REVENUE_CONFIDENCE_CAP:.2f}.")
        else:
            caveats = ["The move is measured from December of the prior year to the in-year extreme; drivers are tested over the same interval.",
                       "Correlation over a short window is evidence of co-movement, not proof of causation; component weights are not applied.",
                       "Context notes are background mechanisms; they are not tested and never supply numbers."]
        caveats.append("Context snippets from Wikipedia corroborate hypotheses but never supply numbers.")
        return {"summary": summary, "because": because, "counter_evidence": counter,
                "inconclusive": [{"claim": h.statement, "detail": h.detail, "hypothesis": h.id} for h in inc],
                "confidence": {"score": score, "label": confidence_label(score), "rationale": rationale,
                               "coverage": round(coverage, 3), "coverage_kind": kind, "independent_drivers": n_groups},
                "caveats": caveats, "target_finding": o["target_finding"], "premise_ok": o["premise_ok"], "actual_direction": o["actual_dir"]}

    def _narrate(self) -> None:
        """Optional: LLM rewrites the evidence as prose. Rejected unless every sentence cites real findings."""
        t = self.trace
        if not self.llm:
            return
        self._enforce("llm.narrate")
        facts = "\n".join(f"[{f.id}] {f.statement}" for f in t.findings if f.kind != "context")[:9000]
        hyps = "\n".join(f"{h.id} {h.verdict.upper()}: {h.statement} - {h.detail} (evidence {', '.join(h.evidence or h.counter)})" for h in t.hypotheses)
        system = ("You are an analyst writing for a busy reader. Using ONLY the findings provided, write 4-6 sentences explaining why. "
                  "Every sentence must end with one or more citations like [F3]. Do not introduce any number that is not in the findings. "
                  "Mention the strongest counter-evidence in one sentence. Plain English, no headings.")
        user = f"Question: {t.question}\n\nFindings:\n{facts}\n\nHypothesis tests:\n{hyps}\n\nDeterministic summary: {t.answer['summary']}"
        try:
            txt = self.llm.chat(system, user, temperature=0.1, max_tokens=500).strip()
        except Exception as e:
            t.llm["error"] = str(e)[:200]
            return
        ok, why = validate_narrative(txt, t)
        t.answer["narrative"] = txt if ok else None
        t.answer["narrative_check"] = {"passed": ok, "reason": why}
        t.step("narrate", f"{t.llm['provider']} narrative {'accepted' if ok else 'rejected'}: {why}")

    # -- memory ------------------------------------------------------------------------------
    def _remember(self, o: dict[str, Any]) -> None:
        t = self.trace
        score = t.answer.get("confidence", {}).get("score", 0)
        if not o.get("premise_ok", True) or score < REMEMBER_MIN_CONFIDENCE:
            why = "the question's premise was wrong" if not o.get("premise_ok", True) else f"confidence {score} < {REMEMBER_MIN_CONFIDENCE}"
            t.step("remember", f"Not written to memory: {why}")
            t.answer["memory_skipped"] = why
            return
        self._enforce("memory.write")
        with self.memory_lock:
            written, conflicts = write_trace_memories(self.memory, t.to_dict(), (self.contract or {}).get("contractId"))
        t.memory_written = written
        t.conflicts.extend(conflicts)
        t.step("remember", f"Wrote {len(written)} memories with provenance; {len(conflicts)} conflicts with prior beliefs")

    def _explain_belief(self, question: str) -> Trace:
        t = self.trace
        self._enforce("memory.recall")
        with self.memory_lock:
            rec = self.memory.query(question, {"threshold": 0.2, "topN": 8})
        items = []
        for m in rec["memories"]:
            mem = m["memory"]
            with self.memory_lock:
                hist = self.memory.ledger.history(mem["id"])
            items.append({"id": mem["id"], "content": mem["content"], "score": m["score"], "confidence": mem["confidence"],
                          "provenance": mem.get("provenance"), "history": [h["action"] for h in hist], "supersededBy": mem.get("supersededBy")})
        t.memory_recall = items
        if items:
            lines = [f"- {i['content']} (confidence {i['confidence']:.2f}; learned via {i['provenance'].get('origin')}: {i['provenance'].get('label')})" for i in items[:6]]
            summary = "Here is what I already believe and why:\n" + "\n".join(lines)
        else:
            summary = "I have no stored findings on that yet. Ask me a why-question first and I will remember the evidence."
        t.answer = {"summary": summary, "memory": items, "confidence": {"score": 0, "label": "n/a", "rationale": "Recall only; no new investigation."}}
        t.step("recall", f"Explained {len(items)} stored beliefs with provenance")
        return t

    def feedback(self, memory_id: str, kind: str) -> dict:
        with self.memory_lock:
            r = self.memory.feedback(memory_id, kind)
            self.memory.save()
        return r

    def _finish(self, t: Trace, t0: float) -> Trace:
        t.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        t.step("done", f"Finished in {time.time() - t0:.1f}s")
        return t


def write_trace_memories(memory: HMEM, t: dict[str, Any], contract_id: str | None = None,
                         source_label: str | None = None) -> tuple[list[str], list[dict]]:
    """Write a trace's data findings (with provenance) and its conclusion to H-MEM. Works on a serialised trace,
    so a saved example run can seed memory exactly as a live run would."""
    written: list[str] = []
    ids_by_f: dict[str, str] = {}
    backend = t.get("backend")
    for f in t.get("findings", []):
        if f["kind"] == "context" or not f.get("query_id") and not f.get("fact_key"):
            continue
        q = next((q for q in t.get("queries", []) if q["query_id"] == f.get("query_id")), None)
        rec = memory.add({
            "content": f["statement"], "type": "factual", "layer": "working",
            "source": f"harness:{backend}", "confidence": 0.9 if f["kind"] == "data" else 0.8,
            "tags": ["finding", f"kind:{f['kind']}", f"q:{t['question'][:60]}"] + ([f"seed:{source_label}"] if source_label else []),
            "factKey": f.get("fact_key"), "value": f.get("value"),
            "provenance": {"origin": "harness_query", "label": f"{f['id']} of '{t['question']}'",
                           "refs": {"query_id": f.get("query_id"), "rows": f.get("rows"), "source_url": f.get("source_url"),
                                    "sql": q["sql"] if q else None, "backend": backend}},
        })
        ids_by_f[f["id"]] = rec["id"]
        written.append(rec["id"])
    a = t["answer"]
    conclusion = memory.add({
        "content": f"Q: {t['question']} A: {a['summary']} (confidence {a['confidence']['label']} {a['confidence']['score']})",
        "type": "factual", "layer": "long_term", "source": "because:analysis",
        "confidence": a["confidence"]["score"], "tags": ["conclusion"] + ([f"seed:{source_label}"] if source_label else []),
        "derivedFrom": [ids_by_f[e] for b in a.get("because", []) for e in b["evidence"] if e in ids_by_f][:20],
        "provenance": {"origin": "model_inference", "label": f"conclusion for '{t['question']}'",
                       "refs": {"contract": contract_id, **({"seeded_from": source_label} if source_label else {})}},
    })
    written.append(conclusion["id"])
    conflicts = []
    for c in memory.conflicts.scan():
        conflicts.append({**c, "outcome": memory.conflicts.autoResolve(c)})
    memory.save()
    return written, conflicts


def seed_memory_from_trace(memory: HMEM, trace: dict[str, Any], label: str, lock: Any = None) -> int:
    """Preload memories from a saved run (e.g. the public demo's Intel example) unless already present.
    Returns how many memories were written (0 if already seeded or the run is not memory-worthy)."""
    a = trace.get("answer") or {}
    if not a.get("confidence") or a.get("blocked") or a.get("memory_skipped") or not trace.get("findings"):
        return 0
    if not a.get("premise_ok", True) or a["confidence"].get("score", 0) < REMEMBER_MIN_CONFIDENCE:
        return 0
    lock = lock or threading.RLock()
    with lock:
        if any(f"seed:{label}" in (r.get("tags") or []) for r in memory.store.all()):
            return 0
        written, _ = write_trace_memories(memory, trace, (trace.get("scout") or {}).get("contract", {}) and
                                          (trace["scout"].get("contract") or {}).get("contractId"), source_label=label)
    return len(written)


_END = r"(?:(?![\w])|(?=(?:[BMKkx]|bn|pp|pts?)\b))"
_NUM = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?" + _END + r"|(?<![\w.,])\d+(?:\.\d+)?" + _END)


def number_tokens(text: str) -> list[str]:
    """Whole numbers as tokens: '11.1' is one token (never matches '1' or '11'); '1,234' -> '1234'.
    Tokens inside identifiers (F12, Q-7278ba1cb2, CIK0000050863, D7G7) are ignored."""
    out = []
    for m in _NUM.finditer(text):
        n = m.group(0).replace(",", "")
        if n.endswith("."):
            n = n[:-1]
        out.append(n)
    return out


def validate_narrative(text: str, t: Trace) -> tuple[bool, str]:
    ids = {f.id for f in t.findings}
    cited = set(re.findall(r"\[(F\d+)\]", text)) | {x.strip() for grp in re.findall(r"\[((?:F\d+\s*,\s*)+F\d+)\]", text) for x in grp.split(",")}
    if not cited:
        return False, "no citations"
    if cited - ids:
        return False, f"cites unknown findings {sorted(cited - ids)}"
    sents = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if len(s) > 20]
    uncited = [s for s in sents if not re.search(r"\[F\d+", s)]
    if uncited:
        return False, f"{len(uncited)} sentence(s) without a citation"
    corpus = " ".join(f.statement for f in t.findings) + " " + t.answer.get("summary", "")
    known = set(number_tokens(corpus))
    nums = number_tokens(re.sub(r"\[F\d+(?:\s*,\s*F\d+)*\]", "", text))
    unknown = [n for n in nums if n not in known and len(n) > 1]
    if unknown:
        return False, f"numbers not found in evidence: {unknown[:5]}"
    return True, f"{len(cited)} findings cited, all numbers grounded"
