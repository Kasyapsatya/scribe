"""Scribe — single chat over an Apollo reserve study: Draft the DVR memo, or Analyse the study. Run: streamlit run app.py"""
from pathlib import Path
import base64, re, shutil, time, uuid
import pandas as pd
import plotly.io as pio
import streamlit as st
import agents as A
import brief as B
import drafting as D
import specialists as X
import steward
import store
import chat_sync
from ledger import Ledger
from config import USER_KEY, team_key, secret
from study import DATA, load_study, seg_name, signature

ROOT = Path(__file__).parent
LOGO = ROOT / "assets" / "accenture.png"
AVATAR = str(ROOT / "assets" / "avatar.png")
def suggestions(S):
    """Starter questions built from the sub-segments actually loaded."""
    seg = [seg_name(x) for x in S.segments] or ["the study"]
    a, b, c = (seg * 3)[:3] if len(seg) < 3 else seg[:3]
    return ["Draft the DVR memo", "Draft the memo — sensitivity from the lowest to the highest method, explain AvE above $1m", f"Summarise how LDFs were selected for {b}",
            f"How is AvE doing for {a}?", f"Select BF for all accident years for {b} — how do ultimates change?",
            f"Show the average case OS diagnostic for {c}"]
ABOUT = """**How I work** — a team of agents over your Apollo study:

- **Orchestrator** routes each request: readiness, drafting or analysis.
- **Data Steward** checks every file before work starts.
- **Brief agent** reads any drafting instructions you give (sensitivity scenarios, scope, materiality, your own text for a section) and checks them against the study; the memo says what it applied, what it defaulted and what it could not do.
- **Drafting team** builds each memo section from defined Apollo columns, then the **Verifier** ties every figure to its source cell and the **Assembler** writes the Word memo.
- **Analyst** answers questions using specialist agents: **Study Navigator**, **Exhibit reader**, **LDF Explainer**, **Diagnostics**, **Scenario** (re-weights Apollo's own method indications) and **Claims Investigator**.

I never generate numbers: every figure comes from the study and is verified before you see it."""
DOT = """digraph{rankdir=LR;bgcolor="transparent";node[shape=box,style="rounded,filled",fillcolor="#F6F3FA",color="#A100FF",fontname=Arial,fontsize=10];
U[label="You",shape=oval,fillcolor="#FFFFFF"];O[label="Orchestrator",fillcolor="#E9D7FF"];DS[label="Data Steward"];
subgraph cluster_d{label="Drafting";color="#CCCCCC";BA[label="Brief agent (Gemini)"];DT[label="Drafting team"];V1[label="Verifier"];AS[label="Assembler → .docx"]}
subgraph cluster_a{label="Analysis";color="#CCCCCC";AN[label="Analyst (Gemini)"];N[label="Study Navigator"];E[label="Exhibit reader"];L[label="LDF Explainer"];
DG[label="Diagnostics"];SC[label="Scenario"];CI[label="Claims Investigator"];V2[label="Verifier"]}
F[label="Apollo packs · claims · AvE",shape=cylinder,fillcolor="#FFFFFF"];
U->O;O->DS;O->BA->DT->V1->AS;O->AN;AN->{N E L DG SC CI};AN->V2;{DS N E L DG SC CI DT}->F[style=dotted,arrowhead=none]}"""

