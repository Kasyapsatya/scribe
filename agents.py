"""Orchestrator (router), Analyst (Gemini plans + specialist/Claims Investigator tools), Section Writers (parallel,
one Gemini call per section, verifier-gated), and the ledger-based ref verifier shared by chat answers and the
memo. The Analyst never falls back to keyword scripting: without Gemini it says so and points to the relevant tabs."""
import json, re, time
from typing import Literal
from pydantic import BaseModel
from config import resolve_model, make_client, is_quota_error, forget_model, retry_after, key_id
from ledger import PH, untraced
from study import seg_name
import specialists as X
import tools as T
import drafting as D
import brief as B

_COOLDOWN = {}      # key fingerprint → {"until", "why"}: per-minute limit → Google's retry delay; daily / network → longer


def gemini_state():
    """Cool-down state of the API key in use by this session (one teammate's exhausted key never blocks another's)."""
    return _COOLDOWN.setdefault(key_id(), {"until": 0.0, "why": ""})


def gemini_model():
    """Model id if Gemini is usable now, else None (never raises)."""
    if time.time() < gemini_state()["until"]: return None
    try: return resolve_model()
    except Exception as e:
        _api_failed(e); return None


def _agent(instructions, tools=None, output_schema=None):
    from agno.agent import Agent
    from agno.models.google import Gemini
    return Agent(model=Gemini(id=resolve_model(), client=make_client(), retries=0), instructions=instructions,
                 tools=tools or [], output_schema=output_schema, markdown=output_schema is None, tool_call_limit=8)


def _api_failed(e):
    """Pause Gemini for as long as the error warrants instead of switching it off for the session."""
    if "404" in str(e) or "not enabled" in str(e).lower(): forget_model()
    bad_key = "API_KEY_INVALID" in str(e) or "API key not valid" in str(e)
    wait = retry_after(e) if is_quota_error(e) else None
    state = gemini_state()
    state["until"] = time.time() + (wait + 1 if wait else 3600 if is_quota_error(e) else 30 if bad_key else 60)
    state["why"] = ("Gemini rejected the API key — check it under ⚙" if bad_key else "Gemini per-minute limit reached" if wait
                 else "Gemini daily quota reached for this key" if is_quota_error(e) else "Gemini unreachable")


def _run(agent, prompt):
    """Agno returns API errors as content with status ERROR instead of raising — turn them back into exceptions."""
    out = agent.run(prompt)
    if str(getattr(out, "status", "")).upper().endswith("ERROR"): raise RuntimeError(out.content or "Gemini error")
    return out.content or ""


# ---------------- Orchestrator: routing (PLAN_v3 §3.1) ----------------
class Intent(BaseModel):
    kind: Literal["question", "draft", "edit_section", "fill_gaps", "gap_answer", "data_check", "about", "chitchat"]
    segments: list[str] = []
    bases: list[str] = []
    accident_years: list[int] = []
    section_id: str | None = None
    instruction: str = ""
    brief_changes: dict = {}
    question: str | None = None


def route_rules(text):
    """Deterministic fallback kind when Gemini is unavailable (no scope/brief_changes extraction)."""
    t = text.lower()
    if re.search(r"\b(re-?draft|draft|prepare|write|generate|create|update|revise)\b.*\b(memo|dvr|report)\b|^\s*(re-?)?draft\b", t): return "draft"
    if re.search(r"\b(what|which) (data|files)\b|\bdata (is )?available\b|\bready\b|\breadiness\b|\bmissing\b|\bdata check\b", t): return "data_check"
    if re.search(r"what can you do|how do you work|\barchitecture\b|\bwhich agents\b|^\s*help\b|\bcapabilit", t): return "about"
    return "question"


