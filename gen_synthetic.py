"""SYNTHETIC DATA GENERATOR — Apollo-format exhibit packs for Casualty AL / GL / WC (gross + net), a claim file at two
valuations, an AvE file and golden labels. Tab names, column numbering and block layout mirror the sample
'Apollo Output.xlsx'. Everything written here is synthetic and labelled as such."""
import json, math
from pathlib import Path
import numpy as np, pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font

OUT = Path(__file__).parent / "data" / "inputs"
EVAL, PRIOR_EVAL = "2024Q4", "2023Q4"
AYS = list(range(1995, 2025))
AGES = [12 * k for k in range(1, 32)]                       # 12 … 372 months
NET = 0.75                                                  # 25% quota share ceded (locked decision)
ADJ = 0.04                                                  # adjuster as share of loss & legal (paid = incurred)
SEGMENTS = {  # EP in 1995, a-priori ELR, reporting/payment/closure speed (years), severity, seed
    "AL": dict(name="Auto Liability", ep0=20e6, elr=0.66, tau_i=1.8, tau_p=3.0, tau_c=2.2, sev=45e3, seed=11),
    "GL": dict(name="General Liability", ep0=30e6, elr=0.60, tau_i=2.8, tau_p=4.5, tau_c=3.2, sev=70e3, seed=22),
    "WC": dict(name="Workers Comp", ep0=25e6, elr=0.70, tau_i=3.2, tau_p=5.0, tau_c=3.6, sev=30e3, seed=33),
}
PLANTED_PYD = {"AL": {2021: 2.4e6, 2023: 1.5e6}, "GL": {2017: -0.9e6, 2018: -1.1e6, 2019: -1.4e6}, "WC": {2022: 1.2e6}}
PLANTED_CLOSURE = ("WC", range(2017, 2023), 0.15)           # WC: 15% of open claims closed early on latest diagonal → avg case OS up ~18%
METHODS = [  # (Methods_and_Weights header, short key)
    ("Prior Ultimate Loss and Legal", "prior"),
    ("Incurred Loss and Legal Development Method Gross Attritional", "inc_dfm"),
    ("Paid Loss and Legal Development Method Gross Attritional", "paid_dfm"),
    ("Incurred Loss and Legal Bornhuetter-Ferguson Method Gross Attritional", "inc_bf"),
    ("Paid Loss and Legal Bornhuetter-Ferguson Method Gross Attritional", "paid_bf"),
    ("Incurred Loss and Legal Cape Cod Method Gross Attritional", "inc_cc"),
    ("Paid Loss and Legal Cape Cod Method Gross Attritional", "paid_cc"),
    ("Incurred Loss and Legal Ratio Method Gross Attritional", "inc_lr"),
]
AVERAGES = [("3 Pt.", 3, False), ("5 Pt.", 5, False), ("7 Pt.", 7, False), ("All Pt.", 99, False),
            ("3 Pt. Wtd.", 3, True), ("5 Pt. Wtd.", 5, True), ("7 Pt. Wtd.", 7, True), ("All Wtd.", 99, True)]
pct = lambda age, tau: 1 - math.exp(-(age / 12) / tau)       # share emerged by age


# ---------------- model ----------------
def development(tri):
    """ATA factors, their weights, and the standard averages for a dict ay -> cumulative list."""
    ata = {ay: [tri[ay][j + 1] / tri[ay][j] if j + 1 < len(tri[ay]) and tri[ay][j] else None for j in range(len(AGES))] for ay in AYS}
    avgs = {}
    for name, n, wtd in AVERAGES:
        row = []
        for j in range(len(AGES)):
            pts = [(ata[ay][j], tri[ay][j]) for ay in AYS if ata[ay][j] is not None][-n:]
            row.append(None if not pts else round(sum(f * w for f, w in pts) / sum(w for _, w in pts) if wtd else sum(f for f, _ in pts) / len(pts), 3))
        avgs[name] = row
    return ata, avgs


