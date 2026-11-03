"""Agent 2 — Drafting team: memo sections (template order) built from the specialists, the memo Verifier, and the
.docx Assembler. Numbers enter text only as {{Fxxxx}} placeholders rendered from the citation registry. User-steerable choices come
from the drafting brief (brief.py); text the user supplied enters as labelled ("u", label, text) blocks, kept verbatim."""
import html, json, re, time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from docx import Document
from docx.shared import Pt, RGBColor
from study import seg_name, fmt
import specialists as X
import brief as B

TEMPLATE = Path(__file__).parent / "data" / "DVR Memo Template version 1.docx"
OUT = Path(__file__).parent / "output"
IR = lambda what: f"[INPUT REQUIRED: {what}]"
PH = re.compile(r"\{\{(F\d{4})\}\}")
NOTE_MM = "All amounts in tables are in $m."
direction = lambda v: "adverse" if v >= 5e3 else "favorable" if v <= -5e3 else "no material"     # |PYD| < $0.005m reads as nil
lc = lambda label: label[0].lower() + label[1:]                                                  # "Average case OS" → "average case OS"


class Citations:
    """Registry of every number placed in the memo: id → value, unit, source."""
    def __init__(self): self.items, self._key = {}, {}

    def cite(self, value, unit, source):
        k = (source, unit, round(float(value), 6))
        if k not in self._key:
            self._key[k] = f"F{len(self.items) + 1:04d}"; self.items[self._key[k]] = dict(value=float(value), unit=unit, source=source)
        return "{{" + self._key[k] + "}}"

    def from_result(self, res, row, col):
        """Placeholder for one cell of a specialist Result (row = value in its first column)."""
        r = res.df[res.df.iloc[:, 0].astype(str) == str(row)].iloc[0]
        return self.cite(r[col], res.units[col], res.sources.get((row, col)) or res.sources.get((str(row), col)) or res.title)

    def render(self, text, ctx="text"):
        return PH.sub(lambda m: fmt(self.items[m.group(1)]["value"], self.items[m.group(1)]["unit"], ctx), text)


def table_block(C, res, rows=None, cols=None):
    """Specialist Result → memo table block of placeholders (rendered x.xx in tables)."""
    cols = cols or list(res.df.columns); rows = rows if rows is not None else list(res.df.iloc[:, 0].astype(str))
    first = res.df.columns[0]
    body = [[str(r) if c == first else (C.from_result(res, r, c) if c in res.units else str(res.df[res.df[first].astype(str) == str(r)].iloc[0][c]))
             for c in cols] for r in rows]
    return ("table", cols, body, res.table_note)


def sec(id_, title, agent, blocks, narrative=False, level=2):
    return dict(id=id_, title=title, agent=agent, blocks=blocks, narrative=narrative, level=level)


# ---------------- sections ----------------
def summary_section(S, C, bases):
    R = {b: X.reserve_summary(S, b) for b in bases}
    t = lambda b, c: C.from_result(R[b], "Total", c)
    lae = lambda b: C.cite(R[b].df.iloc[-1]["Legal need"] + R[b].df.iloc[-1]["Adjuster need"], "mm", f"Legal need + Adjuster need ({b.lower()} total)")
    blocks = [("p", "Attached please find the estimate of unpaid claims for the above-captioned segment.")]
    for b in sorted(bases, reverse=True):                                 # net first, as in the template
        blocks += [("b", f"The {b.lower()} unpaid claim estimate is {t(b, 'Total unpaid')}."),
                   ("b", f"This is comprised of {t(b, 'Case OS')} of case reserves, {t(b, 'IBNR')} of IBNR loss reserves, and {lae(b)} of LAE reserves "
                         f"({t(b, 'Legal need')} legal and {t(b, 'Adjuster need')} adjuster).")]
    blocks += [("p", "This unpaid claim estimate is for"),
               ("b", f"claims incurred but not paid as of the {S.evaluation} evaluation (Apollo exhibit headers),"),
               ("b", f"based on machine transactions through {IR('machine transaction cut-off date')} and non-machine transactions through {IR('non-machine cut-off date')}, and"),
               ("b", f"using additional information provided to me through {IR('date')}.")]
    for b in bases: blocks += [("p", f"Estimates by sub-segment — {b.lower()}:"), table_block(C, R[b])]
    blocks += [("p", "Case OS = Indications col 3; IBNR = col 15; Legal need = col 6 + col 16; Adjuster need = col 17; Total unpaid = their sum.")]
    return sec("summary", "Memo", "2a Summary & Tables", blocks)


