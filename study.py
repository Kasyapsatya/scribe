"""Study loader + shared result format. Parses Apollo packs by LABEL and COLUMN NUMBER into one tidy fact table;
every value keeps its source cell. Used by every agent (Data Steward, drafting, analyst)."""
import re
from dataclasses import dataclass, field
from pathlib import Path
import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

DATA = Path(__file__).parent / "data" / "inputs"
NAMES = {"AL": "Auto Liability", "GL": "General Liability", "WC": "Workers Comp"}   # display labels only; segments come from the packs


def seg_name(code):
    return NAMES.get(code, code)
BASES = ("GROSS", "NET")
TRI_BLOCKS = ["Age-To-Age Factors", "Averages", "Benchmarks", "Manual Selections", "Ultimate Selections"]
END_ROWS = ("Totals", "Prior Total", "Total")
CLAIM_COLS = {"ClaimID", "sub_segment", "accident_year", "Incurred_prior", "Incurred_current", "Incurred_change", "CAT_Flag", "Loss_Description"}
AVE_COLS = {"sub_segment", "basis", "accident_year", "expected_incurred", "actual_incurred"}


# ---------------- parsing ----------------
def _labels(ws):
    """Column-A labels → first row (case-normalised for 'Origin Period')."""
    out = {}
    for r in range(1, ws.max_row + 1):
        v = ws.cell(r, 1).value
        if v in (None, ""): continue
        k = str(v).strip(); k = "Origin Period" if k.lower() == "origin period" else k
        out.setdefault(k, r)
    return out


def _norm_row(v):
    return str(v).replace("/12", "").strip()


def _parse_table(ws, hdr, meta):
    """Summary layout: numbering row '(n)' above header row 'Accident Period'/'Period'."""
    cols = {}
    for c in range(2, ws.max_column + 1):
        h = ws.cell(hdr, c).value
        if h in (None, ""): continue
        num = re.fullmatch(r"\((\d+)\)", str(ws.cell(hdr - 1, c).value or "").strip())
        cols[c] = (str(h), int(num.group(1)) if num else c - 1)
    out = []
    for r in range(hdr + 1, ws.max_row + 1):
        lab = ws.cell(r, 1).value
        if lab in (None, ""): continue
        for c, (h, n) in cols.items():
            v = ws.cell(r, c).value
            if isinstance(v, (int, float)):
                out.append(dict(meta, kind="table", block="table", row=_norm_row(lab), column=h, col_no=n, value=float(v),
                                cell=f"{ws.title}!{get_column_letter(c)}{r}"))
    return out


def _parse_grid(ws, labels, meta, kind):
    """Triangle (blocks) or diagnostic age grid: every block starts with an 'Origin Period' header of ages."""
    starts = [(0, "Data")] + sorted((labels[b], b) for b in TRI_BLOCKS if b in labels)
    out = []
    for i, (r0, blk) in enumerate(starts):
        r1 = starts[i + 1][0] if i + 1 < len(starts) else ws.max_row + 1
        hdr = next((r for r in range(max(r0, 1), r1) if str(ws.cell(r, 1).value).strip().lower() == "origin period"), None)
        if hdr is None: continue
        ages = {c: str(ws.cell(hdr, c).value) for c in range(2, ws.max_column + 1) if ws.cell(hdr, c).value not in (None, "")}
        for r in range(hdr + 1, r1):
            lab = ws.cell(r, 1).value
            if lab in (None, "") or str(lab) in TRI_BLOCKS: continue
            for c, age in ages.items():
                v = ws.cell(r, c).value
                if isinstance(v, (int, float)):
                    out.append(dict(meta, kind=kind, block=blk, row=_norm_row(lab), column=age, col_no=None, value=float(v),
                                    cell=f"{ws.title}!{get_column_letter(c)}{r}"))
    return out


def read_apollo(path):
    """Return (facts, info) for one Apollo pack, or (None, reason) if the file is not an Apollo pack."""
    wb = load_workbook(path, data_only=True, read_only=False)
    facts, tabs, seg, basis, evals, lob = [], {}, None, None, set(), None
    for ws in wb.worksheets:
        title = str(ws["B3"].value or "")
        m = re.search(r"^(.*) - (GROSS|NET) - ATTR detail", title)
        if not m: continue
        code = str(ws["A3"].value or "")
        lob = lob or str(ws["B2"].value or "").replace(" Reserving", "").strip() or None
        seg, basis = code.replace("CAS_", ""), m.group(2)
        head = " ".join(str(ws.cell(r, 2).value or "") for r in range(1, 8))
        ev = re.search(r"Data Evaluated as of (\d{4}Q\d)", head)
        if ev: evals.add(ev.group(1))
        meta = dict(segment=seg, basis=basis, tab=ws.title, title=m.group(1), file=Path(path).name)
        labels = _labels(ws)
        if "Accident Period" in labels or "Period" in labels:            # summary tables first (some also carry an averages block)
            rows = _parse_table(ws, labels.get("Accident Period", labels.get("Period")), meta)
        elif "Origin Period" in labels:
            rows = _parse_grid(ws, labels, meta, "triangle" if "Ultimate Selections" in labels else "grid")
        else: rows = []
        tabs[ws.title] = dict(title=m.group(1), kind=rows[0]["kind"] if rows else "unknown", n=len(rows))
        facts += rows
    if not facts: return None, "not an Apollo pack (no '… - GROSS/NET - ATTR detail' titles)"
    return pd.DataFrame(facts), dict(file=Path(path).name, segment=seg, basis=basis, evals=sorted(evals), tabs=tabs, lob=lob)


