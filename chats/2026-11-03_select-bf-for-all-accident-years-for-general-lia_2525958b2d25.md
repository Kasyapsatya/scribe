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