def pyd_section(S, C, trace, b):
    bases = b["bases"]; main = bases[0]                                # gross when in scope; AvE / claims commentary on this basis
    P = {x: X.pyd(S, x) for x in bases}
    allc = P[main].df.columns[-1]                                      # "All sub-segments", or the only sub-segment
    amounts = " and ".join(f"{C.from_result(P[x], 'Total', allc)} {x.lower()}" for x in bases)
    blocks = [("p", f"This review indicates {direction(P[main].df.iloc[-1][allc])} prior year development (PYD) of {amounts} (positive = adverse), "
                    f"on accident years to {S.eval_year - 1}. The tables below show PYD by accident year since the prior DVR, completed as of the prior evaluation.")]
    for x in bases: blocks += [("p", f"PYD by accident year — {x.lower()}:"), table_block(C, P[x])]
    for seg in S.segments:
        name = seg_name(seg); blocks.append(("h3", name))
        other = "".join(f" ({x.lower()} {C.from_result(P[x], 'Total', name)})" for x in bases[1:])
        lines = [f"{main.capitalize()} PYD for {name} is {direction(P[main].df.iloc[-1][name]).replace('no material', 'nil')} at {C.from_result(P[main], 'Total', name)}{other}."]
        if S.ave is None:
            blocks += [("p", lines[0] + " " + IR("AvE file not provided — drivers of AvE"))]; continue
        if not (S.ave.sub_segment == seg).any():
            blocks += [("p", lines[0] + " " + IR(f"AvE rows for {seg} not provided"))]; continue
        A = X.ave(S, seg, main)
        lines[0] += f" Incurred AvE since the prior review is {C.from_result(A, 'Total', 'A − E')}."
        body = A.df[A.df["Accident year"] != "Total"]
        if b["exclude_cat"] and S.claims is not None:
            c = S.claims[(S.claims.sub_segment == seg) & (S.claims.CAT_Flag == 1)]
            f = 1.0 if main == "GROSS" else S.cell(seg, "NET", "Indications", "Totals", col_no=10)[0] / S.cell(seg, "GROSS", "Indications", "Totals", col_no=10)[0]
            cat = c.Incurred_change.sum() * f
            lines.append(f"Excluding CAT claim movements of {C.cite(cat, 'mm', f'claims file | {seg} CAT_Flag = 1 Incurred_change' + ('' if f == 1 else ' × net/gross incurred (Indications col 10)'))}, "
                         f"incurred AvE is {C.cite(A.df.iloc[-1]['A − E'] - cat, 'mm', f'{seg} A − E less CAT claim movements')}." if len(c) else "No CAT-coded claims moved in the period.")
        order = body["A − E"].abs().sort_values(ascending=False).index
        material = body.loc[order][body.loc[order, "A − E"].abs() >= b["materiality"]].head(b["ays_explained"])
        focus = body[body["Accident year"].isin([str(y) for y in b["focus"].get(seg, [])]) & ~body.index.isin(material.index)]
        material = body.loc[list(material.index) + list(focus.index)]
        labels = []
        for _, r in material.iterrows():
            ay = r["Accident year"]; trace("3f Claims Investigator", f"{seg} AY{ay}: explaining AvE")
            if S.claims is None:
                lines.append(f"AY {ay}: A − E of {C.from_result(A, ay, 'A − E')}. {IR('claims file not provided — driver')}"); continue
            I = X.investigate(S, seg, ay, main); top = I.df.iloc[0]; labels.append(I.summary["label"])
            lead = (f"AY {ay}: actual incurred emergence of {C.from_result(A, ay, 'Actual')} against expected {C.from_result(A, ay, 'Expected')} "
                    f"(A − E {C.from_result(A, ay, 'A − E')}) is driven by {I.summary['explanation']}")
            lines.append(lead + (f": claim {top.Claim} ({top.Description}) moved by {C.from_result(I, top.Claim, 'Incurred movement')}."
                                 if I.summary["label"] != "TREND" else
                                 f" ({C.cite(I.summary['claims_in_ay'], 'int', f'claims file | {seg} AY{ay} claim count')} claims contributed)."))
        if not len(material):
            lines.append("No accident year shows a material AvE deviation; selections were not changed in response to AvE.")
        elif set(labels) == {"CAT"}:
            lines.append("How the AvE was considered: the deviation is catastrophe-driven and is excluded from the assessment of development patterns.")
        elif "TREND" in labels:
            lines.append(f"How the AvE was considered: the broad-based deviation was reviewed against the selected development factors; {IR('analyst conclusion on whether the trend is reflected in selections')}.")
        else:
            lines.append("How the AvE was considered: development factors were retained because the deviation is explained by individual claims rather than a change in development pattern.")
        blocks += [("p", x) for x in lines]
        blocks.append(table_block(C, A))
    return sec("pyd", "Prior Year Development", "2b PYD / AvE", blocks, narrative=True)


