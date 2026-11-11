"""Evaluation suite (CLAUDE.md §8). Offline checks run without Gemini; the Q&A golden set needs Gemini.  Run: python evaluate.py"""
import hashlib, json, re, shutil, tempfile
from pathlib import Path
from docx import Document
from openpyxl import load_workbook
from study import load_study, DATA, seg_name
import specialists as X
import drafting as D
import agents as A
import brief as B
import steward
import memo as M
import tools as T
from ledger import Ledger, CalcError, untraced

GOLDEN = json.loads((DATA.parent / "golden_labels.json").read_text())
ROUTES = [("What data is available?", "data_check"), ("Is anything missing?", "data_check"), ("Draft the DVR memo", "draft"),
          ("Please prepare the memo", "draft"), ("What can you do?", "about"), ("How were LDFs selected for GL?", "question"),
          ("Select BF for all AYs for WC", "question"), ("Show settlement rate for WC", "question"), ("Why is the memo PYD adverse?", "question"),
          ("What drove AvE on AL 2021?", "question"), ("Redraft with high = Paid BF", "draft"), ("Update the memo: exclude CAT", "draft")]
# Orchestrator brief_changes golden set (needs Gemini): instruction → fields the validated brief must hold
BRIEF_GOLD = [("Draft the reserving memo for the entire Casualty business, consider highest of all the methods as the high end and lowest of all methods "
               "as low end for the sensitivity testing.", lambda b, bad: b["sensitivity"][0]["type"] == "min_max" and "sensitivity" in b["user"]),
              ("Draft the memo for GL and WC only, net basis", lambda b, bad: b["segments"] == ["GL", "WC"] and b["bases"] == ["NET"]),
              ("Draft the memo: sensitivity low = paid development, high = BF, accident years 2020 to 2024",
               lambda b, bad: b["sensitivity"][0].get("low") == "paid_dfm" and b["sensitivity"][0].get("high") == "inc_bf"
               and b["sensitivity"][0].get("ay_from") == 2020),
              ("Draft the memo and explain AvE above $1m; also test the ELR at +10%", lambda b, bad: b["materiality"] == 1e6 and len(bad) >= 1),
              ("Draft the memo. Business description: commercial auto fleets and contractors' general liability, written through brokers.",
               lambda b, bad: "business" in b["context"])]


def check(name, ok, detail=""):
    return dict(check=name, result="PASS" if ok else "FAIL", detail=detail)


