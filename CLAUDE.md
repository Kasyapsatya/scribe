# CLAUDE.md — Scribe v2: Apollo DVR Chat Agents (Agent Bheem)

> Status: **LOCKED v2.1** (2026-11-03). Supersedes v1 (multi-tab dashboard). Decisions in §9.

## 1. Mission
One **Accenture-branded chat** over a finished Apollo reserve study (AL, GL, WC × gross/net) with two capabilities:
1. **Draft**: the first-draft DVR memo (template order), tables from defined Apollo columns, cited commentary, `.docx` download — steered by the user's instructions through a **drafting brief** (§5a) on top of the fixed rules below.
2. **Analyse**: answer anything about the study (LDF selection, PYD, AvE, diagnostics, method what-ifs, claims drivers) with tables and charts, each figure cited to its tab/cell.

On opening, the chat runs a **readiness check**, says what is available and flags anything missing *before* any work.
Out of scope: re-running Apollo or recalculating method indications. What-ifs only **re-weight Apollo's own method indications**.

## 2. Non-negotiables
- The LLM never produces a number. Numbers come from tools; memo text uses `{{Fxxxx}}` placeholders; chat answers are verified against the tool results of that turn.
- Nothing invented: anything the files can't support → `[INPUT REQUIRED: …]` (memo) or "not in the data" (chat).
- The team Gemini key and model live only in secrets (`.streamlit/secrets.toml` locally, the platform's Secrets when hosted) — **never on the UI**.
  A teammate may enter their own key under ⚙ (header): held in memory for that browser session only, never stored, shown or logged; empty → team key.
  `SHOW_KEY_SETTINGS = false` hides ⚙. Cool-downs and the rate limiter are tracked **per key** (fingerprint), so one exhausted key never blocks another. Every call has a 20 s timeout and no retry loops; on quota/network failure the app says so plainly and never hangs (drafting still works: it is code-led).
- **Gemini call budget:** memo draft = 1 call (all narrative sections polished together) + 1 Brief-agent call only when the request carries instructions; analysis question = 2–3 calls (tool choice → answer); first use = 1 extra probe (model cached 24 h). A per-key limiter keeps calls under `GEMINI_RPM` (secrets, default 5/min); a per-minute 429 pauses Gemini only for Google's retry delay, a daily-quota 429 for an hour. Agno returns API errors as content with status ERROR — always check status.
- No UI clutter: logo header with "New chat", "Chats" (history + export) and a small ⚙ (own key) button, chat. A collapsed "How I got this" panel per answer is the only extra.
- Clean, minimal, non-redundant code with traceable variable origins.
- **Generalised to the workbooks:** sub-segments, line of business, evaluation date, AYs, ages, averages, methods and weights are all read
  from the packs — nothing is fixed to AL/GL/WC or to this dataset. Narrative follows the data: LDF wording from reason codes (prior retained
  ± consistent with averages, moved to an average, benchmark, manual override, judgmental within/outside averages — judgmental ones ask
  for rationale); selection rationale from the method families actually weighted (incl. Prior/Booked ultimate). Methods_and_Weights is read
  as same-named column pairs (indication, weight) before the first "Weight Totals" (adjuster methods follow in real Apollo files).
  Tunable thresholds sit at the top of `specialists.py` / `brief.py` (TOL, NEAR, AVG_RANGE, MATERIAL_AVE default, diagnostic CY rule).
  `evaluate.py` proves it by editing workbook copies (new LDF basis, Prior-ultimate weight, sub-segment removed).

## 3. Data (folder `data/inputs/`, plus uploads from the chat into the same folder)
| Item | Recognised by | Required for |
|---|---|---|
| Apollo pack per sub-segment × basis (6) | sheet title `… - GROSS/NET - ATTR detail` + segment code `CAS_AL/GL/WC` (not file name) | everything |
| Claims file | columns `ClaimID, sub_segment, accident_year, Incurred_prior, Incurred_current, Incurred_change, CAT_Flag, Loss_Description` | PYD/AvE drivers |
| AvE file | columns `sub_segment, basis, accident_year, expected_incurred, actual_incurred` | AvE commentary |
| Memo template | `DVR Memo Template version 1.docx` in `data/` | drafting |

Apollo tab families (layout mirrors the sample `Apollo Output.xlsx`; parse by **label and column number**, never row number):
- **Summary tables** (header row `Accident Period`, numbering row `(1)…(n)`, rows `PRIOR`, AYs, `Totals` / `Prior Total` / `Total`): Indications (Ex 1, 18 cols), Indicated_Ratios, Methods_and_Weights (19 cols: 8 method pairs value/weight, Booked Ultimate pair, Weight Totals), Ultimate_Adjustments, IBNR_and_Ratios, Comparison_With_Prior (12 cols), Method_Ratios, ELRIncLossLegal, trend tabs.
- **Development triangles** (blocks Data → Age-To-Age Factors → Averages → Benchmarks → Manual Selections → Ultimate Selections): IncLossLegalTri, PaidLossLegalTri, ReportedTri, ClosedTri.
- **Diagnostics** (age grid, `Origin Period` header): PaidToIncurred, Frequency, IncLossRToG, IncurredToEarnedPremium, SettlementRate, AverageCaseOS, EarnedPremiumRToG, PaidToEarnedPremium, PaidLegalToLoss, PaidLegalToIncdLoss.

## 4. Memo table definitions (all from Apollo; gross and net in **separate** tables)
| Item | Source |
|---|---|
| Case OS | Indications col (3) |
| IBNR | Indications col (15) |
| Legal need | Indications col (6) + col (16) |
| Adjuster need | Indications col (17) |
| Total unpaid | Case OS + IBNR + Legal need + Adjuster need |
| PYD by AY | Comparison_With_Prior col (12), **prior AYs only** (≤ evaluation year − 1); total = `Prior Total` row |
| Reinsurance ratio by AY | net EP / gross EP, Indications col (1) |
Formatting: text `$x.xxm` (negatives `-$x.xxm`); tables `x.xx` (negatives `(x.xx)`) with the note "All amounts in tables are in $m." PYD sign: positive = adverse.

## 5. Agents
| # | Agent | LLM? | Scope | Output |
|---|---|---|---|---|
| 0 | **Orchestrator** | rules first, LLM for analysis | Route each message: readiness / draft / about / analyse; carry segment & basis context | routed reply + steps |
| 1 | **Data Steward** | no | Inventory vs §3; checks: all 6 packs, required tabs/columns, eval date consistent, AY rows tie to totals, net ≤ gross; capability matrix | readiness card |
| 1b | **Brief agent** | yes (1 call, validated) | Turns drafting instructions into brief changes (§5a); Python validates every choice against the study and the user's words | brief + not-applied list + assumptions |
| 2 | **Drafting team** | wording only (verifier-gated) | Sections in template order using §4; LDF reason codes; method/selection narrative; diagnostics; `[INPUT REQUIRED]` | memo preview + `.docx` + evidence log |
| 3 | **Analyst** (LLM + specialist tool-agents) | yes | Answers questions by calling the specialists below; states assumptions (e.g. "BF" ⇒ Incurred BF unless told) | answer + tables/charts |
| 3a | Study Navigator | no | Catalogue of tabs/blocks/columns; finds where an answer lives | locations |
| 3b | Exhibit reader | no | Returns any table/triangle block with cell refs | table |
| 3c | LDF explainer | no | Reason codes per age (SAME_AS_PRIOR, MATCH_AVG, WITHIN_RANGE, BENCHMARK, MANUAL, UNITY_TAIL) | table + chart |
| 3d | Diagnostics | no | Any Apollo diagnostic tab; flags latest diagonal vs prior diagonals | table + chart + flags |
| 3e | Scenario | no | Re-weight Apollo method indications (Methods_and_Weights) for chosen AYs; compare with selected | AY table + chart |
| 3f | Claims Investigator / AvE | no | AvE by AY; one-off large / release / CAT / trend classification with claim IDs | table + chart |
| 4 | **Verifier** | no | Memo: every number is a placeholder. Chat: every figure matches a tool result this turn, else flagged | pass/flag |

### 5a. Drafting brief (`brief.py`) — what the user can steer; everything else is fixed (§2, §4)
| Field | Meaning | Default |
|---|---|---|
| segments / bases | memo scope | all sub-segments; gross and net |
| sensitivity | list of scenarios: `min_max` (lowest/highest Apollo method per AY, optional method subset), `methods` (low = method A, high = method B), `weights` (re-weighting); each with optional AY range (other AYs at selected) | `min_max`, all actuarial methods, all AYs |
| materiality / ays_explained / focus | which AYs the PYD/AvE commentary explains | $0.5m; 2 per sub-segment; none |
| exclude_cat | also show AvE excluding CAT claim movements | off |
| context | user's own text for scope, business, reinsurance, Stat/GAAP, limitations, roles, collaboration, responsibility, data, risks — inserted verbatim, labelled "Analyst input"; replaces the `[INPUT REQUIRED]` markers in single-topic sections | none |
Validation: methods/sub-segments/AYs must exist in the study; weights sum to 100%; any amount or weight must appear in the user's instruction;
context text must be the user's own words. Rejected choices and Gemini's "unsupported" items (anything needing Apollo to be re-run, e.g. ELR ± x%, tail
factors) are listed as **Not applied** with the reason. The memo opens with **Basis of this draft** (applied / default / assumption / not applied),
repeated in the chat reply. The brief is stored per chat (artifact `brief`), so "redraft with high = Paid BF" changes one choice. Plain "draft the memo"
skips Gemini (`needs_reading`). If the brief agent asks a question, the next reply resumes the draft. Gemini unavailable → draft with the chat's
previous brief or the defaults, and say so.

Gemini unavailable → no keyword scripting: the Analyst says Gemini is needed and lists the relevant Apollo tabs (Navigator); if Gemini had already chosen tools that turn, their verified tables are shown. A saved Gemini answer to the same question may be replayed, labelled as saved.

## 6. Storage and sharing
SQLite `output/scribe.db`: chats (id, title, created) and messages (role, content, artifacts JSON: tables, Plotly JSON, file paths, steps). Header "Chats" button lists past chats; "New chat" starts one.
On a hosted app the database is shared by the team and is lost when the app restarts; the durable record is the **chat export**: "Chats → ⬆ Save this
chat to the team repo" writes `chats/<date>_<title>_<id>.md` (question, answer, tables, "how Scribe got this") through the GitHub contents API
(`export.py`; `GITHUB_TOKEN`, `GITHUB_REPO` in secrets) — else a `.md` download. Uploads go to a private per-session copy of `data/inputs/`
(`output/sessions/<id>/`), so one user's files never change the study others see. Memo files are named per chat (`…_<chat id>.docx` + `.evidence.json`).

## 7. Stack
Python 3.11+, openpyxl/pandas (tidy fact table), Agno + Gemini (model auto-resolved and cached; key in secrets), Plotly, python-docx, Streamlit ≥ 1.43 (chat file upload). Theme in `.streamlit/config.toml` (Accenture purple `#A100FF`, black/white).

## 8. Evaluation (`python evaluate.py`)
Brief: Gemini only for instructed drafts; validation applies supported choices and rejects invented figures/text/options; memo follows the brief
(scope, bases, scenarios, user text, basis note); sensitivity arithmetic (BF-all = Scenario agent; min/max brackets every method); Brief golden set (needs Gemini).
Table tie-outs to §4 columns; PYD prior-only; readiness detects a removed file; router accuracy on test prompts; scenario arithmetic (BF-all = BF column); LDF codes vs golden; planted drivers (AL 2021 one-off, AL 2023 CAT, GL 2019 release, WC 2022 trend); planted diagnostic (WC average case OS jump on latest diagonal); template coverage; Q&A golden set (needs Gemini).

## 9. Locked decisions
v1 (still valid): synthetic AYs 1995–2024; claims at 2023Q4 and 2024Q4; net = 75% of gross (25% QS); sub-segments AL/GL/WC; AvE by AY gross & net; ADC paragraph dropped; Gemini free tier via Agno.
v2 (2026-11-03): PYD = Comparison col 12; reinsurance ratio = EP only; formats per §4; official Accenture logo (`assets/accenture.png`, supplied by KK); what-ifs take Apollo indications and may re-weight, never recalculate — ask or state the BF paid/incurred assumption; data folder **and** chat uploads; one memo for all three sub-segments; chats stored; no model/key on the UI.
v2.2 (2026-11-03): hosted on Streamlit Community Cloud from a private GitHub repo for the team; optional per-session Gemini key (⚙, hideable); chats exported to the repo `chats/` as references.
v2.1 (2026-11-03): drafting follows the user's instructions through a validated drafting brief (§5a) on top of the fixed rules; sensitivity scenarios
are chosen by the user (default lowest/highest of all methods, stated in the memo); unsupported requests are reported, never silently dropped.
Synthetic Apollo packs mirror the sample's tabs and column numbering; incurred adjuster = paid adjuster (no adjuster case reserve); `SelUltiLegalDev` and method-detail tabs (DFM/BF/CC detail, Adjuster tabs) are not generated.