def static_sections(S, C):
    R = X.reinsurance(S); cols = [c for c in R.df.columns if c != "Accident year"]
    flat = all(R.df[c].round(4).nunique() == 1 for c in cols)
    ri = [("p", "The table below shows the net-to-gross earned premium ratio by accident year (Indications col 1, net ÷ gross)."), table_block(C, R),
          ("p", ("The ratio is constant across sub-segments and accident years, consistent with a quota share cession. " if flat else "")
                + IR("treaty names, effective dates, cession %, limits, XoL protections and their role in the net analysis"))]
    return [
        sec("scope", "Scope and Background", "1 Orchestrator", [
            ("p", f"The scope of this unpaid claim estimate includes the following segments: {S.lob} — {', '.join(seg_name(s) for s in S.segments)}, written on {IR('paper / legal entity')}."),
            ("p", f"Generally, this business is identified in our systems by the following coding: {IR('system coding')}."),
            ("p", f"This analysis excludes {IR('exclusions, e.g. A&E, mass tort, CAT')}."),
            ("p", "The purposes of this unpaid claims estimate are to establish liability estimates for internal and external financial reporting."),
            ("b", f"This estimate is an actuarial central estimate of {IR('nominal / discounted')} value."),
            ("b", "The net estimate is net of all reinsurance, which is presumed to be collectible to the extent that it was collectible historically."),
            ("b", "The Loss Adjustment Expenses (LAE) included in this estimate are defense costs (legal) and adjusting costs (adjuster)."),
            ("b", f"The LAE not included in this estimate are: ULE; {IR('other')}.")]),
        sec("business", "Description of the Business", "1 Orchestrator", [("p", IR("coverages, growth/contraction, underwriting changes, limit and attachment profile — not in the Apollo output"))]),
        sec("reinsurance", "Reinsurance", "2a Summary & Tables", ri)]


def ldf_paragraph(S, C, seg, tri, nm):
    """One paragraph per triangle: every sentence follows from the LDF Explainer's reason codes. Returns (text, changes, risks, Result)."""
    L = X.ldf(S, seg, "GROSS", tri); df = L.df; name = seg_name(seg); changes, risks = [], []
    cell = lambda age, col: C.from_result(L, age, col)
    rng = lambda age: f"{cell(age, 'Avg low')}–{cell(age, 'Avg high')}"
    same = df[df.Basis == "Same as prior"]; ok = same[same["Prior in range"] == "Yes"]; out = same[same["Prior in range"] == "No"]
    txt = f"To select the {nm} development factors, the {nm} triangle of the {name} sub-segment was used (Apollo tab {tri}). "
    if len(same):
        txt += (f"For {C.cite(len(same), 'int', f'{tri} {seg}: ages retained at prior')} of {C.cite(len(df), 'int', f'{tri} {seg}: development ages')} "
                f"development ages the prior selections were retained")
        txt += (f"; at {C.cite(len(ok), 'int', f'{tri} {seg}: retained ages consistent with averages')} of these the prior is consistent with the recent "
                "3/5/7-point averages, so the emerging data does not suggest a change. " if len(ok) else ". ")
    for _, r in out.iterrows():
        age = int(r["Age (months)"])
        txt += f"At {age} months the prior {cell(age, 'Prior')} was retained although the recent averages range {rng(age)}. "
    if len(out): txt += IR("rationale for retaining the prior where it sits outside the recent averages") + " "
    for _, r in df[df.Basis != "Same as prior"].iterrows():
        age, b = int(r["Age (months)"]), r.Basis
        if b.startswith("Selected the"):
            txt += f"At {age} months the selection moved from the prior {cell(age, 'Prior')} to {cell(age, 'Selected')}, {b[0].lower() + b[1:]}. "
            changes.append(f"{name} {nm} LDF at {age} months: {cell(age, 'Prior')} → {cell(age, 'Selected')} ({lc(b)}).")
        elif b == "Benchmark":
            txt += f"At {age} months the selection of {cell(age, 'Selected')} follows the benchmark pattern (prior {cell(age, 'Prior')}). "
            changes.append(f"{name} {nm} LDF at {age} months: {cell(age, 'Prior')} → {cell(age, 'Selected')} (benchmark).")
        else:                                                        # manual override / judgmental within or outside the averages
            what = {"Judgmental (manual override)": "a manual override of", "Judgmental, within range of recent averages": "a judgmental selection, within the recent averages, of"
                    }.get(b, "a judgmental selection, outside the recent averages, of")
            txt += f"At {age} months {what} {cell(age, 'Selected')} was made (prior {cell(age, 'Prior')}; recent averages {rng(age)}). {IR('rationale for the judgmental selection')} "
            changes.append(f"{name} {nm} LDF at {age} months: {cell(age, 'Prior')} → {cell(age, 'Selected')} ({lc(b).replace('judgmental (manual override)', 'manual override')}). {IR('reason')}")
            risks.append(f"{name}: the judgmental {nm} LDF at {age} months is a material assumption.")
    tail = L.summary["no_development_from_months"]
    return txt + (f"No further development is assumed from {tail} months." if tail else ""), changes, risks, L