def pattern(tau):
    f = [round(pct(a + 12, tau) / pct(a, tau), 3) for a in AGES]
    return [x if x > 1.0005 else 1.0 for x in f]


def build_segment(code):
    s = SEGMENTS[code]; rng = np.random.default_rng(s["seed"])
    ep = {ay: s["ep0"] * 1.035 ** (ay - 1995) * rng.uniform(0.92, 1.08) for ay in AYS}
    ult = {ay: ep[ay] * s["elr"] * rng.lognormal(0, 0.08) for ay in AYS}
    legal = {ay: 0.08 * rng.uniform(0.85, 1.15) for ay in AYS}                     # legal share of loss & legal
    n_age = {ay: 2024 - ay + 1 for ay in AYS}                                       # diagonal length
    noisy = lambda base, sd: base * rng.lognormal(0, sd)
    inc = {ay: [noisy(ult[ay] * pct(a, s["tau_i"]), .006) for a in AGES[:n_age[ay]]] for ay in AYS}
    paid = {ay: [min(noisy(ult[ay] * pct(a, s["tau_p"]), .008), inc[ay][i]) for i, a in enumerate(AGES[:n_age[ay]])] for ay in AYS}
    for ay, amt in PLANTED_PYD[code].items():
        inc[ay][-1] += amt * 0.95                                                   # planted movement on latest diagonal
    n_ult = {ay: ult[ay] / s["sev"] for ay in AYS}
    rep = {ay: [round(n_ult[ay] * pct(a, s["tau_i"] * 0.6)) for a in AGES[:n_age[ay]]] for ay in AYS}
    clo = {ay: [min(round(n_ult[ay] * pct(a, s["tau_c"])), rep[ay][i]) for i, a in enumerate(AGES[:n_age[ay]])] for ay in AYS}
    seg, ays, factor = PLANTED_CLOSURE
    if code == seg:
        for ay in ays: clo[ay][-1] = round(clo[ay][-1] + factor * (rep[ay][-1] - clo[ay][-1]))
    tris = {}
    for key, tri, tau in [("inc", inc, s["tau_i"]), ("paid", paid, s["tau_p"]), ("rep", rep, s["tau_i"] * 0.6), ("clo", clo, s["tau_c"])]:
        ata, avgs = development(tri); theory = pattern(tau)
        # prior review selected ~the 5-pt weighted average (rounded), unity in the tail
        prior = [1.0 if t == 1.0 or avgs["5 Pt. Wtd."][j] is None else max(1.0, round(avgs["5 Pt. Wtd."][j] + rng.normal(0, 0.001), 3))
                 for j, t in enumerate(theory)]
        sel, manual = list(prior), [1] * len(AGES)
        if key == "inc" and code == "GL": prior[2] = theory[2]; sel[2] = avgs["5 Pt. Wtd."][2]   # planted: 36m moves off prior to 5-pt wtd
        if key == "inc" and code == "WC": sel[1] = round(prior[1] + 0.045, 3); manual[1] = sel[1]  # planted: judgmental override at 24m
        if key == "inc" and code == "AL": prior[3] = sel[3] = round(avgs["7 Pt. Wtd."][3] + 0.03, 3)  # planted: 48m prior kept, outside averages
        tris[key] = dict(tri=tri, ata=ata, avgs=avgs, prior=prior, sel=sel, manual=manual,
                         bench=[round(x * 1.02, 3) if x > 1 else 1.0 for x in prior], cdf=[float(np.prod(sel[j:])) for j in range(len(AGES))])
    ind, w = {}, {}
    for ay in AYS:
        j = n_age[ay] - 1; I, P = inc[ay][-1], paid[ay][-1]
        ci, cp = tris["inc"]["cdf"][j], tris["paid"]["cdf"][j]
        bf = lambda base, cdf, elr: base + elr * ep[ay] * (1 - 1 / cdf)
        ind[ay] = dict(inc_dfm=I * ci, paid_dfm=P * cp, inc_bf=bf(I, ci, s["elr"]), paid_bf=bf(P, cp, s["elr"]),
                       inc_cc=bf(I, ci, s["elr"] * .98), paid_cc=bf(P, cp, s["elr"] * .98), inc_lr=s["elr"] * ep[ay])
        w[ay] = ({"inc_dfm": 1} if ay <= 2014 else {"inc_dfm": .5, "inc_bf": .5} if ay <= 2020 else
                 {"inc_bf": .5, "inc_cc": .5} if ay <= 2022 else {"inc_bf": 1} if ay == 2023 else {"inc_bf": .5, "inc_lr": .5})
    final = {ay: max(sum(ind[ay][m] * v for m, v in w[ay].items()), inc[ay][-1]) for ay in AYS}
    prior_ult = {ay: final[ay] - PLANTED_PYD[code].get(ay, final[ay] * rng.normal(0, .004)) for ay in AYS if ay < 2024}
    for ay in AYS: ind[ay]["prior"] = prior_ult.get(ay, 0.0)
    return dict(code=code, s=s, ep=ep, inc=inc, paid=paid, legal=legal, tris=tris, ind=ind, w=w, final=final,
                prior_ult=prior_ult, n_age=n_age, prior_row=dict(ep=s["ep0"] * 2.2, ult=s["ep0"] * 2.2 * s["elr"]))


