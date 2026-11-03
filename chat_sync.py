"""Chats kept in the team's GitHub repo, automatically. After every reply the chat is written to `chats/<date>_<title>_<id>.md` on a
separate branch (default "chats", created if missing, so saves never redeploy the app). Each file is a readable reference (questions,
answers, tables, how Scribe got them) with the full chat embedded at the end for the app to reload. The Chats list pulls new or changed
files at most once a minute, so history survives restarts and everyone sees the team's chats. Needs GITHUB_TOKEN (fine-grained,
this repo only, Contents: read and write) and GITHUB_REPO ("owner/name") in secrets; without them chats stay on the server only."""
import base64, json, re, threading, time, urllib.error, urllib.request, zlib
import store
from config import secret

DIR = "chats"
DATA = re.compile(r"<!-- scribe-chat-data: ([A-Za-z0-9+/=]+) -->")
STATUS = {"error": "", "pulled": 0.0}       # last failure (shown in the Chats menu) and time of the last pull
_SHA = {}                                    # file name → blob sha on GitHub (needed to update a file)
_LOCK = threading.Lock()


def enabled():
    return bool(secret("GITHUB_TOKEN") and secret("GITHUB_REPO"))


def branch():
    return secret("GITHUB_BRANCH", "chats")


def _api(method, path, body=None, raw=False):
    req = urllib.request.Request(f"https://api.github.com/repos/{secret('GITHUB_REPO')}/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {secret('GITHUB_TOKEN')}", "User-Agent": "scribe-app",
                                          "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"})
    out = urllib.request.urlopen(req, timeout=20).read()
    return out.decode() if raw else json.loads(out or b"{}")


def _ensure_branch():
    """Create the chats branch from the default branch the first time."""
    try: _api("GET", f"git/ref/heads/{branch()}")
    except urllib.error.HTTPError as e:
        if e.code != 404: raise
        base = _api("GET", "")["default_branch"]
        _api("POST", "git/refs", {"ref": f"refs/heads/{branch()}", "sha": _api("GET", f"git/ref/heads/{base}")["object"]["sha"]})


# ---------------- chat ↔ Markdown ----------------
def _table(a):
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    rows = [f"| {' | '.join(esc(c) for c in a['columns'])} |", "|" + "---|" * len(a["columns"])] + [f"| {' | '.join(esc(c) for c in r)} |" for r in a["records"]]
    return f"**{a['title']}**\n\n" + "\n".join(rows) + (f"\n\n*{a['note']}*" if a.get("note") else "")


def to_markdown(chat_id):
    """(file name, Markdown): readable from the first question on; the full chat (all artifacts) is embedded, compressed, at the end."""
    title, created = store.chat(chat_id); msgs = store.messages(chat_id)
    out = [f"# {title}", f"*Scribe chat · {time.strftime('%d %b %Y %H:%M', time.localtime(created))} · id {chat_id}*"]
    for m in msgs[next((i for i, m in enumerate(msgs) if m["role"] == "user"), len(msgs)):]:
        out.append(f"\n## {'Question' if m['role'] == 'user' else 'Scribe'}\n\n{m['content']}")
        for a in m["artifacts"]:
            if a["type"] == "table": out.append(_table(a))
            elif a["type"] == "file": out.append("*Draft memo produced: `" + re.split(r"[\\/]", a["path"])[-1] + "`*")
            elif a["type"] == "steps": out.append("<details><summary>How Scribe got this</summary>\n\n"
                                                  + "\n".join(f"- **{x}** — {y}" for x, y in a["items"]) + "\n</details>")
    blob = base64.b64encode(zlib.compress(json.dumps(dict(id=chat_id, title=title, created=created, messages=msgs)).encode(), 9)).decode()
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "chat"
    return f"{time.strftime('%Y-%m-%d', time.localtime(created))}_{slug}_{chat_id}.md", "\n\n".join(out) + f"\n\n<!-- scribe-chat-data: {blob} -->\n"


# ---------------- save (after every reply, in the background) / pull (Chats list) ----------------
def _save(chat_id):
    name, text = to_markdown(chat_id); path = f"{DIR}/{name}"
    body = lambda: {"message": f"Scribe chat: {name}", "branch": branch(), "content": base64.b64encode(text.encode()).decode()} | (
        {"sha": _SHA[name]} if name in _SHA else {})
    with _LOCK:
        try:
            _ensure_branch()
            try: _SHA[name] = _api("PUT", f"contents/{path}", body())["content"]["sha"]
            except urllib.error.HTTPError as e:
                if e.code not in (409, 422): raise                                  # sha unknown or stale: fetch it and retry once
                _SHA[name] = _api("GET", f"contents/{path}?ref={branch()}")["sha"]
                _SHA[name] = _api("PUT", f"contents/{path}", body())["content"]["sha"]
            STATUS["error"] = ""
        except Exception as e:
            STATUS["error"] = f"Last save to GitHub failed: {getattr(e, 'code', '')} {getattr(e, 'reason', e)}".strip()


def save(chat_id):
    if enabled(): threading.Thread(target=_save, args=(chat_id,), daemon=True).start()


def pull(every=60):
    """Load chats that are new or changed on GitHub into the local store (at most once per `every` seconds)."""
    if not enabled() or time.time() - STATUS["pulled"] < every: return
    STATUS["pulled"] = time.time()
    try:
        for f in _api("GET", f"contents/{DIR}?ref={branch()}"):
            if not f["name"].endswith(".md") or _SHA.get(f["name"]) == f["sha"]: continue
            m = DATA.search(_api("GET", f"contents/{DIR}/{f['name']}?ref={branch()}", raw=True))
            if m:
                c = json.loads(zlib.decompress(base64.b64decode(m.group(1))))
                store.replace_chat(c["id"], c["title"], c["created"], c["messages"])
            _SHA[f["name"]] = f["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404: STATUS["error"] = f"Could not read chats from GitHub: {e.code} {e.reason}"   # 404 = no chats saved yet
    except Exception as e:
        STATUS["error"] = f"Could not read chats from GitHub: {e}"