def run(S=None, qa=False):
    S = S or load_study(); out = []
    # 1 tie-outs to the defined Apollo columns
    for basis in ("GROSS", "NET"):
        R = X.reserve_summary(S, basis).df.set_index("Sub-segment")
        diffs = []
        for seg in S.segments:
            name = seg_name(seg)
            c = lambda n: S.cell(seg, basis, "Indications", "Totals", col_no=n)[0]
            exp = dict(zip(["Case OS", "IBNR", "Legal need", "Adjuster need"], [c(3), c(15), c(6) + c(16), c(17)]))
            diffs += [abs(R.loc[name, k] - v) for k, v in exp.items()] + [abs(R.loc[name, "Total unpaid"] - sum(exp.values()))]
        out.append(check(f"Reserve summary ties to Indications cols 3/15/6+16/17 ({basis.lower()})", max(diffs) < 0.01))
        P = X.pyd(S, basis).df
        ays_ok = all(a == "PRIOR" or a == "Total" or int(a) < S.eval_year for a in P["Accident year"])
        tot_ok = all(abs(P.iloc[-1][seg_name(s)] - S.cell(s, basis, "Comparison_With_Prior", "Prior Total", col_no=12)[0]) < 0.01 for s in S.segments)
        out.append(check(f"PYD = Comparison col 12, prior AYs only ({basis.lower()})", ays_ok and tot_ok))
    R = X.reinsurance(S).df
    out.append(check("Reinsurance ratio = net EP ÷ gross EP by AY", abs(R["Auto Liability"].iloc[5] - S.cell("AL", "NET", "Indications", "1999", col_no=1)[0]
                                                                        / S.cell("AL", "GROSS", "Indications", "1999", col_no=1)[0]) < 1e-9))
    # 2 scenario arithmetic: 100% Incurred BF equals Apollo's BF column for every AY
    sc = X.scenario(S, "GL", "GROSS", {"inc_bf": 1.0}).df; ind, _, _ = X._methods(S, "GL", "GROSS")
    body = sc[sc["Accident year"] != "Total"]
    out.append(check("What-if (BF for all AYs) = Apollo Incurred BF column", all(abs(r.Scenario - ind.loc[r["Accident year"], "inc_bf"]) < 0.01 for _, r in body.iterrows())))
    # 3 LDF reason codes vs golden
    ok = tot = 0; miss = []
    for seg, codes in GOLDEN["ldf_codes"].items():
        got = {int(r["Age (months)"]): r.Basis for _, r in X.ldf(S, seg).df.iterrows()}
        for age, want in zip(range(12, 384, 12), codes):
            if want == "UNITY_TAIL": continue
            tot += 1; b = got.get(age, "")
            hit = (want == "SAME_AS_PRIOR" and b == "Same as prior") or (want == "MANUAL" and b.startswith("Judgmental (manual")) \
                  or (want.startswith("MATCH_AVG") and want.split(":")[1] in b)
            ok += hit; miss += [] if hit else [f"{seg} {age}m want {want} got {b or 'tail'}"]
    out.append(check("LDF reason codes vs golden labels", ok == tot, f"{ok}/{tot}" + (f"; {miss}" if miss else "")))
    flagged = {seg: X.ldf(S, seg).summary["retained_but_prior_outside_recent_averages"] for seg in S.segments}
    out.append(check("Retained priors outside the averages are flagged (planted AL 48m only)", flagged == {"AL": [48], "GL": [], "WC": []}, str(flagged)))
    # 4 planted claim drivers and diagnostics
    drv = [(d, X.investigate(S, d["sub_segment"], d["accident_year"]).summary["label"]) for d in GOLDEN["drivers"]]
    out.append(check("Planted AvE drivers found (one-off / CAT / release / trend)", all(d["label"] == g for d, g in drv),
                     "; ".join(f"{d['sub_segment']} {d['accident_year']}: {g}" for d, g in drv)))
    dg = X.diagnostic(S, "WC", "GROSS", "AverageCaseOS").summary
    others = [X.diagnostic(S, s, "GROSS", "AverageCaseOS").summary["calendar_year_shift"] for s in ("AL", "GL")]
    out.append(check("Planted diagnostic: WC average case OS shift up, none for AL/GL", dg["calendar_year_shift"] == "up" and others == [None, None],
                     f"WC {dg['calendar_year_shift']}, AL/GL {others}"))
    # 5 readiness detects a missing file
    with tempfile.TemporaryDirectory() as tmp:
        for p in DATA.iterdir():
            if p.name != "Apollo_GL_NET.xlsx": shutil.copy(p, tmp)
        r = steward.check(load_study(tmp))
    out.append(check("Data Steward flags a removed pack (GL net)", not r["draft_ready"] and any("GL net" in g for g in r["gaps"]), ", ".join(r["gaps"])))
    # 5b generalisation: edit copies of the workbooks and check every output follows
    with tempfile.TemporaryDirectory() as tmp:
        for p in DATA.iterdir(): shutil.copy(p, tmp)
        cellref = lambda seg, tab, **k: S.q(seg, "GROSS", tab, **k).iloc[0].cell.split("!")[1]
        wb = load_workbook(Path(tmp) / "Apollo_AL_GROSS.xlsx")
        avg3 = S.q("AL", "GROSS", "IncLossLegalTri", row="3 Pt.", column="72", block="Averages").value.iloc[0]
        wb["IncLossLegalTri"][cellref("AL", "IncLossLegalTri", row="Selection", column="72", block="Ultimate Selections")] = round(avg3, 3)
        ws = wb["Methods_and_Weights"]
        for r in S.q("AL", "GROSS", "Methods_and_Weights", row="2016").itertuples():          # AY 2016: 100% prior ultimate
            if r.col_no % 2 == 0 and r.col_no <= 18: ws[r.cell.split("!")[1]] = 1.0 if r.col_no == 2 else 0.0
        wb.save(Path(tmp) / "Apollo_AL_GROSS.xlsx")
        S2 = load_study(tmp); L = X.ldf(S2, "AL").df.set_index("Age (months)")
        sel_text = " ".join(b[1] for sec in D.draft(S2).sections if sec["id"] in ("selection", "changes", "assumptions") for b in sec["blocks"] if b[0] in ("p", "b"))
        out.append(check("G1 edited workbook: new LDF basis, Prior-ultimate weight and memo wording follow the data",
                         L.loc[72, "Basis"].startswith("Selected the 3 Pt.") and "100% Prior ultimate" in X.method_weights(S2, "AL").df.Weights.str.cat()
                         and "the prior ultimate is retained" in sel_text and "AL incurred" not in sel_text and "72 months" in sel_text,
                         f"72m → {L.loc[72, 'Basis']}"))
    with tempfile.TemporaryDirectory() as tmp:
        for p in DATA.iterdir():
            if "_WC_" not in p.name: shutil.copy(p, tmp)
        S3 = load_study(tmp); r3 = steward.check(S3); d3 = D.draft(S3)
        memo = " ".join(str(b[1]) for sec in d3.sections for b in sec["blocks"] if b[0] in ("p", "b", "h3"))
        out.append(check("G2 sub-segment removed: data check and memo adapt (AL, GL only)",
                         S3.segments == ["AL", "GL"] and r3["draft_ready"] and "Workers Comp" not in memo and not any(d3.issues.values()),
                         f"{S3.segments}, ready={r3['draft_ready']}, gaps={r3['gaps']}, WC in memo={'Workers Comp' in memo}, issues={ {k: v for k, v in d3.issues.items() if v} }"))
    # 6 router
    hits = sum(A.route_rules(q) == k for q, k in ROUTES)
    out.append(check("Orchestrator regex fallback (route_rules) on test prompts", hits == len(ROUTES), f"{hits}/{len(ROUTES)}"))
    # 6b drafting brief: validation keeps only choices the study and the user's words support
    txt = "Draft for GL and WC, net only. Low = paid DFM, high = BF for AYs 2020-2024, 50/50 inc DFM and BF too. Explain AvE above $1m. Business: contractors' GL via brokers."
    good = {"segments": ["GL", "WC"], "bases": ["NET"], "materiality": 1e6, "context": {"business": "contractors' GL via brokers"},
            "sensitivity": [{"type": "methods", "low": "paid_dfm", "high": "inc_bf", "ay_from": 2020, "ay_to": 2024},
                            {"type": "weights", "weights": {"inc_dfm": 0.5, "inc_bf": 0.5}}]}
    b1, bad1, assum1 = B.validate(good, S, txt, B.DEFAULT)
    wrong = {"segments": ["XX"], "materiality": 1.5e6, "context": {"business": "a leading national book with strong growth"},
             "sensitivity": [{"type": "weights", "weights": {"inc_bf": 0.7, "inc_dfm": 0.3}}], "tail": 1.05}
    b2, bad2, assum2 = B.validate(wrong, S, txt, B.DEFAULT)
    out.append(check("Brief validation: applies supported choices, rejects invented figures / text / unknown options",
                     not bad1 and b1["user"] == sorted(good) and len(bad2) == 5 and b2["user"] == [], f"{len(bad2)} rejected: {[r for r, _ in bad2]}"))
    # 6c memo follows the brief: scope, bases, sensitivity scenarios, context, basis note
    d1 = D.draft(S, brief=b1, rejected=[("ELR +10%", "needs Apollo to be re-run")])
    memo1 = " ".join(str(x[1]) + str(x[2:]) for sec in d1.sections for x in sec["blocks"])
    sens = next(sec for sec in d1.sections if sec["id"] == "sensitivity")
    tabs = [x for x in sens["blocks"] if x[0] == "table" and x[1][0] == "Sub-segment"]
    R = X.sensitivity(S.only(["GL", "WC"]), "NET", good["sensitivity"][0]).df.set_index("Sub-segment")
    hi_ok = abs(d1.citations.items[D.PH.findall(tabs[0][2][-1][3])[0]]["value"] - R.loc["Total", "High"]) < 0.01
    basis = " ".join(str(x[1:]) for x in next(sec for sec in d1.sections if sec["id"] == "basis")["blocks"])
    out.append(check("Memo follows the brief: GL/WC net only, chosen scenarios, user text, basis note lists applied / default / not applied",
                     "Auto Liability" not in memo1 and "— gross:" not in memo1 and len(tabs) == 2 and hi_ok and "contractors' GL via brokers" in memo1
                     and "Applied as instructed" in basis and "Default (not specified)" in basis and "ELR +10%" in basis and not any(d1.issues.values()),
                     f"{len(tabs)} scenario tables, high total ties={hi_ok}, issues={ {k: v for k, v in d1.issues.items() if v} }"))
    # 6d sensitivity arithmetic: one-method re-weighting equals the Scenario agent; min/max brackets every method
    sw = X.sensitivity(S, "GROSS", {"type": "weights", "weights": {"inc_bf": 1.0}}).df.set_index("Sub-segment")
    mm = X.sensitivity(S, "GROSS", {"type": "min_max"}).df.set_index("Sub-segment")
    same = all(abs(sw.loc[seg_name(g), "Scenario − selected"] - X.scenario(S, g, "GROSS", {"inc_bf": 1.0}).df.iloc[-1].Change) < 0.01 for g in S.segments)
    brk = all(mm.loc[seg_name(g), "Low"] <= X.sensitivity(S, "GROSS", {"type": "methods", "low": k, "high": k}).df.set_index("Sub-segment").loc[seg_name(g), "Low"] + 0.01
              <= mm.loc[seg_name(g), "High"] + 0.02 for g in S.segments for k in X._methods(S, g, "GROSS")[0].columns if k in X.ACTUARIAL)
    out.append(check("Sensitivity: BF-all = Scenario agent; min/max brackets every single-method result", same and brk))
    # 7 memo: tie-out + template coverage
    d = D.draft(S)
    out.append(check("Memo: every figure traced to a source cell", not any(d.issues.values()), f"{len(d.citations.items)} figures, {d.flags} input flags"))
    heads = [p.text.strip() for p in Document(D.TEMPLATE).paragraphs if p.style.name == "Heading 2" and "Template" not in p.text]
    got = {p.text.strip() for p in Document(d.docx).paragraphs}
    miss = [h for h in heads if h not in got]
    out.append(check("Memo covers every template section", not miss, ", ".join(miss) or f"{len(heads)}/{len(heads)}"))
    # 9 ledger: dedupe by source, calculate arithmetic, rejects unsafe/unknown expressions, derivation chain
    Lchk = Ledger()
    r1 = Lchk.cite(1.0, "mm", "src a"); r2 = Lchk.cite(1.0, "mm", "src a"); r3 = Lchk.cite(1.0, "mm", "src b")
    ref_a, val_a = Lchk.calculate("F0001 * 3 - F0001", "mm", "check")
    rejected = []
    for bad in ("__import__('os')", "F0001.value", "F9999", "F0001[0]"):
        try: Lchk.calculate(bad, "mm", "bad"); rejected.append(False)
        except CalcError: rejected.append(True)
    out.append(check("Ledger: dedupe by (source, unit, value); calculate arithmetic; rejects unsafe/unknown expressions",
                     r1 == r2 and r1 != r3 and abs(val_a - 2.0) < 1e-9 and all(rejected) and Lchk.explain(ref_a)["derivation"] == "F0001 * 3 - F0001",
                     f"dedupe={r1 == r2}, calc={val_a}, rejected={rejected}"))
    # 10 to_llm(ledger): every numeric table cell's ref resolves to the same value
    L2 = Ledger(); res = X.reserve_summary(S, "GROSS"); payload = res.to_llm(L2)
    ok_cells = all(abs(L2.items[m.group(1)]["value"] - row[c]) < max(1.0, abs(row[c])) * 1e-6
                   for row, disp_row in zip(res.df.to_dict(orient="records"), payload["table"])
                   for c, v in disp_row.items() if isinstance(v, str) and (m := re.search(r"\[(F\d{4})\]", v)))
    out.append(check("to_llm(ledger): every numeric cell's ref resolves to the same value", ok_cells))
    # 11 ref verifier: placeholders + exempt tokens pass; a bare number is flagged
    refs = list(L2.items)[:2]
    good = f"The unpaid estimate is {{{{{refs[0]}}}}}, up from {{{{{refs[1]}}}}} in 2021 at age 24 months (CLM000154, col 3)."
    bad = f"The unpaid estimate is $1.46m, up from {{{{{refs[1]}}}}}."
    out.append(check("Ref verifier: placeholders + exempt tokens pass, a bare number is flagged",
                     not untraced(good, L2) and any("1.46" in u for u in untraced(bad, L2))))
    # 12 scripted-model wiring test (no Gemini): tool dispatch, allow-lists, placeholder rendering
    L3 = Ledger(); turn3 = dict(results=[], steps=[])
    fns = {f.__name__: f for f in T.build_tools(S, L3, turn3, T.STUDY_TOOLS + T.EVIDENCE_TOOLS)}       # Analyst's set
    pyd_json = json.loads(fns["prior_year_development"]("GROSS", "GL"))
    tagged = next(v for row in pyd_json["table"] for v in row.values() if isinstance(v, str) and "[F" in v)
    ref_id = tagged.split("[")[1].rstrip("]")
    canned = f"GL's prior year development is {{{{{ref_id}}}}}."
    out.append(check("Scripted-model wiring: Analyst tool set excludes claim_movements; a placeholder answer dispatches, verifies and renders",
                     "claim_movements" not in fns and not untraced(canned, L3) and "{{" not in L3.render(canned) and len(turn3["steps"]) >= 1))
    # 13 claim_movements: loss descriptions + the planted top claims
    cm_al, cm_gl = X.claim_movements(S, "AL", 2021, "GROSS"), X.claim_movements(S, "GL", 2019, "GROSS")
    out.append(check("claim_movements returns loss descriptions and the planted top claims (AL 2021 CLM000154, GL 2019 CLM000302)",
                     "Description" in cm_al.df.columns and cm_al.df.iloc[0].Claim == "CLM000154" and cm_gl.df.iloc[0].Claim == "CLM000302"))
    # 14 scan_study: surfaces the planted review items
    items = X.scan_study(S, Ledger())
    want = [("diagnostic", "WC", dict(tab="AverageCaseOS")), ("ldf", "WC", dict(age=24)), ("ldf", "AL", dict(age=48)), ("ave", "AL", dict(accident_year="2021"))]
    found = [any(i["category"] == cat and i["sub_segment"] == seg and all(i.get(k) == v for k, v in extra.items()) for i in items) for cat, seg, extra in want]
    out.append(check("scan_study surfaces the planted review items (WC case OS shift, WC/AL LDFs, AL 2021 AvE)", all(found), str(found)))
    # 15 memo state: draft shares the chat ledger; read/search; fill_input inserts a verbatim 'u' block; docx rebuilds
    led4 = Ledger(); d4 = D.draft(S, ledger=led4)
    ms = M.from_draft(d4, B.DEFAULT, "test subject")
    sec, hits, before = M.read_section(ms, "summary"), M.search(ms, "IBNR"), M.open_inputs(ms)
    filled = M.fill(ms, before[0]["item_id"], "2024-12-31")
    after = M.open_inputs(ms)
    filled_section = next(s for s in ms.sections if s["id"] == filled["section_id"])
    M.rebuild_docx(ms)
    out.append(check("Memo state: draft shares the chat ledger; read_section/search work; fill_input inserts a verbatim 'u' block; docx rebuilds",
                     ms.ledger is led4 and sec["id"] == "summary" and bool(hits) and len(before) > 0 and len(after) == len(before) - 1
                     and any(b == ("u", "Analyst input", f"{filled['what']}: 2024-12-31") for b in filled_section["blocks"]) and Path(ms.docx).exists()))
    # 16 replace_section replaces narrative text only; tables and 'u' blocks are untouched
    before_sec = next(s for s in ms.sections if s["id"] == "pyd"); tables0, u0 = [b for b in before_sec["blocks"] if b[0] == "table"], [b for b in before_sec["blocks"] if b[0] == "u"]
    texts = M.read_section(ms, "pyd")["texts"]
    M.replace_section(ms, "pyd", [t.upper() for t in texts])
    after_sec = next(s for s in ms.sections if s["id"] == "pyd")
    out.append(check("replace_section replaces only p/b/h3 text, in order; tables and 'u' blocks are kept",
                     [b for b in after_sec["blocks"] if b[0] == "table"] == tables0 and [b for b in after_sec["blocks"] if b[0] == "u"] == u0
                     and [b[1] for b in after_sec["blocks"] if b[0] in ("p", "b", "h3")] == [t.upper() for t in texts]))
    # 17 brief.validate states the BF assumption only when the instruction left paid/incurred unspecified
    _, _, bare = B.validate({"sensitivity": [{"type": "weights", "weights": {"inc_bf": 1.0}}]}, S, "Select BF for all AYs for WC", B.DEFAULT)
    _, _, qualified = B.validate({"sensitivity": [{"type": "weights", "weights": {"paid_bf": 1.0}}]}, S, "Select Paid BF for all AYs for WC", B.DEFAULT)
    out.append(check("brief.validate flags the BF assumption only when paid/incurred was left unspecified",
                     bare == ["BF taken as Incurred BF"] and qualified == [], f"bare={bare}, qualified={qualified}"))
    # 18 Section Writer fallback: with Gemini off, draft() still produces a fully-verified memo of the same shape
    orig_gemini_model = A.gemini_model
    A.gemini_model = lambda: None
    try:
        d6 = D.draft(S, ledger=Ledger(), write=A.write_sections)
    finally:
        A.gemini_model = orig_gemini_model
    out.append(check("Section Writer fallback: Gemini off -> draft() keeps code-drafted wording, same shape, fully verified",
                     len(d6.sections) == len(D.draft(S).sections) and not any(d6.issues.values())))
    # 19 redraft_section fallback: Gemini off fails clearly, without touching the section
    ms6 = M.from_draft(d6, B.DEFAULT, "test subject")
    pyd_before = [b for b in next(s for s in ms6.sections if s["id"] == "pyd")["blocks"]]
    A.gemini_model = lambda: None
    try:
        ok6, reason6, _ = A.redraft_section(S, ms6.ledger, ms6, "pyd", "make it shorter")
    finally:
        A.gemini_model = orig_gemini_model
    pyd_after = [b for b in next(s for s in ms6.sections if s["id"] == "pyd")["blocks"]]
    out.append(check("redraft_section: Gemini off fails clearly and leaves the section untouched",
                     not ok6 and isinstance(reason6, str) and pyd_after == pyd_before))
    # 20 rank_open_inputs: judgmental LDFs first, then PYD/AvE, then diagnostics, then everything else
    ranked = M.rank_open_inputs(ms6)
    sections_in_order = [i["section_id"] for i in ranked]
    prio = lambda sid: M.SECTION_PRIORITY.get(sid, 3)
    out.append(check("rank_open_inputs: most material first (assumptions, pyd, diagnostics, then the rest)",
                     sections_in_order == sorted(sections_in_order, key=prio) and len(ranked) == len(M.open_inputs(ms6)),
                     f"order: {sections_in_order[:6]}…"))
    # 21 Interviewer tool wiring + fallback: list_open_inputs/fill_input work via tools.py; Gemini off fails clearly
    turn8 = dict(results=[], steps=[])
    fns8 = {f.__name__: f for f in T.build_tools(S, ms6.ledger, turn8, T.INTERVIEW_TOOLS, ms6)}
    items8 = json.loads(fns8["list_open_inputs"]())
    filled8 = json.loads(fns8["fill_input"](items8[0]["item_id"], "test answer"))
    items8_after = json.loads(fns8["list_open_inputs"]())
    A.gemini_model = lambda: None
    try:
        out9, steps9 = A.interview_turn(S, ms6.ledger, ms6, "let's fill the gaps", None)
    finally:
        A.gemini_model = orig_gemini_model
    out.append(check("Interviewer: list_open_inputs/fill_input work standalone; interview_turn fails clearly when Gemini is off",
                     len(items8_after) == len(items8) - 1 and filled8["item_id"] == items8[0]["item_id"] and out9 is None and bool(steps9)))
    # 22 First Look fallback: deterministic, varied categories, each line carries a ref
    cands10 = X.scan_study(S, Ledger())
    picked10 = A.first_look_fallback(cands10)
    out.append(check("First Look fallback: up to 5 items, no Gemini, each line cites a figure",
                     1 <= len(picked10) <= 5 and len({c["category"] for c in picked10}) >= min(3, len({c["category"] for c in cands10}))
                     and all("[F" in c["text"] for c in picked10)))
    # 23 First Look caching: selection/phrasing cached per signature; figures are re-cited fresh for a different ledger
    sig10 = ("test-sig-" + hashlib.sha256(b"evaluate.py").hexdigest()[:8],)
    cache_path10 = D.OUT / f"first_look_{hashlib.sha256(str(sig10).encode()).hexdigest()[:16]}.json"
    cache_path10.unlink(missing_ok=True)
    A.gemini_model = lambda: None
    try:
        led11, led12 = Ledger(), Ledger()
        items11, by_gemini11, steps11 = A.first_look(S, led11, sig10)                       # miss -> fallback (Gemini off), not cached (by_gemini False)
        items12, by_gemini12, steps12 = A.first_look(S, led12, sig10)                       # fallback again: nothing was cached for a non-Gemini result
    finally:
        A.gemini_model = orig_gemini_model
        cache_path10.unlink(missing_ok=True)
    out.append(check("First Look: Gemini-off results are never cached (fallback runs fresh each time)",
                     not by_gemini11 and not by_gemini12 and not cache_path10.exists()))
    # 24 First Look cache hit: reuses the cached selection/phrasing but re-derives the figure fresh for this ledger
    sig13 = ("test-sig-hit-" + hashlib.sha256(b"evaluate2").hexdigest()[:8],)
    cache_path13 = D.OUT / f"first_look_{hashlib.sha256(str(sig13).encode()).hexdigest()[:16]}.json"
    cache_path13.parent.mkdir(exist_ok=True)
    cache_path13.write_text(json.dumps(dict(by_gemini=True, items=[dict(selector=A._selector(cands10[0]), what_to_check="Cached reason.", question="Cached question?")])))
    try:
        led14 = Ledger(); items14, by_gemini14, steps14 = A.first_look(S, led14, sig13)
    finally:
        cache_path13.unlink(missing_ok=True)
    ref_match = re.search(r"\[(F\d{4})\]", items14[0]["text"]) if items14 else None
    out.append(check("First Look: a cache hit reuses the cached phrasing and re-derives the figure fresh for this ledger",
                     by_gemini14 and len(items14) == 1 and items14[0]["what_to_check"] == "Cached reason." and items14[0]["question"] == "Cached question?"
                     and bool(ref_match) and ref_match.group(1) in led14.items))
    # 8 Q&A golden set (Gemini)
    if qa:
        gold = [("What is the total net PYD for General Liability?", X.pyd(S, "NET", "GL").summary["total"]["General Liability"]),
                ("What is the gross total unpaid for Workers Comp?", X.reserve_summary(S, "GROSS").display().set_index("Sub-segment").loc["Workers Comp", "Total unpaid"]),
                ("Which claim drove the AvE on Auto Liability AY 2021?", "CLM000154"),
                ("If we used 100% incurred BF for all GL accident years, what is the total change in ultimate?", X.scenario(S, "GL", "GROSS", {"inc_bf": 1}).summary["total_change"])]
        hits = 0
        for q, want in gold:
            led = Ledger(); rendered = led.render(A.analyse(S, q, [], led)[0])
            hits += want.replace("$", "").replace("m", "") in rendered.replace("$", "")
        out.append(check("Q&A golden set (Gemini)", hits == len(gold), f"{hits}/{len(gold)}"))
        res = []
        for q, ok in BRIEF_GOLD:
            intent = A.route_llm(S, q, [], memo_exists=False, section_ids=[], interview_pending=None)
            b, bad, _ = B.validate(intent.brief_changes if intent else {}, S, q, B.DEFAULT)
            res.append(bool(intent) and bool(ok(b, bad)))
        out.append(check("Orchestrator brief_changes golden set (Gemini)", all(res), f"{sum(res)}/{len(res)}"))
        intent_gold = [("draft the memo for GL", dict(kind="draft", segments=["GL"])), ("help me understand IBNR for AL", dict(kind="question")),
                       ("make the PYD section shorter", dict(kind="edit_section", section_id="pyd")), ("let's fill the gaps", dict(kind="fill_gaps"))]
        res2 = []
        for q, want in intent_gold:
            intent = A.route_llm(S, q, [], memo_exists=True, section_ids=D.ORDER, interview_pending=None)
            res2.append(bool(intent) and all(getattr(intent, k) == v for k, v in want.items()))
        out.append(check("Orchestrator intent golden set (Gemini)", all(res2), f"{sum(res2)}/{len(res2)}"))
        # full draft: every Section Writer section verifies
        led7 = Ledger(); d7 = D.draft(S, write=A.write_sections, ledger=led7)
        out.append(check("Section Writer: full draft verifies (Gemini)", not any(d7.issues.values()), f"{len(d7.citations.items)} figures"))
        # redraft one section: only that section changes, instruction followed, docx rebuilds
        ms7 = M.from_draft(d7, B.DEFAULT, "test subject")
        other_before = {s["id"]: s["blocks"] for s in ms7.sections if s["id"] != "pyd"}
        ok7, texts7, _ = A.redraft_section(S, led7, ms7, "pyd", "Make this section two short paragraphs for the Reserve Committee.")
        if ok7: M.replace_section(ms7, "pyd", texts7); M.rebuild_docx(ms7)
        other_after = {s["id"]: s["blocks"] for s in ms7.sections if s["id"] != "pyd"}
        out.append(check("redraft_section (Gemini): only the named section changes and the docx rebuilds",
                         ok7 and other_after == other_before and Path(ms7.docx).exists(), f"ok={ok7}"))
        # Interviewer: item 1 offered, the actuary's answer lands verbatim, item 2 offered
        before8 = M.rank_open_inputs(ms7)
        out1, _ = A.interview_turn(S, ms7.ledger, ms7, "let's fill the gaps", None)
        ok8 = bool(out1) and not out1.done and out1.item_id == before8[0]["item_id"]
        ans_text = "Because claims staff flagged a change in settlement patterns this quarter."
        out2, _ = A.interview_turn(S, ms7.ledger, ms7, ans_text, out1.item_id if out1 else None)
        filled_sec = next(s for s in ms7.sections if s["id"] == before8[0]["section_id"]) if ok8 else None
        verbatim_ok = ok8 and any(b[0] == "u" and ans_text in b[2] for b in filled_sec["blocks"])
        out.append(check("Interviewer (Gemini): offers the top-ranked item, records the actuary's answer verbatim, offers the next item",
                         ok8 and verbatim_ok and bool(out2) and not out2.done and out2.item_id != out1.item_id, f"item1={out1.item_id if out1 else None}"))
        # First Look: five items, each citing a figure; a second call for the same signature hits the cache
        sig_live = ("evaluate-live",)
        cache_path_live = D.OUT / f"first_look_{hashlib.sha256(str(sig_live).encode()).hexdigest()[:16]}.json"
        cache_path_live.unlink(missing_ok=True)
        try:
            led15 = Ledger(); items15, by_gemini15, _ = A.first_look(S, led15, sig_live)
            led16 = Ledger(); items16, by_gemini16, steps16 = A.first_look(S, led16, sig_live)
        finally:
            cache_path_live.unlink(missing_ok=True)
        out.append(check("First Look (Gemini): five items each citing a figure; a repeat call for the same study hits the cache",
                         by_gemini15 and len(items15) == 5 and all("[F" in it["text"] for it in items15)
                         and by_gemini16 and any("cache" in s[1].lower() for s in steps16), f"{len(items15)} items"))
    return out


if __name__ == "__main__":
    import sys
    for r in run(qa="--qa" in sys.argv): print(f"{r['result']:4}  {r['check']}" + (f"  [{r['detail']}]" if r["detail"] else ""))
