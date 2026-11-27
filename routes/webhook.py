import html
import io
import json
import logging
import re
import time
import zipfile
import requests
from config import config
from core.ai import ask_ai
from core.project_reader import read_zip_bytes, read_tar_bytes, read_single_source
from core.storage import (
    clear_history, register_user, is_banned, is_admin, add_admin, remove_admin,
    set_banned, stats, list_admins, set_setting, get_setting, subscribers,
    set_subscription, recent_audit, get_membership, consume_ai_request,
    grant_membership, revoke_membership, membership_stats,
)

log = logging.getLogger("zeno.telegram")
API = f"https://api.telegram.org/bot{config.bot_token}"
FILE_API = f"https://api.telegram.org/file/bot{config.bot_token}"
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "ZenoAI-Telegram/1.0"})
MAX_MESSAGE = 3900


def _api(method, data=None, files=None, timeout=60):
    response = HTTP.post(f"{API}/{method}", data=data, files=files, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", f"Telegram {method} failed"))
    return payload.get("result")


def send_text(chat_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": str(text)[:4096], "disable_web_page_preview": True}
    if reply_markup:
        import json
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    if parse_mode:
        payload["parse_mode"] = parse_mode
    return _api("sendMessage", payload)


CODE_LANGUAGES = {
    "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
    "typescript": ".ts", "ts": ".ts", "jsx": ".jsx", "tsx": ".tsx",
    "java": ".java", "kotlin": ".kt", "dart": ".dart", "c": ".c",
    "cpp": ".cpp", "c++": ".cpp", "csharp": ".cs", "cs": ".cs",
    "go": ".go", "rust": ".rs", "rs": ".rs", "php": ".php",
    "ruby": ".rb", "rb": ".rb", "swift": ".swift", "sql": ".sql",
    "html": ".html", "css": ".css", "json": ".json", "yaml": ".yaml",
    "yml": ".yml", "bash": ".sh", "sh": ".sh", "shell": ".sh",
    "powershell": ".ps1", "ps1": ".ps1", "xml": ".xml", "toml": ".toml",
    "markdown": ".md", "md": ".md",
}
CODE_REQUEST_RE = re.compile(
    r"\b(write|create|generate|build|implement|code|program|script|develop)\b",
    re.IGNORECASE,
)
FILE_HINT_RE = re.compile(
    r"^\s*(?:#|//|/\*|<!--|;|--)?\s*(?:file|filename|path)\s*:\s*([^\s*]+)",
    re.IGNORECASE,
)


def _code_blocks(answer):
    fence = chr(96) * 3
    pattern = re.compile(r"(?s)" + re.escape(fence) + r"([A-Za-z0-9_+#.-]*)(?:[ \t]+([^\n]+))?\n(.*?)" + re.escape(fence))
    blocks = []
    for match in pattern.finditer(answer):
        language = (match.group(1) or "").strip().lower()
        hint = (match.group(2) or "").strip()
        body = match.group(3).strip("\n\r")
        filename = None
        if hint and ("/" in hint or "." in hint) and len(hint.split()) == 1:
            filename = hint.strip(chr(96) + "'\"")
        lines = body.splitlines()
        if lines:
            file_match = FILE_HINT_RE.match(lines[0])
            if file_match:
                filename = file_match.group(1).strip(chr(96) + "'\"")
                body = "\n".join(lines[1:]).lstrip("\n")
        blocks.append({"language": language, "filename": filename, "content": body})
    return blocks


def _safe_output_name(name):
    name = str(name or "").replace("\\", "/").split("/")[-1].strip()
    if not name or name in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name):
        return None
    return name if "." in name else None


def _build_code_artifact(answer, user_prompt):
    if not CODE_REQUEST_RE.search(user_prompt or ""):
        return None
    blocks = _code_blocks(answer)
    if not blocks:
        return None
    if len(blocks) > 1:
        files = []
        for block in blocks:
            filename = _safe_output_name(block["filename"])
            if not filename:
                return None
            files.append((filename, block["content"]))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for filename, content in files:
                bundle.writestr(filename, content)
        archive.seek(0)
        archive.name = "zenoai_project.zip"
        return ("zip", archive)
    block = blocks[0]
    filename = _safe_output_name(block["filename"])
    if not filename:
        extension = CODE_LANGUAGES.get(block["language"].split("-", 1)[0])
        if extension:
            filename = "main" + extension
    if not filename:
        return None
    extension = "." + filename.rsplit(".", 1)[-1].lower()
    mime = {
        ".py": "text/x-python", ".js": "text/javascript", ".ts": "text/typescript",
        ".java": "text/x-java-source", ".html": "text/html", ".css": "text/css",
        ".json": "application/json", ".sql": "application/sql", ".sh": "text/x-shellscript",
        ".xml": "application/xml", ".md": "text/markdown",
    }.get(extension, "text/plain")
    output = io.BytesIO(block["content"].encode("utf-8"))
    output.name = filename
    return ("file", output, mime)


def _send_document(chat_id, file_obj, filename, caption, mime_type="application/octet-stream"):
    file_obj.seek(0)
    return _api(
        "sendDocument",
        {"chat_id": chat_id, "caption": caption},
        files={"document": (filename, file_obj, mime_type)},
        timeout=120,
    )


def send_long_answer(chat_id, answer, user_prompt=""):
    artifact = _build_code_artifact(answer, user_prompt)
    if artifact:
        kind, file_obj, *metadata = artifact
        if kind == "zip":
            _send_document(chat_id, file_obj, "zenoai_project.zip", "✨ ZenoAI generated project")
        else:
            _send_document(chat_id, file_obj, file_obj.name, "✨ ZenoAI generated source code", metadata[0])
        explanation = re.sub(r"(?s)" + re.escape(chr(96) * 3) + r".*?" + re.escape(chr(96) * 3), "", answer).strip()
        if explanation:
            send_text(chat_id, explanation[:MAX_MESSAGE])
        return
    if len(answer) <= MAX_MESSAGE:
        return send_text(chat_id, answer)
    send_text(chat_id, "🧩 The complete response is attached as a text file so the formatting stays intact.")
    file_obj = io.BytesIO(answer.encode("utf-8"))
    return _send_document(chat_id, file_obj, "zenoai_answer.txt", "✨ ZenoAI complete response", "text/plain")


def home_keyboard():
    """Zeno Empire home menu. Button styles are supported by Telegram Bot API 9.4+."""
    return {"inline_keyboard": [
        [
            {"text": "💻 Start coding", "callback_data": "mode_coding", "style": "primary"},
            {"text": "🧩 Debug code", "callback_data": "mode_debug", "style": "success"},
        ],
        [
            {"text": "📦 Analyze project", "callback_data": "mode_zip", "style": "primary"},
            {"text": "👤 Membership", "callback_data": "membership", "style": "success"},
        ],
        [
            {"text": "⚙️ Settings", "callback_data": "settings"},
            {"text": "📚 Help", "callback_data": "help"},
        ],
        [
            {"text": "ℹ️ About", "callback_data": "about"},
            {"text": "🧹 Clear chat", "callback_data": "clear", "style": "danger"},
        ],
    ]}


def admin_keyboard(owner=False):
    rows = [
        [
            {"text": "📊 Statistics", "callback_data": "adm_stats", "style": "primary"},
            {"text": "🛡 User tools", "callback_data": "adm_users"},
        ],
        [
            {"text": "📣 Announcement", "callback_data": "adm_broadcast", "style": "success"},
            {"text": "🩺 Service health", "callback_data": "adm_health"},
        ],
    ]
    if owner:
        rows.extend([
            [
                {"text": "👑 Admin list", "callback_data": "own_admins"},
                {"text": "🧠 AI controls", "callback_data": "own_ai", "style": "primary"},
            ],
            [
                {"text": "👤 Memberships", "callback_data": "own_membership", "style": "success"},
                {"text": "📢 Force join", "callback_data": "own_forcejoin"},
            ],
            [
                {"text": "🔧 Maintenance", "callback_data": "own_maintenance", "style": "danger"},
                {"text": "📜 Audit log", "callback_data": "own_audit"},
            ],
        ])
    rows.append([{"text": "⬅️ Main menu", "callback_data": "home"}])
    return {"inline_keyboard": rows}



def _force_join_entries():
    try:
        value = json.loads(get_setting("force_join_chats", "[]"))
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _force_join_prompt(chat_id, user_id):
    entries = _force_join_entries()
    missing = []
    for entry in entries:
        try:
            member = _api("getChatMember", {"chat_id": entry["chat_id"], "user_id": int(user_id)})
            if member.get("status") not in {"member", "administrator", "creator"}:
                missing.append(entry)
        except Exception:
            log.exception("Force-join verification failed")
            send_text(chat_id, "⚠️ Membership verification is unavailable. Please contact the owner.")
            return False
    if not missing:
        return True
    rows = [[{"text": "📢 Join " + x.get("title", x["chat_id"]), "url": x["url"]}] for x in missing]
    rows.append([{"text": "🔄 I've joined — Verify", "callback_data": "verify_join"}])
    send_text(chat_id, "🔒 <b>Join the required channels/groups to use ZenoAI.</b>", {"inline_keyboard": rows}, "HTML")
    return False


def _membership_text(user_id):
    item = get_membership(user_id)
    if item["plan"] == "trial":
        detail = "Trial requests left: <b>{}</b>\nExpires: <code>{} UTC</code>".format(item["trial_remaining"], html.escape(str(item["expires_at"])))
    elif item["plan"] == "granted":
        detail = "Expires: <code>{} UTC</code>\nAI requests: <b>Unlimited</b>".format(html.escape(str(item["expires_at"])))
    else:
        left = max(0, item["free_daily_limit"] - item["used_today"])
        detail = "Free requests left today: <b>{}/{}</b>".format(left, item["free_daily_limit"])
    return "👤 <b>Your ZenoAI membership</b>\n━━━━━━━━━━━━━━━━━━\nPlan: <b>{}</b>\n{}".format(item["plan"].capitalize(), detail)


def _membership_gate(chat_id, user_id):
    allowed, reason = consume_ai_request(user_id)
    if allowed:
        return True
    message = "🎁 Your trial allowance is finished. You are now on the Free plan." if reason == "trial_exhausted" else "⏳ Your Free daily limit is reached. Try tomorrow or contact the owner for access."
    send_text(chat_id, message + "\n\n" + _membership_text(user_id), home_keyboard(), "HTML")
    return False

def _welcome(message):
    user = message.get("from") or {}
    first_name = html.escape(user.get("first_name") or "there")
    text = (
        "⚡ <b>ZENO EMPIRE</b>\n"
        "<i>Powered by ZenoAI</i>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<blockquote><b>Welcome, {first_name}!</b>\n"
        "Your personal AI workspace to plan, code, debug and understand software.</blockquote>\n"
        " <b>WHAT YOU CAN DO</b>\n"
        "💻 <b>Build</b> — generate code and project files\n"
        "🛠 <b>Debug</b> — understand errors and fix issues\n"
        "📦 <b>Analyze</b> — inspect ZIP and source files safely\n"
        "🧠 <b>Chat</b> — continue with conversation context\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>“Build with clarity. Debug with confidence. Keep creating.”</i>\n\n"
        "<b>Choose an action below</b> or send a message to begin."
    )
    keyboard = home_keyboard()
    if is_admin((message.get("from") or {}).get("id", 0)):
        keyboard["inline_keyboard"].insert(0, [
            {"text": "👑 Admin / Owner Panel", "callback_data": "admin_panel", "style": "primary"}
        ])
    send_text(message["chat"]["id"], text, keyboard, "HTML")


def _help(chat_id):
    text = (
        "📚 <b>ZenoAI Help</b>\n━━━━━━━━━━━━━━━━━━\n"
        "💬 Chat: send a question or coding task.\n💻 Generate: describe your app and requirements.\n"
        "🛠 Debug: paste an error and related code.\n📦 Project review: upload a ZIP.\n"
        "📄 Source files: send code/text files directly.\n\n"
        "/start — dashboard\n/help — help\n/clear — clear chat\n/about — about\n"
        "/subscribe — receive bot announcements\n/unsubscribe — stop announcements\n"
        "Uploaded code is inspected as text and never executed."
    )
    send_text(chat_id, text, home_keyboard(), "HTML")


def _download_telegram_file(file_id):
    meta = _api("getFile", {"file_id": file_id})
    file_path = meta.get("file_path", "")
    if not file_path or ".." in file_path.split("/"):
        raise ValueError("Telegram returned an invalid file path.")
    max_bytes = config.max_upload_mb * 1024 * 1024
    response = HTTP.get(f"{FILE_API}/{file_path}", stream=True, timeout=60)
    response.raise_for_status()
    chunks, total = [], 0
    for chunk in response.iter_content(64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"File is larger than the {config.max_upload_mb} MB upload limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _analyze_upload(message, document):
    chat_id = message["chat"]["id"]
    filename = document.get("file_name") or "uploaded-file"
    send_text(chat_id, "📥 <b>File received.</b> Checking its contents safely…", parse_mode="HTML")
    data = _download_telegram_file(document["file_id"])
    if filename.lower().endswith(".zip"):
        context, file_count, text_count = read_zip_bytes(data)
    elif filename.lower().endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
        context, file_count, text_count = read_tar_bytes(data)
        task = ("Review this uploaded software project. Summarize its architecture, list confirmed issues by severity "
                "and file path, explain causes and provide concrete fixes. Mention unverified areas; do not claim tests ran. "
                f"Archive entries: {file_count}; text files included: {text_count}.")
    else:
        context, _, _ = read_single_source(filename, data)
        task = f"Review {filename}: explain its purpose, identify concrete bugs and risks, and suggest corrected code."
    send_long_answer(chat_id, ask_ai(chat_id, task, extra_context=context), task)


def _admin_help(chat_id, owner):
    lines = [
        "🛡 <b>Admin commands</b>",
        "/stats — bot statistics",
        "/ban USER_ID and /unban USER_ID",
        "/broadcast MESSAGE — send to opted-in subscribers",
    ]
    if owner:
        lines += [
            "", "👑 <b>Owner-only commands</b>",
            "/addadmin USER_ID and /removeadmin USER_ID",
            "/model MODEL_ID — change APInex model",
            "/prompt TEXT — replace AI system instructions",
            "/maintenance on|off",
            "/grant USER_ID DAYS and /revoke USER_ID",
            "/forceadd CHAT_ID JOIN_URL", "/forceremove CHAT_ID", "/forcelist",
            "/limits TRIAL_DAYS TRIAL_REQUESTS FREE_DAILY_LIMIT",
            "/audit — recent administrative actions",
        ]
    lines += ["", "Use the dashboard buttons for quick status and control panels."]
    send_text(chat_id, "\n".join(lines), admin_keyboard(owner), "HTML")


def _admin_command(message, text, user_id):
    chat_id = message["chat"]["id"]
    owner = user_id == config.owner_id
    parts = text.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if command == "/admin":
        _admin_help(chat_id, owner)
    elif command == "/stats":
        s = stats()
        m = membership_stats()
        send_text(chat_id, f"📊 <b>ZenoAI statistics</b>\nUsers: {s['users']}\nSubscribers: {s['subscribers']}\nBanned: {s['banned']}\nAdmins: {s['admins']}\nAI messages stored: {s['messages']}\n\n👤 <b>Memberships</b>\nTrial: {m['trial']}\nGranted: {m['granted']}\nFree: {m['free']}\nExpired: {m['expired']}", parse_mode="HTML")
    elif command in {"/ban", "/unban"}:
        if not arg.isdigit():
            send_text(chat_id, f"Usage: {command} USER_ID")
            return
        target = int(arg)
        if target == config.owner_id:
            send_text(chat_id, "⛔ Owner account cannot be banned.")
            return
        if is_admin(target) and not owner:
            send_text(chat_id, "⛔ Admins cannot ban another admin.")
            return
        set_banned(user_id, target, command == "/ban")
        send_text(chat_id, f"✅ User {target} {'banned' if command == '/ban' else 'unbanned'}.")
    elif command == "/broadcast":
        if not arg:
            send_text(chat_id, "Usage: /broadcast Your announcement text")
            return
        recipients = subscribers()
        send_text(chat_id, f"📣 Sending to {len(recipients)} users who opted in…")
        sent = failed = 0
        for target in recipients:
            try:
                send_text(target, "📣 <b>ZenoAI announcement</b>\n\n" + html.escape(arg), parse_mode="HTML")
                sent += 1
                time.sleep(0.12)
            except Exception:
                failed += 1
        send_text(chat_id, f"✅ Announcement finished. Delivered: {sent}; failed: {failed}.")
    elif command == "/addadmin" and owner:
        if not arg.isdigit():
            send_text(chat_id, "Usage: /addadmin USER_ID")
            return
        add_admin(user_id, int(arg))
        send_text(chat_id, f"✅ Admin access granted to {arg}.")
    elif command == "/removeadmin" and owner:
        if not arg.isdigit():
            send_text(chat_id, "Usage: /removeadmin USER_ID")
            return
        remove_admin(user_id, int(arg))
        send_text(chat_id, f"✅ Admin access removed from {arg}.")
    elif command == "/model" and owner:
        if not arg or len(arg) > 120 or any(ch.isspace() for ch in arg):
            send_text(chat_id, "Usage: /model provider-model-id")
            return
        set_setting("model", arg, user_id)
        send_text(chat_id, f"✅ AI model updated to <code>{html.escape(arg)}</code>.", parse_mode="HTML")
    elif command == "/prompt" and owner:
        if not arg:
            send_text(chat_id, "Usage: /prompt Paste your full AI behaviour instructions")
            return
        if len(arg) > 20000:
            send_text(chat_id, "Prompt is too long (maximum 20,000 characters).")
            return
        set_setting("system_prompt", arg, user_id)
        send_text(chat_id, "✅ AI behaviour instructions updated.")
    elif command == "/maintenance" and owner:
        value = arg.lower()
        if value not in {"on", "off"}:
            send_text(chat_id, "Usage: /maintenance on|off")
            return
        set_setting("maintenance", value, user_id)
        send_text(chat_id, f"🔧 Maintenance mode: {value.upper()}")

    elif command == "/grant" and owner:
        values = arg.split()
        if len(values) != 2 or not values[0].isdigit() or not values[1].isdigit():
            send_text(chat_id, "Usage: /grant USER_ID DAYS")
            return
        grant_membership(user_id, int(values[0]), int(values[1]))
        send_text(chat_id, "Granted {} days to {}.".format(values[1], values[0]))
    elif command == "/revoke" and owner:
        if not arg.isdigit():
            send_text(chat_id, "Usage: /revoke USER_ID")
            return
        revoke_membership(user_id, int(arg))
        send_text(chat_id, "Access revoked for {}.".format(arg))

    elif command == "/limits" and owner:
        values = arg.split()
        if len(values) != 3 or not all(x.isdigit() for x in values):
            send_text(chat_id, "Usage: /limits TRIAL_DAYS TRIAL_REQUESTS FREE_DAILY_LIMIT")
            return
        days, trial_requests, free_daily = map(int, values)
        if not (1 <= days <= 365 and 1 <= trial_requests <= 10000 and 0 <= free_daily <= 1000):
            send_text(chat_id, "Limits: trial days 1–365, trial requests 1–10000, free daily 0–1000.")
            return
        set_setting("trial_days", days, user_id)
        set_setting("trial_requests", trial_requests, user_id)
        set_setting("free_daily_limit", free_daily, user_id)
        send_text(chat_id, "✅ New-user trial and Free plan limits updated. Existing memberships keep their current expiry and trial balance.")
    elif command == "/forceadd" and owner:
        values = arg.split(maxsplit=1)
        if len(values) != 2 or not (values[0].startswith("@") or values[0].lstrip("-").isdigit()) or not values[1].startswith("https://t.me/"):
            send_text(chat_id, "Usage: /forceadd @channel https://t.me/channel")
            return
        entries = _force_join_entries()
        if any(str(x.get("chat_id")).lower() == values[0].lower() for x in entries):
            send_text(chat_id, "Already configured.")
            return
        entries.append({"chat_id": values[0], "url": values[1], "title": values[0]})
        set_setting("force_join_chats", json.dumps(entries), user_id)
        send_text(chat_id, "Added. Make the bot an administrator there so Telegram can verify membership.")
    elif command == "/forceremove" and owner:
        if not arg:
            send_text(chat_id, "Usage: /forceremove @channel")
            return
        entries = [x for x in _force_join_entries() if str(x.get("chat_id")).lower() != arg.lower()]
        set_setting("force_join_chats", json.dumps(entries), user_id)
        send_text(chat_id, "Force-join destination removed.")
    elif command == "/forcelist" and owner:
        entries = _force_join_entries()
        body = "\n".join("• {} — {}".format(x.get("chat_id"), x.get("url")) for x in entries) or "No force-join destinations configured."
        send_text(chat_id, "📢 <b>Force-join destinations</b>\n" + html.escape(body), parse_mode="HTML")
    elif command == "/audit" and owner:
        rows = recent_audit()
        body = "\n".join(f"#{r['id']} actor={r['actor_id']} {r['action']} target={r['target_id'] or '-'}" for r in rows) or "No audit entries yet."
        send_text(chat_id, "📜 <b>Recent audit log</b>\n" + html.escape(body), parse_mode="HTML")
    else:
        send_text(chat_id, "⛔ This command is owner-only or unavailable.")


def _handle_message(message):
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    user = message.get("from") or {}
    user_id = user.get("id")
    if not chat_id or not user_id:
        return
    register_user(user)
    if is_banned(user_id):
        send_text(chat_id, "⛔ Your access to this bot is currently restricted.")
        return
    text = (message.get("text") or message.get("caption") or "").strip()
    if text.startswith("/start"):
        _welcome(message)
        return
    if text.startswith("/help"):
        _help(chat_id)
        return
    if text.startswith("/subscribe"):
        set_subscription(user_id, True)
        send_text(chat_id, "🔔 You are subscribed to ZenoAI announcements.")
        return
    if text.startswith("/unsubscribe"):
        set_subscription(user_id, False)
        send_text(chat_id, "🔕 You will no longer receive announcements.")
        return
    if text.startswith("/clear"):
        clear_history(chat_id)
        send_text(chat_id, "🧹 Chat context cleared. Start a fresh conversation!", home_keyboard())
        return
    if text.startswith("/about"):
        send_text(chat_id, f"✨ <b>{html.escape(config.bot_name)}</b>\nAI coding assistant powered by APInex.", home_keyboard(), "HTML")
        return
    if text.startswith("/membership"):
        send_text(chat_id, _membership_text(user_id), home_keyboard(), "HTML")
        return
    if text.split(maxsplit=1)[0].split("@", 1)[0].lower() in {
        "/admin", "/stats", "/ban", "/unban", "/broadcast", "/addadmin",
        "/removeadmin", "/model", "/prompt", "/maintenance", "/audit",
        "/grant", "/revoke", "/forceadd", "/forceremove", "/forcelist", "/limits"
    }:
        if not is_admin(user_id):
            send_text(chat_id, "⛔ You do not have permission to use administrative commands.")
            return
        _admin_command(message, text, user_id)
        return
    if get_setting("maintenance", "off") == "on" and not is_admin(user_id):
        send_text(chat_id, "🛠 ZenoAI is temporarily under maintenance. Please try again later.")
        return
    if not is_admin(user_id) and not _force_join_prompt(chat_id, user_id):
        return
    document = message.get("document")
    if document:
        try:
            if not is_admin(user_id) and not _membership_gate(chat_id, user_id):
                return
            _analyze_upload(message, document)
        except Exception as exc:
            log.exception("Document analysis failed")
            send_text(chat_id, f"⚠️ I couldn't analyze that file: {str(exc)[:700]}")
        return
    if not text:
        send_text(chat_id, "Please send a text prompt or upload a ZIP/source file. Use /help for supported features.")
        return
    try:
        if not is_admin(user_id) and not _membership_gate(chat_id, user_id):
            return
        send_text(chat_id, "🧠 <b>ZenoAI is thinking…</b>", parse_mode="HTML")
        send_long_answer(chat_id, ask_ai(chat_id, text), text)
    except Exception:
        log.exception("AI response failed")
        send_text(chat_id, "⚠️ I couldn't complete that request. Check the APInex key, model ID and account limits.")


def _handle_callback(callback):
    callback_id = callback.get("id")
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    user_id = (callback.get("from") or {}).get("id")
    data = callback.get("data", "")
    try:
        if callback_id:
            _api("answerCallbackQuery", {"callback_query_id": callback_id})
        if not chat_id or not user_id:
            return
        register_user(callback.get("from") or {})
        if is_banned(user_id):
            send_text(chat_id, "⛔ Your access is restricted.")
            return
        owner = user_id == config.owner_id
        admin = is_admin(user_id)
        if data.startswith(("adm_", "own_")) and not admin:
            send_text(chat_id, "⛔ Administrative access required.")
            return
        if data.startswith("own_") and not owner:
            send_text(chat_id, "⛔ Owner-only control.")
            return
        if data == "admin_panel":
            _admin_help(chat_id, owner)
        elif data == "home":
            send_text(chat_id, f"✨ <b>{html.escape(config.bot_name)}</b>\nChoose what you'd like to do.", home_keyboard(), "HTML")
        elif data == "help":
            _help(chat_id)
        elif data == "about":
            send_text(chat_id, f"✨ <b>{html.escape(config.bot_name)}</b>\nAI coding assistant powered by APInex.", home_keyboard(), "HTML")
        elif data == "membership":
            send_text(chat_id, _membership_text(user_id), home_keyboard(), "HTML")
        elif data == "verify_join":
            if _force_join_prompt(chat_id, user_id):
                send_text(chat_id, "Membership verified. You can now use ZenoAI.", home_keyboard())
        elif data == "settings":
            model = get_setting("model", config.apinex_model)
            send_text(chat_id, f"⚙️ <b>Settings</b>\nModel: <code>{html.escape(model)}</code>\nMemory: {config.max_context_messages} messages\nUpload limit: {config.max_upload_mb} MB", {
                "inline_keyboard": [[{"text": "🧹 Clear chat", "callback_data": "clear"}], [{"text": "⬅️ Home", "callback_data": "home"}]]
            }, "HTML")
        elif data == "clear":
            clear_history(chat_id)
            send_text(chat_id, "🧹 Conversation context cleared.", home_keyboard())
        elif data == "mode_coding":
            send_text(chat_id, "💻 Describe the app, language, features and output you need.")
        elif data == "mode_debug":
            send_text(chat_id, "🛠 Send the full error message and related code.")
        elif data == "mode_zip":
            send_text(chat_id, "📦 Upload your project as a ZIP. I'll inspect source files without executing them.")
        elif data in {"adm_stats", "adm_health"}:
            if data == "adm_stats":
                s = stats()
                send_text(chat_id, f"📊 Users: {s['users']}\nSubscribers: {s['subscribers']}\nBanned: {s['banned']}\nAdmins: {s['admins']}\nStored messages: {s['messages']}")
            else:
                send_text(chat_id, "🩺 Bot process is responding. Render health endpoint: /health")
        elif data == "adm_users":
            send_text(chat_id, "🛡 User management commands:\n/ban USER_ID\n/unban USER_ID\n/stats")
        elif data == "adm_broadcast":
            send_text(chat_id, "📣 Send /broadcast MESSAGE to announce to users who explicitly subscribed with /subscribe.")
        elif data == "own_admins":
            rows = list_admins()
            body = "\n".join(f"• {r['user_id']} (added by {r['added_by']})" for r in rows) or "No additional admins."
            send_text(chat_id, "👑 <b>Admins</b>\n" + html.escape(body) + "\n\n/addadmin USER_ID\n/removeadmin USER_ID", parse_mode="HTML")
        elif data == "own_membership":
            m = membership_stats()
            send_text(chat_id, "👤 <b>Membership overview</b>\nTrial: {}\nGranted: {}\nFree: {}\nExpired: {}\n\n/grant USER_ID DAYS\n/revoke USER_ID".format(m["trial"], m["granted"], m["free"], m["expired"]), parse_mode="HTML")
        elif data == "own_forcejoin":
            entries = _force_join_entries()
            body = "\n".join("{} — {}".format(x.get("chat_id"), x.get("url")) for x in entries) or "No required channels/groups configured."
            send_text(chat_id, "📢 <b>Force-join management</b>\n{}\n\n/forceadd @channel https://t.me/channel\n/forceremove @channel\n/forcelist\n\nThe bot must be an administrator in every required destination.".format(html.escape(body)), parse_mode="HTML")
        elif data == "own_ai":
            model = get_setting("model", config.apinex_model)
            send_text(chat_id, f"🧠 Current model: <code>{html.escape(model)}</code>\nChange: /model MODEL_ID\nUpdate behavior: /prompt YOUR INSTRUCTIONS", parse_mode="HTML")
        elif data == "own_maintenance":
            state = get_setting("maintenance", "off")
            send_text(chat_id, f"🔧 Maintenance is currently {state.upper()}.\nUse /maintenance on or /maintenance off.")
        elif data == "own_audit":
            rows = recent_audit()
            body = "\n".join(f"#{r['id']} {r['actor_id']} {r['action']} -> {r['target_id'] or '-'}" for r in rows) or "No audit records."
            send_text(chat_id, "📜 <b>Audit log</b>\n" + html.escape(body), parse_mode="HTML")
        else:
            send_text(chat_id, "Menu option unavailable. Use /start.")
    except Exception:
        log.exception("Callback handling failed")
