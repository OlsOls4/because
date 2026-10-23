# Why did US unemployment spike in 2020?

_Backend: **duckdb** · LLM: none · started 2026-10-23T09:47:22Z_

## Answer

US unemployment rose in 2020: 3.6% (2019-12) -> 14.8% (2020-04 peak), +11.2 pts; 2018 average 3.9%. The evidence is consistent with these drivers: (1) leisure & hospitality employment fell during the run-up to the 2020 peak; (2) initial jobless claims rose during the run-up to the 2020 peak; (3) industrial production fell during the run-up to the 2020 peak.

**Confidence: High (0.85)** - 6 of 7 hypotheses supported across 5 independent cause group(s); supported drivers carry ~100% of the tested evidence weight (de-duplicated by cause; this is not a share of the move); 100% of queries returned data; 12 context corroborations; macro answers are capped at 0.85 (no CPI weights applied).

## Because

- **Leisure & hospitality employment fell during the run-up to the 2020 peak** - Changed -48.1% over the same interval, the direction needed, and co-moved with the target (r=-1.00). Co-movement, not proof of cause. Context (not tested): in 2020 COVID-19 shutdowns closed restaurants, hotels and venues. [F4]
- **Initial jobless claims rose during the run-up to the 2020 peak** - Changed +2,110,850 over the same interval (its best lead, 1 month earlier), the direction needed, and co-moved with the target (r=+0.99). Co-movement, not proof of cause. Context (not tested): Claims count new layoffs filing for benefits. [F2, F9, F10, F11, F12]
- **Total payroll employment fell during the run-up to the 2020 peak** - Changed -14.1% over the same interval, the direction needed, and co-moved with the target (r=-0.91). Co-movement, not proof of cause. [F3, F9, F10, F11, F12]
- **Industrial production fell during the run-up to the 2020 peak** - Changed -16.9% over the same interval, the direction needed, and co-moved with the target (r=-0.91). Co-movement, not proof of cause. [F7, F9, F10, F11, F12]
- **Manufacturing employment fell during the run-up to the 2020 peak** - Changed -10.8% over the same interval, the direction needed, and co-moved with the target (r=-0.87). Co-movement, not proof of cause. [F6]
- **Construction employment fell during the run-up to the 2020 peak** - Changed -13.4% over the same interval, the direction needed, and co-moved with the target (r=-0.59). Co-movement, not proof of cause. Context (not tested): Construction is cyclical and sensitive to housing demand and credit. [F5]

## Counter-evidence and limits

- Correlation is not causation: each supported driver moved the needed way over the same interval and co-moved with the target, which is consistent with it contributing but does not prove it did; component weights are not applied. [F1]
- Weak or no support: The federal funds rate rose during the run-up to the 2020 peak. Change of -0.0 pts is below the materiality threshold (0.25 pts). [F8]

## Caveats

- The move is measured from December of the prior year to the in-year extreme; drivers are tested over the same interval.
- Correlation over a short window is evidence of co-movement, not proof of causation; component weights are not applied.
- Context notes are background mechanisms; they are not tested and never supply numbers.
- Context snippets from Wikipedia corroborate hypotheses but never supply numbers.

## Hypotheses tested

| # | Hypothesis | Test | Verdict | Detail |
|---|---|---|---|---|
| H1 | Initial jobless claims rose during the run-up to the 2020 peak | Initial jobless claims (FRED ICSA): sign and size of its change over the target's 2019-12 -> 2020-04 interval (shifted by its best lead of 0-9 months), and lead correlation r >= 0.5 | **supported** | Changed +2,110,850 over the same interval (its best lead, 1 month earlier), the direction needed, and co-moved with the target (r=+0.99). Co-movement, not proof of cause. |
| H2 | Total payroll employment fell during the run-up to the 2020 peak | Total nonfarm payrolls (FRED PAYEMS): sign and size of its change over the target's 2019-12 -> 2020-04 interval (shifted by its best lead of 0-9 months), and lead correlation r >= 0.5 | **supported** | Changed -14.1% over the same interval, the direction needed, and co-moved with the target (r=-0.91). Co-movement, not proof of cause. |
| H3 | Leisure & hospitality employment fell during the run-up to the 2020 peak | Leisure & hospitality employment (FRED USLAH): sign and size of its change over the target's 2019-12 -> 2020-04 interval (shifted by its best lead of 0-9 months), and lead correlation r >= 0.5 | **supported** | Changed -48.1% over the same interval, the direction needed, and co-moved with the target (r=-1.00). Co-movement, not proof of cause. |
| H4 | Construction employment fell during the run-up to the 2020 peak | Construction employment (FRED USCONS): sign and size of its change over the target's 2019-12 -> 2020-04 interval (shifted by its best lead of 0-9 months), and lead correlation r >= 0.5 | **supported** | Changed -13.4% over the same interval, the direction needed, and co-moved with the target (r=-0.59). Co-movement, not proof of cause. |
| H5 | Manufacturing employment fell during the run-up to the 2020 peak | Manufacturing employment (FRED MANEMP): sign and size of its change over the target's 2019-12 -> 2020-04 interval (shifted by its best lead of 0-9 months), and lead correlation r >= 0.5 | **supported** | Changed -10.8% over the same interval, the direction needed, and co-moved with the target (r=-0.87). Co-movement, not proof of cause. |
| H6 | Industrial production fell during the run-up to the 2020 peak | Industrial production (FRED INDPRO): sign and size of its change over the target's 2019-12 -> 2020-04 interval (shifted by its best lead of 0-9 months), and lead correlation r >= 0.5 | **supported** | Changed -16.9% over the same interval, the direction needed, and co-moved with the target (r=-0.91). Co-movement, not proof of cause. |
| H7 | The federal funds rate rose during the run-up to the 2020 peak | Federal funds rate (FRED FEDFUNDS): sign and size of its change over the target's 2019-12 -> 2020-04 interval (shifted by its best lead of 0-9 months), and lead correlation r >= 0.5 | **inconclusive** | Change of -0.0 pts is below the materiality threshold (0.25 pts). |

