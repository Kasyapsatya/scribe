"""Agent 0 Orchestrator (router), Agent 3 Analyst (Gemini plans + specialist tools), chat Verifier, and the verifier-gated
memo polish. The Analyst never falls back to keyword scripting: without Gemini it says so and points to the relevant tabs."""
import json, re, time
from config import resolve_model, make_client, is_quota_error, forget_model, retry_after, key_id
from study import seg_name
import specialists as X
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


def _agent(instructions, tools=None):
    from agno.agent import Agent
    from agno.models.google import Gemini
    return Agent(model=Gemini(id=resolve_model(), client=make_client(), retries=0), instructions=instructions,
                 tools=tools or [], markdown=True, tool_call_limit=8)


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


# ---------------- Analyst tools (one closure set per turn; results/steps collected for the UI) ----------------
def build_tools(S, turn):
    def run(agent, desc, job):
        """Run one specialist; any error (bad argument, missing tab) goes back to the model as text instead of crashing the turn."""
        try:
            res = job(); turn["results"].append(res); turn["steps"].append((agent, f"{desc} — requested by Gemini"))
            return json.dumps(res.to_llm(), default=str)
        except Exception as e:
            turn["steps"].append((agent, f"{desc} — failed: {e}")); return f"ERROR: {e}"

    def seg_ok(s):
        if s.upper() not in S.segments: raise ValueError(f"sub_segment must be one of {S.segments}")
        return s.upper()

    years = lambda s: [int(x) for x in re.findall(r"\d{4}", s or "")]

    def find_sections(question: str) -> str:
        """Study Navigator: list the Apollo tabs (with column numbers / blocks) most relevant to a question. Use first when unsure where data lives."""
        turn["steps"].append(("3a Study Navigator", f"Searched the tab catalogue for “{question}” — requested by Gemini"))
        return json.dumps(X.navigate(S, question))

    def reserve_summary(basis: str = "GROSS") -> str:
        """Unpaid by sub-segment: Case OS (Indications col 3), IBNR (col 15), Legal need (col 6 + col 16), Adjuster need (col 17), Total unpaid. basis GROSS or NET."""
        return run("3b Exhibit reader", f"Reserve summary ({basis})", lambda: X.reserve_summary(S, basis.upper()))

    def prior_year_development(basis: str = "GROSS", sub_segment: str = "") -> str:
        """PYD by accident year (Comparison_With_Prior col 12, prior accident years only). sub_segment AL/GL/WC or empty for all."""
        return run("3b Exhibit reader", f"PYD by AY ({basis} {sub_segment or 'all'})", lambda: X.pyd(S, basis.upper(), seg_ok(sub_segment) if sub_segment else None))

    def reinsurance_ratios() -> str:
        """Net-to-gross earned premium ratio by accident year for each sub-segment (Indications col 1)."""
        return run("3b Exhibit reader", "Net-to-gross EP by AY", lambda: X.reinsurance(S))

    def ldf_selection(sub_segment: str, triangle: str = "incurred", basis: str = "GROSS") -> str:
        """LDF Explainer: selected vs prior LDF per age with the reason (same as prior, matches an average, judgmental, benchmark). triangle 'incurred' or 'paid'."""
        tri = "PaidLossLegalTri" if triangle.lower().startswith("paid") else "IncLossLegalTri"
        return run("3c LDF Explainer", f"{tri} selections ({sub_segment} {basis})", lambda: X.ldf(S, seg_ok(sub_segment), basis.upper(), tri))

    def method_weights(sub_segment: str, basis: str = "GROSS") -> str:
        """Method weights by accident-year band from Methods_and_Weights."""
        return run("3b Exhibit reader", f"Method weights ({sub_segment} {basis})", lambda: X.method_weights(S, seg_ok(sub_segment), basis.upper()))

    def method_indications(sub_segment: str, basis: str = "GROSS", ay_from: int = 0, ay_to: int = 0) -> str:
        """Apollo's indication by method and AY (loss & legal) vs the selected ultimate, with the spread between methods."""
        return run("3b Exhibit reader", f"Method indications ({sub_segment} {basis})", lambda: X.method_indications(S, seg_ok(sub_segment), basis.upper(), ay_from or None, ay_to or None))

    def what_if_weights(sub_segment: str, weights: str, basis: str = "GROSS", ay_from: int = 0, ay_to: int = 0) -> str:
        """Scenario agent: re-weight Apollo's own method indications (no recalculation) for AYs ay_from..ay_to (0 = all) and compare with the
        selected ultimate. weights like 'inc_bf:1' or 'inc_dfm:0.5,inc_bf:0.5'. Keys: inc_dfm, paid_dfm, inc_bf, paid_bf, inc_cc, paid_cc, inc_lr,
        prior (prior ultimate), booked (booked ultimate)."""
        parse = lambda: {k.strip(): float(v) for k, v in (p.split(":") for p in weights.split(",") if ":" in p)}
        return run("3e Scenario", f"Re-weight {weights} ({sub_segment} {basis}, {f'AY {ay_from}–{ay_to or ay_from}' if ay_from else 'all AYs'})",
                   lambda: X.scenario(S, seg_ok(sub_segment), basis.upper(), parse(), ay_from or None, ay_to or None))

    def diagnostic(sub_segment: str, tab: str, basis: str = "GROSS") -> str:
        """Diagnostics agent: latest diagonal vs prior accident years for an Apollo diagnostic tab; flags calendar-year shifts and AY outliers.
        tab one of: PaidToIncurred, IncurredToEarnedPremium, PaidToEarnedPremium, SettlementRate, AverageCaseOS, Frequency, IncLossRToG,
        EarnedPremiumRToG, PaidLegalToLoss, PaidLegalToIncdLoss, ReportedTri, ClosedTri."""
        return run("3d Diagnostics", f"{tab} ({sub_segment} {basis})", lambda: X.diagnostic(S, seg_ok(sub_segment), basis.upper(), tab))

    def actual_vs_expected(sub_segment: str, basis: str = "GROSS") -> str:
        """Incurred actual vs expected by accident year since the prior review."""
        return run("3f Claims Investigator", f"AvE by AY ({sub_segment} {basis})", lambda: X.ave(S, seg_ok(sub_segment), basis.upper()))

    def investigate_claims(sub_segment: str, accident_year: int, basis: str = "GROSS") -> str:
        """Claims Investigator: what drove an AY's AvE — one-off large claim, reserve release, CAT or broad trend — with the largest claim movements."""
        return run("3f Claims Investigator", f"Claim drivers {sub_segment} AY{accident_year}", lambda: X.investigate(S, seg_ok(sub_segment), accident_year, basis.upper()))

    def read_exhibit(sub_segment: str, tab: str, basis: str = "GROSS", columns: str = "", accident_years: str = "", block: str = "") -> str:
        """Read any Apollo tab. columns = column numbers like '3,15' (summary tabs); accident_years like '2021,2022';
        block for triangles: Data, Age-To-Age Factors, Averages, Benchmarks, Manual Selections, Ultimate Selections."""
        return run("3b Exhibit reader", f"{tab} {block} ({sub_segment} {basis})", lambda: X.exhibit(S, seg_ok(sub_segment), basis.upper(), tab,
                   [int(c) for c in re.findall(r"\d+", columns)] or None, years(accident_years) or None, block or None))

    return [find_sections, reserve_summary, prior_year_development, reinsurance_ratios, ldf_selection, method_weights, method_indications,
            what_if_weights, diagnostic, actual_vs_expected, investigate_claims, read_exhibit]


