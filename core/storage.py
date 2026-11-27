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
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
            is_banned INTEGER NOT NULL DEFAULT 0,
            subscribed INTEGER NOT NULL DEFAULT 0,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY, added_by INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER NOT NULL,
            action TEXT NOT NULL, target_id INTEGER, details TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")


def add_message(chat_id, role, content):
    with _connect() as conn:
        conn.execute("INSERT INTO messages(chat_id, role, content) VALUES (?, ?, ?)", (int(chat_id), role, str(content)))


def get_history(chat_id, limit=None):
    limit = max(2, int(limit or config.max_context_messages))
    with _connect() as conn:
        rows = conn.execute("SELECT role, content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?", (int(chat_id), limit)).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def clear_history(chat_id):
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id=?", (int(chat_id),))


def register_user(user):
    user_id = int(user.get("id") or 0)
    if not user_id:
        return
    with _connect() as conn:
        conn.execute("""INSERT INTO users(user_id, username, first_name, subscribed, last_seen)
            VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
            first_name=excluded.first_name, last_seen=CURRENT_TIMESTAMP""",
            (user_id, user.get("username"), user.get("first_name")))


def is_banned(user_id):
    with _connect() as conn:
        row = conn.execute("SELECT is_banned FROM users WHERE user_id=?", (int(user_id),)).fetchone()
    return bool(row and row["is_banned"])


def set_banned(actor_id, user_id, banned):
    with _connect() as conn:
        conn.execute("""INSERT INTO users(user_id, is_banned, subscribed) VALUES (?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET is_banned=excluded.is_banned""", (int(user_id), int(bool(banned))))
    audit(actor_id, "ban" if banned else "unban", user_id)


def is_admin(user_id):
    if int(user_id) == config.owner_id:
        return True
    with _connect() as conn:
        return conn.execute("SELECT 1 FROM admins WHERE user_id=?", (int(user_id),)).fetchone() is not None


def add_admin(actor_id, user_id):
    if int(user_id) == config.owner_id:
        raise ValueError("The owner already has full access.")
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO admins(user_id, added_by) VALUES (?, ?)", (int(user_id), int(actor_id)))
    audit(actor_id, "add_admin", user_id)


def remove_admin(actor_id, user_id):
    if int(user_id) == config.owner_id:
        raise ValueError("Owner permissions cannot be changed.")
    with _connect() as conn:
        conn.execute("DELETE FROM admins WHERE user_id=?", (int(user_id),))
    audit(actor_id, "remove_admin", user_id)


def list_admins():
    with _connect() as conn:
        return [dict(row) for row in conn.execute("SELECT user_id, added_by, created_at FROM admins ORDER BY created_at").fetchall()]


def set_setting(key, value, actor_id):
    with _connect() as conn:
        conn.execute("""INSERT INTO settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""", (key, str(value)))
    audit(actor_id, "setting:" + key, None, str(value)[:300])


def get_setting(key, default=None):
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def audit(actor_id, action, target_id=None, details=None):
    with _connect() as conn:
        conn.execute("INSERT INTO audit_log(actor_id, action, target_id, details) VALUES (?, ?, ?, ?)",
                     (int(actor_id), action, int(target_id) if target_id is not None else None, details))


def stats():
    with _connect() as conn:
        users = conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        banned = conn.execute("SELECT COUNT(*) n FROM users WHERE is_banned=1").fetchone()["n"]
        admins = conn.execute("SELECT COUNT(*) n FROM admins").fetchone()["n"]
        messages = conn.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"]
        subscribers = conn.execute("SELECT COUNT(*) n FROM users WHERE subscribed=1 AND is_banned=0").fetchone()["n"]
    return {"users": users, "banned": banned, "admins": admins, "messages": messages, "subscribers": subscribers}


def subscribers():
    with _connect() as conn:
        return [row["user_id"] for row in conn.execute("SELECT user_id FROM users WHERE subscribed=1 AND is_banned=0")]


def set_subscription(user_id, enabled):
    with _connect() as conn:
        conn.execute("UPDATE users SET subscribed=? WHERE user_id=?", (int(bool(enabled)), int(user_id)))


def recent_audit(limit=15):
    with _connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]
