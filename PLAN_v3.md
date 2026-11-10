# PLAN_v3.md — Scribe v3: an agentic memo you can draft, edit and talk to

> For Claude Code. Read `CLAUDE.md` (v2.1, locked) first — everything there still holds unless this plan changes it.
> Owner: KK. Written 2026-11-10. Work on branch **`feature/agentic-v3`**; `main` stays as the working v2 demo.

## 0. How to use this plan
1. `git status` must be clean on `main` (commit or stash anything pending), then `git checkout -b feature/agentic-v3`.
2. Work phase by phase (§8). Each phase ends with `python evaluate.py` green (offline checks) and one commit
   `v3 phase N: <title>`. Do not start a phase until the previous one passes.
3. Gemini paths need the paid key in `.streamlit/secrets.toml` (`GEMINI_API_KEY`). Run the live checks (§9) after
   phases 3, 5 and 7. If a live check fails, fix prompts/tool docstrings, not the deterministic code.
4. When all phases pass, update `CLAUDE.md` to v3 (§10) and `README.md`.
5. Ask KK before changing anything in §1 "Decisions".

## 1. Decisions (agreed with KK)
| Topic | Decision |
|---|---|
| Goal | An LLM-powered chat in which agents **draft / update the DVR memo on demand** and **answer any question on the Apollo output using tools**, with every number traceable. |
| Framework | **Stay on Agno** (2.x). No LangGraph. Agents are Agno `Agent`s with Python tools; chat state stays in the SQLite store. |
| Model | **Paid Gemini key**. Design for capability, not for the free-tier 5 RPM. Keep the per-key limiter, timeouts and cool-downs; raise `GEMINI_RPM` default to 60 (secrets can override). |
| In scope | LLM Orchestrator · evidence ledger with figure references in chat and memo · `calculate` · Analyst with memo tools · Claims Investigator agent (reads loss descriptions) · Section Writer agents · Interviewer (fills `[INPUT REQUIRED]` gaps) · First Look. |
| Out of scope (now) | Schema Mapper, Reviewer agent, prior-memo retrieval. Do not build them; leave clean seams. |
| Unchanged | §4 table definitions, data recognition (§3), storage/sharing (§6), Streamlit 1.56.0 pin, UI minimalism, readiness check on open, what-ifs only re-weight Apollo indications. |

## 2. The one rule everything hangs on
**The LLM never writes a number. It can only point to one.**
- Every number a tool returns to an LLM carries a reference `Fxxxx` registered in an **evidence ledger** (value, unit, source cell, derivation).
- Agents write `{{Fxxxx}}` placeholders — in chat answers as well as in the memo. Python renders the values.
- Arithmetic goes through the `calculate` tool, which evaluates an expression over references and registers the result as a new reference with its derivation.
- The verifier becomes a hard check: every placeholder must exist in the ledger, and no bare numbers may remain (same `ALLOWED` patterns as `drafting.py`: years, ages, claim IDs, column numbers, exhibit numbers…). On failure: **one** automatic retry ("replace these numbers with references: …"); if it still fails, show the answer with the untraced tokens flagged (never hide them).

Refs are created lazily — only for numbers a tool actually returns to an LLM — and deduplicated by `(source, unit, rounded value)`, exactly as `drafting.Citations.cite` does today. A typical answer touches 20–60 refs and uses 4–10.

## 3. Target architecture
```
user message ─► Orchestrator (LLM, structured output) ─► Python dispatch
                    │ kind, scope, instruction, section_id
     ┌──────────────┼───────────────┬────────────────┬──────────────┬─────────────┐
     ▼              ▼               ▼                ▼              ▼             ▼
  Analyst     Drafting run     Section Writer    Interviewer    First Look    data_check /
 (Q&A, tools)  (brief → code    (one section,     (asks for      (on study    about (no LLM)
     │         tables → Section  write or edit)    judgements)    load)
     │         Writers → docx)
     └──────► Claims Investigator (sub-agent; only agent that reads claim rows)
                    │
        all agents ─► Python tools (specialists.py, ledger.py, memo.py) ─► Study (Apollo packs, claims, AvE) + memo state
                    └─► evidence ledger (per chat, persisted) ◄── renderer (chat + memo + .docx + evidence log)
```