# ---------------- workbook writing (sample layout) ----------------
def header(ws, title, exhibit, basis, code):
    ws["B2"] = "Casualty Reserving"; ws["A3"] = code; ws["B3"] = f"{title} - {basis} - ATTR detail"
    ws["B5"] = f"Data Evaluated as of {EVAL}"; ws["J5"] = f"Exhibit {exhibit}"          # same cells as the sample
    ws["B6"] = f"Accounting Evaluated as of {EVAL}"; ws["B7"] = "SYNTHETIC DATA — prototype demonstration only"
    ws["B7"].font = Font(color="C00000", bold=True)


def summary_table(wb, tab, title, exhibit, basis, code, cols, rows, totals):
    """cols exclude 'Accident Period'; rows = [(label, [values])]; totals = [(label, [values])] written from row 43."""
    ws = wb.create_sheet(tab); header(ws, title, exhibit, basis, code)
    ws.cell(11, 1, "Accident Period")
    for c, h in enumerate(cols, start=2):
        ws.cell(10, c, f"({c - 1})"); ws.cell(11, c, h)
    for r, (lab, vals) in enumerate(rows + totals, start=12):
        r = r if r - 12 < len(rows) else 43 + (r - 12 - len(rows))
        ws.cell(r, 1, lab)
        for c, v in enumerate(vals, start=2):
            if v is not None: ws.cell(r, c, round(v, 6) if isinstance(v, float) else v)


def age_grid(wb, tab, title, exhibit, basis, code, grid, prior_value):
    """Diagnostic layout: 'Accident' / 'Origin Period' header, rows PRIOR + AYs, one column per age."""
    ws = wb.create_sheet(tab); header(ws, title, exhibit, basis, code)
    ws.cell(10, 1, "Accident"); ws.cell(11, 1, "Origin Period")
    for j, a in enumerate(AGES, start=2): ws.cell(11, j, str(a))
    ws.cell(12, 1, "PRIOR")
    for j in range(len(AGES)): ws.cell(12, 2 + j, prior_value)
    for i, ay in enumerate(AYS, start=13):
        ws.cell(i, 1, str(ay))
        for j, v in enumerate(grid[ay], start=2): ws.cell(i, j, round(v, 6))


