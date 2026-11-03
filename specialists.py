"""Specialist agents (deterministic). Each returns a study.Result: table + units + cell sources + summary + chart.
Shared by the drafting team and the Analyst — one implementation per question type."""
import re
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from study import Result, seg_name, fmt

PURPLE, DARK, GREY = "#A100FF", "#460073", "#8C8C8C"
TOL = 0.0015
ACTUARIAL = {"inc_dfm": "Incurred development", "paid_dfm": "Paid development", "inc_bf": "Incurred BF", "paid_bf": "Paid BF",
             "inc_cc": "Incurred Cape Cod", "paid_cc": "Paid Cape Cod", "inc_lr": "Incurred loss ratio"}
METHOD_KEYS = ACTUARIAL | {"prior": "Prior ultimate", "booked": "Booked ultimate"}        # every weightable column in Methods_and_Weights
AVG_RANGE = ["3 Pt.", "5 Pt.", "7 Pt.", "3 Pt. Wtd.", "5 Pt. Wtd.", "7 Pt. Wtd."]          # averages that define the "recent range"
NEAR = lambda f: max(0.003, 0.02 * (f - 1))   # "consistent with the averages": range widened by 0.003 or 2% of the development portion
DIAG = {  # tab -> (label, unit)
    "PaidToIncurred": ("Paid-to-incurred ratio", "pct"), "IncurredToEarnedPremium": ("Incurred loss ratio", "pct"),
    "PaidToEarnedPremium": ("Paid loss ratio", "pct"), "SettlementRate": ("Settlement rate", "pct"),
    "AverageCaseOS": ("Average case OS per open claim ($)", "num"), "Frequency": ("Reported frequency per million EP", "num"),
    "IncLossRToG": ("Incurred retention-to-gross", "pct"), "EarnedPremiumRToG": ("EP retention-to-gross", "pct"),
    "PaidLegalToLoss": ("Paid legal-to-paid loss", "pct"), "PaidLegalToIncdLoss": ("Paid legal-to-incurred loss", "pct"),
    "ReportedTri": ("Reported claim counts", "int"), "ClosedTri": ("Closed claim counts", "int")}


def _fig(title, ytitle):
    f = go.Figure(); f.update_layout(title=title, template="simple_white", height=360, margin=dict(l=40, r=20, t=50, b=40),
                                     yaxis_title=ytitle, legend=dict(orientation="h", y=-0.2), font=dict(family="Arial"))
    return f


def method_key(header):
    h = header.lower()
    if h.startswith("prior ultimate"): return "prior"
    if h.startswith("booked ultimate"): return "booked"
    kind = "inc" if h.startswith("incurred") else "paid" if h.startswith("paid") else None
    m = "dfm" if "development" in h else "bf" if "bornhuetter" in h else "cc" if "cape cod" in h else "lr" if "ratio method" in h else None
    return f"{kind}_{m}" if kind and m else None


def prior_ays(S, seg, basis, tab):
    return ["PRIOR"] + [str(a) for a in S.ay_rows(seg, basis, tab) if a < S.eval_year]


# ---------------- reserves, PYD, reinsurance ----------------
def reserve_summary(S, basis):
    """Case OS = Indications col 3; IBNR = col 15; Legal need = col 6 + col 16; Adjuster need = col 17; Total unpaid = sum."""
    rows, src = [], {}
    for seg in S.segments:
        name = seg_name(seg)
        if not S.has(seg, basis, "Indications"): continue
        v = {n: S.cell(seg, basis, "Indications", "Totals", col_no=n) for n in (3, 6, 15, 16, 17)}
        r = {"Sub-segment": name, "Case OS": v[3][0], "IBNR": v[15][0], "Legal need": v[6][0] + v[16][0], "Adjuster need": v[17][0]}
        r["Total unpaid"] = r["Case OS"] + r["IBNR"] + r["Legal need"] + r["Adjuster need"]
        src.update({(name, "Case OS"): v[3][1], (name, "IBNR"): v[15][1], (name, "Legal need"): f"{v[6][1]} + {v[16][1].split(' | ')[1]}",
                    (name, "Adjuster need"): v[17][1], (name, "Total unpaid"): "Sum of Case OS, IBNR, Legal need, Adjuster need"})
        rows.append(r)
    if not rows: raise KeyError(f"no {basis.lower()} Apollo packs with an Indications tab")
    df = pd.DataFrame(rows)
    tot = {"Sub-segment": "Total", **df.drop(columns="Sub-segment").sum().to_dict()}
    for c in tot:
        if c != "Sub-segment": src[("Total", c)] = f"Sum of sub-segment {c} ({basis.lower()})"
    df = pd.concat([df, pd.DataFrame([tot])], ignore_index=True)
    units = {c: "mm" for c in df.columns if c != "Sub-segment"}
    return Result(f"Unpaid claim estimate — {basis.lower()}", df, units, src,
                  summary={c: fmt(tot[c], "mm", "text") for c in units})


