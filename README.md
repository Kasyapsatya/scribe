# Scribe v2 — Apollo DVR chat agents (prototype, synthetic data)

One Accenture-branded chat over an Apollo reserve study: **draft** the DVR memo, or **analyse** anything in the study.
See `CLAUDE.md` for the locked design.

## Run (Windows)
```
cd scribe_v2
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .streamlit\secrets.toml.example .streamlit\secrets.toml    # paste your Gemini key (or copy your existing secrets.toml)
streamlit run app.py
```
The first answer that needs Gemini checks which model your key can use (≤ 1 min) and remembers it for 24 h.
If Gemini is unavailable (quota or network) Scribe says so: the Analyst points you to the relevant Apollo tabs, and drafting still works (code-written wording and default drafting choices, all figures cited).

## Use
- On opening, the **Data Steward** checks the files in `data/inputs/` and says what is ready or missing.
- Ask anything: "Summarise how LDFs were selected for GL", "Select BF for all accident years for GL — how do ultimates change?",
  "Show the average case OS diagnostic for Workers Comp", "What drove AvE on AL 2021?", "Draft the DVR memo".
- Steer the draft in your own words, e.g. "Draft the memo for the Casualty business; sensitivity low = lowest method, high = highest method; explain AvE above $1m;
  business description: …". The **Brief agent** reads it, Python checks it, and the memo opens with a *Basis of this draft* note
  (applied / default / not applied). "Redraft with high = Paid BF" changes one choice.
- Attach files with the **+** in the chat box (Apollo `.xlsx`, claims/AvE `.csv`); they are saved to `data/inputs/` and re-checked.
- Chats are stored in `output/scribe.db`; open past chats from the **Chats** button in the header.

## Share with the team (Streamlit Community Cloud, free)
1. Push this folder to a **private** GitHub repo (`.gitignore` keeps `secrets.toml` and `output/` out).
2. On [share.streamlit.io](https://share.streamlit.io): **Create app** → the repo, branch `main`, main file `app.py`; *Advanced settings* → Python 3.12
   and paste your secrets (see `.streamlit/secrets.toml.example`: `GOOGLE_API_KEY`, and `GITHUB_TOKEN` / `GITHUB_REPO` for chat export).
3. **Share** → invite teammates by email (the app stays private to invited viewers; Community Cloud allows one private app per account).
- Teammates can paste their own Gemini key under **⚙** (kept for their browser session only); otherwise the team key is used.
  Set `SHOW_KEY_SETTINGS = false` in secrets to hide ⚙.
- **Chats → ⬆ Save this chat to the team repo** writes the chat to `chats/` as Markdown. Hosted chat history resets when the app restarts;
  the exported files are the permanent reference.
- Files a teammate uploads stay in their own session; the shared study in `data/inputs/` changes only through the repo.

## Files
| File | Role |
|---|---|
| `app.py` | Chat UI, chat storage wiring, handlers |
| `agents.py` | Orchestrator (routing), Brief agent, Analyst (Gemini + tools), chat Verifier, memo polish |
| `brief.py` | Drafting brief: user-steerable choices, defaults, validation, basis note |
| `specialists.py` | Specialist agents: reserves, PYD, reinsurance, LDF Explainer, methods/what-if/sensitivity, Diagnostics, AvE, Claims Investigator, Exhibit reader, Study Navigator |
| `drafting.py` | Drafting team, memo Verifier, Word Assembler, memo preview |
| `steward.py` | Data Steward (readiness check) |
| `study.py` | Apollo parser (label + column number), shared Result format and number formatting |
| `store.py` | SQLite chat storage |
| `export.py` | Chat → Markdown reference, saved to the repo's `chats/` (GitHub API) or downloaded |
| `chats/` | Exported chats (references) |
| `config.py` | Secrets, per-session key, per-key rate limit, Gemini model selection (timeouts, no retries) |
| `gen_synthetic.py` | Regenerates the synthetic study in `data/inputs/` and `data/golden_labels.json` |
| `evaluate.py` | Evaluation suite: `python evaluate.py` (add `--qa` to include the Gemini Q&A set) |
