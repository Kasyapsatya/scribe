"""Chat storage (SQLite). Messages keep their artifacts (tables, charts, files, steps) so past chats replay exactly."""
import json, sqlite3, time, uuid
from pathlib import Path

DB = Path(__file__).parent / "output" / "scribe.db"


def _con():
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, title TEXT, created REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, content TEXT, artifacts TEXT, created REAL)")
    return con


def new_chat():
    cid = uuid.uuid4().hex[:12]
    with _con() as c: c.execute("INSERT INTO chats VALUES (?, ?, ?)", (cid, "New chat", time.time()))
    return cid


def add(chat_id, role, content, artifacts=()):
    with _con() as c:
        c.execute("INSERT INTO messages (chat_id, role, content, artifacts, created) VALUES (?, ?, ?, ?, ?)",
                  (chat_id, role, content, json.dumps(list(artifacts)), time.time()))
        if role == "user":
            c.execute("UPDATE chats SET title = ? WHERE id = ? AND title = 'New chat'", (content[:48], chat_id))


def messages(chat_id):
    with _con() as c:
        rows = c.execute("SELECT role, content, artifacts FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
    return [dict(role=r, content=m, artifacts=json.loads(a)) for r, m, a in rows]


def chats(limit=30):
    with _con() as c:
        return c.execute("SELECT c.id, c.title, c.created FROM chats c WHERE EXISTS (SELECT 1 FROM messages m WHERE m.chat_id = c.id AND m.role = 'user') "
                         "ORDER BY c.created DESC LIMIT ?", (limit,)).fetchall()


def chat(chat_id):
    """(title, created) of one chat."""
    with _con() as c: return c.execute("SELECT title, created FROM chats WHERE id = ?", (chat_id,)).fetchone()


def last_answer(question):
    """Most recent stored Gemini answer to the same question — used when Gemini is unavailable."""
    with _con() as c:
        row = c.execute("SELECT a.content, a.artifacts FROM messages q JOIN messages a ON a.chat_id = q.chat_id AND a.id = "
                        "(SELECT MIN(id) FROM messages WHERE chat_id = q.chat_id AND id > q.id AND role = 'assistant') "
                        "WHERE q.role = 'user' AND lower(trim(q.content)) = lower(trim(?)) AND a.artifacts LIKE '%\"gemini\": true%' "
                        "ORDER BY a.id DESC LIMIT 1", (question,)).fetchone()
    return (row[0], json.loads(row[1])) if row else None
