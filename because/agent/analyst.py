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
import time
from pathlib import Path
from typing import Any

from because.agent.common import clamp, confidence_label
from because.agent.company import CompanyPlaybook
from because.agent.macro import MacroPlaybook
from because.agent.parse import llm_parse, parse_question
from because.agent.trace import Trace
from because.harness import DataHarness, get_harness
from because.harness.base import QueryResult, SourceSpec
from because.llm import get_llm
from because.meterless.hmem import HMEM
from because.meterless.scout import ContractRefused, Scout
from because.research.background import gdelt, sentences_mentioning, wikipedia

HERE = Path(__file__).parent


def _home() -> Path:
    p = Path(os.environ.get("BECAUSE_HOME", ".because"))
    p.mkdir(parents=True, exist_ok=True)
    return p


class Analyst:
    def __init__(self, harness: DataHarness | None = None, memory: HMEM | None = None, llm: Any = "auto",
                 background: bool = True, scout: Scout | None = None) -> None:
        self.harness = harness or get_harness()
        self.memory = memory if memory is not None else HMEM(path=_home() / "memory.json")
        self.llm = get_llm() if llm == "auto" else llm
        self.use_background = background and os.environ.get("BECAUSE_BACKGROUND", "1") != "0"
        self.scout = scout or Scout(intentRegistry=str(HERE / "intents.json"), policyPack="because-default")
        self.trace: Trace = Trace(question="")
        self.contract: dict | None = None

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
        if decision.get("clarification"):
            c = decision["clarification"]
            hint = ""
            if c.get("missing"):
                hint = " Try naming " + " and ".join(c["missing"]) + ", e.g. 'Why did Intel's gross margin fall in 2024?' or 'Why did UK inflation spike in 2022?'"
            t.answer = {"summary": c["question"] + hint, "clarification": c,
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

    # -- answer --------------------------------------------------------------------------
    def _compose(self, o: dict[str, Any]) -> dict[str, Any]:
        t = self.trace
        sup = sorted([h for h in t.hypotheses if h.verdict == "supported"], key=lambda h: -h.weight)
        con = [h for h in t.hypotheses if h.verdict == "contradicted"]
        inc = [h for h in t.hypotheses if h.verdict == "inconclusive"]
        verb = "rose" if o["actual_dir"] == "up" else "fell"
        summary = f"{o['subject']} {verb} in {o['period']}: {o['move']}."
        if sup:
            summary += " The evidence points mainly to " + "; ".join(f"({i + 1}) {h.statement[0].lower() + h.statement[1:]}" for i, h in enumerate(sup[:3])) + "."
        else:
            summary += " None of the tested drivers clearly explains it."
        because = [{"claim": h.statement, "detail": h.detail, "evidence": h.evidence, "hypothesis": h.id, "weight": h.weight} for h in sup]
        counter = []
        if not o["premise_ok"]:
            counter.append({"claim": f"Premise check: the question assumes the metric went {t.plan.get('asked_direction')}, but it {verb}.",
                            "evidence": o["target_fids"][:1]})
        for h in con:
            counter.append({"claim": f"Not supported: {h.statement}. {h.detail}", "evidence": h.counter, "hypothesis": h.id})
        if getattr(self, "_last_remainder", None) is not None:
            pass
        for h in inc:
            if h.counter or h.evidence:
                counter.append({"claim": f"Weak or no support: {h.statement}. {h.detail}", "evidence": h.counter or h.evidence, "hypothesis": h.id})
        rem = next((f for f in t.findings if f.statement.startswith("Bridge:")), None)
        if rem and "unexplained" in rem.statement:
            counter.append({"claim": "Part of the move is not explained by tagged line items.", "evidence": [rem.id]})
        elif rem:
            counter.append({"claim": "Driver sizes overlap: treat each one's contribution as an upper bound.", "evidence": [rem.id]})
        coverage = o.get("coverage", 0.0)
        data_q = [q for q in t.queries if not q.get("error")]
        completeness = len([q for q in data_q if q["rows"]]) / max(1, len(t.queries))
        context_hits = sum(1 for h in sup for e in h.evidence if (t.finding(e) and t.finding(e).kind == "context"))
        score = 0.25 + 0.45 * coverage + 0.15 * completeness + (0.1 if context_hits else 0.0) - (0.1 if not o["premise_ok"] else 0) - 0.04 * min(len(con), 3)
        if t.plan.get("playbook") == "company_metric" and t.plan.get("metric") == "revenue":
            score = min(score, 0.7)  # revenue hypotheses are indirect without segment data
        score = round(clamp(score, 0.05, 0.95), 2)
        rationale = (f"{len(sup)} of {len(t.hypotheses)} hypotheses supported; supported drivers cover ~{coverage * 100:.0f}% of the move; "
                     f"{completeness * 100:.0f}% of queries returned data; {context_hits} context corroborations"
                     + ("; premise contradicted" if not o["premise_ok"] else "") + (f"; {len(con)} hypotheses contradicted" if con else "") + ".")
        caveats = ["Values are the latest filed (as-restated) figures per period; XBRL tags vary by company and year."] + \
                  (["SEC companyfacts has no segment breakdown, so which products or customers drove revenue is not established here; "
                    "confidence is capped at 0.70."] if t.plan.get("metric") == "revenue" else []) if t.plan.get("playbook") == "company_metric" else \
                  ["Correlation over a short window is evidence of co-movement, not proof of causation; component weights are not applied."]
        caveats.append("Context snippets from Wikipedia corroborate hypotheses but never supply numbers.")
        return {"summary": summary, "because": because, "counter_evidence": counter,
                "inconclusive": [{"claim": h.statement, "detail": h.detail, "hypothesis": h.id} for h in inc],
                "confidence": {"score": score, "label": confidence_label(score), "rationale": rationale}, "caveats": caveats,
                "target_finding": o["target_finding"]}

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
        self._enforce("memory.write")
        written = []
        ids_by_f = {}
        for f in t.findings:
            if f.kind == "context" or not f.query_id and not f.fact_key:
                continue
            q = next((q for q in t.queries if q["query_id"] == f.query_id), None)
            rec = self.memory.add({
                "content": f.statement, "type": "factual", "layer": "working",
                "source": f"harness:{t.backend}", "confidence": 0.9 if f.kind == "data" else 0.8,
                "tags": ["finding", f"kind:{f.kind}", f"q:{t.question[:60]}"], "factKey": f.fact_key, "value": f.value,
                "provenance": {"origin": "harness_query", "label": f"{f.id} of '{t.question}'",
                               "refs": {"query_id": f.query_id, "rows": f.rows, "source_url": f.source_url,
                                        "sql": q["sql"] if q else None, "backend": t.backend}},
            })
            ids_by_f[f.id] = rec["id"]
            written.append(rec["id"])
        a = t.answer
        conclusion = self.memory.add({
            "content": f"Q: {t.question} A: {a['summary']} (confidence {a['confidence']['label']} {a['confidence']['score']})",
            "type": "factual", "layer": "long_term", "source": "because:analysis",
            "confidence": a["confidence"]["score"], "tags": ["conclusion"],
            "derivedFrom": [ids_by_f[e] for b in a.get("because", []) for e in b["evidence"] if e in ids_by_f][:20],
            "provenance": {"origin": "model_inference", "label": f"conclusion for '{t.question}'", "refs": {"contract": (self.contract or {}).get("contractId")}},
        })
        written.append(conclusion["id"])
        t.memory_written = written
        found = self.memory.conflicts.scan()
        for c in found:
            outcome = self.memory.conflicts.autoResolve(c)
            t.conflicts.append({**c, "outcome": outcome})
        self.memory.save()
        t.step("remember", f"Wrote {len(written)} memories with provenance; {len(found)} conflicts with prior beliefs")

    def _explain_belief(self, question: str) -> Trace:
        t = self.trace
        self._enforce("memory.recall")
        rec = self.memory.query(question, {"threshold": 0.2, "topN": 8})
        items = []
        for m in rec["memories"]:
            mem = m["memory"]
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
        r = self.memory.feedback(memory_id, kind)
        self.memory.save()
        return r

    def _finish(self, t: Trace, t0: float) -> Trace:
        t.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        t.step("done", f"Finished in {time.time() - t0:.1f}s")
        return t


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
    nums = re.findall(r"\d+(?:\.\d+)?", re.sub(r"\[F\d+(?:,\s*F\d+)*\]", "", text))
    unknown = [n for n in nums if n not in corpus and len(n) > 1]
    if unknown:
        return False, f"numbers not found in evidence: {unknown[:5]}"
    return True, f"{len(cited)} findings cited, all numbers grounded"