def pyd(S, basis, seg=None):
    """Comparison_With_Prior col 12 (change in ultimate loss, legal and adjuster), prior accident years only."""
    segs = [seg] if seg else [s for s in S.segments if S.has(s, basis, "Comparison_With_Prior")]
    ays = prior_ays(S, segs[0], basis, "Comparison_With_Prior")
    labels = ays + ["Total"]; df = pd.DataFrame({"Accident year": labels}); src = {}
    for s in segs:
        vals = []
        for lab, row in zip(labels, ays + ["Prior Total"]):                 # 'Prior Total' row = prior AYs only
            v, c = S.cell(s, basis, "Comparison_With_Prior", row, col_no=12); vals.append(v); src[(lab, seg_name(s))] = c
        df[seg_name(s)] = vals
    if len(segs) > 1:
        df["All sub-segments"] = df[[seg_name(s) for s in segs]].sum(axis=1)
    units = {c: "mm" for c in df.columns if c != "Accident year"}
    tot = df[df["Accident year"] == "Total"].iloc[0]; body = df[df["Accident year"] != "Total"]
    key = "All sub-segments" if len(segs) > 1 else seg_name(segs[0])
    top = body.reindex(body[key].abs().sort_values(ascending=False).index).head(3)
    f = _fig(f"Prior year development by accident year — {basis.lower()} ($m)", "$m")
    for s in segs: f.add_bar(x=body["Accident year"], y=body[seg_name(s)] / 1e6, name=seg_name(s))
    f.update_layout(barmode="relative"); f.update_traces(marker_line_width=0)
    for tr, col in zip(f.data, [PURPLE, DARK, GREY]): tr.marker.color = col
    return Result(f"Prior year development — {basis.lower()} (positive = adverse)", df.reset_index(drop=True), units, src,
                  summary=dict(total={c: fmt(tot[c], "mm", "text") for c in units},
                               largest_ays=[dict(ay=r["Accident year"], change=fmt(r[key], "mm", "text")) for _, r in top.iterrows()]),
                  chart=f, note=f"Prior accident years only (to {S.eval_year - 1}); source Comparison_With_Prior col 12.")


def reinsurance(S):
    """Net-to-gross earned premium (Indications col 1) by accident year."""
    segs = [s for s in S.segments if S.has(s, "GROSS", "Indications") and S.has(s, "NET", "Indications")]
    ays = ["PRIOR"] + [str(a) for a in S.ay_rows(segs[0], "GROSS", "Indications")]
    df = pd.DataFrame({"Accident year": ays}); src = {}
    for s in segs:
        ratios = []
        for ay in ays:
            g, gs = S.cell(s, "GROSS", "Indications", ay, col_no=1); n, ns = S.cell(s, "NET", "Indications", ay, col_no=1)
            ratios.append(n / g); src[(ay, seg_name(s))] = f"{ns} ÷ {gs}"
        df[seg_name(s)] = ratios
    units = {seg_name(s): "pct" for s in segs}
    return Result("Net-to-gross earned premium by accident year", df, units, src,
                  summary={seg_name(s): dict(min=fmt(df[seg_name(s)].min(), "pct"), max=fmt(df[seg_name(s)].max(), "pct")) for s in segs},
                  note="Net EP ÷ gross EP, Indications col 1.")


# ---------------- LDF selections ----------------
PREF = ["3 Pt. Wtd.", "5 Pt. Wtd.", "7 Pt. Wtd.", "All Wtd.", "3 Pt.", "5 Pt.", "7 Pt.", "All Pt."]
CODE_TEXT = {"SAME_AS_PRIOR": "Same as prior", "MANUAL": "Judgmental (manual override)", "BENCHMARK": "Benchmark",
             "WITHIN_RANGE": "Judgmental, within range of recent averages", "OUTSIDE_RANGE": "Judgmental, outside range of recent averages"}