def rationale(keys):
    """Standard wording by the method families that carry weight in an AY band (from Methods_and_Weights)."""
    fam = {k.split("_")[-1] for k in keys}; side = {k.split("_")[0] for k in keys if "_" in k}
    basis = "incurred" if side == {"inc"} else "paid" if side == {"paid"} else "paid and incurred"
    if fam == {"prior"}: return "the prior ultimate is retained"
    if fam == {"booked"}: return "the booked ultimate is retained"
    if fam == {"dfm"}: return f"these years are mature enough to rely on the {basis} development method"
    if "lr" in fam: return "the a-priori loss ratio carries weight because reported experience is still limited"
    if "dfm" in fam and fam & {"bf", "cc"}: return "blending development with expected-loss methods tempers volatility as these years mature"
    if fam <= {"bf", "cc"}: return "expected-loss methods (BF / Cape Cod) reduce reliance on immature reported losses"
    return None


def selection_paragraphs(S, C, seg):
    _, W, wsrc = X._methods(S, seg, "GROSS"); out = [("h3", seg_name(seg))]
    for b in X.method_weights(S, seg).df.itertuples():
        frm = b[1].split("–")[0]; keys = [k for k, v in W.loc[frm].items() if v > 0]
        wtxt = " and ".join(f"{C.cite(W.loc[frm, k], 'pct0', wsrc[(frm, k, 'w')])} {X.METHOD_KEYS[k]}" for k in keys)
        why = rationale(keys)
        out.append(("p", f"{'Accident year' if '–' not in b[1] else 'Accident years'} {b[1]}: {wtxt}; " + (f"{why}." if why else IR("rationale for these weights"))))
    if S.has(seg, "GROSS", "Ultimate_Adjustments"):
        adj = S.q(seg, "GROSS", "Ultimate_Adjustments"); adj = adj[adj.column.str.contains("Adjustment") & adj.row.str.isdigit()].value.abs().sum()
        out.append(("p", "No ultimate adjustments were applied (Ultimate_Adjustments, Exhibit 4)." if adj == 0 else IR("explain the ultimate adjustments in Ultimate_Adjustments")))
    return out


def elr_trend(S, C, seg):
    e = S.q(seg, "GROSS", "ELRIncLossLegal", column="A-Priori Ultimate LR"); e = e[e.row.str.isdigit()]
    t = S.q(seg, "GROSS", "LossTrend"); t = t[t.column.str.startswith("Incremental") & t.row.str.isdigit()]
    parts = []
    if len(e):
        lo, hi = e.loc[e.value.idxmin()], e.loc[e.value.idxmax()]
        parts.append("Expected loss ratio: an a-priori loss and legal ratio of " +
                     (C.cite(lo.value, "pct", f"{lo.file} | {lo.cell}") if abs(hi.value - lo.value) < 1e-9 else
                      f"{C.cite(lo.value, 'pct', f'{lo.file} | {lo.cell}')} to {C.cite(hi.value, 'pct', f'{hi.file} | {hi.cell}')} by accident year")
                     + " was used in the expected-loss methods (ELRIncLossLegal).")
    if len(t):
        r = t.loc[t.row.astype(int).idxmax()]
        parts.append(f"Loss cost trend of {C.cite(r.value, 'pct', f'{r.file} | {r.cell}')} per annum (LossTrend, latest period).")
    return " ".join(parts + [f"Source of trend and rate assumptions: {IR('pricing / benchmark source')}."]) if parts else IR("ELR and trend assumptions — tabs not found")