ANALYST_RULES = """You are Scribe's Analyst for an Apollo reserve study: {lob} sub-segments {segments}; bases GROSS and NET;
evaluation {evaluation}. Use the sub-segment codes in tool calls.
- Answer ONLY from tool results. Call tools first; use find_sections when unsure where something lives.
- Every number you write must be copied exactly as a tool returned it ($x.xxm amounts, x.x% ratios, x.xxx factors). Never compute new numbers.
- Name the Apollo tab (and column number where given) behind each key figure.
- Tables and charts from your tool calls are shown automatically under your answer — refer to them, do not reproduce whole tables.
- "BF" without paid/incurred: assume Incurred BF and say so in one line. If a what-if lacks weights or AYs, assume all AYs and 100% to the
  named method, and state the assumption. If the sub-segment is ambiguous and it matters, ask one short question.
- Be concise: a direct answer, then 2–5 bullets of interpretation (what it means for the reserve review). No preamble."""


# ---------------- chat verifier ----------------
NUM = re.compile(r"(?<![A-Za-z0-9])-?\$?\(?\d[\d,]*\.?\d*\)?[m%]?(?![A-Za-z0-9])")


def _val(tok):
    neg = tok.startswith("-") or tok.startswith("(") or "$(" in tok
    x = float(re.sub(r"[^\d.]", "", tok) or "nan")
    return -x if neg else x