ORCH_RULES = """You are Scribe's Orchestrator for an Apollo reserve study: {lob}; sub-segments {segments}; bases GROSS and
NET; accident years {ays}; evaluation {evaluation}. Apollo method keys: {methods}.
Classify the user's latest message into exactly one kind:
- "question": anything about the study's numbers, methods, diagnostics, LDFs, AvE, claims, or "why does the memo say X" —
  including requests to explain or understand a figure. Never use "about" for these.
- "draft": draft or redraft the memo (first draft, or re-run it with new instructions).
- "edit_section": change or rewrite ONE existing section of an already-drafted memo (e.g. "make the PYD section shorter").
  section_id must be one of: {order}. instruction = the user's words, verbatim. Only valid when a memo already exists
  (see context below) — otherwise use "draft".
- "fill_gaps": the user wants to start answering the memo's open [INPUT REQUIRED] items ("let's fill the gaps", "what's
  still missing"). Only valid when a memo already exists.
- "gap_answer": the context below says an interview is active and this message answers the pending item, or says "skip"/"stop".
- "data_check": asking what data is available, what's missing, or whether Scribe is ready.
- "about": asking how Scribe works, its architecture, or what it can do — not a question about the study itself.
- "chitchat": greetings or anything with no study content.
segments/bases/accident_years: scope named in THIS message (empty = unspecified/all) — set these for "question" and
"draft" alike (e.g. "draft the memo for GL" -> segments ["GL"]; "how is AvE doing for AL" -> segments ["AL"]).
brief_changes (only for "draft"): include ONLY fields this message actually sets, same JSON contract as the drafting
brief — {"segments": [...] or null, "bases": [...], "sensitivity": [{"type":"min_max","methods":[keys] or null,
"ay_from":int|null,"ay_to":int|null} | {"type":"methods","low":key,"high":key,...} | {"type":"weights",
"weights":{key:fraction},...}], "materiality": number, "ays_explained": int, "focus": {seg: [years]},
"exclude_cat": bool, "context": {section: "the user's own words, verbatim"}}. Never invent a figure or sentence not
in the user's words. "BF" without paid/incurred = inc_bf.
instruction: the user's words verbatim, for "edit_section" only.
question: set only if the request is genuinely ambiguous with no reasonable assumption; otherwise null."""


def orchestrator(S):
    lo, hi = B._years(S)
    rules = ORCH_RULES
    for k, v in dict(lob=S.lob, segments=", ".join(f"{s} ({seg_name(s)})" for s in S.segments), ays=f"{lo}–{hi}", evaluation=S.evaluation,
                     methods=", ".join(f"{k} = {n}" for k, n in X.METHOD_KEYS.items()), order=", ".join(D.ORDER)).items():
        rules = rules.replace(f"{{{k}}}", str(v))
    return _agent(rules, output_schema=Intent)


def route_llm(S, text, history, memo_exists, section_ids, interview_pending):
    """One Gemini call -> Intent, or None if Gemini is unavailable/fails (caller falls back to route_rules)."""
    if not gemini_model(): return None
    try:
        ctx = "\n".join(f"{r}: {m[:400]}" for r, m in history[-6:])
        ctx += ("\nA memo already exists in this chat" + (f" (sections: {', '.join(section_ids)})." if section_ids else ".")
                if memo_exists else "\nNo memo has been drafted in this chat yet.")
        if interview_pending: ctx += f"\nAn interview is active; pending item: {interview_pending}."
        return _run(orchestrator(S), f"{ctx}\n\nUser message: {text}")
    except Exception as e:
        _api_failed(e); return None


# ---------------- Claims Investigator (agent-as-tool for the Analyst/Section Writer) ----------------
class ClaimsVerdict(BaseModel):
    label: Literal["one_off", "release", "cat", "trend", "mixed"]
    rationale: str                 # ≤3 sentences, every number a {{Fxxxx}} placeholder
    claims: list[str]              # ClaimIDs supporting the label
    ex_cat_ref: str | None = None  # Fxxxx from calculate(), only when CAT claims contributed
    rule_label: str                # claim_movements' own rule-based label, for comparison
    agrees_with_rule: bool