### 3.1 Agents (all Agno `Agent`, Gemini)
| Agent | Decides | Tools it may call | Calls per use |
|---|---|---|---|
| **Orchestrator** | Message kind and scope; drafting instructions (absorbs today's Brief agent); which agent acts | none (structured output only) | 1 |
| **Analyst** | Which evidence answers the question, in what order; how to explain it | all study tools **except** `claim_movements`; `calculate`, `explain_figure`, `read_memo_section`, `search_memo`; `ask_claims_investigator` | 2–4 (Agno tool loop) |
| **Claims Investigator** | What drove an AY's movement — one-off, release, CAT, trend — from amounts **and** loss descriptions | `claim_movements`, `actual_vs_expected`, `calculate` | 1–2 |
| **Section Writer** | What one narrative section says and which evidence supports it; redrafts that section on request | all study tools except `claim_movements`; `calculate`, `read_memo_section`, `ask_claims_investigator` | 1–2 per section |
| **Interviewer** | Which open judgement to ask next (materiality order); where the answer goes | `list_open_inputs`, `fill_input`, `explain_figure` | 1 per turn |
| **First Look** | The five items most worth a reviewer's time | `scan_study`, `ldf_selection`, `diagnostic`, `actual_vs_expected`, `method_indications` | 1, cached per study signature |

Deterministic (no LLM): Data Steward (`steward.py`), all specialists, ledger, verifier, memo assembler.

**Orchestrator implementation.** Use one Agno `Agent` with `output_schema` (Pydantic) — check the installed Agno version's parameter name
(`output_schema` in 2.x; older `response_model`). Python then dispatches. Prefer this over `agno.team.Team` routing: one call, deterministic
dispatch, easy to test. If Team routing in the installed version gives the same control with no extra calls, it may be used; document why.

```python
class Intent(BaseModel):
    kind: Literal["question", "draft", "edit_section", "fill_gaps", "gap_answer", "data_check", "about", "chitchat"]
    segments: list[str] = []          # validated against S.segments
    bases: list[str] = []             # GROSS / NET
    accident_years: list[int] = []
    section_id: str | None = None     # for edit_section; must be in drafting.ORDER
    instruction: str = ""             # the user's instruction, verbatim, for edit_section / draft
    brief_changes: dict = {}          # same JSON contract as today's BRIEF_RULES; validated by brief.validate()
    question: str | None = None       # one clarifying question if scope is genuinely ambiguous
```
Context given to the Orchestrator: study shape (LoB, segments, bases, evaluation), whether a memo exists in this chat and its section ids,
whether an interview is active (and the pending item), the last 6 turns.
Fallback when Gemini is unavailable: today's regex `route()` (keep it, rename `route_rules`), plus `brief.needs_reading`.
Fixes the v2 bugs: "draft the memo for GL" now scopes to GL; "help me understand IBNR" is a question, not "about".

## 4. Tools (Python; LLM only chooses and passes arguments)
Every tool returns JSON built by `Result.to_llm(ledger)`; numeric cells become `"<display> [Fxxxx]"` strings (e.g. `"-1.46 [F0031]"`),
summary scalars likewise. Docstrings are the tool descriptions the LLM sees — keep them precise.

| Group | Tool | Purpose | Status |
|---|---|---|---|
| Study | `find_sections(question)` | Tabs/columns behind a question | exists (`X.navigate`) |
| | `reserve_summary(basis)` | Case OS, IBNR, legal, adjuster, total unpaid by sub-segment | exists |
| | `prior_year_development(basis, sub_segment)` | PYD by prior AY (Comparison col 12) | exists |
| | `reinsurance_ratios()` | Net/gross EP by AY | exists |
| | `ldf_selection(sub_segment, triangle, basis)` | Selected vs prior LDF per age + reason code | exists |
| | `method_weights(sub_segment, basis)` | Weights by AY band | exists |
| | `method_indications(sub_segment, basis, ay_from, ay_to)` | Each method's ultimate vs selected; spread | exists |
| | `what_if_weights(sub_segment, weights, basis, ay_from, ay_to)` | Re-weight Apollo indications (no recalculation) | exists |
| | `sensitivity_range(sub_segment, basis, spec)` | Low/high ultimates from Apollo indications | wrap `X.sensitivity` |
| | `diagnostic(sub_segment, tab, basis)` | Latest diagonal vs prior AYs; flags | exists |
| | `actual_vs_expected(sub_segment, basis)` | Incurred AvE by AY | exists |
| | `claim_movements(sub_segment, accident_year, basis, n)` | Claim rows: ClaimID, prior/current/change, CAT flag, **Loss_Description**, share of A−E; plus `rule_label` from today's `investigate()` | **new** (Claims Investigator only) |
| | `read_exhibit(sub_segment, tab, basis, columns, accident_years, block)` | Any tab/block with cell refs | exists |
| Evidence | `calculate(expression, unit, label)` | e.g. `"F0031 / F0040"`, `"F0012 - F0013"`, `"F0001 + F0002 + F0003"`; result registered as a new ref with derivation | **new** |
| | `explain_figure(ref)` | Value, unit, source cell, derivation chain | **new** |
| Memo | `read_memo_section(section_id)` | Section text with placeholders + its refs | **new** |
| | `search_memo(text)` | Section ids/snippets mentioning a topic | **new** |
| | `redraft_section(section_id, instruction)` | Section Writer rewrites one section; docx rebuilt | **new** (Orchestrator dispatch, not exposed to Analyst) |
| | `list_open_inputs()` | Open `[INPUT REQUIRED]` items + judgmental selections needing rationale, ranked | **new** |
| | `fill_input(item_id, text)` | Insert the actuary's words verbatim (block type `"u"`, labelled "Analyst input") | **new** |
| Data | `data_check()` | Data Steward card | exists (`steward.check`) |
| | `scan_study()` | Deterministic candidate review items for First Look | **new** |
| Agent-as-tool | `ask_claims_investigator(sub_segment, accident_year, basis)` | Runs the Claims Investigator; returns its classification JSON | **new** |

`calculate` safety: parse with `ast`, allow only ref names, numeric literals, `+ - * /`, parentheses, unary minus, `abs`, `sum`, `min`, `max`.
Reject anything else with a plain error message back to the LLM. Units: `mm`, `pct`, `factor`, `num`; ratio of two `mm` → `pct` unless told.

## 5. Module plan (minimal churn; keep file names where possible)
| File | Change |
|---|---|
| `ledger.py` **new** | `Ledger` = today's `drafting.Citations` generalised: `cite(value, unit, source)`, `from_result(res,row,col)`, `register_result(res)` (refs for every numeric cell + summary scalar), `calculate(expr, unit, label)`, `explain(ref)`, `render(text, ctx)`, `to_json()/from_json()`. Ref ids stay `F0001…` and are **per chat** (one ledger shared by chat answers and the memo). `drafting.Citations` becomes an alias or thin subclass so the memo code keeps working. |
| `study.py` | `Result.to_llm(ledger=None)`: when a ledger is passed, numeric cells/summary scalars are emitted with their refs. No change to `display()`. |
| `specialists.py` | Add `claim_movements()` and `scan_study()`. Keep `investigate()` (its label becomes `rule_label`). `DRIVER_TEXT` stays only as the no-LLM fallback. |
| `agents.py` | Keep Gemini plumbing (`gemini_state`, `_agent`, `_run`, `_api_failed`, limiter use). Move tool closures to `tools.py`. Add the agent factories and their instructions: `orchestrator()`, `analyst()`, `claims_investigator()`, `section_writer(section_id)`, `interviewer()`, `first_look()`. Replace `verify_answer` with the ref verifier (§2). Remove `polish_memo` once Section Writers pass (keep its gate logic in the verifier). |
| `tools.py` **new** | `build_tools(S, ledger, memo, turn, allow)` → the tool closures in §4, filtered by the agent's allow-list. Each call appends a step `("<agent>", "<what> — requested by Gemini")` as today. |
| `memo.py` **new** | Memo state per chat: sections (with placeholders), brief, open inputs, docx path. `load(chat_id)`, `save(chat_id, memo)`, `read_section`, `search`, `replace_section`, `open_inputs()`, `fill(item_id, text)`, `rebuild_docx()` (calls `drafting.assemble`). Persist as a chat artifact `memo_state` (JSON) — not only HTML. |
| `drafting.py` | `draft()` builds tables and fixed sections in code as today, then calls `section_writer` for narrative sections: **pyd, assumptions, methods, selection, diagnostics, risks**. Run writers in parallel (`ThreadPoolExecutor`, bounded by the limiter). Each writer output must pass `verify()`; on failure or Gemini unavailable, keep the code-drafted section (today's text) and say so in steps. Agent conclusions are wrapped "*Proposed — actuary to confirm:* …" where they state a judgement. |
| `brief.py` | Keep `validate()` and `describe()`; the Orchestrator supplies `brief_changes`. `needs_reading` remains for the no-LLM fallback. |
| `app.py` | Dispatch on `Intent.kind`. Render placeholders in answers as values with a hover source (`<span title="source">value</span>`) and a collapsed **"Figures"** list (ref → value → source/derivation). First Look card after the readiness card. Interview mode: while `interview` artifact is active, show "Item k of n" and route replies as `gap_answer`. Update `ABOUT`/`DOT` to the v3 agents. |
| `evaluate.py` | Add the checks in §7. |
| `first_look.py` | Not needed — First Look lives in `agents.py` + `tools.scan_study`; cache JSON at `output/first_look_<signature>.json`. |

## 6. Agent behaviour specs (put these into the instructions; keep each prompt short)
**Common rules (all agents):** answer only from tool results; write every number as `{{Fxxxx}}`; never type digits for amounts, ratios
or factors; use `calculate` for any arithmetic; name the Apollo tab/column behind key figures; if the data can't support something, say
"not in the data" (chat) or leave `[INPUT REQUIRED: …]` (memo); state assumptions in one line ("BF" ⇒ Incurred BF).

**Analyst.** Plan the steps first (internally), call tools, then answer: one direct sentence, then 2–5 bullets of interpretation for the
reserve review. Tables/charts from its tool calls are shown automatically — refer to them, don't reproduce them. For claim-level
"why" questions, call `ask_claims_investigator`, never interpret raw claims itself. For "why does the memo say…", use
`search_memo` / `read_memo_section` and then the study tools to explain.

**Claims Investigator.** Input: sub-segment, AY, basis. Read `claim_movements` and `actual_vs_expected`. Output JSON:
`{label: one_off|release|cat|trend|mixed, rationale (≤3 sentences, refs only), claims: [ClaimID…], ex_cat_ref (via calculate when CAT
present), rule_label, agrees_with_rule: bool}`. Use the **Loss_Description** text (e.g. "liability accepted", "settled below reserve",
"winter storm", repeated causes such as "occupational hearing loss" across AYs). If it disagrees with `rule_label`, say why.

**Section Writer.** Input: section id, its current code-drafted blocks (placeholders), the brief, optional user instruction. Pull the
evidence it needs, write the section's paragraphs/bullets with placeholders only. Never touch blocks of type `"u"` (actuary's words) or
tables. Keep the template's intent for that section. Judgements are labelled "Proposed — actuary to confirm".

**Interviewer.** Start with `list_open_inputs()`; ask **one** item at a time, most material first (judgmental LDFs outside averages,
then PYD/AvE drivers needing confirmation, then single-topic sections: reinsurance, Stat/GAAP, limitations, roles…). Show the evidence
(refs) with the question. When the user answers, call `fill_input(item_id, text)` with the user's words **verbatim**, confirm where it
went, then ask the next item. "skip" → next item; "stop" → end interview.

**First Look.** Call `scan_study()` (deterministic candidates with refs: largest PYD AYs, judgmental/outside-average LDFs, diagnostic
calendar-year flags, largest |A−E| with its rule label, widest method spread). Pick and phrase the top five, each one line with refs and
a one-line "what to check". Return also a follow-up question per item (rendered as clickable suggestions). Fallback without Gemini:
show the top five candidates by a fixed priority order, deterministic wording.

## 7. Evaluation additions (`evaluate.py`, offline unless marked live)
- **Ledger:** refs dedupe by source; `calculate` arithmetic matches pandas; rejects `__import__`, names that aren't refs, attribute access; derivation chain in `explain`.
- **to_llm refs:** every numeric cell in a `to_llm(ledger)` payload resolves in the ledger to the same displayed value.
- **Ref verifier:** passes an answer with only placeholders; flags a bare `$1.46m`; allows years/ages/claim IDs.
- **Scripted-model wiring test:** a `FakeModel`/scripted runner that calls the tool closures in a fixed sequence (e.g. PYD → calculate → ask_claims_investigator) and returns a canned answer with placeholders → renders, verifies, produces steps. Proves dispatch, tool allow-lists (Analyst cannot call `claim_movements`), and rendering without Gemini.
- **Memo state:** draft → `memo_state` saved → `read_section`/`search` work → `fill_input` inserts verbatim `"u"` block and the docx rebuilds → `replace_section` keeps tables and `"u"` blocks.
- **Section Writer fallback:** with Gemini off, `draft()` produces today's memo (same section count, all sections verify).
- **scan_study:** contains the planted items (WC AverageCaseOS shift, WC 24m manual LDF, AL 48m prior outside averages, AL 2021 one-off, widest spread AY).
- **claim_movements:** returns descriptions; AL 2021 top claim CLM000154; GL 2019 CLM000302 release.
- **Orchestrator fallback:** regex router still passes the existing ROUTES set.
- **Live (needs key, `python evaluate.py --live`):** Orchestrator intent golden set (incl. "draft the memo for GL" → draft, segments [GL]; "help me understand IBNR for AL" → question; "make the PYD section shorter" → edit_section pyd; "let's fill the gaps" → fill_gaps); Q&A golden set answers verify with 0 untraced; Claims Investigator labels on the four planted drivers and flags the WC hearing-loss cluster; full draft: all writer sections verify.

## 8. Phases (commit after each; `python evaluate.py` green before moving on)
1. **Ledger + refs.** `ledger.py`; `Result.to_llm(ledger)`; `drafting.Citations` → ledger; `calculate`, `explain_figure`. Memo output identical to v2 (diff the evidence JSON counts).
2. **Tools module.** Move closures to `tools.py` with allow-lists; add `claim_movements`, `sensitivity_range`, `scan_study`; ref verifier + one-retry loop.
3. **Analyst on refs.** Analyst instructions/rendering with placeholders; "Figures" panel; Claims Investigator agent + `ask_claims_investigator`. *Live check.*
4. **Memo state + memo tools.** `memo.py`; persist `memo_state`; `read_memo_section`, `search_memo` for the Analyst.
5. **Orchestrator.** Structured-output intent + dispatch in `app.py`; regex fallback; absorbs Brief agent (keep `brief.validate`). *Live check.*
6. **Section Writers.** Parallel writers in `draft()`; `edit_section` → `redraft_section`; verifier gate + fallback; remove `polish_memo`.
7. **Interviewer.** `list_open_inputs`, `fill_input`, interview artifact and UI ("Item k of n", skip/stop). *Live check.*
8. **First Look.** `scan_study` + agent + cache + clickable suggestions after the readiness card.
9. **Docs.** `CLAUDE.md` v3, `README.md`, `ABOUT`/`DOT` diagram in the app.

## 9. Live demo script (run after phases 3, 5, 7 with the paid key)
1. Open a new chat → readiness card, then First Look with five items (WC case OS, WC 24m override, AL 48m prior, AL 2021, WC 2024 spread).
2. "Why did GL develop favourably, and how much of it is AY 2019?" → PYD, `calculate` share, Claims Investigator: CLM000302 release; all figures are refs.
3. "What is driving WC AY 2022?" → Claims Investigator reads descriptions: occupational hearing loss cluster across AY 2022–2024.
4. "Draft the memo for GL only, explain AvE above $1m" → scoped draft; Basis of this draft shows scope and threshold.
5. "Where does the total gross unpaid come from?" → `explain_figure` on the memo total.
6. "Make the PYD section two paragraphs for the Reserve Committee" → only that section changes; docx rebuilt.
7. "Let's fill the gaps" → Interviewer item 1 (WC 24m override rationale); answer; verbatim in Assumptions; item 2 offered.
8. Turn Gemini off (bad key under ⚙) → draft still works (code wording), Analyst says Gemini is needed and points to tabs.

## 10. CLAUDE.md v3 changes (phase 9)
- §2: replace "chat answers are verified against the tool results" with the reference rule (§2 above); new call budget (paid key, `GEMINI_RPM` default 60); Gemini-unavailable fallbacks per agent.
- §5: replace the agent table with §3.1; specialists listed as tools; add the tool table (§4).
- §5a: brief changes come from the Orchestrator's `brief_changes`; validation unchanged.
- §8: add §7 checks. §9: add "v3 (2026-11): agentic Orchestrator, ledger refs everywhere, Section Writers, Interviewer, First Look; Agno kept; paid Gemini; Schema Mapper/Reviewer deferred."

## 11. Guardrails for the implementer
- No new frameworks or services. New dependencies only if unavoidable (none expected beyond `pydantic`, already pulled in by Agno).
- Keep functions small and variables traceable to their source (KK's standing preference). No dead code: delete what v3 replaces once its fallback path is covered by tests.
- Never call Gemini in a loop without the limiter; every agent run keeps the 20 s timeout and `_run()` status check.
- Do not change Apollo parsing, §4 column definitions or the synthetic data generator.
- Keep the app working on `main`'s data folder and on chat uploads.