def sensitivity_blocks(S, C, b):
    """One low / selected / high table per scenario in the brief and basis, by sub-segment and in total."""
    out = [("p", "The range of reasonable estimates was tested with the scenarios below, using Apollo's own method indications; nothing was recalculated.")]
    for sp in b["sensitivity"]:
        for basis in b["bases"]:
            R = X.sensitivity(S, basis, sp)
            if sp["type"] == "weights":
                lead = f"{X.scenario_label(sp)} ({basis.lower()}): the total ultimate changes by {C.from_result(R, 'Total', 'Scenario − selected')} against the selected {C.from_result(R, 'Total', 'Selected')}."
            else:
                lead = (f"{X.scenario_label(sp)} ({basis.lower()}): total ultimate from {C.from_result(R, 'Total', 'Low')} to {C.from_result(R, 'Total', 'High')} "
                        f"against the selected {C.from_result(R, 'Total', 'Selected')} ({C.from_result(R, 'Total', 'Low − selected')} / {C.from_result(R, 'Total', 'High − selected')}).")
                flip = [x for x in R.df[R.df.Low > R.df.High]["Sub-segment"] if x != "Total"]
                if flip: lead += f" For {', '.join(flip)} the chosen low method is above the chosen high method."
            out += [("p", lead), table_block(C, R)]
        if sp["type"] == "min_max":
            out.append(("p", "Taking the lowest (highest) method in every accident year gives an outer bound: no single method produces that result for all years at once."))
    return out


def analysis_sections(S, C, trace, b):
    changes, risks, diag, assum, sel, sens = [], [], [], [], [], []
    weighted = set()
    for seg in S.segments:
        name = seg_name(seg)
        assum.append(("h3", f"Loss development factors — {name}"))
        for tri, nm in [("IncLossLegalTri", "incurred loss and legal"), ("PaidLossLegalTri", "paid loss and legal")]:
            if not S.has(seg, "GROSS", tri): continue
            txt, ch, rk, L = ldf_paragraph(S, C, seg, tri, nm); assum.append(("p", txt)); changes += ch; risks += rk
            if tri == "IncLossLegalTri":
                assum.append(table_block(C, L, rows=[str(a) for a in L.df["Age (months)"].head(10)], cols=["Age (months)", "Prior", "Selected", "Avg low", "Avg high", "Basis"]))
        assum.append(("p", elr_trend(S, C, seg)))
        sel += selection_paragraphs(S, C, seg)
        _, W, _ = X._methods(S, seg, "GROSS"); used = [k for k, v in W.max().items() if v > 0]; weighted |= set(used)
        # Diagnostics: every Apollo diagnostic tab present
        found, checked = [], []
        for tab in ("PaidToIncurred", "AverageCaseOS", "SettlementRate", "IncurredToEarnedPremium", "PaidToEarnedPremium", "Frequency"):
            if not S.has(seg, "GROSS", tab): continue
            D = X.diagnostic(S, seg, "GROSS", tab); sm = D.summary; checked.append(lc(sm["diagnostic"]))
            if sm["calendar_year_shift"]:
                k = sm[f"last_6_ays_{sm['calendar_year_shift']}"]
                found.append(f"{lc(sm['diagnostic'])} moved {sm['calendar_year_shift']} on the latest diagonal for {C.cite(k, 'int', f'{tab} {seg}: AYs moving together')} "
                             f"of the last six accident years (median change {C.cite(D.df.tail(6).Change.abs().median(), 'pct', f'{tab} {seg}: median |change| last 6 AYs')})")
                risks.append(f"{name}: a calendar-year shift in {lc(sm['diagnostic'])} on the latest diagonal should be reviewed with Claims.")
        diag.append(("p", f"{name}: " + ("; ".join(found) + ". " + IR("assess whether this reflects a change in case reserving or settlement practice and its effect on selections")
                                        if found else f"no consistent shift on the latest diagonal in {', '.join(checked)}." if checked else IR("no Apollo diagnostic tabs found"))))
        # Sensitivity detail: weighted methods' indications for the most recent accident years
        M = X.method_indications(S, seg, b["bases"][0], S.eval_year - 3, S.eval_year)
        shown = [X.ACTUARIAL[k] for k in X.ACTUARIAL if k in used and X.ACTUARIAL[k] in M.df.columns] or [c for c in M.df.columns if c in X.ACTUARIAL.values()][:4]
        sens += [("p", f"{name} — Apollo method indications for the most recent accident years (loss & legal, {b['bases'][0].lower()}):"),
                 table_block(C, M, cols=["Accident year"] + shown + ["Selected", "Spread / selected"])]
        ages = S.q(seg, "GROSS", "IncLossLegalTri", row="Age to Ultimate", block="Ultimate Selections")
        if len(ages):
            first = ages.loc[ages.column.astype(int).idxmin()]
            risks.insert(0, f"{name}: the {first.column}-to-ultimate incurred factor of {C.cite(first.value, 'factor', f'{first.file} | {first.cell}')} makes the latest accident year highly dependent on the expected-loss assumptions.")
    used_names = [X.METHOD_KEYS[k] for k in X.METHOD_KEYS if k in weighted]; unused = [X.ACTUARIAL[k] for k in X.ACTUARIAL if k not in weighted]
    combined = any("Loss and Legal" in c for c in S.q(S.segments[0], "GROSS", "Methods_and_Weights").column.unique())
    methods = [("p", "Analysis was performed on the following basis: " + ("loss and legal combined, adjuster separately." if combined else IR("analysis basis"))),
               ("p", "The following methods were evaluated and used as an indication of the selected ultimate:")]
    methods += [("b", f"{m} — weighted") for m in used_names] + [("b", f"{m} — evaluated, not weighted") for m in unused]
    if not any(k.startswith("paid") for k in weighted):
        methods.append(("p", "Paid-based indications were calculated but not weighted; they serve as a reasonableness check on the incurred methods."))
    methods.append(("p", IR("strengths and shortcomings of each method for this book, and the Cape Cod ELR / decay derivation")))
    return [sec("sensitivity", "Sensitivity Testing", "2e Diagnostics & Risks", sensitivity_blocks(S, C, b) + sens
                + [("p", IR("benchmarking against industry LDFs and any additional sensitivity tests"))]),
            sec("risks", "Key Risks to the Reserve Estimate", "2e Diagnostics & Risks", [("b", r) for r in risks] + [("b", IR("other key risks (social inflation, legal environment, data)"))]),
            sec("stat", "Stat/GAAP", "1 Orchestrator", [("p", IR("Stat/GAAP — select applicable option"))]),
            sec("limitations", "Limitations", "1 Orchestrator", [("p", IR("limitations — select applicable option"))]),
            sec("methodology", "Analysis Methodology", "1 Orchestrator", []),
            sec("diagnostics", "Diagnostics", "2e Diagnostics & Risks", [("p", "In addition to the exhibit pack, the Apollo diagnostic tabs were reviewed; latest-diagonal values were compared with the same age in the three previous accident years:")] + diag, True, 3),
            sec("assumptions", "Assumptions", "2c Assumptions", assum, True, 3),
            sec("methods", "Methods", "2d Methods & Selection", methods, True, 3),
            sec("selection", "Selection of Ultimates", "2d Methods & Selection", sel, True, 3),
            sec("changes", "Changes in Assumptions, Procedures, or Methods", "2c Assumptions",
                [("p", "Changes from the prior DVR, identified by comparing current Apollo selections with the prior selections:")]
                + ([("b", c) for c in changes] or [("b", "No LDF selections changed from the prior review.")])
                + [("p", f"Data, segmentation and methodology changes: {IR('confirm none')}.")], True)]