st.set_page_config(page_title="Scribe | Accenture", page_icon=AVATAR, layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
[data-testid="stToolbar"], [data-testid="stDecoration"], footer, #MainMenu {display:none !important}
.block-container {padding-top: 3.6rem; max-width: 1100px}
header[data-testid="stHeader"] {background: transparent; height: 2.5rem}
.brand {display:flex; align-items:flex-end; gap:14px}
.brand .app {font-size:0.95rem; color:#460073; letter-spacing:.02em}
.memo {font-family: Georgia, serif; line-height:1.5; padding: 4px 8px}
.memo h3 {color:#460073; border-bottom:2px solid #A100FF; padding-bottom:2px; margin-top:1.2rem}
.memo h4 {color:#460073}
.memo table {border-collapse:collapse; font-size:.8rem; margin:6px 0} .memo td, .memo th {border:1px solid #D9D2E3; padding:3px 7px}
.memo th {background:#F6F3FA} .memo .note {font-size:.75rem; font-style:italic; color:#555} .ir {color:#C00000; font-weight:700}
[data-testid="stPopoverBody"] button {justify-content:flex-start}
</style>""", unsafe_allow_html=True)

SHOW_KEY = str(secret("SHOW_KEY_SETTINGS", "true")).lower() != "false"   # owner can hide the ⚙ key box from secrets
USER_KEY.set(st.session_state.get("user_key", "") if SHOW_KEY else "")   # this session's own Gemini key (memory only), else the team key


@st.cache_resource(show_spinner="Reading the Apollo study…", max_entries=12)
def study(folder, sig):
    return load_study(folder)


def data_dir():
    """The shared study folder, or this session's private copy once the user has uploaded files (uploads never change others' data)."""
    return Path(st.session_state.get("data_dir", DATA))


S = study(str(data_dir()), signature(data_dir()))


# ---------------- artifacts ----------------
def result_artifacts(results):
    out = []
    for r in results:
        d = r.display()
        out.append(dict(type="table", title=r.title, columns=[str(c) for c in d.columns], records=d.astype(str).values.tolist(), note=r.table_note))
        if r.chart is not None: out.append(dict(type="chart", fig=r.chart.to_json()))
    return out


def md(text):
    """Escape '$' so amounts like $1.23m are not read as LaTeX by Streamlit's markdown."""
    return re.sub(r"(?<!\\)\$", r"\\$", text or "")


def render(msg, idx, last, ledger):
    with st.chat_message(msg["role"], avatar=None if msg["role"] == "user" else AVATAR):
        content = ledger.render_html(msg["content"]) if msg["role"] == "assistant" else msg["content"]
        st.markdown(md(content), unsafe_allow_html=True)
        steps = figs = None
        for k, a in enumerate(msg["artifacts"]):
            t = a["type"]
            if t == "table":
                st.markdown(md(f"**{a['title']}**"))
                st.dataframe(pd.DataFrame(a["records"], columns=a["columns"]), hide_index=True, width="stretch")
                if a["note"]: st.caption(md(a["note"]))
            elif t == "chart": st.plotly_chart(pio.from_json(a["fig"]), width="stretch", key=f"c{idx}_{k}")
            elif t == "memo":
                with st.expander("Memo preview", expanded=False): st.markdown(a["html"], unsafe_allow_html=True)
            elif t == "file":
                if Path(a["path"]).exists():
                    st.download_button("⬇ Download the draft memo (.docx)", Path(a["path"]).read_bytes(), Path(a["path"]).name, key=f"f{idx}_{k}")
                else: st.caption("The Word file from this draft is no longer on the server — ask “draft the memo” to rebuild it.")
            elif t == "graph": st.graphviz_chart(a["dot"])
            elif t == "steps": steps = a
            elif t == "figures": figs = a
            elif t == "suggestions" and last:
                cols = st.columns(3)
                for j, s in enumerate(a["items"]):
                    if cols[j % 3].button(s, key=f"s{idx}_{j}", width="stretch"): st.session_state.pending = s
        if steps:
            with st.expander("How I got this", expanded=False):
                st.markdown(md("\n".join(f"- **{a}** — {d}" for a, d in steps["items"])))
        if figs:
            with st.expander("Figures", expanded=False):
                st.markdown(md("\n".join(f"- **{f['ref']}** {f['display']} — {f['source']}" + (f" ({f['derivation']})" if f["derivation"] else "")
                                         for f in figs["items"])))


# ---------------- handlers ----------------
def readiness():
    text, _ = steward.readiness_message(S)
    return text, [dict(type="suggestions", items=suggestions(S))]


def last_artifact(cid, kind):
    """Most recent artifact of a kind in this chat (the drafting brief, a draft waiting for an answer)."""
    for m in reversed(store.messages(cid)):
        for a in m["artifacts"]:
            if a["type"] == kind: return a
    return None


def do_draft(status, prompt, prev):
    """Brief agent (only if the request carries instructions) → Data Steward → Drafting team. prev = this chat's current brief."""
    b, rejected, assumptions, note = prev or B.DEFAULT, [], [], ""
    steps = [("0 Orchestrator", "Routed to the Drafting team")]
    if B.needs_reading(prompt, S):
        try:
            b, rejected, assumptions, question = A.read_brief(S, prompt, b)
            steps.append(("1 Brief agent", "Gemini read your drafting instructions; Python checked every choice against the study and your words"))
            if question: return question, [dict(type="pending_draft", text=prompt), dict(type="steps", items=steps)]
        except RuntimeError as e:
            note = f"\n\n*{e} — I could not read your drafting instructions, so this draft uses {'this chat’s previous brief' if prev else 'the defaults'}.*"
            steps.append(("1 Brief agent", f"Skipped: {e}"))
    scope = S.only(b["segments"]) if b["segments"] else S
    chk = steward.check(scope)
    if not chk["draft_ready"]:
        return f"I can't draft the full memo yet — missing: {', '.join(chk['gaps'])}. Upload the files here or add them to the data folder.", []
    steps.append(("1 Data Steward", "All required files present"))
    trace = lambda agent, msg: (steps.append((agent, msg)), status.write(f"**{agent}** — {msg}"))
    d = D.draft(S, polish=A.polish_memo, trace=trace, brief=b, rejected=rejected, assumptions=assumptions, tag=st.session_state.chat)
    unpaid = " · ".join(f"**{X.reserve_summary(scope, x).summary['Total unpaid']} {x.lower()}**" for x in b["bases"])
    pyd = " · ".join(f"**{list(X.pyd(scope, x).summary['total'].values())[-1]} {x.lower()}**" for x in b["bases"])
    basis = next(s for s in d.sections if s["id"] == "basis")["blocks"][1:]
    brief_lines = "\n".join(f"- {D.basis_text(d.citations, x)}" for x in basis)
    ok = sum(not v for v in d.issues.values())
    text = (f"**First draft ready** — {len(d.sections)} sections, {len(d.citations.items)} figures cited to source cells, "
            f"{d.flags} items flagged for your input, built in {d.seconds:.0f}s.\n\n"
            f"- Unpaid: {unpaid}\n- Prior year development (prior AYs): {pyd}\n"
            f"- Verifier: {ok}/{len(d.sections)} sections tie out to the Apollo cells\n\n"
            f"**Drafting basis** (also at the top of the memo):\n{brief_lines}\n\n"
            "Open the preview below or download the Word memo. Red text marks what the data cannot support. "
            "To change a choice, just say so — e.g. “redraft with high = Paid BF”." + note)
    return text, [dict(type="memo", html=D.to_html(d.sections, d.citations)), dict(type="file", path=str(d.docx)), dict(type="brief", brief=b),
                  dict(type="steps", items=steps)]


def do_analyse(text, history, ledger):
    answer, results, steps, issues, gemini, refs_used = A.analyse(S, text, history, ledger)
    if not gemini and (saved := store.last_answer(text)):
        return saved[0] + f"\n\n*Saved answer from an earlier session — {A.gemini_state()['why'] or 'Gemini is unavailable'} right now.*", saved[1]
    if issues: answer += f"\n\n*⚠️ The Verifier could not trace {', '.join(issues)} to the study — treat with caution.*"
    figs = [ledger.explain(r) for r in dict.fromkeys(refs_used) if r in ledger.items]
    arts = result_artifacts(results) + [dict(type="steps", items=steps)]
    if figs: arts.append(dict(type="figures", items=figs))
    return answer, arts + ([dict(type="meta", gemini=True)] if gemini else [])


def handle(prompt, files):
    global S
    cid = st.session_state.chat
    if files:
        if data_dir() == DATA:                                        # first upload: private copy of the shared study for this session
            mine = ROOT / "output" / "sessions" / uuid.uuid4().hex[:12]; shutil.copytree(DATA, mine); st.session_state.data_dir = str(mine)
        for f in files: (data_dir() / Path(f.name).name).write_bytes(f.getbuffer())
        S = study(str(data_dir()), signature(data_dir()))
        store.add(cid, "user", (prompt + "\n\n" if prompt else "") + "Uploaded: " + ", ".join(f.name for f in files))
        text, arts = readiness(); store.add(cid, "assistant", "Files received and checked.\n\n" + text, arts)
        if not prompt: return
    else:
        store.add(cid, "user", prompt)
    msgs = store.messages(cid); history = [(m["role"], m["content"]) for m in msgs][:-1]
    kind = A.route(prompt)
    pend = next((a for a in (msgs[-2]["artifacts"] if len(msgs) > 1 else []) if a["type"] == "pending_draft"), None)   # Scribe asked before drafting
    if pend and kind != "draft": kind, prompt = "draft", f"{pend['text']}\nAnswer to your question: {prompt}"
    with st.chat_message("assistant", avatar=AVATAR):
        if kind == "draft":
            with st.status("Drafting the memo…", expanded=True) as status:
                text, arts = do_draft(status, prompt, (last_artifact(cid, "brief") or {}).get("brief")); status.update(label="Memo drafted", state="complete", expanded=False)
        else:
            with st.spinner("Working on it…"):
                if kind == "readiness": text, arts = readiness()
                elif kind == "about": text, arts = ABOUT, [dict(type="graph", dot=DOT)]
                else:
                    ledger = Ledger.from_json((last_artifact(cid, "ledger") or {}).get("json", ""))
                    text, arts = do_analyse(prompt, history, ledger)
                    arts = arts + [dict(type="ledger", json=ledger.to_json())]
    store.add(cid, "assistant", text, arts); chat_sync.save(cid)        # every chat is kept in the team repo automatically


# ---------------- layout ----------------
if "chat" not in st.session_state:
    st.session_state.chat = store.new_chat(); store.add(st.session_state.chat, "assistant", *readiness())

logo = base64.b64encode(LOGO.read_bytes()).decode()
head, new, hist, *gear = st.columns([6, 1.6, 1.2, 0.5] if SHOW_KEY else [6, 1.6, 1.2], vertical_alignment="bottom")
head.markdown(f'<div class="brand"><img src="data:image/png;base64,{logo}" height="34"><span class="app">Scribe · Apollo reserve study assistant</span></div>',
              unsafe_allow_html=True)
if new.button("＋ New chat", width="stretch", type="primary"):
    st.session_state.chat = store.new_chat(); store.add(st.session_state.chat, "assistant", *readiness()); st.rerun()
with hist.popover("Chats", width="stretch"):                  # always visible (the sidebar collapses on narrow screens)
    chat_sync.pull()                                           # the team's chats from GitHub (at most once a minute)
    for cid, title, created in store.chats():
        label = f"{'▸ ' if cid == st.session_state.chat else ''}{title} · {time.strftime('%d %b %H:%M', time.localtime(created))}"
        if st.button(label, key=f"chat_{cid}", width="stretch"):
            st.session_state.chat = cid; st.rerun()
    st.divider()
    name, md_text = chat_sync.to_markdown(st.session_state.chat)
    st.download_button("⬇ Download this chat (.md)", md_text, name, width="stretch")
    st.caption(chat_sync.STATUS["error"] or (f"Chats are saved to GitHub automatically (branch `{chat_sync.branch()}`)." if chat_sync.enabled()
               else "Chats are kept on this server only."))
if SHOW_KEY:
    with gear[0].popover("⚙", help="Use your own Gemini API key"):
        k = st.text_input("Your Gemini API key (optional)", value=st.session_state.get("user_key", ""), type="password",
                          help="Kept in memory for this browser session only — never saved or shown. Leave empty to use the team key.")
        if k != st.session_state.get("user_key", ""): st.session_state.user_key = k.strip(); st.rerun()
        st.caption("Using **your key**." if st.session_state.get("user_key") else "Using the **team key**." if team_key()
                   else "No team key is configured — add yours to use the Analyst.")
        st.caption("Free keys: aistudio.google.com/apikey")
st.divider()
msgs = store.messages(st.session_state.chat)
chat_ledger = Ledger.from_json((last_artifact(st.session_state.chat, "ledger") or {}).get("json", ""))
for i, m in enumerate(msgs): render(m, i, i == len(msgs) - 1, chat_ledger)

entry = st.chat_input("Ask about the study, or say “draft the memo”…", accept_file="multiple", file_type=["xlsx", "csv"])
prompt, files = (entry.text, entry.files) if entry else (st.session_state.pop("pending", None), [])
if prompt or files:
    if prompt: st.chat_message("user").markdown(md(prompt))
    handle(prompt or "", files); st.rerun()
