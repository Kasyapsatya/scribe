"""Tool closures the agents call (PLAN_v3 §4): study and evidence tools (memo tools follow in memo.py, phase 4).
build_tools(S, ledger, turn, allow) returns only the closures named in `allow`, so e.g. the Analyst never sees
claim_movements (Claims Investigator only). Every call tags its numbers with the ledger and logs a step for the
"How I got this" panel. turn = {"results": [...], "steps": [...]}, appended to as tools run."""
import json, re
import specialists as X
from ledger import CalcError
from study import fmt

STUDY_TOOLS = ["find_sections", "reserve_summary", "prior_year_development", "reinsurance_ratios", "ldf_selection",
               "method_weights", "method_indications", "what_if_weights", "sensitivity_range", "diagnostic",
               "actual_vs_expected", "read_exhibit"]
EVIDENCE_TOOLS = ["calculate", "explain_figure"]


def build_tools(S, ledger, turn, allow):
    def run(agent, desc, job):
        """Run one specialist; any error (bad argument, missing tab) goes back to the model as text instead of crashing the turn."""
        try:
            res = job(); turn["results"].append(res); turn["steps"].append((agent, f"{desc} — requested by Gemini"))
            return json.dumps(res.to_llm(ledger), default=str)
        except Exception as e:
            turn["steps"].append((agent, f"{desc} — failed: {e}")); return f"ERROR: {e}"

    def seg_ok(s):
        if s.upper() not in S.segments: raise ValueError(f"sub_segment must be one of {S.segments}")
        return s.upper()

    years = lambda s: [int(x) for x in re.findall(r"\d{4}", s or "")]

    def find_sections(question: str) -> str:
        """Study Navigator: list the Apollo tabs (with column numbers / blocks) most relevant to a question. Use first when unsure where data lives."""
        turn["steps"].append(("Study Navigator", f"Searched the tab catalogue for “{question}” — requested by Gemini"))
        return json.dumps(X.navigate(S, question))

    def reserve_summary(basis: str = "GROSS") -> str:
        """Unpaid by sub-segment: Case OS (Indications col 3), IBNR (col 15), Legal need (col 6 + col 16), Adjuster need (col 17), Total unpaid. basis GROSS or NET."""
        return run("Exhibit reader", f"Reserve summary ({basis})", lambda: X.reserve_summary(S, basis.upper()))

    def prior_year_development(basis: str = "GROSS", sub_segment: str = "") -> str:
        """PYD by accident year (Comparison_With_Prior col 12, prior accident years only). sub_segment AL/GL/WC or empty for all."""
        return run("Exhibit reader", f"PYD by AY ({basis} {sub_segment or 'all'})", lambda: X.pyd(S, basis.upper(), seg_ok(sub_segment) if sub_segment else None))

    def reinsurance_ratios() -> str:
        """Net-to-gross earned premium ratio by accident year for each sub-segment (Indications col 1)."""
        return run("Exhibit reader", "Net-to-gross EP by AY", lambda: X.reinsurance(S))

    def ldf_selection(sub_segment: str, triangle: str = "incurred", basis: str = "GROSS") -> str:
        """LDF Explainer: selected vs prior LDF per age with the reason (same as prior, matches an average, judgmental, benchmark). triangle 'incurred' or 'paid'."""
        tri = "PaidLossLegalTri" if triangle.lower().startswith("paid") else "IncLossLegalTri"
        return run("LDF Explainer", f"{tri} selections ({sub_segment} {basis})", lambda: X.ldf(S, seg_ok(sub_segment), basis.upper(), tri))

    def method_weights(sub_segment: str, basis: str = "GROSS") -> str:
        """Method weights by accident-year band from Methods_and_Weights."""
        return run("Exhibit reader", f"Method weights ({sub_segment} {basis})", lambda: X.method_weights(S, seg_ok(sub_segment), basis.upper()))

    def method_indications(sub_segment: str, basis: str = "GROSS", ay_from: int = 0, ay_to: int = 0) -> str:
        """Apollo's indication by method and AY (loss & legal) vs the selected ultimate, with the spread between methods."""
        return run("Exhibit reader", f"Method indications ({sub_segment} {basis})", lambda: X.method_indications(S, seg_ok(sub_segment), basis.upper(), ay_from or None, ay_to or None))

    def what_if_weights(sub_segment: str, weights: str, basis: str = "GROSS", ay_from: int = 0, ay_to: int = 0) -> str:
        """Scenario agent: re-weight Apollo's own method indications (no recalculation) for AYs ay_from..ay_to (0 = all) and compare with the
        selected ultimate. weights like 'inc_bf:1' or 'inc_dfm:0.5,inc_bf:0.5'. Keys: inc_dfm, paid_dfm, inc_bf, paid_bf, inc_cc, paid_cc, inc_lr,
        prior (prior ultimate), booked (booked ultimate)."""
        parse = lambda: {k.strip(): float(v) for k, v in (p.split(":") for p in weights.split(",") if ":" in p)}
        return run("Scenario", f"Re-weight {weights} ({sub_segment} {basis}, {f'AY {ay_from}–{ay_to or ay_from}' if ay_from else 'all AYs'})",
                   lambda: X.scenario(S, seg_ok(sub_segment), basis.upper(), parse(), ay_from or None, ay_to or None))

    def sensitivity_range(sub_segment: str, basis: str = "GROSS", kind: str = "min_max", methods: str = "", low: str = "", high: str = "",
                           weights: str = "", ay_from: int = 0, ay_to: int = 0) -> str:
        """Scenario agent: a low/high (kind='min_max' or 'methods') or re-weighted (kind='weights') ultimate for one sub-segment from
        Apollo's own method indications — no recalculation. methods = comma list of method keys for min_max (empty = all actuarial
        methods). low/high = method keys for 'methods'. weights like 'inc_bf:0.5,inc_dfm:0.5' for 'weights'. AYs ay_from..ay_to
        (0 = all; other AYs keep the selected ultimate)."""
        def job():
            spec = {"type": kind, "ay_from": ay_from or None, "ay_to": ay_to or None}
            if kind == "min_max": spec["methods"] = [m.strip() for m in methods.split(",") if m.strip()] or None
            elif kind == "methods": spec["low"], spec["high"] = low, high
            elif kind == "weights": spec["weights"] = {k.strip(): float(v) for k, v in (p.split(":") for p in weights.split(",") if ":" in p)}
            else: raise ValueError("kind must be min_max, methods or weights")
            return X.sensitivity(S, basis.upper(), spec, [seg_ok(sub_segment)])
        return run("Scenario", f"Sensitivity {kind} ({sub_segment} {basis})", job)

    def diagnostic(sub_segment: str, tab: str, basis: str = "GROSS") -> str:
        """Diagnostics agent: latest diagonal vs prior accident years for an Apollo diagnostic tab; flags calendar-year shifts and AY outliers.
        tab one of: PaidToIncurred, IncurredToEarnedPremium, PaidToEarnedPremium, SettlementRate, AverageCaseOS, Frequency, IncLossRToG,
        EarnedPremiumRToG, PaidLegalToLoss, PaidLegalToIncdLoss, ReportedTri, ClosedTri."""
        return run("Diagnostics", f"{tab} ({sub_segment} {basis})", lambda: X.diagnostic(S, seg_ok(sub_segment), basis.upper(), tab))

    def actual_vs_expected(sub_segment: str, basis: str = "GROSS") -> str:
        """Incurred actual vs expected by accident year since the prior review."""
        return run("Claims Investigator", f"AvE by AY ({sub_segment} {basis})", lambda: X.ave(S, seg_ok(sub_segment), basis.upper()))

    def claim_movements(sub_segment: str, accident_year: int, basis: str = "GROSS", n: int = 10) -> str:
        """Claims Investigator only: claim-level rows behind an AY's AvE — ClaimID, prior/current/change incurred, CAT flag, loss
        description, each claim's share of the AY's A−E — plus the rule-based driver label (one-off / release / CAT / trend)."""
        return run("Claims Investigator", f"Claim movements {sub_segment} AY{accident_year}", lambda: X.claim_movements(S, seg_ok(sub_segment), accident_year, basis.upper(), n))

    def read_exhibit(sub_segment: str, tab: str, basis: str = "GROSS", columns: str = "", accident_years: str = "", block: str = "") -> str:
        """Read any Apollo tab. columns = column numbers like '3,15' (summary tabs); accident_years like '2021,2022';
        block for triangles: Data, Age-To-Age Factors, Averages, Benchmarks, Manual Selections, Ultimate Selections."""
        return run("Exhibit reader", f"{tab} {block} ({sub_segment} {basis})", lambda: X.exhibit(S, seg_ok(sub_segment), basis.upper(), tab,
                   [int(c) for c in re.findall(r"\d+", columns)] or None, years(accident_years) or None, block or None))

    def calculate(expression: str, unit: str, label: str) -> str:
        """Arithmetic over existing references, e.g. 'F0031 / F0040' or 'F0012 - F0013'. unit: mm, pct, factor or num (a ratio
        of two mm figures is a pct unless you want the raw mm difference). Registers the result as a new reference with its
        derivation — use this for ANY arithmetic; never compute a number yourself."""
        try:
            ref, value = ledger.calculate(expression, unit, label)
            turn["steps"].append(("Evidence", f"calculate({expression}) = {ref} — requested by Gemini"))
            return json.dumps({"ref": ref, "display": fmt(value, unit, "text")})
        except CalcError as e:
            return f"ERROR: {e}"

    def explain_figure(ref: str) -> str:
        """Value, unit, source cell and derivation chain for one reference (e.g. 'F0031')."""
        try:
            turn["steps"].append(("Evidence", f"explain_figure({ref}) — requested by Gemini"))
            return json.dumps(ledger.explain(ref))
        except KeyError as e:
            return f"ERROR: {e}"

    def scan_study() -> str:
        """First Look: deterministic review candidates — largest PYD AYs, judgmental/outside-range LDFs, diagnostic
        calendar-year shifts, largest |A−E| with its driver label, widest method spread — each with its reference."""
        try:
            items = X.scan_study(S, ledger)
            turn["steps"].append(("Data Steward", f"Scanned the study: {len(items)} review candidates — requested by Gemini"))
            return json.dumps(items)
        except Exception as e:
            turn["steps"].append(("Data Steward", f"Scan failed: {e}")); return f"ERROR: {e}"

    tools = dict(find_sections=find_sections, reserve_summary=reserve_summary, prior_year_development=prior_year_development,
                 reinsurance_ratios=reinsurance_ratios, ldf_selection=ldf_selection, method_weights=method_weights,
                 method_indications=method_indications, what_if_weights=what_if_weights, sensitivity_range=sensitivity_range,
                 diagnostic=diagnostic, actual_vs_expected=actual_vs_expected, claim_movements=claim_movements,
                 read_exhibit=read_exhibit, calculate=calculate, explain_figure=explain_figure, scan_study=scan_study)
    missing = [a for a in allow if a not in tools]
    if missing: raise KeyError(f"tools.build_tools: not implemented yet: {missing}")
    return [tools[a] for a in allow]
