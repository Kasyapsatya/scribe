"""Evidence ledger (PLAN_v3 §2): every number an agent can point to gets a reference Fxxxx — value, unit, source, and for a
calculated figure its derivation. Numbers enter memo text and chat answers only as {{Fxxxx}} placeholders; Python renders them.
Generalises drafting.py's old Citations registry (kept as an alias there) to also cover tool results and arithmetic."""
import ast, json, re
import pandas as pd
from study import fmt

PH = re.compile(r"\{\{(F\d{4})\}\}")
_MM = re.compile(r"^(-?)\$([\d,]+\.\d{2})m$")
_PCT = re.compile(r"^(-?)(\d+(?:\.\d+)?)%$")
_FACTOR = re.compile(r"^-?\d+\.\d{3}$")
_NUM = re.compile(r"^-?[\d,]+\.\d{2}$")
_INT = re.compile(r"^-?[\d,]+$")
_REF = re.compile(r"^F\d{4}$")
_CALC_FNS = {"abs": lambda a: abs(a[0]), "sum": sum, "min": min, "max": max}


def _parse_display(s):
    """Reverse one of study.fmt's text outputs back to (value, unit); None if s is not a formatted figure."""
    if m := _MM.match(s):
        v = float(m.group(2).replace(",", "")) * 1e6; return (-v if m.group(1) else v), "mm"
    if m := _PCT.match(s):
        v = float(m.group(2)) / 100; return (-v if m.group(1) else v), "pct"
    if _FACTOR.match(s): return float(s), "factor"
    if _NUM.match(s): return float(s.replace(",", "")), "num"
    if _INT.match(s) and len(s.replace(",", "").replace("-", "")) > 0: return float(s.replace(",", "")), "int"
    return None


class CalcError(ValueError):
    pass


class Ledger:
    """Registry of every number an agent may cite: id -> value, unit, source, derivation. Ref ids are per chat
    (shared by chat answers and the memo) and deduplicated by (source, unit, rounded value)."""
    def __init__(self):
        self.items, self._key = {}, {}

    def cite(self, value, unit, source):
        k = (source, unit, round(float(value), 6))
        if k not in self._key:
            self._key[k] = f"F{len(self.items) + 1:04d}"; self.items[self._key[k]] = dict(value=float(value), unit=unit, source=source, derivation=None)
        return "{{" + self._key[k] + "}}"

    def from_result(self, res, row, col):
        """Placeholder for one cell of a specialist Result (row = value in its first column)."""
        r = res.df[res.df.iloc[:, 0].astype(str) == str(row)].iloc[0]
        return self.cite(r[col], res.units[col], res.sources.get((row, col)) or res.sources.get((str(row), col)) or res.title)

    def render(self, text, ctx="text"):
        return PH.sub(lambda m: fmt(self.items[m.group(1)]["value"], self.items[m.group(1)]["unit"], ctx), text)

    def tag(self, value, unit, source):
        """'<table-style display> [Fxxxx]' for a tool result shown to an LLM; no ref for a missing value."""
        if value is None or (isinstance(value, float) and pd.isna(value)): return fmt(value, unit, "table")
        return f"{fmt(value, unit, 'table')} [{self.cite(value, unit, source)[2:-2]}]"

    def _tag_struct(self, node, source):
        if isinstance(node, dict): return {k: self._tag_struct(v, source) for k, v in node.items()}
        if isinstance(node, list): return [self._tag_struct(v, source) for v in node]
        if isinstance(node, str):
            parsed = _parse_display(node)
            return self.tag(*parsed, source) if parsed else node
        return node

    def register_result(self, res, max_rows=40):
        """Annotate a specialist Result's table and summary with refs for every numeric figure. Returns (table, summary)."""
        cols = list(res.df.columns); first = cols[0]; table = []
        for _, row in res.df.head(max_rows).iterrows():
            label = str(row[first])
            rec = {first: label}
            for c in cols[1:]:
                if c in res.units:
                    src = res.sources.get((label, c)) or res.sources.get((str(label), c)) or res.title
                    rec[c] = self.tag(row[c], res.units[c], src)
                else:
                    rec[c] = row[c]
            table.append(rec)
        return table, self._tag_struct(res.summary, res.title)

    def _eval(self, node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            l, r = self._eval(node.left), self._eval(node.right)
            return {ast.Add: l + r, ast.Sub: l - r, ast.Mult: l * r, ast.Div: l / r}[type(node.op)]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -self._eval(node.operand)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in self.items: raise CalcError(f"unknown reference {node.id}")
            return self.items[node.id]["value"]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _CALC_FNS:
            return _CALC_FNS[node.func.id]([self._eval(a) for a in node.args])
        raise CalcError(f"expression may only use references, numbers, + - * /, parentheses, abs, sum, min, max")

    def calculate(self, expression, unit, label):
        """Evaluate an expression over existing refs (e.g. 'F0031 / F0040'); registers the result as a new ref with its derivation."""
        try:
            value = self._eval(ast.parse(expression, mode="eval").body)
        except CalcError: raise
        except (SyntaxError, ZeroDivisionError, TypeError) as e: raise CalcError(str(e))
        ref = self.cite(value, unit, label or expression)[2:-2]
        self.items[ref]["derivation"] = expression
        return ref, value

    def explain(self, ref):
        """Value, unit, source and derivation (if calculated) for one reference."""
        if ref not in self.items: raise KeyError(f"unknown reference {ref}")
        it = self.items[ref]
        return dict(ref=ref, value=it["value"], unit=it["unit"], display=fmt(it["value"], it["unit"], "text"),
                    source=it["source"], derivation=it["derivation"])

    def to_json(self):
        return json.dumps(self.items)

    @classmethod
    def from_json(cls, s):
        led = cls(); led.items = json.loads(s) if s else {}
        for ref, it in led.items.items(): led._key[(it["source"], it["unit"], round(float(it["value"]), 6))] = ref
        return led
