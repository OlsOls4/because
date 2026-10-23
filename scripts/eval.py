#!/usr/bin/env python
"""Evaluation: does Because get the direction right, find the reference driver, and is its confidence calibrated?

    SAMPLE_MODE=1 BECAUSE_USER_AGENT="because-agent you@example.com" python scripts/eval.py            # run all, then report
    python scripts/eval.py --only uk-cpi-2023 tsla-ni-2024                                              # run a subset
    python scripts/eval.py --report                                                                     # re-aggregate saved results

Each question has a reference answer summarised from a public source (10-K MD&A / earnings release, ONS / BLS
release). Scores, per question:
  * premise   - the direction Because measured equals the reference direction (catches false premises too)
  * driver    - a reference driver appears among Because's SUPPORTED hypotheses (keyword match, listed per question)
  * calibration - mean confidence of driver hits vs misses (a calibrated system is more confident when it is right)

Results: examples/eval/raw/<id>.json (one per question), examples/eval/results.json, examples/eval/results.md.
Background (Wikipedia) context is OFF by default so runs are reproducible; --background turns it on (it can add
up to +0.10 confidence through context corroboration).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples" / "eval"
EDGAR = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={t}&type=10-K&dateb=&owner=include&count=40"

QUESTIONS: list[dict] = [
    # --- the four showcase questions -------------------------------------------------------------------
    {"id": "intc-gm-2024", "question": "Why did Intel's gross margin fall in 2024?", "direction": "down", "showcase": True,
     "drivers": [r"restructuring|impairment", r"depreciation", r"inventory"],
     "reference": "Gross margin fell to 32.7% on large restructuring/impairment and accelerated-depreciation charges (Intel 7 assets), "
                  "higher inventory reserves and higher unit costs from new process nodes (Intel 2024 10-K, MD&A).",
     "source": EDGAR.format(t="INTC")},
    {"id": "uk-cpi-2022", "question": "Why did UK inflation spike in 2022?", "direction": "up", "showcase": True,
     "drivers": [r"household energy|gas prices", r"food"],
     "reference": "CPI rose to 11.1% in October 2022, the largest upward contributions from housing and household services "
                  "(electricity and gas) and food (ONS, Consumer price inflation, October 2022).",
     "source": "https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation/october2022"},
    {"id": "nvda-rev-fy24", "question": "Why did Nvidia's revenue jump in fiscal 2024?", "direction": "up", "showcase": True,
     "drivers": [r"demand"],
     "reference": "Revenue rose 126% to $60.9B, led by Data Center (+217%) on demand for Hopper GPUs for large language models "
                  "(NVIDIA FY2024 10-K). Because has no segment data, so only 'demand-led' is checkable.",
     "source": EDGAR.format(t="NVDA")},
    {"id": "us-unemp-2020", "question": "Why did US unemployment spike in 2020?", "direction": "up", "showcase": True,
     "drivers": [r"leisure", r"jobless claims", r"payroll"],
     "reference": "Unemployment jumped to 14.7% in April 2020 as COVID-19 shutdowns cut 20.5M payroll jobs, with leisure and "
                  "hospitality down 7.7M (BLS Employment Situation, April 2020).",
     "source": "https://www.bls.gov/news.release/archives/empsit_05082020.htm"},
    # --- companies ---------------------------------------------------------------------------------------
    {"id": "meta-ni-2022", "question": "Why did Meta's net income drop in 2022?", "direction": "down",
     "drivers": [r"r&d", r"restructuring", r"sg&a", r"gross profit fell|revenue fell"],
     "reference": "Net income fell 41%: revenue declined 1% while costs and expenses rose 22% (Reality Labs R&D, headcount, "
                  "$4.6B restructuring charges) (Meta Q4/FY2022 results; 2022 10-K).",
     "source": EDGAR.format(t="META")},
    {"id": "tsla-gm-2023", "question": "Why did Tesla's gross margin fall in 2023?", "direction": "down",
     "drivers": [r"pric"],
     "reference": "Automotive gross margin fell mainly on lower average selling prices after price reductions across models "
                  "(Tesla 2023 10-K, MD&A).",
     "source": EDGAR.format(t="TSLA")},
    {"id": "aapl-rev-fy23", "question": "Why did Apple's revenue decline in fiscal 2023?", "direction": "down",
     "drivers": [r"\bmac\b|ipad|currenc|exchange"],
     "reference": "Net sales fell 3% on lower Mac and iPad sales and a stronger US dollar, partly offset by Services "
                  "(Apple FY2023 10-K, MD&A).",
     "source": EDGAR.format(t="AAPL")},
    {"id": "msft-ni-fy23", "question": "Why did Microsoft's net income fall in fiscal 2023?", "direction": "down",
     "drivers": [r"r&d|sg&a|restructuring|impairment"],
     "reference": "Net income slipped 1% as operating expenses grew (R&D, plus a Q2 charge for severance, hardware portfolio "
                  "changes and lease consolidation) faster than gross margin (Microsoft FY2023 10-K).",
     "source": EDGAR.format(t="MSFT")},
    {"id": "nvda-ni-fy24", "question": "Why did Nvidia's net income jump in fiscal 2024?", "direction": "up",
     "drivers": [r"gross profit|revenue"],
     "reference": "Net income rose to $29.8B from $4.4B on revenue growth and gross margin expansion to 72.7% (NVIDIA FY2024 10-K).",
     "source": EDGAR.format(t="NVDA")},
    {"id": "tsla-ni-2024", "question": "Why did Tesla's net income fall in 2024?", "direction": "down",
     "drivers": [r"income tax"],
     "reference": "Net income fell 53% mainly because 2023 included a one-time $5.9B non-cash tax benefit from releasing a "
                  "valuation allowance; operating income also fell on lower vehicle prices (Tesla 2024 10-K).",
     "source": EDGAR.format(t="TSLA")},
    {"id": "intc-ni-2024", "question": "Why did Intel's net income fall in 2024?", "direction": "down",
     "drivers": [r"income tax", r"restructuring|impairment", r"gross profit"],
     "reference": "Net loss of $18.8B: a non-cash charge to establish a valuation allowance on US deferred tax assets, "
                  "restructuring and impairment charges, and lower gross margin (Intel 2024 10-K).",
     "source": EDGAR.format(t="INTC")},
    {"id": "intc-rev-2023", "question": "Why did Intel's revenue fall in 2023?", "direction": "down",
     "drivers": [r"demand"],
     "reference": "Revenue fell 14% on weaker PC and server demand (customer inventory digestion) across CCG and DCAI "
                  "(Intel 2023 10-K).",
     "source": EDGAR.format(t="INTC")},
    # --- macro -------------------------------------------------------------------------------------------
    {"id": "uk-cpi-2023", "question": "Why did UK inflation fall in 2023?", "direction": "down",
     "drivers": [r"household energy|gas prices", r"transport|oil", r"food"],
     "reference": "CPI fell from 10.5% (Dec 2022) to 3.9% (Nov 2023); housing and household services (energy) inflation turned "
                  "negative, motor fuel prices fell and food inflation eased from 19.2% in March (ONS, Consumer price inflation, November 2023).",
     "source": "https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation/november2023"},
    {"id": "us-cpi-2021", "question": "Why did US inflation rise in 2021?", "direction": "up",
     "drivers": [r"energy", r"used-car", r"oil"],
     "reference": "CPI-U rose 7.0% over 2021; energy +29.3% and used cars and trucks +37.3% were among the largest increases "
                  "(BLS CPI, December 2021).",
     "source": "https://www.bls.gov/news.release/archives/cpi_01122022.htm"},
    {"id": "us-cpi-2023", "question": "Why did US inflation fall in 2023?", "direction": "down",
     "drivers": [r"energy|oil"],
     "reference": "CPI-U inflation fell from 6.5% (Dec 2022) to 3.4% (Dec 2023); the energy index declined over the year "
                  "(BLS CPI, December 2023).",
     "source": "https://www.bls.gov/news.release/archives/cpi_01112024.htm"},
    {"id": "uk-unemp-2009", "question": "Why did UK unemployment rise in 2009?", "direction": "up",
     "drivers": [r"industrial production"],
     "reference": "Unemployment rose to about 8% during the 2008-09 recession that followed the global financial crisis, as "
                  "output fell (ONS labour market statistics; GDP fell about 4% in 2009).",
     "source": "https://www.ons.gov.uk/employmentandlabourmarket/peoplenotinwork/unemployment/timeseries/mgsx/lms"},
    {"id": "us-unemp-2009", "question": "Why did US unemployment rise in 2009?", "direction": "up",
     "drivers": [r"construction|manufacturing", r"payroll|jobless claims", r"industrial production"],
     "reference": "Unemployment rose to 10.2% in October 2009 as first published (10.0% after revisions), with payrolls still "
                  "falling and the largest job losses in construction, manufacturing and retail (BLS Employment Situation, October 2009).",
     "source": "https://www.bls.gov/news.release/archives/empsit_11062009.htm"},
    # --- false-premise traps -----------------------------------------------------------------------------
    {"id": "trap-uk-cpi-2023-rise", "question": "Why did UK inflation rise in 2023?", "direction": "down", "trap": True,
     "drivers": [r"household energy|gas prices", r"transport|oil", r"food"],
     "reference": "False premise: UK CPI inflation fell during 2023 (ONS).",
     "source": "https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation/november2023"},
    {"id": "trap-nvda-rev-fy24-fall", "question": "Why did Nvidia's revenue fall in fiscal 2024?", "direction": "up", "trap": True,
     "drivers": [r"demand"],
     "reference": "False premise: NVIDIA revenue rose 126% in FY2024 (10-K).",
     "source": EDGAR.format(t="NVDA")},
]


def run_one(q: dict, analyst) -> dict:
    t0 = time.time()
    t = analyst.ask(q["question"]).to_dict()
    a = t.get("answer", {})
    sup = [h for h in t.get("hypotheses", []) if h["verdict"] == "supported"]
    hits = [p for p in q["drivers"] if any(re.search(p, h["statement"], re.I) for h in sup)]
    conf = a.get("confidence", {})
    actual = a.get("actual_direction")
    return {
        "id": q["id"], "question": q["question"], "trap": bool(q.get("trap")), "showcase": bool(q.get("showcase")),
        "reference_direction": q["direction"], "measured_direction": actual,
        "premise_correct": actual == q["direction"],
        "premise_flagged": a.get("premise_ok") is False,
        "driver_hit": bool(hits), "drivers_matched": hits, "driver_patterns": q["drivers"],
        "confidence": conf.get("score"), "confidence_label": conf.get("label"),
        "summary": a.get("summary"), "supported": [h["statement"] for h in sup],
        "verdicts": {h["id"]: [h["verdict"], h["statement"]] for h in t.get("hypotheses", [])},
        "reference": q["reference"], "source": q["source"], "backend": t.get("backend"),
        "error": None if a.get("confidence", {}).get("label") != "n/a" else a.get("summary"),
        "seconds": round(time.time() - t0, 1),
    }


def report() -> dict:
    rows = []
    for q in QUESTIONS:
        p = OUT / "raw" / f"{q['id']}.json"
        if p.exists():
            r = json.loads(p.read_text())
            # references and driver patterns come from QUESTIONS (re-scored from the saved supported hypotheses)
            r.update(reference=q["reference"], source=q["source"], driver_patterns=q["drivers"],
                     reference_direction=q["direction"], trap=bool(q.get("trap")), showcase=bool(q.get("showcase")))
            r["drivers_matched"] = [pt for pt in q["drivers"] if any(re.search(pt, s, re.I) for s in r.get("supported", []))]
            r["driver_hit"] = bool(r["drivers_matched"])
            r["premise_correct"] = r.get("measured_direction") == q["direction"]
            rows.append(r)
    n = len(rows)
    ok = [r for r in rows if not r["error"]]
    hits = [r for r in ok if r["driver_hit"]]
    misses = [r for r in ok if not r["driver_hit"]]
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    brier = mean([((r["confidence"] or 0) - (1.0 if r["driver_hit"] else 0.0)) ** 2 for r in ok])
    traps = [r for r in rows if r["trap"]]
    summary = {
        "questions": n, "answered": len(ok), "errors": [r["id"] for r in rows if r["error"]],
        "premise_correct": sum(r["premise_correct"] for r in rows), "driver_hits": len(hits),
        "traps_flagged": sum(r["premise_flagged"] for r in traps), "traps": len(traps),
        "mean_confidence_hits": mean([r["confidence"] for r in hits]), "mean_confidence_misses": mean([r["confidence"] for r in misses]),
        "brier_driver": brier, "showcase_driver_hits": sum(r["driver_hit"] for r in rows if r["showcase"]),
        "failures": [{"id": r["id"], "why": ("wrong direction" if not r["premise_correct"] else "") +
                      ("; " if not r["premise_correct"] and not r["driver_hit"] else "") +
                      ("reference driver not among supported hypotheses" if not r["driver_hit"] else "")}
                     for r in rows if not (r["premise_correct"] and r["driver_hit"])],
    }
    (OUT / "results.json").write_text(json.dumps({"summary": summary, "results": rows}, indent=2))
    md = ["# Because evaluation results", "",
          f"Generated by `scripts/eval.py` ({rows[0]['backend'] if rows else '-'} backend, background context off). "
          "See [docs/EVAL.md](../../docs/EVAL.md) for the method.", "",
          f"- **Direction correct:** {summary['premise_correct']}/{n} (false-premise traps flagged: {summary['traps_flagged']}/{summary['traps']})",
          f"- **Reference driver found among supported hypotheses:** {summary['driver_hits']}/{len(ok)}",
          f"- **Calibration:** mean confidence when the driver was found {summary['mean_confidence_hits']}, "
          f"when it was missed {summary['mean_confidence_misses']}; Brier score vs driver hit {summary['brier_driver']}",
          f"- **Errors:** {', '.join(summary['errors']) or 'none'}", "",
          "| id | question | direction (ref / measured) | driver found | confidence | supported hypotheses |", "|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['id']}{' (trap)' if r['trap'] else ''} | {r['question']} | {r['reference_direction']} / {r['measured_direction']}"
                  f"{' ✓' if r['premise_correct'] else ' ✗'} | {'✓ ' + ', '.join(r['drivers_matched']) if r['driver_hit'] else '✗'} | "
                  f"{r['confidence_label']} ({r['confidence']}) | {'; '.join(r['supported'][:4]) or '-'} |")
    md += ["", "## Failures (committed as-is)", ""]
    for f in summary["failures"]:
        r = next(x for x in rows if x["id"] == f["id"])
        md.append(f"- **{f['id']}** - {f['why']}. Reference: {r['reference']} ([source]({r['source']})). Because said: {r['summary']}")
    md += ["", "## References", ""] + [f"- **{r['id']}**: {r['reference']} [source]({r['source']})" for r in rows]
    (OUT / "results.md").write_text("\n".join(md) + "\n")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="question ids to run")
    ap.add_argument("--report", action="store_true", help="only aggregate saved results")
    ap.add_argument("--background", action="store_true", help="enable Wikipedia context (off by default)")
    ap.add_argument("--skip-existing", action="store_true")
    a = ap.parse_args()
    (OUT / "raw").mkdir(parents=True, exist_ok=True)
    (OUT / "questions.json").write_text(json.dumps(QUESTIONS, indent=2))
    if not a.report:
        sys.path.insert(0, str(ROOT))
        from because.agent import Analyst
        from because.harness import get_harness
        from because.meterless.hmem import HMEM

        analyst = Analyst(harness=get_harness(os.environ.get("BECAUSE_BACKEND") if not os.environ.get("SAMPLE_MODE") else "duckdb"),
                          memory=HMEM(path=Path(tempfile.mkdtemp()) / "eval-mem.json"), llm=None, background=a.background)
        for q in QUESTIONS:
            if a.only and q["id"] not in a.only:
                continue
            p = OUT / "raw" / f"{q['id']}.json"
            if a.skip_existing and p.exists():
                continue
            print(f"... {q['id']}: {q['question']}", file=sys.stderr)
            try:
                r = run_one(q, analyst)
            except Exception as e:  # record, never hide
                r = {"id": q["id"], "question": q["question"], "error": f"{type(e).__name__}: {e}", "trap": bool(q.get("trap")),
                     "showcase": bool(q.get("showcase")), "reference_direction": q["direction"], "measured_direction": None,
                     "premise_correct": False, "premise_flagged": False, "driver_hit": False, "drivers_matched": [],
                     "driver_patterns": q["drivers"], "confidence": None, "confidence_label": None, "summary": None, "supported": [],
                     "verdicts": {}, "reference": q["reference"], "source": q["source"], "backend": None}
            p.write_text(json.dumps(r, indent=2))
    s = report()
    print(json.dumps({k: v for k, v in s.items() if k != "failures"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
