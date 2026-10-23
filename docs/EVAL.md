# Evaluating Because

`scripts/eval.py` asks Because 19 "why" questions and scores each answer against a reference answer from a
public source: 10-K MD&A or an earnings release for companies, ONS and BLS releases for macro. The results are
committed in [`examples/eval/`](../examples/eval/), failures included.

```bash
SAMPLE_MODE=1 BECAUSE_USER_AGENT="because-agent you@example.com" python scripts/eval.py      # run everything (about 1 minute warm, a few cold)
python scripts/eval.py --only uk-cpi-2023 tsla-ni-2024                                         # run a subset
python scripts/eval.py --report                                                                # re-score saved runs
```

## Headline (DuckDB backend, live public endpoints, Wikipedia context off, no LLM)

| Metric | Result |
|---|---|
| Direction measured correctly | **19/19** (both false-premise traps flagged) |
| Reference driver found among *supported* hypotheses | **16/19** (showcase questions: 4/4) |
| Mean confidence, driver found | **0.71** |
| Mean confidence, driver missed | **0.52** |
| Brier score (confidence vs driver found) | 0.124 |

Full table: [`examples/eval/results.md`](../examples/eval/results.md). Raw per-question records:
`examples/eval/raw/*.json`. Questions, reference answers and match patterns: `examples/eval/questions.json`
(generated from `QUESTIONS` in `scripts/eval.py`).

## What is scored

* **Direction (premise).** The direction Because measured (start of year to in-year extreme for macro; FY-1 to FY
  for companies) must equal the reference direction. Two questions are traps with a false premise ("Why did UK
  inflation *rise* in 2023?", "Why did Nvidia's revenue *fall* in fiscal 2024?"). Because has to measure the true
  direction and flag the premise. It does not explain the false one.
* **Driver.** At least one reference driver has to appear among the hypotheses Because marked **supported**. It is
  matched by a keyword pattern on the hypothesis statement, and the pattern for each question is in the file.
  Inconclusive or contradicted hypotheses do not count.
* **Calibration.** Mean confidence on hits vs misses, plus a Brier score. A calibrated system is more confident
  when it is right.

## Failures (committed as-is)

| id | Why it fails | What it would take |
|---|---|---|
| `tsla-gm-2023` | The reference cause is **price cuts** (lower average selling price). No price or ASP series is available from XBRL companyfacts, so no pricing hypothesis is tested. Because answered at Low (0.34), which is the right confidence for a miss. | Segment or unit data (deliveries, ASP), e.g. from 10-K text or Tesla's delivery releases |
| `aapl-rev-fy23` | The reference cause is lower **Mac/iPad** sales plus a **strong dollar**. Without segment or FX data, Because offered generic signals ("inventory piled up as demand fell", operating leverage) at Medium (0.66). Overconfident for a miss. | Segment revenue (10-K segment note) and an FX series |
| `intc-rev-2023` | The reference cause is weak **PC/server demand**. The demand-led test (inventory vs revenue) was inconclusive because Intel cut inventory, and only "pricing pressure" was supported. Medium (0.55). | Segment revenue; a customer-inventory proxy |

All three misses are revenue or price questions on companies, where companyfacts has no segment, unit or price
data. Macro questions scored 8/8 on the driver metric (including the UK trap).

## Honest caveats

* **Lenient matches.** `nvda-rev-fy24` counts "demand outstripped supply" as a hit for the reference "Data Center
  demand for Hopper GPUs". The direction is right, but Because cannot name the segment. `msft-ni-fy23` is a hit on
  "R&D expense rose". Microsoft's FY2023 drop was small (-1%), and Because's other supported driver (higher tax)
  is real but is not the reason the reference emphasises.
* **The single-driver cap also hits correct single-cause answers.** `nvda-ni-fy24` is a correct hit (gross profit
  rose), but gross profit and revenue share one cause group, so confidence is capped at 0.55. This is by design (see
  below). It lowers the hit-side calibration number.
* **Trap answers keep a fairly high confidence (0.70 to 0.73)**, because the measured move and its drivers are
  right. The premise flag shows as counter-evidence and costs 0.10. These answers are **not** written to memory.
* **Small sample.** 19 questions is a sanity check, not a benchmark. The references are short summaries by the eval
  author of the linked filing or release. The SEC links go to the company's 10-K filing list on EDGAR.
* With Wikipedia context on (`--background`), macro answers can gain up to +0.10 confidence from corroboration,
  still capped at 0.85.

## How confidence is computed (since this eval)

`because/agent/analyst.py: confidence_score()` combines these terms. The tests check that it is monotone: it never
falls as coverage, data completeness, context or independent drivers increase, and never rises with
contradictions or a failed premise.

* `0.25 + 0.45 x coverage + 0.15 x completeness + 0.10 if context corroborates - 0.10 if the premise failed - 0.04 per contradicted hypothesis (max 3)`
* **Coverage is labelled honestly.** For margin and profit questions it is the share of the change that the
  supported line items add up to (a bridge). For macro questions it is the share of tested evidence weight carried
  by supported drivers, de-duplicated by cause group (gas, oil and household energy count as one). It is never
  presented as a share of the move. For revenue questions it is the share of indirect signals that agree.
* **Caps:** macro 0.85 (CPI component weights are not applied); company revenue 0.70 (no segment data); one
  independent driver 0.55; no supported driver 0.35; largest driver is a one-off item 0.65. One-off items are
  prior-year tax benefits, tax charged on an operating loss, and single-year charges above 2.5 times the
  prior-years average. They are flagged in the answer.