CLAIMS_RULES = """You are Scribe's Claims Investigator for one accident year of one sub-segment. Call claim_movements
first (it gives you the rule-based label, loss descriptions and each claim's share of A − E), then actual_vs_expected
if you need the AY's total A − E. Classify the deviation as:
- one_off: a single claim's movement dominates (and is positive — a reserve increase).
- release: a single claim's movement dominates and is negative (a reserve release).
- cat: CAT-flagged claims explain most of the movement; use calculate for the ex-CAT figure (ex_cat_ref).
- trend: spread across many claims, often with a repeated cause in their loss descriptions (e.g. "occupational
  hearing loss" across several AYs) — no single claim dominates.
- mixed: none of the above clearly dominates.
Read the loss descriptions yourself: if they suggest a different label than claim_movements' rule_label, say so
in rationale and set agrees_with_rule to false. Every number in rationale must be a {{Fxxxx}} placeholder from a
tool result or calculate() — never type a digit. List the ClaimIDs (claims) that best support your label."""


def claims_investigator(S, ledger, turn):
    return _agent(CLAIMS_RULES, T.build_tools(S, ledger, turn, ["claim_movements", "actual_vs_expected", "calculate"]), output_schema=ClaimsVerdict)


def _ask_claims_investigator(S, ledger, turn):
    def ask_claims_investigator(sub_segment: str, accident_year: int, basis: str = "GROSS") -> str:
        """Claims Investigator: what drove one accident year's AvE deviation, reading claim amounts AND loss
        descriptions (one-off, release, CAT, trend or mixed). Always use this for claim-level "why" questions —
        never call claim_movements yourself."""
        try:
            out = _run(claims_investigator(S, ledger, turn), f"Sub-segment {sub_segment}, accident year {accident_year}, basis {basis}.")
            turn["steps"].append(("Claims Investigator", f"Classified {sub_segment} AY{accident_year}: {out.label} — requested by Gemini"))
            return out.model_dump_json()
        except Exception as e:
            _api_failed(e); turn["steps"].append(("Claims Investigator", f"failed: {e}")); return f"ERROR: {e}"
    return ask_claims_investigator


def analyst_tools(S, ledger, turn, memo=None):
    return T.build_tools(S, ledger, turn, T.STUDY_TOOLS + T.EVIDENCE_TOOLS + T.MEMO_TOOLS, memo) + [_ask_claims_investigator(S, ledger, turn)]


ANALYST_RULES = """You are Scribe's Analyst for an Apollo reserve study: {lob} sub-segments {segments}; bases GROSS and NET;
evaluation {evaluation}. Use the sub-segment codes in tool calls.
- Answer ONLY from tool results. Call tools first; use find_sections when unsure where something lives.
- Every number you write — amount, ratio, factor — must be a {{Fxxxx}} placeholder copied from a tool result. Never type a
  digit yourself; use calculate for any arithmetic (shares, differences, sums) — it returns a new {{Fxxxx}} for the result.
- For claim-level "why" questions (what drove an AY's movement), call ask_claims_investigator — never interpret claims yourself.
- For "why does the memo say X" or "where does the memo cover Y", use search_memo then read_memo_section — these work only
  once a memo has been drafted in this chat; say so if they return an error.
- Name the Apollo tab (and column number where given) behind each key figure.
- Tables and charts from your tool calls are shown automatically under your answer — refer to them, do not reproduce whole tables.
- "BF" without paid/incurred: assume Incurred BF and say so in one line. If a what-if lacks weights or AYs, assume all AYs and 100% to the
  named method, and state the assumption. If the sub-segment is ambiguous and it matters, ask one short question.
- Be concise: a direct answer, then 2–5 bullets of interpretation (what it means for the reserve review). No preamble."""


# ---------------- results summary when Gemini fails after choosing tools ----------------
def headline(res):
    """One bullet per result from its summary (scalar values only) — deterministic, already formatted."""
    show = lambda v: ", ".join(f"{a} {b}" for a, b in v.items()) if isinstance(v, dict) else v
    items = [f"{k.replace('_', ' ')}: {show(v)}" for k, v in res.summary.items()
             if k != "label" and isinstance(v, (str, int, dict)) and v != "" and not (isinstance(v, dict) and any(isinstance(x, dict) for x in v.values()))][:4]
    return f"- **{res.title}** — " + ("; ".join(items) if items else "see table")


