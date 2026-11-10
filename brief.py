"""Drafting brief: the user-steerable choices for a memo draft. The Orchestrator (agents.route_llm) proposes
brief_changes from the user's words in the same call that routes the message; this module validates them against
the study and the instruction text, merges them with the chat's current brief, and describes the result for the
memo's "Basis of this draft" section. The fixed rules (CLAUDE.md §2, §4) are not part of the brief."""
import copy, re
import specialists as X

MATERIAL_AVE = 0.5e6                        # default gross |A − E| that triggers a claims investigation in the PYD commentary
DEFAULT = dict(segments=None, bases=["GROSS", "NET"], sensitivity=[{"type": "min_max", "methods": None, "ay_from": None, "ay_to": None}],
               materiality=MATERIAL_AVE, ays_explained=2, focus={}, exclude_cat=False, context={}, user=[])
CONTEXT_SECTIONS = {"scope": "Scope and Background", "business": "Description of the Business", "reinsurance": "Reinsurance", "stat": "Stat/GAAP",
                    "limitations": "Limitations", "roles": "Roles and Responsibilities", "collaboration": "Collaboration",
                    "responsibility": "Responsibility for Assumptions and Methods", "data": "Data", "risks": "Key Risks to the Reserve Estimate"}
LABELS = {"segments": "Scope", "bases": "Bases", "sensitivity": "Sensitivity scenarios", "materiality": "AvE threshold", "ays_explained": "Accident years explained",
          "focus": "Accident years to cover", "exclude_cat": "CAT exclusion", "context": "Your text for a memo section"}


def _figures(text):
    """Every figure the user wrote, in the forms a brief may hold it ($1m → 1e6, 50% → 0.5, 50/50 → 0.5)."""
    out = set()
    for x, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(m\b|mm\b|mn\b|million|k\b|%)?", text.lower().replace(",", "")):
        x = float(x); out |= {x, x / 100}
        if unit in ("m", "mm", "mn", "million"): out.add(x * 1e6)
        if unit == "k": out.add(x * 1e3)
    return out


def _years(S):
    ays = sorted({int(a) for seg in S.segments for a in X._methods(S, seg, "GROSS")[0].index})
    return ays[0], ays[-1]


BF_AMBIGUOUS = re.compile(r"\bbf\b", re.I)
BF_QUALIFIED = re.compile(r"\b(incurred|paid)\b[^.]{0,20}\bbf\b|\bbf\b[^.]{0,20}\b(incurred|paid)\b", re.I)


