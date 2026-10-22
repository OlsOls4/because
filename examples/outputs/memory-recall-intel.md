# What do you already know about Intel's margins?

_Backend: **duckdb** · LLM: none · started 2026-10-22T10:48:11Z_

## Answer

Here is what I already believe and why:
- Intel revenue FY2023: $54.23B (us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, 10-K filed 2026-01-23, period ending 2023-12-30) (confidence 0.90; learned via harness_query: F2 of 'Why did Intel's gross margin fall in 2024?')
- Intel gross profit FY2023: $21.71B (us-gaap:GrossProfit, 10-K filed 2026-01-23, period ending 2023-12-30) (confidence 0.90; learned via harness_query: F1 of 'Why did Intel's gross margin fall in 2024?')
- Intel revenue FY2024: $53.10B (us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, 10-K filed 2026-01-23, period ending 2024-12-28) (confidence 0.90; learned via harness_query: F4 of 'Why did Intel's gross margin fall in 2024?')
- Intel depreciation FY2023: $7.85B (us-gaap:Depreciation, 10-K filed 2026-01-23, period ending 2023-12-30) (confidence 0.90; learned via harness_query: F9 of 'Why did Intel's gross margin fall in 2024?')
- Intel gross profit FY2024: $17.34B (us-gaap:GrossProfit, 10-K filed 2026-01-23, period ending 2024-12-28) (confidence 0.90; learned via harness_query: F3 of 'Why did Intel's gross margin fall in 2024?')
- Intel capital expenditure FY2024: $23.94B (us-gaap:PaymentsToAcquirePropertyPlantAndEquipment, 10-K filed 2026-01-23, period ending 2024-12-28) (confidence 0.90; learned via harness_query: F12 of 'Why did Intel's gross margin fall in 2024?')

**Confidence: n/a (0)** - Recall only; no new investigation.

## Reasoning trace

1. **scout** - Intent memory.explain_belief (band high, risk low); signed contract 001A16339BE07000037C6BA8DB1
1. **recall** - Explained 8 stored beliefs with provenance
1. **done** - Finished in 0.0s

## Scout execution contract

Intent `memory.explain_belief` (band high, risk low). Contract `001A16339BE07000037C6BA8DB1`, capabilities ['memory.recall'], expires 2026-10-22T10:53:11Z, HMAC `f44d429f722486f9…`.

## Recalled from memory (H-MEM)

- Intel revenue FY2023: $54.23B (us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, 10-K filed 2026-01-23, period ending 2023-12-30) (score 0.3162, F2 of 'Why did Intel's gross margin fall in 2024?')
- Intel gross profit FY2023: $21.71B (us-gaap:GrossProfit, 10-K filed 2026-01-23, period ending 2023-12-30) (score 0.3147, F1 of 'Why did Intel's gross margin fall in 2024?')
- Intel revenue FY2024: $53.10B (us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax, 10-K filed 2026-01-23, period ending 2024-12-28) (score 0.3069, F4 of 'Why did Intel's gross margin fall in 2024?')
- Intel depreciation FY2023: $7.85B (us-gaap:Depreciation, 10-K filed 2026-01-23, period ending 2023-12-30) (score 0.3037, F9 of 'Why did Intel's gross margin fall in 2024?')
- Intel gross profit FY2024: $17.34B (us-gaap:GrossProfit, 10-K filed 2026-01-23, period ending 2024-12-28) (score 0.3027, F3 of 'Why did Intel's gross margin fall in 2024?')
- Intel capital expenditure FY2024: $23.94B (us-gaap:PaymentsToAcquirePropertyPlantAndEquipment, 10-K filed 2026-01-23, period ending 2024-12-28) (score 0.2967, F12 of 'Why did Intel's gross margin fall in 2024?')
- Intel depreciation FY2024: $9.95B (us-gaap:Depreciation, 10-K filed 2026-01-23, period ending 2024-12-28) (score 0.294, F8 of 'Why did Intel's gross margin fall in 2024?')
- Intel gross margin FY2023 -> FY2024: 40.0% -> 32.7% (-7.4 pts) (score 0.2862, F5 of 'Why did Intel's gross margin fall in 2024?')