def analyse(S, text, history, ledger, memo=None):
    """Analyst turn. Gemini plans which tools (incl. the Claims Investigator and, once a memo exists, the memo
    tools) to call; Python runs them and the ref verifier checks every figure against the ledger, with one retry
    if it finds untraced numbers. Returns (answer — raw {{Fxxxx}} text, results, steps, untraced issues, by_gemini, refs used)."""
    turn = dict(results=[], steps=[("Orchestrator", "Routed to the Analyst")])
    if gemini_model():
        try:
            ctx = "\n".join(f"{r}: {m[:600]}" for r, m in history[-6:])
            agent = _agent(ANALYST_RULES.format(lob=S.lob, segments=", ".join(f"{s} ({seg_name(s)})" for s in S.segments), evaluation=S.evaluation),
                           analyst_tools(S, ledger, turn, memo))
            prompt = (f"Conversation so far:\n{ctx}\n\n" if ctx else "") + f"User question: {text}"
            answer = _run(agent, prompt)
            issues = untraced(answer, ledger)
            if issues:
                retry = _run(agent, f"Your previous answer had untraced numbers ({'; '.join(issues)}). Every amount, ratio or factor must be "
                                     f"a {{{{Fxxxx}}}} placeholder from a tool result or calculate(). Rewrite the full answer:\n\n{answer}")
                retry_issues = untraced(retry, ledger)
                if len(retry_issues) < len(issues): answer, issues = retry, retry_issues
            n = len(PH.findall(answer))
            turn["steps"].append(("Verifier", f"{n - len(issues)}/{n} figures traced to the ledger" + (f"; untraced: {', '.join(issues)}" if issues else "")))
            return answer, turn["results"], turn["steps"], issues, True, PH.findall(answer)
        except Exception as e:
            _api_failed(e); turn["steps"].append(("Orchestrator", f"{gemini_state()['why']} while writing the answer"))
            if turn["results"]:                          # Gemini had already chosen the specialists: show what they returned
                return (f"{gemini_state()['why']} before Gemini could write its commentary. These are the exhibits Gemini chose for this question:\n\n"
                        + "\n".join(headline(r) for r in turn["results"])), turn["results"], turn["steps"], [], False, []
    hits = X.navigate(S, text)[:4]
    turn["steps"].append(("Study Navigator", "Searched the tab catalogue (no AI) to point you to the right exhibits"))
    tabs = "; ".join(f"**{h['tab']}** ({h['title']})" for h in hits) or "none matched — try naming the exhibit"
    return (f"{gemini_state()['why'] or 'Gemini is unavailable'}. The Analyst needs Gemini to plan which exhibits to read, so I can't answer this "
            f"one right now — {'it will work again once the limit resets' if 'quota' in gemini_state()['why'] or 'limit' in gemini_state()['why'] else 'please try again shortly'}. "
            f"The Apollo tabs most relevant to your question: {tabs}."), [], turn["steps"], [], False, []


# ---------------- Section Writer (parallel, one Gemini call per section) ----------------
WRITER_SECTIONS = ["pyd", "assumptions", "methods", "selection", "diagnostics", "risks"]


class SectionDraft(BaseModel):
    texts: list[str]   # same length and order as the section's current paragraphs/bullets/headings


SECTION_WRITER_RULES = """You are Scribe's Section Writer for the "<title>" section of a DVR (reserve review) memo on an Apollo
study: <lob>; sub-segments <segments>; evaluation <evaluation>.
Rewrite the <n> paragraph(s)/bullet(s) below into clear, professional actuarial prose — same number of items, same order,
each about the same thing as before (don't merge, split, reorder or drop any). Pull any extra evidence you need with your
tools. Every number must be a {{Fxxxx}} placeholder copied from a tool result or calculate(); never type a digit. Keep every
[INPUT REQUIRED: ...] marker verbatim — never answer it yourself. Where you state a judgement or conclusion the data does
not settle on its own (e.g. your read of a diagnostic or a driver), prefix that sentence "Proposed — actuary to confirm:".
<instruction>
Current text (one item per line):
<texts>
Return exactly <n> item(s) in "texts", same order."""