def ldf(S, seg, basis="GROSS", tri="IncLossLegalTri"):
    """Reason code per age, checked in this order: SAME_AS_PRIOR, MANUAL (manual override used), MATCH_AVG:<any Apollo average>,
    BENCHMARK, WITHIN_RANGE / OUTSIDE_RANGE of the recent averages. Ages where selected and prior are both 1.000 form the tail."""
    blk = lambda b, r: S.q(seg, basis, tri, row=r, block=b).set_index("column")
    sel, prior, manual = blk("Ultimate Selections", "Selection"), blk("Benchmarks", "Prior Selections"), blk("Manual Selections", "manual")
    avgs = S.q(seg, basis, tri, block="Averages"); bench = S.q(seg, basis, tri, block="Benchmarks"); bench = bench[bench.row != "Prior Selections"]
    rows, src, tail_from = [], {}, None
    for age in sorted(sel.index, key=int):
        s = sel.loc[age, "value"]; p = prior.loc[age, "value"] if age in prior.index else None
        a = avgs[avgs.column == age]; recent = a[a.row.isin(AVG_RANGE)].value
        m = manual.loc[age, "value"] if age in manual.index else 1.0
        match = sorted([r.row for _, r in a.iterrows() if abs(r.value - s) < TOL], key=lambda x: PREF.index(x) if x in PREF else 99)
        if s == 1.0 and (p is None or p == 1.0):
            tail_from = tail_from or int(age); continue
        inside = lambda x: bool(len(recent)) and recent.min() - NEAR(x) <= x <= recent.max() + NEAR(x)
        code = ("SAME_AS_PRIOR" if p is not None and abs(s - p) < TOL else "MANUAL" if m != 1 and abs(m - s) < TOL
                else f"MATCH_AVG:{match[0]}" if match else "BENCHMARK" if any(abs(v - s) < TOL for v in bench[bench.column == age].value)
                else "WITHIN_RANGE" if inside(s) else "OUTSIDE_RANGE")
        rows.append({"Age (months)": int(age), "Prior": p, "Selected": s, "Avg low": recent.min() if len(recent) else None,
                     "Avg high": recent.max() if len(recent) else None, "Code": code, "Prior in range": p is not None and inside(p),
                     "Basis": CODE_TEXT.get(code, f"Selected the {code.split(':')[-1]} average")})
        src[(int(age), "Selected")] = f"{sel.loc[age, 'file']} | {sel.loc[age, 'cell']}"
        if p is not None: src[(int(age), "Prior")] = f"{prior.loc[age, 'file']} | {prior.loc[age, 'cell']}"
    df = pd.DataFrame(rows)
    f = _fig(f"{seg_name(seg)} — {tri.replace('LossLegalTri', '')} LDFs: selected vs prior", "Age-to-age factor")
    f.add_scatter(x=df["Age (months)"], y=df["Avg high"], line=dict(width=0), showlegend=False, hoverinfo="skip")
    f.add_scatter(x=df["Age (months)"], y=df["Avg low"], fill="tonexty", fillcolor="rgba(161,0,255,0.12)", line=dict(width=0), name="3/5/7-pt average range")
    f.add_scatter(x=df["Age (months)"], y=df["Prior"], name="Prior", line=dict(color=GREY, dash="dash"))
    f.add_scatter(x=df["Age (months)"], y=df["Selected"], name="Selected", mode="lines+markers", line=dict(color=PURPLE))
    changed = df[df.Code != "SAME_AS_PRIOR"]
    outside = [int(r["Age (months)"]) for _, r in df[(df.Code == "SAME_AS_PRIOR") & ~df["Prior in range"]].iterrows()]
    df = df.drop(columns="Code").assign(**{"Prior in range": df["Prior in range"].map({True: "Yes", False: "No"})})
    return Result(f"{seg_name(seg)} — {'incurred' if tri.startswith('Inc') else 'paid'} loss & legal LDF selections ({basis.lower()})",
                  df, {"Prior": "factor", "Selected": "factor", "Avg low": "factor", "Avg high": "factor"}, src,
                  summary=dict(ages=len(df), same_as_prior=int((df.Basis == "Same as prior").sum()), no_development_from_months=tail_from,
                               retained_but_prior_outside_recent_averages=outside,
                               changes=[dict(age=int(r["Age (months)"]), prior=fmt(r.Prior, "factor"), selected=fmt(r.Selected, "factor"), basis=r.Basis)
                                        for _, r in changed.iterrows()]),
                  chart=f, note=f"Averages range = min/max of 3/5/7-pt simple and 3/5-pt weighted. No development assumed from {tail_from} months.")