def triangle(wb, tab, title, exhibit, basis, code, T, scale, prior_value):
    ws = wb.create_sheet(tab); header(ws, title, exhibit, basis, code)
    ages_row = lambda r: [ws.cell(r, 2 + j, str(a)) for j, a in enumerate(AGES)]
    ws.cell(10, 1, "Maturity"); ws.cell(12, 1, "Origin Period"); ages_row(12)
    ws.cell(13, 1, "PRIOR")
    for j in range(len(AGES)): ws.cell(13, 2 + j, round(prior_value, 2))
    for i, ay in enumerate(AYS, start=14):
        ws.cell(i, 1, str(ay))
        for j, v in enumerate(T["tri"][ay], start=2): ws.cell(i, j, round(v * scale, 2))
    ws.cell(47, 1, "Age-To-Age Factors"); ws.cell(49, 1, "Origin Period")
    for j, a in enumerate(AGES): ws.cell(49, 2 + j, f"{a}-{a + 12}")
    for i, ay in enumerate(AYS, start=51):
        ws.cell(i, 1, str(ay))
        for j, v in enumerate(T["ata"][ay], start=2):
            if v is not None: ws.cell(i, j, round(v, 4))
    blocks = [("Averages", 83, list(T["avgs"].items())),
              ("Benchmarks", 108, [("Prior Selections", T["prior"]), (f"Benchmark - {title} Casualty Industry", T["bench"])]),
              ("Manual Selections", 117, [("manual", T["manual"])]),
              ("Ultimate Selections", 124, [("Selection", T["sel"]), ("Age to Ultimate", [round(x, 3) for x in T["cdf"]])])]
    for name, r0, rows in blocks:
        ws.cell(r0, 1, name); ws.cell(r0 + 2, 1, "Origin Period"); ages_row(r0 + 2)
        for i, (lab, vals) in enumerate(rows, start=r0 + 3):
            ws.cell(i, 1, lab)
            for j, v in enumerate(vals, start=2):
                if v is not None: ws.cell(i, j, v)