def basis_section(S, C, b, rejected, assumptions):
    """What this draft followed: your instructions, the defaults used, and anything not applied (and why)."""
    applied, defaults = B.describe(b, S, lambda v, unit: C.cite(v, unit, "Drafting brief (your instruction or the default)"))
    blocks = [("p", "Scribe drafted this memo to the brief below. Remove this section before issue.")]
    blocks += [("b", f"Applied as instructed — {x}.") for x in applied] + [("b", f"Default (not specified) — {x}.") for x in defaults]
    blocks += [("u", "Assumption", a) for a in assumptions] + [("u", "Not applied", f"“{r}” — {why}") for r, why in rejected]
    return sec("basis", "Basis of this draft", "1 Orchestrator", blocks)


def apply_context(secs, b):
    """Text the user supplied goes into its section verbatim, labelled. In single-topic sections it replaces the [INPUT REQUIRED] markers."""
    for s in secs:
        if s["id"] not in b["context"]: continue
        if s["id"] != "scope":
            s["blocks"] = [(k, re.sub(r"\s*\[INPUT REQUIRED:[^\]]*\]", "", t)) if k in ("p", "b") else (k, t, *rest)
                           for k, t, *rest in s["blocks"]]
            s["blocks"] = [x for x in s["blocks"] if not (x[0] in ("p", "b") and not x[1].strip())]
        s["blocks"].append(("u", "Analyst input", b["context"][s["id"]]))