# ---------------- methods, weights, what-if ----------------
def _methods(S, seg, basis):
    """AY × method indications and weights from the loss & legal block of Methods_and_Weights (columns before the first
    'Weight Totals'). Each method appears as a pair of same-named columns: the first is the indication, the second the weight."""
    d = S.q(seg, basis, "Methods_and_Weights"); d = d[d.row.str.isdigit()]
    wt = d[d.column == "Weight Totals"].col_no
    if len(wt): d = d[d.col_no < wt.min()]                                  # adjuster methods follow in some Apollo layouts
    first = d.groupby("column").col_no.min()
    d = d.assign(key=d.column.map(method_key), kind=["v" if n == first[c] else "w" for c, n in zip(d.column, d.col_no)]).dropna(subset=["key"])
    ind = d[d.kind == "v"].pivot_table(index="row", columns="key", values="value")
    w = d[d.kind == "w"].pivot_table(index="row", columns="key", values="value")
    src = {(r.row, r.key, r.kind): f"{r.file} | {r.cell} (col {int(r.col_no)})" for r in d.itertuples()}
    return ind, w, src


def selected_ll(S, seg, basis, ay):
    """Selected ultimate loss & legal = Indications col 11 + col 12 (method indications are loss & legal)."""
    (a, sa), (b, sb) = S.cell(seg, basis, "Indications", ay, col_no=11), S.cell(seg, basis, "Indications", ay, col_no=12)
    return a + b, f"{sa} + {sb.split(' | ')[1]}"


def method_weights(S, seg, basis="GROSS"):
    ind, w, _ = _methods(S, seg, basis); rows = []
    for ay in sorted(w.index, key=int):
        txt = " + ".join(f"{v:.0%} {METHOD_KEYS[k]}" for k, v in w.loc[ay].items() if v > 0)
        if rows and rows[-1]["Weights"] == txt: rows[-1]["To"] = int(ay)
        else: rows.append({"From": int(ay), "To": int(ay), "Weights": txt})
    df = pd.DataFrame(rows); df.insert(0, "Accident years", [f"{r.From}" if r.From == r.To else f"{r.From}–{r.To}" for r in df.itertuples()])
    return Result(f"{seg_name(seg)} — method weights by accident year ({basis.lower()})", df[["Accident years", "Weights"]], {}, {},
                  summary=dict(bands=df[["Accident years", "Weights"]].to_dict(orient="records")), note="Source: Methods_and_Weights (even-numbered weight columns).")


def method_indications(S, seg, basis="GROSS", ay_from=None, ay_to=None):
    ind, w, src = _methods(S, seg, basis)
    ays = [a for a in sorted(ind.index, key=int) if (ay_from is None or int(a) >= ay_from) and (ay_to is None or int(a) <= ay_to)]
    cols = [k for k in ACTUARIAL if k in ind.columns]
    df = ind.loc[ays, cols].rename(columns=ACTUARIAL).reset_index().rename(columns={"row": "Accident year"})
    df["Selected"] = [selected_ll(S, seg, basis, a)[0] for a in ays]
    run = df[[ACTUARIAL[k] for k in cols]].replace(0, np.nan)                  # 0 = method not run by Apollo for that AY
    lo, hi = run.min(axis=1), run.max(axis=1)
    df["Spread / selected"] = (hi - lo) / df["Selected"]
    units = {c: "mm" for c in df.columns if c not in ("Accident year", "Spread / selected")} | {"Spread / selected": "pct"}
    s2 = {(a, ACTUARIAL[k]): v for (a, k, t), v in src.items() if a in ays and t == "v" and k in ACTUARIAL}
    return Result(f"{seg_name(seg)} — method indications, loss & legal ({basis.lower()})", df, units, s2,
                  summary=dict(widest=[dict(ay=r["Accident year"], spread=fmt(r["Spread / selected"], "pct")) for _, r in df.nlargest(3, "Spread / selected").iterrows()]),
                  note="Apollo method indications (Methods_and_Weights odd columns); Selected = Indications col 11 + col 12.")


