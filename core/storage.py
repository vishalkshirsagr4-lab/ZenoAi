import sqlite3
from pathlib import Path
from config import config


def _connect():
    path = Path(config.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def boot():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_id_id ON messages(chat_id, id)")


def add_message(chat_id, role, content):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages(chat_id, role, content) VALUES (?, ?, ?)",
            (int(chat_id), role, str(content))
        )


def get_history(chat_id, limit=None):
    limit = max(2, int(limit or config.max_context_messages))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (int(chat_id), limit)
        ).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def clear_history(chat_id):
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id=?", (int(chat_id),))