## Evidence (every claim's source)

| # | Finding | Kind | Query · rows | Source |
|---|---|---|---|---|
| F1 | US unemployment rate (FRED UNRATE): 3.6% in 2019-12 (start of 2020) -> 14.8% in 2020-04 (the 2020 peak), +11.2 pts; for context, the 2018 average was 3.9% | data | `Q-a0adfde54c` · [47, 51] | [link](https://fred.stlouisfed.org/graph/fredgraph.csv?id=UNRATE) |
| F2 | Initial jobless claims (FRED ICSA) (level): 224,400 in 2019-11 -> 2,335,250 in 2020-03 (its best lead, 1 month earlier), a change of +2,110,850; correlation with the target r=+0.99 at a 1-month lead | data | `Q-3730de9f03` · [165, 200, 218] | [link](https://fred.stlouisfed.org/graph/fredgraph.csv?id=ICSA) |
| F3 | Total nonfarm payrolls (FRED PAYEMS) (level): 151,794 in 2019-12 -> 130,426 in 2020-04, a change of -14.1%; correlation with the target r=-0.91 at a 0-month lead | data | `Q-3cb198cf08` · [39, 47, 51] | [link](https://fred.stlouisfed.org/graph/fredgraph.csv?id=PAYEMS) |
| F4 | Leisure & hospitality employment (FRED USLAH) (level): 16,805 in 2019-12 -> 8,719 in 2020-04, a change of -48.1%; correlation with the target r=-1.00 at a 0-month lead | data | `Q-998a31e690` · [39, 47, 51] | [link](https://fred.stlouisfed.org/graph/fredgraph.csv?id=USLAH) |
| F5 | Construction employment (FRED USCONS) (level): 7,527 in 2019-12 -> 6,522 in 2020-04, a change of -13.4%; correlation with the target r=-0.59 at a 0-month lead | data | `Q-6982017e39` · [39, 47, 51] | [link](https://fred.stlouisfed.org/graph/fredgraph.csv?id=USCONS) |
| F6 | Manufacturing employment (FRED MANEMP) (level): 12,760 in 2019-12 -> 11,382 in 2020-04, a change of -10.8%; correlation with the target r=-0.87 at a 0-month lead | data | `Q-d90e36492d` · [39, 47, 51] | [link](https://fred.stlouisfed.org/graph/fredgraph.csv?id=MANEMP) |
| F7 | Industrial production (FRED INDPRO) (level): 101.7 in 2019-12 -> 84.6 in 2020-04, a change of -16.9%; correlation with the target r=-0.91 at a 0-month lead | data | `Q-4b322e1739` · [39, 47, 51] | [link](https://fred.stlouisfed.org/graph/fredgraph.csv?id=INDPRO) |
| F8 | Federal funds rate (FRED FEDFUNDS) (level): 2.4% in 2019-03 -> 2.4% in 2019-07 (its best lead, 9 months earlier), a change of -0.0 pts; correlation with the target r=+0.07 at a 9-month lead | data | `Q-6d8ba26811` · [30, 38, 42] | [link](https://fred.stlouisfed.org/graph/fredgraph.csv?id=FEDFUNDS) |
| F9 | Context (Wikipedia, 'COVID-19 recession'): The recession began in most countries in February 2020. | context |  | [link](https://en.wikipedia.org/wiki/COVID-19_recession) |
| F10 | Context (Wikipedia, 'COVID-19 recession'): After a year of global economic slowdown that saw stagnation of economic growth and consumer activity, the COVID-19 lockdowns and other precautions taken in early 2020 drove the global economy into crisis. | context |  | [link](https://en.wikipedia.org/wiki/COVID-19_recession) |
| F11 | Context (Wikipedia, 'COVID-19 recession'): The first major sign of recession was the 2020 stock market crash, which saw major indices drop 20 to 30% in late February and March. | context |  | [link](https://en.wikipedia.org/wiki/COVID-19_recession) |
| F12 | Context (Wikipedia, 'COVID-19 recession'): Recovery began in early April 2020; by April 2022, the GDP for most major economies had either returned to or exceeded pre-pandemic levels and many market indices recovered or even set new records by late 2020. | context |  | [link](https://en.wikipedia.org/wiki/COVID-19_recession) |

## Queries run through the data harness

- `Q-a0adfde54c` (duckdb, 72 rows) - US unemployment rate (FRED UNRATE)
  ```sql
  SELECT period, value FROM fred_series_unrate WHERE period >= '2016-01' AND period <= '2021-12-31' ORDER BY period
  ```
  source: `fred_series_unrate` <- https://fred.stlouisfed.org/graph/fredgraph.csv?id=UNRATE
- `Q-3730de9f03` (duckdb, 313 rows) - Initial jobless claims (FRED ICSA)
  ```sql
  SELECT period, value FROM fred_series_icsa WHERE period >= '2016-01' AND period <= '2021-12-31' ORDER BY period
  ```
  source: `fred_series_icsa` <- https://fred.stlouisfed.org/graph/fredgraph.csv?id=ICSA
- `Q-3cb198cf08` (duckdb, 72 rows) - Total nonfarm payrolls (FRED PAYEMS)
  ```sql
  SELECT period, value FROM fred_series_payems WHERE period >= '2016-01' AND period <= '2021-12-31' ORDER BY period
  ```
  source: `fred_series_payems` <- https://fred.stlouisfed.org/graph/fredgraph.csv?id=PAYEMS
- `Q-998a31e690` (duckdb, 72 rows) - Leisure & hospitality employment (FRED USLAH)
  ```sql
  SELECT period, value FROM fred_series_uslah WHERE period >= '2016-01' AND period <= '2021-12-31' ORDER BY period
  ```
  source: `fred_series_uslah` <- https://fred.stlouisfed.org/graph/fredgraph.csv?id=USLAH
- `Q-6982017e39` (duckdb, 72 rows) - Construction employment (FRED USCONS)
  ```sql
  SELECT period, value FROM fred_series_uscons WHERE period >= '2016-01' AND period <= '2021-12-31' ORDER BY period
  ```
  source: `fred_series_uscons` <- https://fred.stlouisfed.org/graph/fredgraph.csv?id=USCONS
- `Q-d90e36492d` (duckdb, 72 rows) - Manufacturing employment (FRED MANEMP)
  ```sql
  SELECT period, value FROM fred_series_manemp WHERE period >= '2016-01' AND period <= '2021-12-31' ORDER BY period
  ```
  source: `fred_series_manemp` <- https://fred.stlouisfed.org/graph/fredgraph.csv?id=MANEMP
- `Q-4b322e1739` (duckdb, 72 rows) - Industrial production (FRED INDPRO)
  ```sql
  SELECT period, value FROM fred_series_indpro WHERE period >= '2016-01' AND period <= '2021-12-31' ORDER BY period
  ```
  source: `fred_series_indpro` <- https://fred.stlouisfed.org/graph/fredgraph.csv?id=INDPRO
- `Q-6d8ba26811` (duckdb, 72 rows) - Federal funds rate (FRED FEDFUNDS)
  ```sql
  SELECT period, value FROM fred_series_fedfunds WHERE period >= '2016-01' AND period <= '2021-12-31' ORDER BY period
  ```
  source: `fred_series_fedfunds` <- https://fred.stlouisfed.org/graph/fredgraph.csv?id=FEDFUNDS

## Evidence graph

```mermaid
flowchart LR
  Q(["Why did US unemployment spike in 2020?"])
  H1["H1 Initial jobless claims rose during the run-up t…<br/><i>supported</i>"]:::sup
  Q --> H1
  F2 -->|supports| H1
  F9 -->|supports| H1
  F10 -->|supports| H1
  F11 -->|supports| H1
  H2["H2 Total payroll employment fell during the run-up…<br/><i>supported</i>"]:::sup
  Q --> H2
  F3 -->|supports| H2
  F9 -->|supports| H2
  F10 -->|supports| H2
  F11 -->|supports| H2
  H3["H3 Leisure & hospitality employment fell during th…<br/><i>supported</i>"]:::sup
  Q --> H3
  F4 -->|supports| H3
  H4["H4 Construction employment fell during the run-up …<br/><i>supported</i>"]:::sup
  Q --> H4
  F5 -->|supports| H4
  H5["H5 Manufacturing employment fell during the run-up…<br/><i>supported</i>"]:::sup
  Q --> H5
  F6 -->|supports| H5
  H6["H6 Industrial production fell during the run-up to…<br/><i>supported</i>"]:::sup
  Q --> H6
  F7 -->|supports| H6
  F9 -->|supports| H6
  F10 -->|supports| H6
  F11 -->|supports| H6
  H7["H7 The federal funds rate rose during the run-up t…<br/><i>inconclusive</i>"]:::inc
  Q --> H7
  F8 -.->|against| H7
  F2["F2 Initial jobless claims (FRED ICSA) (level):…"]:::fact
  Q_3730de9f03[("Q-3730de9f03")]:::q
  Q_3730de9f03 --> F2
  F9[/"F9 Context (Wikipedia, 'COVID-19 recession'): …"/]:::ctx
  F10[/"F10 Context (Wikipedia, 'COVID-19 recession'): …"/]:::ctx
  F11[/"F11 Context (Wikipedia, 'COVID-19 recession'): …"/]:::ctx
  F3["F3 Total nonfarm payrolls (FRED PAYEMS) (level…"]:::fact
  Q_3cb198cf08[("Q-3cb198cf08")]:::q
  Q_3cb198cf08 --> F3
  F4["F4 Leisure & hospitality employment (FRED USLA…"]:::fact
  Q_998a31e690[("Q-998a31e690")]:::q
  Q_998a31e690 --> F4
  F5["F5 Construction employment (FRED USCONS) (leve…"]:::fact
  Q_6982017e39[("Q-6982017e39")]:::q
  Q_6982017e39 --> F5
  F6["F6 Manufacturing employment (FRED MANEMP) (lev…"]:::fact
  Q_d90e36492d[("Q-d90e36492d")]:::q
  Q_d90e36492d --> F6
  F7["F7 Industrial production (FRED INDPRO) (level)…"]:::fact
  Q_4b322e1739[("Q-4b322e1739")]:::q
  Q_4b322e1739 --> F7
  F8["F8 Federal funds rate (FRED FEDFUNDS) (level):…"]:::fact
  Q_6d8ba26811[("Q-6d8ba26811")]:::q
  Q_6d8ba26811 --> F8
  classDef sup fill:#d1fae5,stroke:#047857,color:#064e3b
  classDef con fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
  classDef inc fill:#f3f4f6,stroke:#6b7280,color:#374151
  classDef fact fill:#eff6ff,stroke:#1d4ed8,color:#1e3a8a
  classDef ctx fill:#fef9c3,stroke:#a16207,color:#713f12
  classDef q fill:#ede9fe,stroke:#6d28d9,color:#4c1d95
```

## Reasoning trace

1. **scout** - Intent explain.macro_series (band high, risk low); signed contract 001A168286C6E00003B03D09E7D
1. **recall** - H-MEM recalled 1 prior beliefs (minimal)
1. **plan** - Pull US unemployment rate (FRED UNRATE) for 2016-2021; measure the move from Dec 2019 to the 2020 extreme; test 7 candidate drivers over the same interval
1. **findings** - Target 3.6 (2019-12) -> 14.8 (2020-04), +11.2 pts: up
1. **hypotheses** - Tested 7 drivers; 6 supported across 5 cause groups
1. **context** - Background search found 4 context snippets (context corroborates, never supplies numbers)
1. **remember** - Wrote 9 memories with provenance; 0 conflicts with prior beliefs
1. **done** - Finished in 2.7s

## Scout execution contract

Intent `explain.macro_series` (band high, risk low). Contract `001A168286C6E00003B03D09E7D`, capabilities ['background.search', 'harness.query', 'llm.narrate', 'memory.recall', 'memory.write'], expires 2026-10-23T09:52:22Z, HMAC `dbd2f87666e39807…`.

## Recalled from memory (H-MEM)

- UK unemployment rate (FRED LRHUTTTTGBM156S): averaged 4.23 in the 6-18 months before the 2022-10 peak (vs 4.60 in 2020); 3.90 at 2022-10 (score 0.4524, F11 of 'Why did UK inflation spike in 2022?')

_Wrote 9 memories with provenance; 0 conflicts with prior beliefs._
