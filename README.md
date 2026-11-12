# Scribe v3 — Apollo DVR chat agents (prototype, synthetic data)

One Accenture-branded chat over an Apollo reserve study: an Orchestrator routes every message to **draft**, **edit**
or **fill gaps** in the DVR memo, or to **analyse** anything in the study — every number a reference back to its
source cell, never typed by an LLM. See `CLAUDE.md` for the locked design and `PLAN_v3.md` for how it got here.

## Run (Windows)
```
cd scribe_v2
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml    # paste your Gemini key (or copy your existing secrets.toml)
streamlit run app.py
```
The first answer that needs Gemini checks which model your key can use (≤ 1 min) and remembers it for 24 h.
If Gemini is unavailable (quota or network) Scribe says so: the Analyst points you to the relevant Apollo tabs,
Section Writers keep their code-drafted wording, and drafting itself always works (default choices, all figures cited).

## Use
- On opening, the **Data Steward** checks the files in `data/inputs/` and says what is ready or missing, then
  **First Look** offers the five items most worth reviewing (clickable follow-up questions).
- Ask anything: "Summarise how LDFs were selected for GL", "Select BF for all accident years for GL — how do ultimates change?",
  "Show the average case OS diagnostic for Workers Comp", "What drove AvE on AL 2021?", "Why does the memo say X?", "Draft the DVR memo".
- Steer the draft in your own words, e.g. "Draft the memo for the Casualty business; sensitivity low = lowest method, high = highest method; explain AvE above $1m;
  business description: …". The **Orchestrator** reads it in the same call that routes the message, Python checks
  every choice against the study and your words, and the memo opens with a *Basis of this draft* note
  (applied / default / not applied). "Redraft with high = Paid BF" changes one choice.
- "Make the PYD section shorter" redrafts just that section. "Let's fill the gaps" starts an interview over the
  memo's open `[INPUT REQUIRED]` items, most material first — "skip" moves on, "stop" ends it.
- Every answer carries a collapsed **Figures** panel (ref → value → source) and a **How I got this** trace.
- Attach files with the **+** in the chat box (Apollo `.xlsx`, claims/AvE `.csv`); they are saved to `data/inputs/` and re-checked.
- Chats are stored in `output/scribe.db`; open past chats from the **Chats** button in the header.

## Share with the team (Streamlit Community Cloud, free)
1. Push this folder to a **private** GitHub repo (`.gitignore` keeps `secrets.toml` and `output/` out).
2. On [share.streamlit.io](https://share.streamlit.io): **Create app** → the repo, branch `main`, main file `app.py`; *Advanced settings* → Python 3.12
   and paste your secrets (see `.streamlit/secrets.toml.example`: `GOOGLE_API_KEY`, plus `GITHUB_TOKEN` / `GITHUB_REPO` to keep chats in the repo).
3. **Share** → invite teammates by email (the app stays private to invited viewers; Community Cloud allows one private app per account).
- Teammates can paste their own Gemini key under **⚙** (kept for their browser session only); otherwise the team key is used.
  Set `SHOW_KEY_SETTINGS = false` in secrets to hide ⚙.
- With `GITHUB_TOKEN` / `GITHUB_REPO` set, every chat is saved to the repo automatically after each reply (`chats/` on the `chats`
  branch, created by the app so saves never redeploy it) and the **Chats** list shows the team's chats, also after restarts. Each file is a
  readable Markdown reference. Without a token, chats stay on the server and reset when the hosted app restarts; **Chats → ⬇** downloads one.
- Files a teammate uploads stay in their own session; the shared study in `data/inputs/` changes only through the repo.

## Files
| File | Role |
|---|---|
| `app.py` | Chat UI, chat storage wiring, handlers, ledger/memo-state persistence |
| `agents.py` | Orchestrator, Analyst, Claims Investigator, Section Writers, Interviewer, First Look, chat ref verifier |
| `tools.py` | Tool closures the agents call, per-agent allow-lists, ledger-tagged results |
| `ledger.py` | Evidence ledger: `{{Fxxxx}}` refs, `calculate`, `explain`, the ref verifier, JSON persistence |
| `memo.py` | Per-chat memo state: read/search sections, replace a section's text, open-items ranking, verbatim fill, docx rebuild |
| `brief.py` | Drafting brief: user-steerable choices, defaults, validation, basis note, BF assumption |
| `specialists.py` | Deterministic specialists: reserves, PYD, reinsurance, LDF selection, methods/what-if/sensitivity, diagnostics, AvE, claim movements, First Look candidate scan, exhibit reader, study navigator |
| `drafting.py` | Drafting team (code-drafted sections + Section Writer pass), memo Verifier, Word Assembler, memo preview |
| `steward.py` | Data Steward (readiness check) |
| `study.py` | Apollo parser (label + column number), shared Result format and number formatting |
| `store.py` | SQLite chat storage |
| `chat_sync.py` | Chats ↔ the team repo: saved after every reply, pulled into the Chats list (GitHub API) |
| `chats/` | Saved chats (Markdown references; the app reads them back) |
| `config.py` | Secrets, per-session key, per-key rate limit, Gemini model selection (timeouts, no retries) |
| `gen_synthetic.py` | Regenerates the synthetic study in `data/inputs/` and `data/golden_labels.json` |
| `evaluate.py` | Evaluation suite: `python evaluate.py` (add `--qa` to include the live Gemini checks) |
| `PLAN_v3.md` | The phased plan this architecture was built from |