def write_workbook(seg, basis):
    f = 1.0 if basis == "GROSS" else NET; code = f"CAS_{seg['code']}"; s = seg["s"]; pr = seg["prior_row"]
    wb = Workbook(); toc = wb.active; toc.title = "Table of Contents"
    toc["A1"] = "Casualty Reserving"; toc["A2"] = f"{basis} - ATTR detail ({s['name']})"; toc["A3"] = "SYNTHETIC DATA"

    def comp(ay):  # Indications components (scaled by basis)
        if ay == "PRIOR":
            E, P, I, U, sh = pr["ep"], pr["ult"], pr["ult"], pr["ult"], 0.08
        else:
            E, P, I, U, sh = seg["ep"][ay], seg["paid"][ay][-1], seg["inc"][ay][-1], seg["final"][ay], seg["legal"][ay]
        E, P, I, U = E * f, P * f, I * f, U * f
        c = dict(ep=E, paid_loss=P * (1 - sh), case_loss=(I - P) * (1 - sh), inc_loss=I * (1 - sh),
                 paid_legal=P * sh, case_legal=(I - P) * sh, inc_legal=I * sh, paid_adj=P * ADJ, inc_adj=P * ADJ,
                 ult_loss=U * (1 - sh), ult_legal=U * sh, ult_adj=U * ADJ)
        c.update(inc_total=I + c["inc_adj"], ult_total=U + c["ult_adj"], ibnr_loss=c["ult_loss"] - c["inc_loss"],
                 ibnr_legal=c["ult_legal"] - c["inc_legal"], ibnr_adj=c["ult_adj"] - c["inc_adj"])
        c["ibnr_total"] = c["ibnr_loss"] + c["ibnr_legal"] + c["ibnr_adj"]
        return c
    keys = ["PRIOR"] + AYS; C = {k: comp(k) for k in keys}
    lab = lambda k, suffix="/12": "PRIOR" if k == "PRIOR" else f"{k}{suffix}"
    total = lambda rows: [sum(r[1][i] for r in rows) if all(isinstance(r[1][i], (int, float)) for r in rows) else None for i in range(len(rows[0][1]))]

    ind_cols = [("Earned Premium", "ep"), ("Paid Loss", "paid_loss"), ("Case OS Loss", "case_loss"), ("Incurred Loss", "inc_loss"),
                ("Paid Legal", "paid_legal"), ("Case OS Legal", "case_legal"), ("Incurred Legal", "inc_legal"), ("Paid Adjuster", "paid_adj"),
                ("Incurred Adjuster", "inc_adj"), ("Total Incurred Loss, Legal and Adjuster", "inc_total"), ("Final Adjusted Ultimate Loss", "ult_loss"),
                ("Final Adjusted Ultimate Legal", "ult_legal"), ("Final Adjusted Ultimate Adjuster", "ult_adj"),
                ("Final Adjusted Ultimate Loss, Legal, and Adjuster", "ult_total"), ("Final Adjusted Loss IBNR", "ibnr_loss"),
                ("Final Adjusted Legal IBNR", "ibnr_legal"), ("Final Adjusted Adjuster IBNR", "ibnr_adj"),
                ("Final Adjusted Loss, Legal, and Adjuster IBNR", "ibnr_total")]
    rows = [(lab(k), [C[k][key] for _, key in ind_cols]) for k in keys]
    summary_table(wb, "Indications", "Summary of Indications", 1, basis, code, [h for h, _ in ind_cols], rows, [("Totals", total(rows))])

    n_ult = lambda k: 0 if k == "PRIOR" else seg["tris"]["rep"]["tri"][k][-1] * seg["tris"]["rep"]["cdf"][seg["n_age"][k] - 1]
    ratio_cols = ["Earned Premium", "Incurred Loss Ratio", "Incurred Legal Ratio", "Incurred Adjuster Ratio", "Incurred Loss, Legal, and Adjuster Ratio",
                  "Final Ultimate Loss Ratio", "Final Ultimate Legal Ratio", "Final Ultimate Adjuster Ratio", "Final Ultimate Loss, Legal, and Adjuster Ratio",
                  "Selected Ultimate Reported Claims", "Selected Ultimate Loss and Legal per Claim"]
    rr = lambda c, k: [c["ep"], c["inc_loss"] / c["ep"], c["inc_legal"] / c["ep"], c["inc_adj"] / c["ep"], c["inc_total"] / c["ep"],
                       c["ult_loss"] / c["ep"], c["ult_legal"] / c["ep"], c["ult_adj"] / c["ep"], c["ult_total"] / c["ep"], n_ult(k),
                       (c["ult_loss"] + c["ult_legal"]) / n_ult(k) if n_ult(k) else None]
    summary_table(wb, "Indicated_Ratios", "Summary of Indicated Ratios", 2, basis, code, ratio_cols, [(lab(k), rr(C[k], k)) for k in keys], [])

    mw_cols = [h for h, _ in METHODS for _ in (0, 1)] + ["Booked Ultimate Gross", "Booked Ultimate Gross", "Weight Totals"]
    mw = lambda k: ([pr["ult"] * f, 1] + [pr["ult"] * f, 0] * 7 if k == "PRIOR" else
                    sum([[seg["ind"][k][m] * f, seg["w"][k].get(m, 0.0)] for _, m in METHODS], [])) + [(pr["ult"] if k == "PRIOR" else seg["final"][k]) * f, 0, 1]
    rows = [(lab(k), mw(k)) for k in keys]
    summary_table(wb, "Methods_and_Weights", "Summary of Methods Indications and Weights", 3, basis, code, mw_cols, rows,
                  [("Totals", [v if i % 2 == 0 and i < 18 else None for i, v in enumerate(total(rows))])])

    adj_cols = ["Allocated Ultimate Loss", "Ultimate Loss Adjustment", "Final Adjusted Ultimate Loss", "Allocated Ultimate Legal",
                "Ultimate Legal Adjustment", "Final Adjusted Ultimate Legal", "Allocated Ultimate Adjuster", "Ultimate Adjuster Adjustment", "Final Adjusted Ultimate Adjuster"]
    rows = [(lab(k), [C[k]["ult_loss"], 0.0, C[k]["ult_loss"], C[k]["ult_legal"], 0.0, C[k]["ult_legal"], C[k]["ult_adj"], 0.0, C[k]["ult_adj"]]) for k in keys]
    summary_table(wb, "Ultimate_Adjustments", "Summary of Ultimate Adjustments", 4, basis, code, adj_cols, rows, [("Totals", total(rows))])

    ibnr_cols = [h for h, k in ind_cols if k.startswith("ibnr")] + ratio_cols[1:9]
    rows = [(lab(k), [C[k]["ibnr_loss"], C[k]["ibnr_legal"], C[k]["ibnr_adj"], C[k]["ibnr_total"]] + rr(C[k], k)[1:9]) for k in keys]
    summary_table(wb, "IBNR_and_Ratios", "Summary of IBNR and Ultimate Ratios", 5, basis, code, ibnr_cols, rows,
                  [("Totals", total(rows)[:4] + [None] * 8)])

    cwp_cols = []
    for comp_name in ("Loss", "Legal", "Adjuster", "Loss, Legal, and Adjuster"):
        cwp_cols += [f"Prior Ultimate {comp_name}", f"Final Adjusted Ultimate {comp_name}", f"Change in Ultimate {comp_name}"]
    def cwp(k):
        c = C[k]; cur = [c["ult_loss"], c["ult_legal"], c["ult_adj"]]
        if k == "PRIOR": prv = list(cur)
        elif k in seg["prior_ult"]:
            share = seg["prior_ult"][k] / seg["final"][k]; prv = [x * share for x in cur]   # change split pro rata by component
        else: prv = [0.0, 0.0, 0.0]                                                        # new AY: no prior ultimate
        cur.append(sum(cur)); prv.append(sum(prv))
        return sum([[p, n, n - p] for p, n in zip(prv, cur)], [])
    rows = [(lab(k, ""), cwp(k)) for k in keys]
    summary_table(wb, "Comparison_With_Prior", "Comparison With Prior", 6, basis, code, cwp_cols, rows,
                  [("Prior Total", total(rows[:-1])), ("Total", total(rows))])

    mr_cols = ["Earned Premium"] + [h for h, m in METHODS[1:]] + ["Booked Ultimate Gross"]
    rows = [(lab(k), [C[k]["ep"]] + [(pr["ult"] if k == "PRIOR" else seg["ind"][k][m]) * f / C[k]["ep"] for _, m in METHODS[1:]]
             + [C[k]["ult_total"] / C[k]["ep"]]) for k in keys]
    summary_table(wb, "Method_Ratios", "Summary of Method Ultimate Ratios", 7, basis, code, mr_cols, rows, [])

    rows = [(lab(k), [C[k]["ep"], C[k]["ep"] * s["elr"], s["elr"]]) for k in keys]
    summary_table(wb, "ELRIncLossLegal", "ELR Incurred Loss and Legal Gross Attritional", 18, basis, code,
                  ["Ultimate Earned Premium", "Selected A-Priori Ultimate Loss", "A-Priori Ultimate LR"], rows, [])
    for tab, title, val in [("FrequencyTrend", "Frequency Trend", .01), ("SeverityTrend", "Severity Trend", .04),
                            ("LossTrend", "Loss Trend", .05), ("PremiumTrend", "Premium Trend", .03)]:
        summary_table(wb, tab, f"{title} Gross Attritional", 24, basis, code,
                      [f"Incremental {title}", "Prior Selections", f"Benchmark - {title}"], [(lab(k, ""), [val, val, round(val * 1.1, 3)]) for k in keys], [])

    T = seg["tris"]
    for tab, key, title, ex, scale, prior in [("IncLossLegalTri", "inc", "LDF Incurred Loss and Legal", 19, f, pr["ult"] * f),
                                              ("PaidLossLegalTri", "paid", "LDF Paid Loss and Legal", 20, f, pr["ult"] * f),
                                              ("ReportedTri", "rep", "LDF Reported Count", 34, 1, pr["ult"] / s["sev"]),
                                              ("ClosedTri", "clo", "LDF Closed Count", 35, 1, pr["ult"] / s["sev"])]:
        triangle(wb, tab, f"{title} Gross Attritional", ex, basis, code, T[key], scale, prior)

    g = lambda fn: {ay: [fn(ay, j) for j in range(seg["n_age"][ay])] for ay in AYS}
    I, P, R, Cl = (T[k]["tri"] for k in ("inc", "paid", "rep", "clo"))
    sh = seg["legal"]; ep = seg["ep"]
    diags = [("PaidToIncurred", "Paid Loss-to-Incurred Loss Ratio", 37, g(lambda a, j: P[a][j] / I[a][j]), 1.0),
             ("Frequency", "Frequency (per million in EP)", 38, g(lambda a, j: R[a][j] / ep[a] * 1e6), None),
             ("IncLossRToG", "Inc. Loss Retention-to-Gross", 39, g(lambda a, j: NET), NET),
             ("IncurredToEarnedPremium", "Incurred Loss-to-Earned Premium Ratio", 40, g(lambda a, j: I[a][j] / ep[a]), None),
             ("SettlementRate", "Settlement Rate", 41, g(lambda a, j: Cl[a][j] / R[a][j]), 1.0),
             ("AverageCaseOS", "Average Case OS", 43, g(lambda a, j: (I[a][j] - P[a][j]) * f / max(R[a][j] - Cl[a][j], 1)), 0.0),
             ("EarnedPremiumRToG", "Earned Premium Retention-to-Gross", 44, g(lambda a, j: NET), NET),
             ("PaidToEarnedPremium", "Paid Loss-to-Earned Premium Ratio", 45, g(lambda a, j: P[a][j] / ep[a]), None),
             ("PaidLegalToLoss", "Paid Legal-to-Paid Loss Ratio", 46, g(lambda a, j: sh[a] / (1 - sh[a])), None),
             ("PaidLegalToIncdLoss", "Paid Legal-to-Incurred Loss", 47, g(lambda a, j: P[a][j] * sh[a] / (I[a][j] * (1 - sh[a]))), None)]
    for tab, title, ex, grid, prior in diags:
        age_grid(wb, tab, title, ex, basis, code, grid, prior)
    wb.save(OUT / f"Apollo_{seg['code']}_{basis}.xlsx")