def scenario(S, seg, basis, weights, ay_from=None, ay_to=None):
    """Re-weight Apollo's own method indications for the chosen AYs; other AYs keep the selected ultimate. No recalculation."""
    bad = [k for k in weights if k not in METHOD_KEYS]
    if bad: raise ValueError(f"unknown method keys {bad}; use {list(METHOD_KEYS)}")
    tot_w = sum(weights.values())
    if abs(tot_w - 1) > 1e-6: raise ValueError(f"weights must sum to 100% (got {tot_w:.0%})")
    ind, _, src = _methods(S, seg, basis)
    ays = [a for a in sorted(ind.index, key=int) if (ay_from is None or int(a) >= ay_from) and (ay_to is None or int(a) <= ay_to)]
    rows, cell_src = [], {}
    for a in ays:
        sel, ssrc = selected_ll(S, seg, basis, a); new = sum(ind.loc[a, k] * v for k, v in weights.items())
        rows.append({"Accident year": a, "Selected": sel, "Scenario": new, "Change": new - sel, "Change %": (new - sel) / sel})
        cell_src[(a, "Selected")] = ssrc; cell_src[(a, "Scenario")] = "; ".join(f"{v:.0%} × {src[(a, k, 'v')]}" for k, v in weights.items())
    df = pd.DataFrame(rows); t = df[["Selected", "Scenario", "Change"]].sum()
    df = pd.concat([df, pd.DataFrame([{"Accident year": "Total", **t.to_dict(), "Change %": t.Change / t.Selected}])], ignore_index=True)
    label = " + ".join(f"{v:.0%} {METHOD_KEYS[k]}" for k, v in weights.items())
    f = _fig(f"{seg_name(seg)} — {label} vs selected ({basis.lower()}, $m)", "Change in ultimate, $m")
    body = df[df["Accident year"] != "Total"]
    f.add_bar(x=body["Accident year"], y=body.Change / 1e6, marker_color=[PURPLE if v >= 0 else DARK for v in body.Change], name="Scenario − selected")
    return Result(f"What-if: {label} — {seg_name(seg)} ({basis.lower()}, AY {ays[0]}–{ays[-1]})", df,
                  {"Selected": "mm", "Scenario": "mm", "Change": "mm", "Change %": "pct"}, cell_src,
                  summary=dict(total_change=fmt(t.Change, "mm", "text"), change_pct=fmt(t.Change / t.Selected, "pct"), scenario=label,
                               accident_years=f"{ays[0]}–{ays[-1]}", total_selected=fmt(t.Selected, "mm", "text"), total_scenario=fmt(t.Scenario, "mm", "text"),
                               largest=[dict(ay=r["Accident year"], change=fmt(r.Change, "mm", "text")) for _, r in body.reindex(body.Change.abs().sort_values(ascending=False).index).head(3).iterrows()]),
                  chart=f, note="Loss & legal. Re-weights Apollo's method indications (Methods_and_Weights); nothing is recalculated. Change = scenario − selected; this is also the change in IBNR.")


def scenario_label(spec):
    """Plain-English description of a sensitivity scenario (no figures)."""
    ays = (f"accident years {spec['ay_from'] or 'first'}–{spec['ay_to'] or 'latest'} (other years at the selected ultimate)"
           if spec.get("ay_from") or spec.get("ay_to") else "all accident years")
    if spec["type"] == "min_max":
        meth = ", ".join(ACTUARIAL.get(k, METHOD_KEYS.get(k)) for k in spec["methods"]) if spec.get("methods") else "all Apollo actuarial methods"
        return f"Low / high = lowest / highest indication among {meth}, in each of {ays}"
    if spec["type"] == "methods":
        return f"Low = {METHOD_KEYS[spec['low']]}, high = {METHOD_KEYS[spec['high']]}, for {ays}"
    return "Re-weighted: " + " + ".join(METHOD_KEYS[k] for k in spec["weights"]) + f", for {ays}"


def sensitivity(S, basis, spec, segments=None):
    """Range of ultimates by sub-segment from Apollo's own method indications (Methods_and_Weights) — nothing is recalculated.
    spec: {"type": "min_max", "methods": [keys] | None} | {"type": "methods", "low": key, "high": key} | {"type": "weights", "weights": {key: w}},
    each with optional ay_from / ay_to (other AYs keep the selected ultimate). An AY where a needed method was not run (0) keeps the selected."""
    a0, a1, kind = spec.get("ay_from"), spec.get("ay_to"), spec["type"]
    cols = ["Scenario"] if kind == "weights" else ["Low", "High"]
    rows, src = [], {}
    for seg in segments or S.segments:
        ind, _, isrc = _methods(S, seg, basis); name = seg_name(seg)
        meth = [k for k in (spec.get("methods") or ACTUARIAL) if k in ind.columns]
        tot = dict.fromkeys(["Selected"] + cols, 0.0); kept = 0
        for a in sorted(ind.index, key=int):
            sel = selected_ll(S, seg, basis, a)[0]; tot["Selected"] += sel
            if (a0 and int(a) < a0) or (a1 and int(a) > a1): pts = [sel] * len(cols)
            elif kind == "min_max":
                run = ind.loc[a, meth][ind.loc[a, meth] != 0]; pts = [run.min(), run.max()] if len(run) else [sel, sel]
            elif kind == "methods":
                pts = [ind.loc[a, spec[k]] if spec[k] in ind.columns and ind.loc[a, spec[k]] else sel for k in ("low", "high")]
            else:
                ok = all(k in ind.columns and ind.loc[a, k] for k in spec["weights"])
                pts = [sum(ind.loc[a, k] * w for k, w in spec["weights"].items()) if ok else sel]
            kept += pts == [sel] * len(cols) and not ((a0 and int(a) < a0) or (a1 and int(a) > a1))
            for c, v in zip(cols, pts): tot[c] += v
        rows.append({"Sub-segment": name, **tot, **{f"{c} − selected": tot[c] - tot["Selected"] for c in cols}})
        src.update({(name, "Selected"): f"{name}: sum of Indications col 11 + col 12 by AY",
                    **{(name, c): f"{name}: {scenario_label(spec)} — sum by AY of Methods_and_Weights indications" for c in cols}})
    df = pd.DataFrame(rows)
    df = pd.concat([df, pd.DataFrame([{"Sub-segment": "Total", **df.drop(columns="Sub-segment").sum().to_dict()}])], ignore_index=True)
    units = {c: "mm" for c in df.columns if c != "Sub-segment"}
    f = _fig(f"Sensitivity — {basis.lower()} ultimate vs selected ($m)", "Difference from selected, $m"); body = df[df["Sub-segment"] != "Total"]
    for c, colr in zip(cols, (DARK, PURPLE)): f.add_bar(x=body["Sub-segment"], y=body[f"{c} − selected"] / 1e6, name=c, marker_color=colr)
    t = df.iloc[-1]
    return Result(f"Sensitivity: {scenario_label(spec)} ({basis.lower()})", df, units, src,
                  summary={**{f"total_{c.lower()}_vs_selected": fmt(t[f"{c} − selected"], "mm", "text") for c in cols}, "total_selected": fmt(t.Selected, "mm", "text")},
                  chart=f, note="Loss & legal ultimates. Apollo's own method indications (Methods_and_Weights), re-weighted or picked; nothing is recalculated. "
                                "Accident years where a method was not run keep the selected ultimate.")


