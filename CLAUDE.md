# CLAUDE.md — Scribe v3: Apollo DVR Chat Agents (Agent Bheem)

> Status: **LOCKED v3** (2026-11-11). Supersedes v2.1/v2.2 (see PLAN_v3.md for the migration). Decisions in §9.

## 1. Mission
One **Accenture-branded chat** over a finished Apollo reserve study (AL, GL, WC × gross/net) in which agents **draft,
edit and fill in** the DVR memo on demand and **answer any question** on the study, with every number traceable:
1. **Draft**: the first-draft DVR memo (template order), tables from defined Apollo columns, cited commentary, `.docx`
   download — steered by the user's instructions through a **drafting brief** (§5a) the Orchestrator reads in the same
   call that routes the message. **Redraft one section** on request ("make the PYD section shorter"). **Fill the gaps**:
   an Interviewer asks the open `[INPUT REQUIRED]` items one at a time, most material first, and records the
   actuary's own words verbatim.
2. **Analyse**: answer anything about the study (LDF selection, PYD, AvE, diagnostics, method what-ifs, claims
   drivers, or "why does the memo say X") with tables and charts, each figure a reference back to its tab/cell.

On opening, the chat runs a **readiness check** and a **First Look** (the five items most worth a reviewer's time),
before any work. Out of scope: re-running Apollo or recalculating method indications. What-ifs only **re-weight
Apollo's own method indications**.

## 2. Non-negotiables
- **The LLM never writes a number — it can only point to one.** Every number a tool returns to an agent is registered
  in a per-chat **evidence ledger** (`ledger.py`): value, unit, source cell, and — for a calculated figure — its
  derivation. Agents write `{{Fxxxx}}` placeholders, in chat answers and in the memo alike; Python renders the values.
  Arithmetic goes through the `calculate` tool (ast-restricted to references, numbers, `+ - * /`, `abs`/`sum`/`min`/`max`),
  which registers its result as a new reference. The ref verifier (`ledger.untraced`) is a hard check: every placeholder
  must resolve in the ledger, and no bare number may remain outside the same exemptions as before (years, ages, claim
  IDs, column/exhibit numbers, small counts). On failure: **one** automatic retry ("replace these numbers with
  references: …"); if it still fails, the answer is shown with the untraced tokens flagged, never hidden. One ledger
  is shared by a chat's Q&A and its memo, so the same cell always resolves to the same ref.
- Nothing invented: anything the files can't support → `[INPUT REQUIRED: …]` (memo) or "not in the data" (chat).
- The team Gemini key and model live only in secrets (`.streamlit/secrets.toml` locally, the platform's Secrets when hosted) — **never on the UI**.
  A teammate may enter their own key under ⚙ (header): held in memory for that browser session only, never stored, shown or logged; empty → team key.
  `SHOW_KEY_SETTINGS = false` hides ⚙. Cool-downs and the rate limiter are tracked **per key** (fingerprint), so one exhausted key never blocks another. Every call has a 20 s timeout and no retry loops; on quota/network failure the app says so plainly and never hangs (drafting still works: the code-drafted wording is always a valid fallback).
- **Gemini call budget (paid key; design for capability, not the free-tier 5 RPM):** Orchestrator = 1 call per message
  (classification, scope, and — for a draft — the brief changes, all together). Analyst question = 2–4 calls (its own
  tool-use loop, plus a nested call whenever it asks the Claims Investigator). Section Writer = 1 call per section,
  run in parallel across a draft's narrative sections (bounded by the limiter). Interviewer = 1 call per turn. First
  Look = 1 call, cached per study signature. First use = 1 extra probe (model cached 24 h). A per-key limiter keeps
  calls under `GEMINI_RPM` (secrets, default 60/min on a paid key); a per-minute 429 pauses Gemini only for Google's
  retry delay, a daily-quota 429 for an hour. Agno returns API errors as content with status ERROR — always check status.
  **Gemini unavailable, per agent:** Orchestrator → `route_rules` (regex kind only, no scope/brief_changes). Analyst →
  says so and lists the relevant tabs (Navigator), or shows the tool results Gemini had already chosen before failing.
  Section Writer → the section keeps its code-drafted wording. Interviewer / First Look → first_look falls back to a
  fixed deterministic ranking; the Interviewer says it can't continue right now (nothing already filled is lost).
- No UI clutter: logo header with "New chat", "Chats" (team history + download) and a small ⚙ (own key) button, chat. A collapsed "How I got this" panel and a collapsed "Figures" panel (ref → value → source/derivation) per answer are the only extras.
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
| Agent | Decides | Tools it may call | Calls per use |
|---|---|---|---|
| **Orchestrator** | Message kind (`question`/`draft`/`edit_section`/`fill_gaps`/`gap_answer`/`data_check`/`about`/`chitchat`), scope (segments/bases/accident years), and — for a draft — the brief changes | none (structured output only) | 1 |
| **Data Steward** (`steward.py`) | Inventory vs §3: all 6 packs, required tabs/columns, eval date consistent, AY rows tie to totals, net ≤ gross | — (no LLM) | readiness card |
| **Analyst** | Which evidence answers the question, in what order; how to explain it | all study tools except `claim_movements`; `calculate`, `explain_figure`, `read_memo_section`, `search_memo`; `ask_claims_investigator` | 2–4 |
| **Claims Investigator** | What drove an AY's movement — one-off, release, CAT, trend or mixed — from amounts **and** loss descriptions; agrees or disagrees with the rule-based label | `claim_movements`, `actual_vs_expected`, `calculate` | 1–2 |
| **Section Writer** | What one narrative section says and which evidence supports it; redrafts that section on request | all study tools except `claim_movements`; `calculate`, `read_memo_section`, `ask_claims_investigator` | 1 per section |
| **Interviewer** | Which open judgement to ask next (materiality order, `memo.rank_open_inputs`); where the answer goes | `list_open_inputs`, `fill_input`, `explain_figure` | 1 per turn |
| **First Look** | The five items most worth a reviewer's time, from `scan_study`'s numbered candidates | `scan_study`, `ldf_selection`, `diagnostic`, `actual_vs_expected`, `method_indications` | 1, cached per study signature |

Deterministic (no LLM): Data Steward, every specialist in `specialists.py`, the ledger, the memo verifier/assembler.
All agents are a single Agno `Agent` built fresh per call (`agents._agent`); structured output uses `output_schema`
(a Pydantic model) wherever the caller needs more than prose back — `Intent`, `ClaimsVerdict`, `SectionDraft`,
`InterviewTurn`, `FirstLook`.

### Tools (`tools.py`; every result is tagged through the ledger — `"<display> [Fxxxx]"`)
| Group | Tools |
|---|---|
| Study | `find_sections`, `reserve_summary`, `prior_year_development`, `reinsurance_ratios`, `ldf_selection`, `method_weights`, `method_indications`, `what_if_weights`, `sensitivity_range`, `diagnostic`, `actual_vs_expected`, `read_exhibit` (`tools.STUDY_TOOLS`) |
| Claims (Claims Investigator only) | `claim_movements` — claim rows with `Loss_Description`, each claim's share of the AY's A−E, and the rule-based `rule_label` |
| Evidence | `calculate`, `explain_figure` (`tools.EVIDENCE_TOOLS`) |
| Memo | `read_memo_section`, `search_memo` (`tools.MEMO_TOOLS`); `list_open_inputs`, `fill_input` (`tools.INTERVIEW_TOOLS`) — all `None`-safe when no memo exists yet in this chat |
| Data | `scan_study` — deterministic First Look candidates, each line already carrying its ref |
| Agent-as-tool | `ask_claims_investigator(sub_segment, accident_year, basis)` — Analyst/Section Writer only |

### 5a. Drafting brief (`brief.py`) — what the user can steer; everything else is fixed (§2, §4)
| Field | Meaning | Default |
|---|---|---|
| segments / bases | memo scope | all sub-segments; gross and net |
| sensitivity | list of scenarios: `min_max` (lowest/highest Apollo method per AY, optional method subset), `methods` (low = method A, high = method B), `weights` (re-weighting); each with optional AY range (other AYs at selected) | `min_max`, all actuarial methods, all AYs |
| materiality / ays_explained / focus | which AYs the PYD/AvE commentary explains | $0.5m; 2 per sub-segment; none |
| exclude_cat | also show AvE excluding CAT claim movements | off |
| context | user's own text for scope, business, reinsurance, Stat/GAAP, limitations, roles, collaboration, responsibility, data, risks — inserted verbatim, labelled "Analyst input"; replaces the `[INPUT REQUIRED]` markers in single-topic sections | none |
The Orchestrator reads drafting instructions in the same call that routes the message (`Intent.brief_changes`) — there
is no separate brief-reading call. `brief.validate()` is unchanged: it applies supported choices and rejects anything
the study or the user's own words don't support (methods/sub-segments/AYs must exist; weights sum to 100%; any amount
or weight must appear in the instruction; context text must be the user's own words), and also derives the "BF taken
as Incurred/Paid BF" assumption deterministically (no LLM field) when the instruction left it unqualified. Rejected
choices are listed as **Not applied** with the reason. The memo opens with **Basis of this draft** (applied / default
/ assumption / not applied), repeated in the chat reply. The brief is stored per chat (artifact `brief`), so "redraft
with high = Paid BF" changes one choice. Gemini unavailable → draft with the chat's previous brief or the defaults, and say so.

Gemini unavailable → no keyword scripting: the Analyst says Gemini is needed and lists the relevant Apollo tabs (Navigator); if Gemini had already chosen tools that turn, their verified tables are shown. A saved Gemini answer to the same question may be replayed, labelled as saved.

## 6. Storage and sharing
SQLite `output/scribe.db`: chats (id, title, created) and messages (role, content, artifacts JSON: tables, Plotly
JSON, file paths, steps, and — per chat — a `ledger` artifact (the evidence ledger, JSON) and a `memo_state` artifact
(`memo.py`: sections, brief, docx path, subject) once a memo has been drafted). Header "Chats" button lists past
chats; "New chat" starts one.
With `GITHUB_TOKEN` + `GITHUB_REPO` in secrets, `chat_sync.py` keeps every chat in the team repo automatically: after each reply (background
thread) the chat is written to `chats/<date>_<title>_<id>.md` on branch `GITHUB_BRANCH` (default `chats`, created if missing, so saves never
redeploy the app) — readable Markdown (questions, answers, tables, how Scribe got them) with the full chat embedded (zlib + base64 in an HTML
comment). The Chats list pulls new/changed files at most once a minute, so history is shared by the team and survives restarts. Without a token,
chats stay in the local SQLite only; "Chats → ⬇" downloads one. Uploads go to a private per-session copy of `data/inputs/`
(`output/sessions/<id>/`), so one user's files never change the study others see. Memo files are named per chat (`…_<chat id>.docx` + `.evidence.json`).
First Look's cached selection/phrasing lives at `output/first_look_<hash of the study signature>.json` — shared across chats on the same study.

## 7. Stack
Python 3.11+, openpyxl/pandas (tidy fact table), Agno + Gemini (model auto-resolved and cached; key in secrets), Plotly, python-docx, Streamlit pinned to 1.56.0 (1.58+ switched to a Starlette/uvicorn server; on Community Cloud its static JS files time out with HTTP 500 and the page never loads). Theme in `.streamlit/config.toml` (Accenture purple `#A100FF`, black/white).

## 8. Evaluation (`python evaluate.py`, `--qa` adds the live checks)
Ledger: refs dedupe by `(source, unit, value)`; `calculate` matches pandas and rejects unsafe/unknown expressions;
`to_llm(ledger)` figures resolve to the same value; the ref verifier passes placeholders and exempt tokens, flags a
bare number. A scripted (no-Gemini) wiring test proves tool dispatch, allow-lists and placeholder rendering.
`claim_movements`/`scan_study` cover the planted drivers/diagnostics. Memo state round-trips (`read_section`/`search`/
`open_inputs`/`fill`/`replace_section`/`rebuild_docx`); `brief.validate` states the BF assumption only when
unqualified. Section Writer and `redraft_section` fall back to code-drafted wording with Gemini forced off (a
monkeypatch, so this is deterministic regardless of real quota state); `rank_open_inputs` orders most-material-first;
the Interviewer's tools work standalone and fail clearly off-Gemini. First Look's fallback is deterministic and
never cached; a cache hit re-derives its figure fresh for the current ledger.
Table tie-outs to §4 columns; PYD prior-only; readiness detects a removed file; `route_rules` fallback accuracy on
test prompts; scenario arithmetic (BF-all = BF column; min/max brackets every method); LDF codes vs golden; planted
drivers (AL 2021 one-off, AL 2023 CAT, GL 2019 release, WC 2022 trend); planted diagnostic (WC average case OS jump
on latest diagonal); template coverage.
Live (`--qa`, needs a working key): Orchestrator intent + brief_changes golden sets; Q&A golden set; a full draft
verifies with real Section Writers; `redraft_section` changes only the named section; the Interviewer offers item 1,
records a verbatim answer, offers item 2; First Look returns five cited items and a repeat call hits the cache.

## 9. Locked decisions
v1 (still valid): synthetic AYs 1995–2024; claims at 2023Q4 and 2024Q4; net = 75% of gross (25% QS); sub-segments AL/GL/WC; AvE by AY gross & net; ADC paragraph dropped; Gemini free tier via Agno.
v2 (2026-11-03): PYD = Comparison col 12; reinsurance ratio = EP only; formats per §4; official Accenture logo (`assets/accenture.png`, supplied by KK); what-ifs take Apollo indications and may re-weight, never recalculate — ask or state the BF paid/incurred assumption; data folder **and** chat uploads; one memo for all three sub-segments; chats stored; no model/key on the UI.
v2.2 (2026-11-03): hosted on Streamlit Community Cloud from a private GitHub repo for the team; optional per-session Gemini key (⚙, hideable); every chat saved automatically to the repo (`chats` branch) and reloaded into the Chats list.
v2.1 (2026-11-03): drafting follows the user's instructions through a validated drafting brief (§5a) on top of the fixed rules; sensitivity scenarios
are chosen by the user (default lowest/highest of all methods, stated in the memo); unsupported requests are reported, never silently dropped.
v3 (2026-11-11): agentic Orchestrator (absorbs the old Brief agent into one structured-output call); evidence-ledger
references everywhere instead of literal numbers, shared by a chat's Q&A and its memo; Section Writers replace the
single memo-wide polish with one verifier-gated Gemini call per narrative section, run in parallel, and back
`edit_section` (redraft one section on request); a Claims Investigator sub-agent reads loss descriptions instead of
a fixed rule; an Interviewer fills `[INPUT REQUIRED]` items conversationally, most material first; a First Look
greets a new chat with the five items most worth reviewing, cached per study signature. Agno kept; paid Gemini key
assumed (`GEMINI_RPM` default raised to 60); a Schema Mapper and a Reviewer agent, and prior-memo retrieval, remain
out of scope (clean seams left for them per PLAN_v3.md).
Synthetic Apollo packs mirror the sample's tabs and column numbering; incurred adjuster = paid adjuster (no adjuster case reserve); `SelUltiLegalDev` and method-detail tabs (DFM/BF/CC detail, Adjuster tabs) are not generated.
