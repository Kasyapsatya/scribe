"""Memo state per chat (PLAN_v3 §4/§5a): the drafted sections, sharing this chat's evidence ledger with every
chat answer, plus the brief that produced them and the open [INPUT REQUIRED] items an actuary still has to answer.
Persisted as a chat artifact ("memo_state") so the Analyst can read/search it and the Interviewer can fill it in."""
import json, re
from dataclasses import dataclass, field
from pathlib import Path
import drafting as D
from ledger import Ledger

IR_RE = re.compile(r"\[INPUT REQUIRED:([^\]]*)\]")


@dataclass
class Memo:
    sections: list
    ledger: Ledger
    brief: dict
    docx: str
    subject: str


def from_draft(draft, brief, subject):
    """Wrap a drafting.Draft into this chat's memo state. draft.citations IS the chat's shared ledger
    (drafting.draft was called with it), so every figure the memo cites is also available to chat answers."""
    return Memo(draft.sections, draft.citations, brief, str(draft.docx), subject)


def to_json(memo):
    return json.dumps(dict(sections=memo.sections, brief=memo.brief, docx=memo.docx, subject=memo.subject))


def from_json(s, ledger):
    """ledger = this chat's Ledger (already loaded from the "ledger" artifact) — the memo shares it, not its own copy."""
    d = json.loads(s)
    return Memo(d["sections"], ledger, d["brief"], d["docx"], d["subject"])


def _find(memo, section_id):
    s = next((s for s in memo.sections if s["id"] == section_id), None)
    if s is None: raise KeyError(f"no memo section '{section_id}'; section ids: {', '.join(s['id'] for s in memo.sections)}")
    return s


def read_section(memo, section_id):
    """Section id/title, its narrative texts in order (for replace_section) and the full text (for an LLM to read),
    placeholders intact."""
    s = _find(memo, section_id)
    texts = [b[1] for b in s["blocks"] if b[0] in ("p", "b", "h3")]
    full = "\n".join(texts + [f"{b[1]}: {b[2]}" for b in s["blocks"] if b[0] == "u"])
    return dict(id=s["id"], title=s["title"], texts=texts, text=full)


def search(memo, text, k=5):
    """Section ids/snippets whose text mentions any word in `text` (simple case-insensitive substring match)."""
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2]
    hits = []
    for s in memo.sections:
        paras = [str(b[1]) for b in s["blocks"] if b[0] in ("p", "b", "h3")]
        snippet = next((p for p in paras if any(w in p.lower() for w in words)), "")
        if snippet: hits.append(dict(id=s["id"], title=s["title"], snippet=snippet[:240]))
    return hits[:k]


def replace_section(memo, section_id, texts):
    """Replace a section's p/b/h3 block texts in place and in order (Section Writer output); table and 'u'
    (actuary's words) blocks are never touched. len(texts) must match the section's current narrative blocks."""
    s = _find(memo, section_id)
    idx = [i for i, b in enumerate(s["blocks"]) if b[0] in ("p", "b", "h3")]
    if len(texts) != len(idx): raise ValueError(f"section '{section_id}' has {len(idx)} narrative block(s), got {len(texts)} replacement(s)")
    for i, t in zip(idx, texts): s["blocks"][i] = (s["blocks"][i][0], t)
    return s


def open_inputs(memo):
    """Every [INPUT REQUIRED: ...] item across the memo, each with a stable id for fill()."""
    items = []
    for s in memo.sections:
        for bi, b in enumerate(s["blocks"]):
            if b[0] not in ("p", "b"): continue
            for mi, m in enumerate(IR_RE.finditer(b[1])):
                items.append(dict(item_id=f"{s['id']}:{bi}:{mi}", section_id=s["id"], section_title=s["title"], what=m.group(1).strip()))
    return items


SECTION_PRIORITY = {"assumptions": 0, "pyd": 1, "diagnostics": 2}      # judgmental LDFs > PYD/AvE drivers > diagnostics > everything else


def rank_open_inputs(memo):
    """open_inputs(), most material first (Interviewer, PLAN_v3 §6): judgmental LDF rationale, then PYD/AvE
    driver conclusions, then diagnostics, then the single-topic sections in template order."""
    order = {s["id"]: i for i, s in enumerate(memo.sections)}
    return sorted(open_inputs(memo), key=lambda it: (SECTION_PRIORITY.get(it["section_id"], 3), order.get(it["section_id"], 99)))


def fill(memo, item_id, text):
    """Strip the matched [INPUT REQUIRED: ...] from its sentence; record the actuary's words verbatim as a new
    'u' block ("Analyst input") at the end of that section."""
    sec_id, bi, mi = item_id.split(":"); bi, mi = int(bi), int(mi)
    s = _find(memo, sec_id); b = s["blocks"][bi]
    matches = list(IR_RE.finditer(b[1]))
    if mi >= len(matches): raise KeyError(f"no open item '{item_id}'")
    what = matches[mi].group(1).strip()
    new_text = re.sub(r"\s{2,}", " ", (b[1][:matches[mi].start()] + b[1][matches[mi].end():])).strip()
    s["blocks"][bi] = (b[0], new_text)
    s["blocks"].append(("u", "Analyst input", f"{what}: {text}"))
    return dict(item_id=item_id, section_id=sec_id, what=what)


def rebuild_docx(memo):
    D.assemble(memo.sections, memo.ledger, Path(memo.docx), memo.subject)
