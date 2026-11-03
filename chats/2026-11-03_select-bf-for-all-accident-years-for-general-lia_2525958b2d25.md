# Select BF for all accident years for General Lia

*Scribe chat · 03 Nov 2026 17:05 · id 2525958b2d25*


## Question

Select BF for all accident years for General Liability — how do ultimates change?


## Scribe

Assuming 100% Incurred Bornhuetter-Ferguson (BF) across all accident years (1995–2024) for General Liability (gross).

Selecting 100% Incurred BF increases total ultimate losses by **$1.36m** (**0.1%**), moving the overall ultimate from **$947.73m** to **$949.09m** (`Methods_and_Weights`, `Indications`).

### Interpretation

* **AY 2024 drives the largest increase:** AY 2024 ultimate increases by **$0.89m** (**1.8%**) from **$50.21m** to **$51.10m** because the current selected weighting blends 50% Incurred BF with 50% Incurred Loss Ratio (`Methods_and_Weights`).
* **Intermediate years show moderate increases:** AY 2019 sees the next largest impact, increasing by **$0.40m** (**1.1%**) from **$35.26m** to **$35.67m**, as the current weighting uses 50% Incurred BF and 50% Incurred Development (`Methods_and_Weights`).
* **Zero impact on AY 2023:** AY 2023 shows a change of **$0.00m** (**0.0%**), remaining at **$46.08m**, because Apollo's selected weight is already 100% Incurred BF (`Methods_and_Weights`).
* **Minimal impact on mature years:** For older accident years (1995–2014), where development is largely complete, shifting from 100% Incurred Development to Incurred BF results in near-zero overall change (`Methods_and_Weights`).

**What-if: 100% Incurred BF — General Liability (gross, AY 1995–2024)**

| Accident year | Selected | Scenario | Change | Change % |
|---|---|---|---|---|
| 1995 | 20.37 | 20.37 | 0.00 | 0.0% |
| 1996 | 16.90 | 16.90 | 0.00 | 0.0% |
| 1997 | 19.71 | 19.71 | 0.00 | 0.0% |
| 1998 | 23.01 | 23.01 | 0.00 | 0.0% |
| 1999 | 17.20 | 17.20 | 0.00 | 0.0% |
| 2000 | 25.49 | 25.49 | 0.00 | 0.0% |
| 2001 | 23.04 | 23.04 | 0.00 | 0.0% |
| 2002 | 21.94 | 21.94 | 0.00 | 0.0% |
| 2003 | 20.86 | 20.86 | 0.00 | 0.0% |
| 2004 | 26.11 | 26.11 | 0.00 | 0.0% |
| 2005 | 26.79 | 26.79 | 0.00 | 0.0% |
| 2006 | 25.16 | 25.16 | 0.00 | 0.0% |
| 2007 | 25.73 | 25.73 | 0.00 | 0.0% |
| 2008 | 30.31 | 30.31 | 0.00 | 0.0% |
| 2009 | 31.45 | 31.43 | (0.02) | -0.1% |
| 2010 | 36.45 | 36.42 | (0.03) | -0.1% |
| 2011 | 33.24 | 33.22 | (0.02) | -0.1% |
| 2012 | 35.23 | 35.18 | (0.05) | -0.1% |
| 2013 | 38.43 | 38.36 | (0.07) | -0.2% |
| 2014 | 38.84 | 38.81 | (0.03) | -0.1% |
| 2015 | 37.24 | 37.21 | (0.02) | -0.1% |
| 2016 | 36.85 | 36.82 | (0.03) | -0.1% |
| 2017 | 36.08 | 36.07 | (0.01) | 0.0% |
| 2018 | 41.49 | 41.42 | (0.07) | -0.2% |
| 2019 | 35.26 | 35.67 | 0.40 | 1.1% |
| 2020 | 39.95 | 40.11 | 0.17 | 0.4% |
| 2021 | 47.47 | 47.58 | 0.10 | 0.2% |
| 2022 | 40.84 | 40.99 | 0.15 | 0.4% |
| 2023 | 46.08 | 46.08 | 0.00 | 0.0% |
| 2024 | 50.21 | 51.10 | 0.89 | 1.8% |
| Total | 947.73 | 949.09 | 1.36 | 0.1% |

*Loss & legal. Re-weights Apollo's method indications (Methods_and_Weights); nothing is recalculated. Change = scenario − selected; this is also the change in IBNR. All amounts in tables are in $m.*

**General Liability — method weights by accident year (gross)**

| Accident years | Weights |
|---|---|
| 1995–2014 | 100% Incurred development |
| 2015–2020 | 50% Incurred BF + 50% Incurred development |
| 2021–2022 | 50% Incurred BF + 50% Incurred Cape Cod |
| 2023 | 100% Incurred BF |
| 2024 | 50% Incurred BF + 50% Incurred loss ratio |

*Source: Methods_and_Weights (even-numbered weight columns).*

<details><summary>How Scribe got this</summary>

- **0 Orchestrator** — Routed to the Analyst
- **3e Scenario** — Re-weight inc_bf:1 (GL GROSS, all AYs) — requested by Gemini
- **3b Exhibit reader** — Method weights (GL GROSS) — requested by Gemini
- **4 Verifier** — 21/21 figures traced to tool results
</details>

<!-- scribe-chat-data: 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 -->