# ---------------- diagnostics ----------------
def diagnostic(S, seg, basis, tab, recent=10):
    """Latest diagonal vs the average of the 3 previous accident years at the same age; flags calendar-year shifts and AY outliers."""
    label, unit = DIAG.get(tab, (tab, "num"))
    d = S.q(seg, basis, tab, block="Data"); d = d[d.row.str.isdigit()]
    if d.empty: raise KeyError(f"diagnostic tab '{tab}' not found for {seg} {basis}; available: {', '.join(t for t in DIAG if S.has(seg, basis, t))}")
    grid = d.pivot_table(index="row", columns="column", values="value"); grid.columns = grid.columns.astype(int); grid = grid.sort_index(axis=1)
    cells = {(r.row, int(r.column)): f"{r.file} | {r.cell}" for r in d.itertuples()}
    rows = []
    ays = [a for a in sorted(grid.index, key=int) if int(a) < S.eval_year][-recent:]
    for a in ays:
        r = grid.loc[a].dropna(); age = int(r.index.max()); latest = r[age]
        prev = [grid.loc[str(int(a) - k), age] for k in (1, 2, 3) if str(int(a) - k) in grid.index]
        base = np.mean(prev) if prev else np.nan
        rows.append({"Accident year": a, "Latest age": age, "Latest diagonal": latest, "Prior 3-AY avg (same age)": base,
                     "Change": latest / base - 1 if base else np.nan})
    df = pd.DataFrame(rows); last6 = df.tail(6).Change.dropna()
    up, down = int((last6 > 0.05).sum()), int((last6 < -0.05).sum())
    cy = ("up" if up >= 5 else "down" if down >= 5 else None) if last6.abs().median() >= 0.08 else None
    outliers = df[(df.Change.abs() >= 0.25) & (df["Accident year"].astype(int) >= S.eval_year - 8)]
    f = _fig(f"{seg_name(seg)} — {label}: latest diagonal vs prior years ({basis.lower()})", label)
    f.add_scatter(x=df["Accident year"], y=df["Prior 3-AY avg (same age)"], name="Prior 3-AY average (same age)", line=dict(color=GREY, dash="dash"))
    f.add_scatter(x=df["Accident year"], y=df["Latest diagonal"], name="Latest diagonal", mode="lines+markers", line=dict(color=PURPLE))
    if unit == "pct": f.update_yaxes(tickformat=".0%")
    src = {(r["Accident year"], "Latest diagonal"): cells.get((r["Accident year"], r["Latest age"]), "") for _, r in df.iterrows()}
    return Result(f"{seg_name(seg)} — {label} ({basis.lower()})", df, {"Latest diagonal": unit, "Prior 3-AY avg (same age)": unit, "Change": "pct", "Latest age": "int"}, src,
                  summary=dict(diagnostic=label, calendar_year_shift=cy, last_6_ays_up=up, last_6_ays_down=down,
                               median_abs_change_last_6=fmt(last6.abs().median(), "pct") if len(last6) else None,
                               ay_outliers=[dict(ay=r["Accident year"], change=fmt(r.Change, "pct")) for _, r in outliers.iterrows()]),
                  chart=f, note=f"Apollo tab {tab}. Change = latest diagonal ÷ average of the three previous accident years at the same age − 1.")


