"""Agent 1 — Data Steward (no LLM). Checks the study against what drafting and analysis need, and says what is
missing *before* any work starts."""
from study import BASES, seg_name

CORE = ["Indications", "Comparison_With_Prior", "Methods_and_Weights", "IncLossLegalTri", "PaidLossLegalTri"]
OPTIONAL = {"ELRIncLossLegal": "a-priori ELR commentary", "LossTrend": "trend commentary", "Ultimate_Adjustments": "adjustment commentary",
            "ReportedTri": "claim-count diagnostics", "ClosedTri": "claim-count diagnostics",
            "PaidToIncurred": "diagnostics", "AverageCaseOS": "diagnostics", "SettlementRate": "diagnostics"}


def check(study):
    """Return dict(items=[(status, text)], draft_ready, analysis_ready, gaps=[...])."""
    items, gaps = [], []
    have = {(p["segment"], p["basis"]): p for p in study.packs}
    if not study.packs: gaps.append("Apollo packs")
    for seg in study.segments:                               # sub-segments are whatever the packs contain
        name = seg_name(seg)
        for basis in BASES:
            p = have.get((seg, basis))
            if not p:
                items.append(("missing", f"Apollo pack {name} ({seg}) {basis.lower()} — not found")); gaps.append(f"{seg} {basis.lower()} pack"); continue
            miss = [t for t in CORE if t not in p["tabs"]]
            opt = sorted({OPTIONAL[t] for t in OPTIONAL if t not in p["tabs"]})
            status = "missing" if miss else "warn" if opt else "ok"
            txt = f"Apollo pack {name} ({seg}) {basis.lower()} — {p['file']}, {len(p['tabs'])} tabs"
            if miss: txt += f"; missing core tabs: {', '.join(miss)}"; gaps.append(f"{seg} {basis.lower()} core tabs")
            if opt: txt += f"; without {', '.join(opt)}"
            items.append((status, txt))
    evals = sorted({e for p in study.packs for e in p["evals"]})
    items.append(("ok" if len(evals) == 1 else "missing", f"Evaluation date: {', '.join(evals) or 'not found'}"
                  + ("" if len(evals) <= 1 else " — packs are evaluated at different dates")))
    if len(evals) > 1: gaps.append("inconsistent evaluation dates")
    items += _ties(study)
    items.append(("ok", f"Claims file — {len(study.claims):,} claims, AY {study.claims.accident_year.min()}–{study.claims.accident_year.max()}")
                 if study.claims is not None else ("warn", "Claims file — not found: PYD/AvE drivers cannot be explained at claim level"))
    if study.ave is not None:
        miss = [s for s in study.segments if s not in set(study.ave.sub_segment)]
        items.append(("warn" if miss else "ok", f"AvE file — {len(study.ave)} rows ({', '.join(sorted(study.ave.sub_segment.unique()))})"
                      + (f"; no rows for {', '.join(miss)}" if miss else "")))
    else:
        items.append(("warn", "AvE file — not found: AvE tables and commentary will be flagged [INPUT REQUIRED]"))
    items += [("warn", f"{f} — not used: {why}") for f, why in study.other]
    draft_ready = not gaps
    return dict(items=items, gaps=gaps, draft_ready=draft_ready, analysis_ready=bool(study.packs))


def _ties(study):
    """AY rows reconcile to the Totals row (Indications cols 1 and 14); net never above gross (col 14)."""
    bad, over = [], []
    for p in study.packs:
        seg, basis = p["segment"], p["basis"]
        if "Indications" not in p["tabs"]: continue
        for col in (1, 14):
            d = study.q(seg, basis, "Indications", col_no=col)
            body, tot = d[~d.row.isin(["Totals", "Prior Total", "Total"])].value.sum(), d[d.row == "Totals"].value.sum()
            if abs(body - tot) > max(1.0, 1e-6 * abs(tot)): bad.append(f"{seg} {basis.lower()} col {col}")
        if basis == "NET" and study.has(seg, "GROSS", "Indications"):
            n = study.q(seg, "NET", "Indications", col_no=14).set_index("row").value
            g = study.q(seg, "GROSS", "Indications", col_no=14).set_index("row").value
            if (n > g.reindex(n.index) + 1).any(): over.append(seg)
    return [("missing" if bad else "ok", "Accident-year rows reconcile to Exhibit 1 totals" + (f" — except {', '.join(bad)}" if bad else "")),
            ("missing" if over else "ok", "Net ultimates do not exceed gross" + (f" — except {', '.join(over)}" if over else ""))]


ICON = {"ok": "✅", "warn": "⚠️", "missing": "❌"}


def readiness_message(study):
    r = check(study)
    lines = ["**Data check**", ""] + [f"{ICON[s]} {t}" for s, t in r["items"]] + [""]
    if r["draft_ready"]:
        lines.append(f"**Ready to draft the DVR memo** for {study.lob} ({', '.join(seg_name(s) for s in study.segments)}), gross and net, "
                     f"evaluated {study.evaluation}, and **ready to answer questions** on any part of the study.")
    elif r["analysis_ready"]:
        lines.append(f"**Not ready to draft the full memo** — missing: {', '.join(r['gaps'])}. "
                     "I can still answer questions on the packs that are present. Upload the missing files here or add them to the data folder.")
    else:
        lines.append("**No Apollo output found.** Upload the Apollo packs (.xlsx), the claims file and the AvE file to get started.")
    return "\n\n".join(lines[:2]) + "\n\n" + "  \n".join(lines[2:]), r