# ---------------- claims + AvE ----------------
DESC = {"AL": ["Rear-end collision, soft tissue injury", "Intersection collision, bodily injury", "Fleet truck jackknife, multiple claimants",
               "Delivery van struck pedestrian", "Parking lot property damage", "Lane-change sideswipe, whiplash"],
        "GL": ["Slip and fall at retail premises", "Product defect injury claim", "Contractor property damage", "Trip on uneven sidewalk",
               "Food-borne illness at restaurant", "Falling object injury on site"],
        "WC": ["Back strain lifting materials", "Fall from ladder", "Repetitive strain injury", "Machinery laceration",
               "Forklift accident", "Occupational hearing loss"]}
PLANTED_CLAIMS = {  # (seg, ay): (description, CAT flag, gross movement)
    ("AL", 2021): ("Fleet truck rollover on interstate, severe bodily injury; liability accepted during 2024", 0, 2.3e6),
    ("AL", 2023): ("Multi-vehicle pile-up during winter storm (CAT event)", 1, 1.45e6),
    ("GL", 2019): ("Product defect claim settled below reserve, case reserve released", 0, -1.2e6),
}


def claims_and_ave(segs):
    rng = np.random.default_rng(99); claims, ave = [], []
    occ = ["Healthcare", "Construction", "Retail", "Manufacturing", "Hospitality", "Warehouse", "Office", "Food Processing"]
    regions = ["NJ", "NY", "TX", "CA", "FL", "IL", "PA", "OH", "GA", "TN"]
    for code, seg in segs.items():
        ti, tp = seg["s"]["tau_i"], seg["s"]["tau_p"]
        for ay in range(2015, 2025):
            age = seg["n_age"][ay] * 12; tri_i, tri_p = seg["inc"][ay], seg["paid"][ay]
            inc_now, inc_prev = tri_i[-1], (tri_i[-2] if len(tri_i) > 1 else 0.0)
            if ay < 2024:                                                           # new AY has no prior expectation
                base = seg["prior_ult"][ay]
                for basis, f in (("GROSS", 1.0), ("NET", NET)):
                    ave.append(dict(sub_segment=code, basis=basis, accident_year=ay, period=f"{PRIOR_EVAL} to {EVAL}",
                                    expected_incurred=round(base * (pct(age, ti) - pct(age - 12, ti)) * f, 2), actual_incurred=round((inc_now - inc_prev) * f, 2),
                                    expected_paid=round(base * (pct(age, tp) - pct(age - 12, tp)) * f, 2), actual_paid=round((tri_p[-1] - tri_p[-2]) * f, 2)))
            n = int(rng.integers(12, 25)); planted = PLANTED_CLAIMS.get((code, ay))
            p_mv = planted[2] if planted else 0.0
            p_prev = (0.4 * p_mv if p_mv > 0 else -1.25 * p_mv) if planted else 0.0
            move = (inc_now - inc_prev) - p_mv
            w = rng.dirichlet(np.ones(n) * (40 if (code, ay) == ("WC", 2022) else 1.5))   # WC 2022: broad-based trend
            for i in range(n + bool(planted)):
                if planted and i == n: prev, mv, desc, cat = p_prev, p_mv, planted[0], planted[1]
                else: prev, mv, desc, cat = (inc_prev - p_prev) * w[i], move * w[i], DESC[code][int(rng.integers(0, 6))], 0
                cur = prev + mv; paid_c = cur * rng.uniform(0.2, 0.8)
                claims.append(dict(ClaimID=f"CLM{len(claims) + 1:06d}", policy_number=f"POL{code}{ay}{i:04d}", sub_segment=code, accident_year=ay,
                                   PolicyYear=ay - int(rng.integers(0, 2)), occupation=occ[int(rng.integers(0, 8))], region=regions[int(rng.integers(0, 10))],
                                   Limit=int(rng.choice([1e6, 2e6, 5e6])), Deductible=int(rng.choice([0, 10000, 25000])), Loss_Description=desc, CAT_Flag=cat,
                                   Incurred_prior=round(prev, 2), Incurred_current=round(cur, 2), Paid_current=round(paid_c, 2),
                                   Outstanding_current=round(cur - paid_c, 2), Incurred_change=round(mv, 2)))
    pd.DataFrame(claims).to_csv(OUT / "claims_synthetic.csv", index=False)
    pd.DataFrame(ave).to_csv(OUT / "ave_synthetic.csv", index=False)