# ---------------- AvE and claims ----------------
def ave(S, seg, basis="GROSS"):
    a = S.ave[(S.ave.sub_segment == seg) & (S.ave.basis == basis)].sort_values("accident_year")
    if a.empty: raise KeyError(f"no AvE rows for {seg} {basis}")
    period = str(a.period.iloc[0]) if "period" in a else "since the prior review"
    df = pd.DataFrame({"Accident year": a.accident_year.astype(str), "Expected": a.expected_incurred.values, "Actual": a.actual_incurred.values})
    df["A − E"] = df.Actual - df.Expected
    src = {(r["Accident year"], c): f"ave file | {seg} {basis} AY{r['Accident year']} {c}" for _, r in df.iterrows() for c in ("Expected", "Actual")}
    t = df[["Expected", "Actual", "A − E"]].sum()
    df = pd.concat([df, pd.DataFrame([{"Accident year": "Total", **t.to_dict()}])], ignore_index=True)
    body = df[df["Accident year"] != "Total"]
    f = _fig(f"{seg_name(seg)} — incurred actual vs expected since prior review ({basis.lower()}, $m)", "$m")
    f.add_bar(x=body["Accident year"], y=body.Expected / 1e6, name="Expected", marker_color=GREY)
    f.add_bar(x=body["Accident year"], y=body.Actual / 1e6, name="Actual", marker_color=PURPLE)
    big = body.reindex(body["A − E"].abs().sort_values(ascending=False).index).head(3)
    return Result(f"{seg_name(seg)} — incurred AvE by accident year ({basis.lower()})", df, {"Expected": "mm", "Actual": "mm", "A − E": "mm"}, src,
                  summary=dict(total_a_minus_e=fmt(t["A − E"], "mm", "text"), period=period,
                               largest=[dict(ay=r["Accident year"], a_minus_e=fmt(r["A − E"], "mm", "text")) for _, r in big.iterrows()]),
                  chart=f, note=f"Incurred emergence since the prior review ({period}). A − E positive = adverse.")


DRIVER_TEXT = {"ONE_OFF_LARGE": "a single large claim — a one-off, not an emerging trend",
               "ONE_OFF_RELEASE": "a case reserve release on a single claim settled below its reserve — not indicative of a trend",
               "CAT": "a catastrophe-coded claim — excluded from the attritional trend assessment",
               "TREND": "movement spread across many claims with no dominant claim — may indicate a broader trend"}


def investigate(S, seg, ay, basis="GROSS", n=3):
    """Classify an AY's AvE deviation from the claim file: ONE_OFF_LARGE, ONE_OFF_RELEASE, CAT or TREND."""
    f = 1.0 if basis == "GROSS" else (S.cell(seg, "NET", "Indications", str(ay), col_no=10)[0] /
                                       S.cell(seg, "GROSS", "Indications", str(ay), col_no=10)[0])   # claim file is gross; net via actual net/gross incurred
    c = S.claims[(S.claims.sub_segment == seg) & (S.claims.accident_year == int(ay))].copy()
    a = S.ave[(S.ave.sub_segment == seg) & (S.ave.basis == "GROSS") & (S.ave.accident_year == int(ay))]
    ae = float(a.actual_incurred.iloc[0] - a.expected_incurred.iloc[0]) if len(a) else c.Incurred_change.sum()
    c["abs"] = c.Incurred_change.abs(); top = c.nlargest(n, "abs"); t1 = top.iloc[0]
    share = t1.Incurred_change / ae if ae else 0.0; outlier = t1["abs"] / max(c["abs"].median(), 1)
    cat = c[c.CAT_Flag == 1].Incurred_change.sum()
    label = ("CAT" if ae and cat / ae > 0.5 else ("ONE_OFF_RELEASE" if t1.Incurred_change < 0 else "ONE_OFF_LARGE") if share > 0.5 and outlier >= 3 else "TREND")
    df = pd.DataFrame({"Claim": top.ClaimID, "Description": top.Loss_Description, "CAT": top.CAT_Flag.map({1: "Yes", 0: "No"}),
                       "Incurred movement": top.Incurred_change * f})
    src = {(r.ClaimID, "Incurred movement"): f"claims file | {r.ClaimID} Incurred_change" for r in top.itertuples()}
    return Result(f"{seg_name(seg)} AY {ay} — largest claim movements ({basis.lower()})", df.reset_index(drop=True), {"Incurred movement": "mm"}, src,
                  summary=dict(label=label, explanation=DRIVER_TEXT[label], claims_in_ay=len(c), a_minus_e=fmt(ae * f, "mm", "text"),
                               top_claim=t1.ClaimID, top_claim_movement=fmt(t1.Incurred_change * f, "mm", "text"),
                               top_claim_share_of_a_minus_e=fmt(share, "pct"), cat_movement=fmt(cat * f, "mm", "text")),
                  note="Claim file is gross" + ("." if basis == "GROSS" else f"; net shown at {fmt(f, 'pct')} of gross (net ÷ gross incurred, Indications col 10)."))


