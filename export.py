"""Chat export: a stored chat → a Markdown reference (question, answer, tables, how it was answered), saved to the team's
GitHub repo through the GitHub contents API. Settings in secrets: GITHUB_TOKEN (fine-grained, Contents: read & write on that repo only),
GITHUB_REPO ("owner/name"), optional GITHUB_BRANCH (main) and CHATS_DIR (chats). Without them the app offers a download instead."""
import base64, json, re, time, urllib.error, urllib.request
import store
from config import secret

API = "https://api.github.com/repos/{repo}/contents/{path}"


def _table(a):
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")
    rows = [f"| {' | '.join(esc(c) for c in a['columns'])} |", "|" + "---|" * len(a["columns"])]
    rows += [f"| {' | '.join(esc(c) for c in r)} |" for r in a["records"]]
    return f"**{a['title']}**\n\n" + "\n".join(rows) + (f"\n\n*{a['note']}*" if a.get("note") else "")


def to_markdown(chat_id):
    """(file name, Markdown) for one chat. Charts are omitted (their tables are kept); memo drafts are referenced by file name."""
    title, created = store.chat(chat_id)
    out = [f"# {title}", f"*Scribe chat · {time.strftime('%d %b %Y %H:%M', time.localtime(created))} · id {chat_id}*"]
    msgs = store.messages(chat_id); first_q = next((i for i, m in enumerate(msgs) if m["role"] == "user"), len(msgs))
    for m in msgs[first_q:]:                                             # from the first question (skips the opening data check)
        out.append(f"\n## {'Question' if m['role'] == 'user' else 'Scribe'}\n\n{m['content']}")
        for a in m["artifacts"]:
            if a["type"] == "table": out.append(_table(a))
            elif a["type"] == "file": out.append(f"*Draft memo produced: `{a['path'].replace(chr(92), '/').split('/')[-1]}`*")
            elif a["type"] == "steps": out.append("<details><summary>How Scribe got this</summary>\n\n"
                                                  + "\n".join(f"- **{x}** — {y}" for x, y in a["items"]) + "\n</details>")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "chat"
    return f"{time.strftime('%Y-%m-%d', time.localtime(created))}_{slug}_{chat_id}.md", "\n\n".join(out) + "\n"


def repo_ready():
    return bool(secret("GITHUB_TOKEN") and secret("GITHUB_REPO"))


def push(chat_id):
    """Create or update the chat's Markdown file in the repo. Returns the file's GitHub URL; raises RuntimeError with GitHub's reason."""
    name, text = to_markdown(chat_id)
    repo, branch = secret("GITHUB_REPO"), secret("GITHUB_BRANCH", "main")
    url = API.format(repo=repo, path=f"{secret('CHATS_DIR', 'chats')}/{name}")
    head = {"Authorization": f"Bearer {secret('GITHUB_TOKEN')}", "Accept": "application/vnd.github+json", "User-Agent": "scribe-app"}
    call = lambda method, body=None: json.loads(urllib.request.urlopen(urllib.request.Request(
        url + ("" if body else f"?ref={branch}"), data=json.dumps(body).encode() if body else None, headers=head, method=method), timeout=20).read())
    try:
        try: sha = call("GET")["sha"]                                    # already exported → update it
        except urllib.error.HTTPError as e:
            if e.code != 404: raise
            sha = None
        body = {"message": f"Scribe chat: {name}", "branch": branch, "content": base64.b64encode(text.encode()).decode()} | ({"sha": sha} if sha else {})
        return call("PUT", body)["content"]["html_url"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub refused the export ({e.code}): {json.loads(e.read() or b'{}').get('message', e.reason)}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub unreachable: {e.reason}")