def verify_answer(answer, turn):
    """Every figure in the answer must match a figure returned by this turn's tools (years, ages and small counts exempt)."""
    allowed = {round(abs(_val(t)), 3) for r in turn["results"] for t in NUM.findall(json.dumps(r.to_llm(), default=str, ensure_ascii=False))}
    allowed |= {round(abs(float(x)), 3) for x in re.findall(r"\d+\.?\d*", " ".join(str(s) for s in turn["steps"]))}
    bad, n = [], 0
    for tok in NUM.findall(answer):
        v = abs(_val(tok))
        if v != v or (v.is_integer() and (1990 <= v <= 2035 or v <= 10 or (v % 12 == 0 and v <= 372))): continue
        n += 1
        if round(v, 3) not in allowed: bad.append(tok)
    return n, bad


# ---------------- results summary when Gemini fails after choosing tools ----------------
def headline(res):
    """One bullet per result from its summary (scalar values only) — deterministic, already formatted."""
    show = lambda v: ", ".join(f"{a} {b}" for a, b in v.items()) if isinstance(v, dict) else v
    items = [f"{k.replace('_', ' ')}: {show(v)}" for k, v in res.summary.items()
             if k != "label" and isinstance(v, (str, int, dict)) and v != "" and not (isinstance(v, dict) and any(isinstance(x, dict) for x in v.values()))][:4]
    return f"- **{res.title}** — " + ("; ".join(items) if items else "see table")


def analyse(S, text, history):
    """Analyst turn. Gemini plans which specialists to call; Python runs them. Returns (answer, results, steps, untraced, by_gemini)."""
    turn = dict(results=[], steps=[("0 Orchestrator", "Routed to the Analyst")])
    if gemini_model():
        try:
            ctx = "\n".join(f"{r}: {m[:600]}" for r, m in history[-6:])
            agent = _agent(ANALYST_RULES.format(lob=S.lob, segments=", ".join(f"{s} ({seg_name(s)})" for s in S.segments), evaluation=S.evaluation),
                           build_tools(S, turn))
            answer = _run(agent, (f"Conversation so far:\n{ctx}\n\n" if ctx else "") + f"User question: {text}")
            n, bad = verify_answer(answer, turn)
            turn["steps"].append(("4 Verifier", f"{n - len(bad)}/{n} figures traced to tool results" + (f"; untraced: {', '.join(bad)}" if bad else "")))
            return answer, turn["results"], turn["steps"], bad, True
        except Exception as e:
            _api_failed(e); turn["steps"].append(("0 Orchestrator", f"{gemini_state()['why']} while writing the answer"))
            if turn["results"]:                          # Gemini had already chosen the specialists: show what they returned
                return (f"{gemini_state()['why']} before Gemini could write its commentary. These are the exhibits Gemini chose for this question:\n\n"
                        + "\n".join(headline(r) for r in turn["results"])), turn["results"], turn["steps"], [], False
    hits = X.navigate(S, text)[:4]
    turn["steps"].append(("3a Study Navigator", "Searched the tab catalogue (no AI) to point you to the right exhibits"))
    tabs = "; ".join(f"**{h['tab']}** ({h['title']})" for h in hits) or "none matched — try naming the exhibit"
    return (f"{gemini_state()['why'] or 'Gemini is unavailable'}. The Analyst needs Gemini to plan which exhibits to read, so I can't answer this "
            f"one right now — {'it will work again once the limit resets' if 'quota' in gemini_state()['why'] or 'limit' in gemini_state()['why'] else 'please try again shortly'}. "
            f"The Apollo tabs most relevant to your question: {tabs}."), [], turn["steps"], [], False


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