def closing_sections(files):
    ir = lambda i, t, w: sec(i, t, "1 Orchestrator", [("p", IR(w))])
    return [ir("roles", "Roles and Responsibilities", "signing actuary, credentials, tech and peer reviewers"),
            ir("collaboration", "Collaboration", "input from other reserving actuaries, Claims and Pricing"),
            ir("responsibility", "Responsibility for Assumptions and Methods", "parties relied on and extent of review"),
            sec("distribution", "Distribution and Use", "1 Orchestrator", [("template", "Distribution and Use")]),
            sec("platform", "Platform", "1 Orchestrator", [("p", "The analysis documented in this report was performed in Apollo.")]),
            sec("data", "Data", "1 Data Steward", [("p", "Apollo exhibit packs were read programmatically; every figure in this draft is traced to its source cell in the evidence log. "
                                                          "Checks: accident-year rows reconcile to Exhibit 1 totals, net ultimates do not exceed gross, one evaluation date across packs."),
                                                   ("p", IR("data production, balancing and reconciliation to the general ledger"))]),
            sec("files", "Files", "1 Orchestrator", [("b", f) for f in files]),
            sec("appendix", "Appendix", "1 Orchestrator", [("template", "Appendix")])]


# ---------------- verifier ----------------
ALLOWED = [r"\{\{F\d{4}\}\}", r"\b(19|20)\d{2}(Q[1-4])?\b", r"\b\d{1,3}(–\d{1,3})? months\b", r"(?i)\bExhibits? \d+\b", r"(?i)\bcols? \d+\b",
           r"\bCLM\d+\b", r"(?i)\b\d+ Pt\.", r"\b3/5/7-point\b", r"\b12-to-ultimate\b", r"\(\d+\)", r"^\s*\d{1,3}\s*$", r"\b\d{4}–\d{4}\b"]


def untraced(text, C):
    t = text
    issues = [f"unknown fact {m}" for m in PH.findall(t) if m not in C.items]
    for pat in ALLOWED: t = re.sub(pat, "", t)
    return issues + [f"untraced number '{n}'" for n in re.findall(r"\d[\d,.]*", t)]


def verify(section, C):
    out = []
    for b in section["blocks"]:
        texts = [b[1]] if b[0] in ("p", "b", "h3") else [c for row in b[2] for c in row] if b[0] == "table" else []   # "u" = user's words, verbatim
        for t in texts: out += untraced(str(t), C)
    return out


# ---------------- assembler + preview ----------------
ORDER = ["basis", "summary", "pyd", "scope", "business", "reinsurance", "sensitivity", "risks", "stat", "limitations", "methodology", "diagnostics",
         "assumptions", "methods", "selection", "changes", "roles", "collaboration", "responsibility", "distribution", "platform", "data", "files", "appendix"]


def _template_text(heading):
    out, on = [], False
    for p in Document(TEMPLATE).paragraphs:
        if p.style.name.startswith("Heading 2"): on = p.text.strip() == heading; continue
        if on and p.text.strip(): out.append(p.text.strip())
    return out


def _runs(par, text):
    for part in re.split(r"(\[INPUT REQUIRED:[^\]]*\])", text):
        if part:
            r = par.add_run(part)
            if part.startswith("[INPUT REQUIRED"): r.font.color.rgb = RGBColor(0xC0, 0, 0); r.bold = True


def assemble(sections, C, path, subject):
    doc = Document(TEMPLATE); body = doc.element.body
    for el in list(body):
        if not el.tag.endswith("sectPr"): body.remove(el)
    styles = {s.name for s in doc.styles}; st = lambda n, fb="Normal": n if n in styles else fb
    p = doc.add_paragraph("FIRST DRAFT — generated by Scribe from Apollo output (synthetic data). Red items require input.")
    p.runs[0].font.size = Pt(8); p.runs[0].font.color.rgb = RGBColor(0xA1, 0x00, 0xFF)
    doc.add_paragraph("Memo", style=st("Title"))
    hdr = doc.add_table(rows=4, cols=2); hdr.style = "Table Grid"
    for i, (k, v) in enumerate([("Date:", date.today().isoformat()), ("From:", IR("author")), ("To:", IR("recipients")), ("Subject:", subject)]):
        hdr.cell(i, 0).text = k; _runs(hdr.cell(i, 1).paragraphs[0], v)
    for s in sections:
        if s["title"] != "Memo": doc.add_paragraph(s["title"], style=st("Heading 3" if s["level"] == 3 else "Heading 2"))
        for b in s["blocks"]:
            if b[0] == "template":
                for line in _template_text(b[1]): _runs(doc.add_paragraph(style=st("Normal")), line)
            elif b[0] == "h3": doc.add_paragraph().add_run(C.render(b[1])).bold = True
            elif b[0] == "u": par = doc.add_paragraph(style=st("Normal")); par.add_run(f"{b[1]}: ").italic = True; par.add_run(b[2])
            elif b[0] in ("p", "b"): _runs(doc.add_paragraph(style=st("Bullets" if b[0] == "b" else "Normal", "List Paragraph")), C.render(b[1]))
            elif b[0] == "table":
                tb = doc.add_table(rows=1 + len(b[2]), cols=len(b[1])); tb.style = "Table Grid"
                for j, h in enumerate(b[1]): r = tb.cell(0, j).paragraphs[0].add_run(str(h)); r.bold = True; r.font.size = Pt(8)
                for i, row in enumerate(b[2], start=1):
                    for j, v in enumerate(row): tb.cell(i, j).paragraphs[0].add_run(C.render(str(v), "table")).font.size = Pt(8)
                if b[3]: n = doc.add_paragraph().add_run(b[3]); n.italic = True; n.font.size = Pt(8)
    doc.save(path)