def validate(changes, S, text, prev):
    """Apply the Orchestrator's proposed brief_changes to the current brief. Anything that fails a check is
    reported, never applied. Returns (brief, rejected, assumptions); rejected = [(request, reason)]."""
    b, bad, assumptions = copy.deepcopy(prev), [], []
    figs = _figures(text); lo, hi = _years(S); keys = set(X.METHOD_KEYS)
    traced = lambda v: any(abs(v - f) < 1e-9 * max(1, abs(f)) for f in figs)
    yr = lambda y: y is None or (isinstance(y, int) and lo <= y <= hi)
    bf_bare = bool(BF_AMBIGUOUS.search(text)) and not BF_QUALIFIED.search(text)

    def take(field, value):
        b[field] = value; b["user"] = sorted(set(b["user"]) | {field})

    for field, v in (changes or {}).items():
        try:
            if field == "segments":
                v = None if v in (None, [], "all") else [s.upper() for s in v]
                if v and not set(v) <= set(S.segments): raise ValueError(f"sub-segments must be among {', '.join(S.segments)}")
                take(field, v)
            elif field == "bases":
                v = [x.upper() for x in v]
                if not v or not set(v) <= {"GROSS", "NET"}: raise ValueError("basis must be gross and/or net")
                take(field, [x for x in ("GROSS", "NET") if x in v])
            elif field == "sensitivity":
                out = []
                for sp in v:
                    sp = dict(sp); sp.setdefault("ay_from", None); sp.setdefault("ay_to", None)
                    if not (yr(sp["ay_from"]) and yr(sp["ay_to"])): raise ValueError(f"accident years must be within {lo}–{hi}")
                    if sp.get("type") == "min_max":
                        if sp.get("methods") and not set(sp["methods"]) <= set(X.ACTUARIAL): raise ValueError(f"unknown methods {sp['methods']}")
                    elif sp.get("type") == "methods":
                        if not {sp.get("low"), sp.get("high")} <= keys: raise ValueError("low / high must be Apollo methods")
                    elif sp.get("type") == "weights":
                        w = {k: float(x) for k, x in sp.get("weights", {}).items()}
                        if not w or not set(w) <= keys: raise ValueError("weights must name Apollo methods")
                        if abs(sum(w.values()) - 1) > 1e-6: raise ValueError("weights must add up to 100%")
                        if not (len(set(w.values())) == 1 or all(traced(x) for x in w.values())):
                            raise ValueError("the weights read were not in your instruction — please restate them")
                        sp["weights"] = w
                    else: raise ValueError("unknown scenario type")
                    for k in (sp.get("low"), sp.get("high"), *(sp.get("weights") or {})):
                        if bf_bare and k in ("inc_bf", "paid_bf"): assumptions.append(f"BF taken as {'Incurred' if k == 'inc_bf' else 'Paid'} BF")
                    out.append(sp)
                take(field, out)
            elif field == "materiality":
                v = float(v)
                if v <= 0 or not traced(v): raise ValueError("the amount read was not in your instruction — please restate it")
                take(field, v)
            elif field == "ays_explained":
                if not (isinstance(v, int) and 1 <= v <= 10): raise ValueError("between 1 and 10 accident years")
                take(field, v)
            elif field == "focus":
                v = {s.upper(): [int(y) for y in ys] for s, ys in v.items()}
                if not set(v) <= set(S.segments) or not all(yr(y) for ys in v.values() for y in ys): raise ValueError("unknown sub-segment or accident year")
                take(field, v)
            elif field == "exclude_cat":
                take(field, bool(v))
            elif field == "context":
                words = set(re.findall(r"\w+", text.lower()))
                for sec, t in v.items():
                    tw = re.findall(r"\w+", str(t).lower())
                    if sec not in CONTEXT_SECTIONS: raise ValueError(f"no memo section '{sec}' takes your text")
                    if not tw or sum(w in words for w in tw) / len(tw) < 0.9: raise ValueError(f"the text for {CONTEXT_SECTIONS[sec]} must be your own words — please paste it")
                take(field, {**b["context"], **{k: str(t) for k, t in v.items()}})
            else: raise ValueError("not a drafting option")
        except (ValueError, TypeError, AttributeError) as e:
            bad.append((LABELS.get(field, field), str(e)))
    return b, bad, sorted(set(assumptions))


def describe(b, S, cite):
    """(applied, defaults) lines for the basis note and the chat reply. Figures go through cite(value, unit) → placeholder."""
    ays = lambda ys: ", ".join(str(y) for y in ys)
    lines = {
        "segments": f"Scope: {', '.join(S.segments) if not b['segments'] else ', '.join(b['segments'])} sub-segments",
        "bases": f"Bases: {' and '.join(x.lower() for x in b['bases'])}",
        "sensitivity": "Sensitivity: " + "; ".join(
            X.scenario_label(sp) + (" — weights " + ", ".join(f"{cite(w, 'pct0')} {X.METHOD_KEYS[k]}" for k, w in sp["weights"].items()) if sp["type"] == "weights" else "")
            for sp in b["sensitivity"]),
        "materiality": f"AvE commentary explains accident years with |A − E| of at least {cite(b['materiality'], 'mm')} ({b['bases'][0].lower()})",
        "ays_explained": f"Up to {cite(b['ays_explained'], 'int')} accident years explained per sub-segment",
        "focus": "Always explained: " + "; ".join(f"{s} AY {ays(ys)}" for s, ys in b["focus"].items()),
        "exclude_cat": "AvE also shown excluding CAT claim movements" if b["exclude_cat"] else "",
        "context": "Your text used in: " + ", ".join(CONTEXT_SECTIONS[k] for k in b["context"])}
    applied = [lines[f] for f in lines if f in b["user"] and lines[f]]
    defaults = [lines[f] for f in ("segments", "bases", "sensitivity", "materiality", "ays_explained") if f not in b["user"]]
    return applied, defaults