def section_writer_tools(S, ledger, turn, memo=None):
    return T.build_tools(S, ledger, turn, T.STUDY_TOOLS + ["calculate", "read_memo_section"], memo) + [_ask_claims_investigator(S, ledger, turn)]


def _write_one(S, ledger, sec, instruction="", memo=None):
    """Rewrite one section's narrative blocks. Returns (section_id, new_texts_or_None, turn, reason_if_kept)."""
    turn = dict(results=[], steps=[])
    idx = [i for i, b in enumerate(sec["blocks"]) if b[0] in ("p", "b", "h3")]
    texts = [sec["blocks"][i][1] for i in idx]
    if not texts: return sec["id"], None, turn, "no narrative text in this section"
    rules = SECTION_WRITER_RULES
    for k, v in dict(title=sec["title"], lob=S.lob, segments=", ".join(S.segments), evaluation=S.evaluation, n=len(texts),
                     instruction=f"Follow this instruction while keeping every figure and marker: {instruction}" if instruction else "",
                     texts="\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))).items():
        rules = rules.replace(f"<{k}>", str(v))
    try:
        out = _run(_agent(rules, section_writer_tools(S, ledger, turn, memo), output_schema=SectionDraft),
                   f"Rewrite the {len(texts)} item(s) listed in your instructions.")
    except Exception as e:
        _api_failed(e); return sec["id"], None, turn, gemini_state()["why"] or "Gemini error"
    new_texts = getattr(out, "texts", None)
    if not new_texts or len(new_texts) != len(texts):
        return sec["id"], None, turn, f"expected {len(texts)} rewritten item(s), got {len(new_texts or [])}"
    issues = []
    for old, new in zip(texts, new_texts):
        issues += untraced(new, ledger)
        if new.count("[INPUT REQUIRED") != old.count("[INPUT REQUIRED"): issues.append("an [INPUT REQUIRED] marker changed")
    if issues: return sec["id"], None, turn, f"failed tie-out: {'; '.join(issues[:3])}"
    return sec["id"], new_texts, turn, None


def write_sections(S, sections, ledger, brief, trace, memo=None):
    """Section Writers rewrite the narrative sections in parallel (one Gemini call each, bounded by the per-key
    rate limiter); a section that fails tie-out, or any call made while Gemini is unavailable, keeps its
    code-drafted wording."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    by_id = {s["id"]: s for s in sections}
    targets = [by_id[i] for i in WRITER_SECTIONS if i in by_id and any(b[0] in ("p", "b", "h3") for b in by_id[i]["blocks"])]
    if not targets: return
    if not gemini_model():
        trace("Drafting team", f"{gemini_state()['why'] or 'Gemini unavailable'} — narrative sections use code-drafted wording"); return
    rewritten = []
    with ThreadPoolExecutor(max_workers=len(targets)) as ex:
        futures = {ex.submit(_write_one, S, ledger, s, "", memo): s["id"] for s in targets}
        for fut in as_completed(futures):
            section_id, new_texts, turn, why = fut.result()
            for agent, msg in turn["steps"]: trace(agent, msg)
            if new_texts:
                sec = by_id[section_id]; idx = [i for i, b in enumerate(sec["blocks"]) if b[0] in ("p", "b", "h3")]
                for i, t in zip(idx, new_texts): sec["blocks"][i] = (sec["blocks"][i][0], t)
                rewritten.append(section_id)
            trace("Section Writer", f"{section_id}: " + ("rewritten" if new_texts else f"kept code-drafted wording ({why})"))
    trace("Verifier", f"Section Writer rewrote {len(rewritten)}/{len(targets)} section(s); the rest keep their code-drafted wording")


def redraft_section(S, ledger, memo_state, section_id, instruction):
    """edit_section intent: Section Writer redraft of ONE existing memo section. Returns (ok, new_texts_or_reason, steps)."""
    sec = next((s for s in memo_state.sections if s["id"] == section_id), None)
    if sec is None: return False, f"no memo section '{section_id}'", []
    _, new_texts, turn, why = _write_one(S, ledger, sec, instruction, memo_state)
    return (True, new_texts, turn["steps"]) if new_texts else (False, why, turn["steps"])