def to_html(sections, C):
    esc = lambda t, ctx="text": re.sub(r"(\[INPUT REQUIRED:[^\]]*\])", r'<span class="ir">\1</span>', html.escape(C.render(t, ctx))).replace("$", "&#36;")
    out = ['<div class="memo">']
    for s in sections:
        if s["title"] != "Memo": out.append(f'<h{3 if s["level"] == 2 else 4}>{html.escape(s["title"])}</h{3 if s["level"] == 2 else 4}>')
        ul = False
        for b in s["blocks"]:
            if b[0] != "b" and ul: out.append("</ul>"); ul = False
            if b[0] == "b":
                if not ul: out.append("<ul>"); ul = True
                out.append(f"<li>{esc(b[1])}</li>")
            elif b[0] == "p": out.append(f"<p>{esc(b[1])}</p>")
            elif b[0] == "h3": out.append(f"<p><b>{esc(b[1])}</b></p>")
            elif b[0] == "u": out.append(f"<p><i>{html.escape(b[1])}:</i> {html.escape(b[2]).replace('$', '&#36;')}</p>")
            elif b[0] == "template": out.append(f"<p><i>[Standard template text: {html.escape(b[1])}]</i></p>")
            elif b[0] == "table":
                out.append("<table><tr>" + "".join(f"<th>{html.escape(str(h))}</th>" for h in b[1]) + "</tr>"
                           + "".join("<tr>" + "".join(f"<td>{esc(str(c), 'table')}</td>" for c in row) + "</tr>" for row in b[2]) + "</table>")
                if b[3]: out.append(f'<p class="note">{html.escape(b[3])}</p>')
        if ul: out.append("</ul>")
    return "".join(out) + "</div>"


def basis_text(C, block):
    """One basis-note block as chat text."""
    return C.render(block[1]) if block[0] in ("p", "b") else f"*{block[1]}:* {block[2]}"


@dataclass
class Draft:
    sections: list
    citations: Citations
    docx: Path
    issues: dict
    seconds: float
    flags: int


def draft(S, polish=None, trace=lambda agent, msg: None, brief=None, rejected=(), assumptions=()):
    """Drafting team: build to the brief → verify → (optional LLM polish, verifier-gated) → assemble."""
    t0 = time.time(); OUT.mkdir(exist_ok=True); C = Citations(); b = brief or B.DEFAULT
    if b["segments"]: S = S.only(b["segments"])
    trace("2 Drafting team", "Building memo sections from the Apollo exhibits")
    secs = [basis_section(S, C, b, rejected, assumptions), summary_section(S, C, b["bases"]), pyd_section(S, C, trace, b)]
    secs += static_sections(S, C) + analysis_sections(S, C, trace, b)
    secs += closing_sections(sorted(p["file"] for p in S.packs) + [f for f in ("claims", "AvE") if (S.claims if f == "claims" else S.ave) is not None])
    apply_context(secs, b)
    secs = sorted(secs, key=lambda s: ORDER.index(s["id"]))
    issues = {s["id"]: verify(s, C) for s in secs}
    trace("4 Verifier", f"{sum(not v for v in issues.values())}/{len(secs)} sections tie out to source cells")
    if polish: polish(secs, C, trace)
    path = OUT / f"DVR_Draft_{S.lob.replace(' ', '_')}_{S.evaluation}.docx"
    assemble(secs, C, path, f"DVR — {S.lob} ({', '.join(seg_name(s) for s in S.segments)}) — {S.evaluation}")
    (OUT / "evidence_log.json").write_text(json.dumps({k: dict(v, rendered=fmt(v["value"], v["unit"], "text")) for k, v in C.items.items()}, indent=1))
    flags = sum(json.dumps(s["blocks"]).count("[INPUT REQUIRED") for s in secs)
    trace("5 Assembler", f"Wrote {path.name}: {len(secs)} sections, {len(C.items)} cited figures, {flags} input-required flags")
    return Draft(secs, C, path, issues, time.time() - t0, flags)