# ---------------- generic exhibit reader + navigator ----------------
def _unit_for(header):
    h = header.lower()
    return ("pct" if any(k in h for k in ("ratio", "weight", " lr", "trend")) else "int" if "claims" in h and "per" not in h
            else "num" if "per claim" in h or "per $1m" in h else "mm")


def exhibit(S, seg, basis, tab, col_nos=None, ays=None, block=None):
    """Any Apollo tab: summary tables by column number; triangles/grids by block (ages as columns)."""
    d = S.q(seg, basis, tab, block=block)
    if d.empty: raise KeyError(f"tab {tab} not found for {seg} {basis}")
    if col_nos: d = d[d.col_no.isin(col_nos)]
    if ays: d = d[d.row.isin([str(a) for a in ays])]
    kind = d.kind.iloc[0]
    if kind == "table":
        d = d.assign(column=[f"({int(n)}) {c}" for n, c in zip(d.col_no, d.column)])
    order = list(dict.fromkeys(d.row)); cols = list(dict.fromkeys(d.column))
    df = d.pivot_table(index="row", columns="column", values="value", aggfunc="first").reindex(index=order, columns=cols).reset_index().rename(columns={"row": "Row"})
    units = {c: (_unit_for(c) if kind == "table" else DIAG.get(tab, ("", "factor"))[1] if kind == "grid" else "factor" if block not in (None, "Data") else ("int" if tab in ("ReportedTri", "ClosedTri") else "mm")) for c in cols}
    src = {(r.row, r.column): f"{r.file} | {r.cell}" for r in d.itertuples()}
    return Result(f"{seg_name(seg)} — {d.title.iloc[0]} ({basis.lower()}){' — ' + block if block else ''}", df, units, src,
                  summary=dict(rows=len(df), columns=cols[:20]))


def catalog(S):
    """Tab → title, kind, blocks/columns — from the first pack (all packs share the layout)."""
    seg, basis = S.packs[0]["segment"], S.packs[0]["basis"]
    out = []
    for tab, info in S.packs[0]["tabs"].items():
        d = S.q(seg, basis, tab)
        cols = sorted({(int(n), c) for n, c in zip(d.col_no, d.column) if pd.notna(n)}) if info["kind"] == "table" else []
        out.append(dict(tab=tab, title=info["title"], kind=info["kind"],
                        columns=[f"({n}) {c}" for n, c in cols], blocks=sorted(set(d.block)) if info["kind"] != "table" else []))
    return out


ALIASES = {"ldf": "age factors selections development", "ldfs": "age factors selections development", "pyd": "comparison prior change",
           "development": "age factors", "ibnr": "indications ibnr", "unpaid": "indications case ibnr", "reserve": "indications case ibnr",
           "reinsurance": "retention net", "ceded": "retention", "bf": "bornhuetter methods weights", "cape": "methods weights",
           "weights": "methods weights", "method": "methods weights", "elr": "priori", "trend": "trend", "counts": "reported closed",
           "frequency": "frequency", "severity": "severity", "legal": "legal", "settlement": "settlement", "case": "case average"}


def navigate(S, query, k=5):
    """Rank tabs by word overlap between the question (plus actuarial synonyms) and tab name / title / column headers."""
    tokens = set(re.findall(r"[a-z]+", query.lower()))
    words = {w for w in tokens if len(w) > 2} | {a for w in tokens for a in ALIASES.get(w, "").split()}
    scored = []
    for t in catalog(S):
        text = " ".join([t["tab"], t["title"]] + t["columns"] + t["blocks"]).lower()
        hay = set(re.findall(r"[a-z]+", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t["tab"]).lower() + " " + text))
        score = len(words & hay) + 2 * sum(w in t["tab"].lower() or w in t["title"].lower() for w in words)
        if score: scored.append((score, t))
    return [dict(tab=t["tab"], title=t["title"], kind=t["kind"], columns=t["columns"][:19], blocks=t["blocks"])
            for _, t in sorted(scored, key=lambda x: -x[0])[:k]]
