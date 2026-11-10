"""Agent 0 Orchestrator (router), Agent 3 Analyst (Gemini plans + specialist/Claims Investigator tools), chat Verifier
(ledger-based), and the verifier-gated memo polish. The Analyst never falls back to keyword scripting: without
Gemini it says so and points to the relevant tabs."""
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


# ---------------- Orchestrator: routing ----------------
def route(text):
    t = text.lower()
    if re.search(r"\b(re-?draft|draft|prepare|write|generate|create|update|revise)\b.*\b(memo|dvr|report)\b|^\s*(re-?)?draft\b", t): return "draft"
    if re.search(r"\b(what|which) (data|files)\b|\bdata (is )?available\b|\bready\b|\breadiness\b|\bmissing\b|\bdata check\b", t): return "readiness"
    if re.search(r"what can you do|how do you work|\barchitecture\b|\bwhich agents\b|^\s*help\b|\bcapabilit", t): return "about"
    return "analyse"


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


def analyst_tools(S, ledger, turn):
    return T.build_tools(S, ledger, turn, T.STUDY_TOOLS + T.EVIDENCE_TOOLS) + [_ask_claims_investigator(S, ledger, turn)]


ANALYST_RULES = """You are Scribe's Analyst for an Apollo reserve study: {lob} sub-segments {segments}; bases GROSS and NET;
evaluation {evaluation}. Use the sub-segment codes in tool calls.
- Answer ONLY from tool results. Call tools first; use find_sections when unsure where something lives.
- Every number you write — amount, ratio, factor — must be a {{Fxxxx}} placeholder copied from a tool result. Never type a
  digit yourself; use calculate for any arithmetic (shares, differences, sums) — it returns a new {{Fxxxx}} for the result.
- For claim-level "why" questions (what drove an AY's movement), call ask_claims_investigator — never interpret claims yourself.
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


def analyse(S, text, history, ledger):
    """Analyst turn. Gemini plans which tools (incl. the Claims Investigator) to call; Python runs them and the
    ref verifier checks every figure against the ledger, with one retry if it finds untraced numbers.
    Returns (answer — raw {{Fxxxx}} text, results, steps, untraced issues, by_gemini, refs used)."""
    turn = dict(results=[], steps=[("Orchestrator", "Routed to the Analyst")])
    if gemini_model():
        try:
            ctx = "\n".join(f"{r}: {m[:600]}" for r, m in history[-6:])
            agent = _agent(ANALYST_RULES.format(lob=S.lob, segments=", ".join(f"{s} ({seg_name(s)})" for s in S.segments), evaluation=S.evaluation),
                           analyst_tools(S, ledger, turn))
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


# ---------------- Brief agent: the user's drafting instructions → validated drafting brief (one Gemini call) ----------------
BRIEF_RULES = """You turn a reserving actuary's drafting instructions into changes to a JSON drafting brief for a DVR memo.
Study: <lob>; sub-segments <segments>; accident years <ays>; evaluation <evaluation>. Apollo method keys: <methods>.
Current brief: <current>
Return ONLY a JSON object: {"changes": {...}, "assumptions": ["..."], "unsupported": [{"request": "...", "reason": "..."}], "question": ""}
"changes" holds only the fields the latest instruction sets or changes:
- segments: list of sub-segment codes (null = all). bases: subset of ["GROSS","NET"].
- sensitivity: the FULL list of scenarios, each one of
  {"type":"min_max","methods":[keys] or null (= all actuarial methods),"ay_from":int|null,"ay_to":int|null}
  {"type":"methods","low":key,"high":key,"ay_from":int|null,"ay_to":int|null}
  {"type":"weights","weights":{key: fraction},"ay_from":int|null,"ay_to":int|null}