@dataclass
class Study:
    facts: pd.DataFrame
    packs: list                      # info dicts per Apollo pack
    claims: pd.DataFrame | None
    ave: pd.DataFrame | None
    other: list                      # (file, reason) for files that were not recognised
    evaluation: str | None

    @property
    def segments(self):
        """Sub-segment codes found in the packs (known codes first, then any others alphabetically)."""
        found = {p["segment"] for p in self.packs}
        return [s for s in NAMES if s in found] + sorted(found - set(NAMES))

    @property
    def lob(self):
        return next((p["lob"] for p in self.packs if p.get("lob")), "Reserving")

    @property
    def eval_year(self):
        return int(self.evaluation[:4]) if self.evaluation else None

    def __post_init__(self):
        self._idx = {k: g for k, g in self.facts.groupby(["segment", "basis", "tab"], sort=False)} if len(self.facts) else {}

    def q(self, seg, basis, tab, row=None, col_no=None, column=None, block=None):
        d = self._idx.get((seg, basis, tab), self.facts.iloc[0:0])
        if row is not None: d = d[d.row == str(row)]
        if col_no is not None: d = d[d.col_no == col_no]
        if column is not None: d = d[d.column == str(column)]
        if block is not None: d = d[d.block == block]
        return d

    def cell(self, seg, basis, tab, row, col_no=None, column=None, block=None):
        """Exactly one value + its source; raises if the layout is not as expected."""
        d = self.q(seg, basis, tab, row, col_no, column, block)
        if len(d) != 1: raise KeyError(f"{seg}/{basis}/{tab}/{row}/{col_no or column}: {len(d)} matches")
        r = d.iloc[0]
        return r.value, f"{r.file} | {r.cell}" + (f" (col {int(r.col_no)})" if pd.notna(r.col_no) else "")

    def ay_rows(self, seg, basis, tab):
        return sorted({int(r) for r in self.q(seg, basis, tab).row if r.isdigit()})

    def only(self, segments):
        """The same study restricted to some sub-segments (drafting scope)."""
        return Study(self.facts[self.facts.segment.isin(segments)].reset_index(drop=True), [p for p in self.packs if p["segment"] in segments],
                     self.claims, self.ave, self.other, self.evaluation)

    def has(self, seg, basis, tab):
        return any(p["segment"] == seg and p["basis"] == basis and tab in p["tabs"] for p in self.packs)


def load_study(folder=DATA):
    folder = Path(folder); frames, packs, other = [], [], []
    claims = ave = None
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() == ".xlsx" and not p.name.startswith("~$"):
            df, info = read_apollo(p)
            if df is None: other.append((p.name, info)); continue
            frames.append(df); packs.append(info)
        elif p.suffix.lower() == ".csv":
            d = pd.read_csv(p)
            if CLAIM_COLS <= set(d.columns): claims = d
            elif AVE_COLS <= set(d.columns): ave = d
            else: other.append((p.name, "CSV columns match neither the claims nor the AvE format"))
    evals = sorted({e for pk in packs for e in pk["evals"]})
    facts = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["segment", "basis", "tab", "row", "column", "col_no", "value"])
    return Study(facts, packs, claims, ave, other, evals[-1] if evals else None)


def signature(folder=DATA):
    """Cheap fingerprint of the input folder (names + mtimes) for caching."""
    return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in Path(folder).iterdir() if p.is_file()))


# ---------------- shared result format ----------------
def fmt(v, unit, ctx="table"):
    """ctx='table' → 1.23 / (1.23) in $m; ctx='text' → $1.23m / -$1.23m."""
    if v is None or (isinstance(v, float) and pd.isna(v)): return "–"
    if unit == "mm":
        x = 0.0 if abs(v) < 5e3 else v / 1e6                               # no "(0.00)" / "-$0.00m"
        if ctx == "text": return f"{'-' if x < 0 else ''}${abs(x):,.2f}m"
        return f"({abs(x):,.2f})" if x < 0 else f"{x:,.2f}"
    if unit == "pct" and abs(v) < 0.0005: v = 0.0                        # no "-0.0%"
    return {"pct": lambda: f"{v * 100:.1f}%", "pct0": lambda: f"{v * 100:.0f}%", "factor": lambda: f"{v:.3f}", "int": lambda: f"{int(round(v)):,}",
            "num": lambda: f"{v:,.2f}", "text": lambda: str(v)}[unit]()


@dataclass
class Result:
    """What every specialist returns: a table (raw values), units per column, cell-level sources, a compact summary
    for the LLM, and optionally a Plotly chart."""
    title: str
    df: pd.DataFrame
    units: dict
    sources: dict = field(default_factory=dict)      # (row_label, column) -> "file | Tab!Cell (col n)"
    summary: dict = field(default_factory=dict)
    chart: object = None
    note: str = ""

    def display(self):
        out = self.df.copy()
        for c, u in self.units.items():
            if c in out: out[c] = [fmt(v, u) for v in out[c]]
        return out

    @property
    def table_note(self):
        return " ".join(x for x in [self.note, "All amounts in tables are in $m." if "mm" in self.units.values() else ""] if x)

    def to_llm(self, ledger=None, max_rows=40):
        """JSON for an LLM tool result. Without a ledger: display values only (unchanged from v2). With one: every
        numeric cell/summary figure carries its reference, e.g. "-1.46 [F0031]" (ledger.Ledger.register_result)."""
        files = sorted({s.split("!")[0] for s in self.sources.values() if " | " in s and "!" in s})      # "file | Tab" per source
        if ledger is None:
            return dict(title=self.title, note=self.table_note, summary=self.summary,
                        table=self.display().head(max_rows).to_dict(orient="records"), sources=files[:8])
        table, summary = ledger.register_result(self, max_rows)
        return dict(title=self.title, note=self.table_note, summary=summary, table=table, sources=files[:8])
