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
        conn.execute("""CREATE TABLE IF NOT EXISTS memberships (
            user_id INTEGER PRIMARY KEY, plan TEXT NOT NULL DEFAULT 'trial',
            expires_at TEXT, trial_remaining INTEGER NOT NULL DEFAULT 10,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS daily_usage (
            user_id INTEGER NOT NULL, usage_day TEXT NOT NULL, request_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, usage_day)
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

# Membership defaults: 7-day trial with 10 total AI requests, then 3 requests/day free.
def ensure_membership(user_id):
    with _connect() as conn:
        conn.execute("""INSERT OR IGNORE INTO memberships(user_id, plan, expires_at, trial_remaining)
            VALUES (?, 'trial', datetime('now', '+7 days'), 10)""", (int(user_id),))


def get_membership(user_id):
    ensure_membership(user_id)
    with _connect() as conn:
        row = conn.execute("SELECT user_id, plan, expires_at, trial_remaining FROM memberships WHERE user_id=?", (int(user_id),)).fetchone()
        usage = conn.execute("SELECT request_count FROM daily_usage WHERE user_id=? AND usage_day=date('now')", (int(user_id),)).fetchone()
    item = dict(row)
    if item["plan"] in ("trial", "granted") and item["expires_at"]:
        with _connect() as conn:
            expired = conn.execute("SELECT datetime(?) <= datetime('now')", (item["expires_at"],)).fetchone()[0]
            if expired:
                conn.execute("UPDATE memberships SET plan='free', expires_at=NULL WHERE user_id=?", (int(user_id),))
                item["plan"], item["expires_at"] = "free", None
    item["used_today"] = int(usage["request_count"]) if usage else 0
    item["free_daily_limit"] = int(get_setting("free_daily_limit", "3"))
    return item


def consume_ai_request(user_id):
    item = get_membership(user_id)
    plan = item["plan"]
    with _connect() as conn:
        if plan == "trial":
            row = conn.execute("SELECT trial_remaining FROM memberships WHERE user_id=?", (int(user_id),)).fetchone()
            remaining = int(row["trial_remaining"])
            if remaining <= 0:
                conn.execute("UPDATE memberships SET plan='free', expires_at=NULL WHERE user_id=?", (int(user_id),))
                return False, "trial_exhausted"
            conn.execute("UPDATE memberships SET trial_remaining=trial_remaining-1 WHERE user_id=?", (int(user_id),))
            return True, "trial"
        if plan == "granted":
            return True, "granted"
        limit = int(get_setting("free_daily_limit", "3"))
        conn.execute("""INSERT INTO daily_usage(user_id, usage_day, request_count) VALUES (?, date('now'), 1)
            ON CONFLICT(user_id, usage_day) DO UPDATE SET request_count=request_count+1""", (int(user_id),))
        count = conn.execute("SELECT request_count FROM daily_usage WHERE user_id=? AND usage_day=date('now')", (int(user_id),)).fetchone()["request_count"]
        if count > limit:
            conn.execute("UPDATE daily_usage SET request_count=request_count-1 WHERE user_id=? AND usage_day=date('now')", (int(user_id),))
            return False, "free_limit"
        return True, "free"


def grant_membership(actor_id, user_id, days):
    days = max(1, min(int(days), 3650))
    ensure_membership(user_id)
    with _connect() as conn:
        conn.execute("""UPDATE memberships SET plan='granted',
            expires_at=datetime('now', ?), trial_remaining=0 WHERE user_id=?""", (f"+{days} days", int(user_id)))
    audit(actor_id, "membership_grant", user_id, f"{days} days")


def revoke_membership(actor_id, user_id):
    ensure_membership(user_id)
    with _connect() as conn:
        conn.execute("UPDATE memberships SET plan='free', expires_at=NULL, trial_remaining=0 WHERE user_id=?", (int(user_id),))
    audit(actor_id, "membership_revoke", user_id)


def membership_stats():
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) n FROM memberships").fetchone()["n"]
        trial = conn.execute("SELECT COUNT(*) n FROM memberships WHERE plan='trial' AND datetime(expires_at)>datetime('now') AND trial_remaining>0").fetchone()["n"]
        granted = conn.execute("SELECT COUNT(*) n FROM memberships WHERE plan='granted' AND datetime(expires_at)>datetime('now')").fetchone()["n"]
        expired = conn.execute("SELECT COUNT(*) n FROM memberships WHERE plan IN ('trial','granted') AND datetime(expires_at)<=datetime('now')").fetchone()["n"]
        free = total - trial - granted
    return {"total": total, "trial": trial, "granted": granted, "expired": expired, "free": free}