- materiality: dollar threshold for explaining AvE by accident year ("$1m" -> 1000000).
- ays_explained: how many accident years to explain per sub-segment in the PYD/AvE commentary.
- focus: {segment code: [accident years]} always explained. exclude_cat: true to also show AvE excluding CAT claim movements.
- context: {section: the user's own words} for text the user gives for sections: <sections>. Copy their words exactly; never write your own.
Rules: Scribe never recalculates Apollo. Anything needing new LDFs, ELRs, tail factors, trends or indications Apollo did not produce, a change to
the memo's table definitions, or invented text goes to "unsupported" with a one-line reason. "Highest/lowest of all methods" = min_max.
"BF" without paid/incurred = inc_bf, with the assumption "BF taken as Incurred BF". Assumptions: short sentences without figures.
Set "question" only if the instruction cannot be carried out without an answer and no reasonable assumption exists; otherwise "".
Omit fields the user did not mention."""


def read_brief(S, text, prev):
    """Brief agent. Returns (brief, rejected, assumptions, question); rejected = [(request, reason)]. Raises if Gemini is unavailable."""
    if not gemini_model(): raise RuntimeError(gemini_state()["why"] or "Gemini unavailable")
    lo, hi = B._years(S)
    rules = BRIEF_RULES
    for k, v in dict(lob=S.lob, segments=", ".join(f"{s} ({seg_name(s)})" for s in S.segments), ays=f"{lo}–{hi}", evaluation=S.evaluation,
                     methods=", ".join(f"{k} = {n}" for k, n in X.METHOD_KEYS.items()), current=json.dumps({k: v for k, v in prev.items() if k != "user"}),
                     sections=", ".join(B.CONTEXT_SECTIONS)).items():
        rules = rules.replace(f"<{k}>", str(v))
    try:
        out = json.loads(re.search(r"\{.*\}", re.sub(r"```(json)?", "", _run(_agent(rules), f"Instruction: {text}")), re.S).group(0))
    except Exception as e:
        if not isinstance(e, (ValueError, AttributeError)): _api_failed(e)
        raise RuntimeError(gemini_state()["why"] or "unreadable reply from Gemini")
    brief, bad = B.validate(out.get("changes") or {}, S, text, prev)
    figs = B._figures(text)
    keep = lambda a: all(float(x) in figs for x in re.findall(r"\d+(?:\.\d+)?", a.replace(",", "")) if not 1900 <= float(x) <= 2100)
    assumptions = [a for a in out.get("assumptions") or [] if isinstance(a, str) and keep(a)]
    bad += [(u.get("request", ""), u.get("reason", "")) for u in out.get("unsupported") or [] if isinstance(u, dict)]
    return brief, bad, assumptions, str(out.get("question") or "").strip()


# ---------------- memo polish (one Gemini call per memo, verifier-gated) ----------------
POLISH_RULES = """You edit a first-draft DVR (reserve review) memo written by a reserving actuary. The input is a JSON object mapping a
section id to a list of strings. Rewrite each string into clear, concise, professional actuarial prose. HARD RULES: keep every placeholder
like {{F0012}} exactly; never write a digit yourself; never add facts, reasons or names; keep every [INPUT REQUIRED: ...] marker verbatim.
Return ONLY a JSON object with the same keys, each a list of the same length and order."""


def polish_memo(sections, C, trace):
    """All narrative sections in ONE Gemini call. A sentence that fails tie-out keeps its drafted text."""
    if not gemini_model():
        trace("2 Drafting team", f"{gemini_state()['why'] or 'Gemini unavailable'} — memo uses code-drafted wording (all figures cited)"); return
    idx = {s["id"]: [i for i, b in enumerate(s["blocks"]) if b[0] in ("p", "b")] for s in sections if s["narrative"]}
    by_id = {s["id"]: s for s in sections}
    src = {k: [by_id[k]["blocks"][i][1] for i in v] for k, v in idx.items() if v}
    try:
        reply = _run(_agent(POLISH_RULES), json.dumps(src))
        out = json.loads(re.search(r"\{.*\}", re.sub(r"```(json)?", "", reply), re.S).group(0))
    except Exception as e:
        if not isinstance(e, (ValueError, AttributeError)): _api_failed(e)
        trace("2 Drafting team", f"Gemini polish skipped ({gemini_state()['why'] or 'unreadable reply'}) — code-drafted wording kept"); return
    kept = total = 0
    for k, old_list in src.items():
        new_list = out.get(k) if isinstance(out.get(k), list) and len(out.get(k)) == len(old_list) else [None] * len(old_list)
        for i, new, old in zip(idx[k], new_list, old_list):
            total += 1
            if isinstance(new, str) and sorted(D.PH.findall(new)) == sorted(D.PH.findall(old)) and not D.untraced(new, C) \
                    and new.count("[INPUT REQUIRED") == old.count("[INPUT REQUIRED"):
                by_id[k]["blocks"][i] = (by_id[k]["blocks"][i][0], new); kept += 1
    trace("4 Verifier", f"Gemini polished the memo in 1 call; {kept}/{total} sentences passed tie-out (the rest keep the drafted wording)")