def golden(segs):
    codes = {}
    for code, seg in segs.items():
        sel = seg["tris"]["inc"]["sel"]
        codes[code] = ["MATCH_AVG:5 Pt. Wtd." if (code, j) == ("GL", 2) else "MANUAL" if (code, j) == ("WC", 1)
                       else "UNITY_TAIL" if sel[j] == 1.0 else "SAME_AS_PRIOR" for j in range(len(AGES))]
    g = dict(ldf_codes=codes, ldf_prior_outside_range={"AL": [48]},
             drivers=[dict(sub_segment="AL", accident_year=2021, label="ONE_OFF_LARGE"), dict(sub_segment="AL", accident_year=2023, label="CAT"),
                      dict(sub_segment="GL", accident_year=2019, label="ONE_OFF_RELEASE"), dict(sub_segment="WC", accident_year=2022, label="TREND")],
             diagnostics=[dict(sub_segment="WC", tab="AverageCaseOS", ays=list(PLANTED_CLOSURE[1]), direction="up"),
                          dict(sub_segment="WC", tab="SettlementRate", ays=list(PLANTED_CLOSURE[1]), direction="up")])
    (OUT.parent / "golden_labels.json").write_text(json.dumps(g, indent=1))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    segs = {c: build_segment(c) for c in SEGMENTS}
    for seg in segs.values():
        for basis in ("GROSS", "NET"): write_workbook(seg, basis)
    claims_and_ave(segs); golden(segs)
    print("written:", sorted(p.name for p in OUT.iterdir()))
