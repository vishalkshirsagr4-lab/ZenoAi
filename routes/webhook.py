import html
import io
import logging
import time
import requests
from config import config
from core.ai import ask_ai
from core.project_reader import read_zip_bytes, read_single_source
from core.storage import (
    clear_history, register_user, is_banned, is_admin, add_admin, remove_admin,
    set_banned, stats, list_admins, set_setting, get_setting, subscribers,
    set_subscription, recent_audit,
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


def send_long_answer(chat_id, answer):
    if len(answer) <= MAX_MESSAGE:
        return send_text(chat_id, answer)
    send_text(chat_id, "🧩 The complete answer is attached as a text file so the code and formatting stay intact.")
    file_obj = io.BytesIO(answer.encode("utf-8"))
    file_obj.name = "zenoai_answer.txt"
    return _api("sendDocument", {"chat_id": chat_id, "caption": "✨ ZenoAI complete response"},
                files={"document": ("zenoai_answer.txt", file_obj, "text/plain")}, timeout=90)


def home_keyboard():
    return {"inline_keyboard": [
        [{"text": "💻 Coding", "callback_data": "mode_coding"}, {"text": "🧩 Debug", "callback_data": "mode_debug"}],
        [{"text": "📦 Analyze ZIP", "callback_data": "mode_zip"}, {"text": "⚙️ Settings", "callback_data": "settings"}],
        [{"text": "📚 Help", "callback_data": "help"}, {"text": "ℹ️ About", "callback_data": "about"}],
        [{"text": "🧹 Clear chat", "callback_data": "clear"}],
    ]}


def admin_keyboard(owner=False):
    rows = [
        [{"text": "📊 Statistics", "callback_data": "adm_stats"}, {"text": "🛡 User tools", "callback_data": "adm_users"}],
        [{"text": "📣 Announcement", "callback_data": "adm_broadcast"}, {"text": "🩺 Service health", "callback_data": "adm_health"}],
    ]
    if owner:
        rows.extend([
            [{"text": "👑 Admin list", "callback_data": "own_admins"}, {"text": "🧠 AI controls", "callback_data": "own_ai"}],
            [{"text": "🔧 Maintenance", "callback_data": "own_maintenance"}, {"text": "📜 Audit log", "callback_data": "own_audit"}],
        ])
    rows.append([{"text": "⬅️ Main menu", "callback_data": "home"}])
    return {"inline_keyboard": rows}


def _welcome(message):
    user = message.get("from") or {}
    first_name = html.escape(user.get("first_name") or "there")
    text = (
        f"✨ <b>Welcome to {html.escape(config.bot_name)}, {first_name}!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🤖 Your AI workspace for coding, debugging and project analysis.\n\n"
        "💻 Generate code and complete project files\n🛠 Find bugs and understand errors\n"
        "📦 Upload a ZIP to inspect source files\n🧠 Continue conversations with chat context\n\n"
        "<i>Choose a tool below, or simply send me a message.</i>"
    )
    send_text(message["chat"]["id"], text, home_keyboard(), "HTML")


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
        task = ("Review this uploaded software project. Summarize its architecture, list confirmed issues by severity "
                "and file path, explain causes and provide concrete fixes. Mention unverified areas; do not claim tests ran. "
                f"Archive entries: {file_count}; text files included: {text_count}.")
    else:
        context, _, _ = read_single_source(filename, data)
        task = f"Review {filename}: explain its purpose, identify concrete bugs and risks, and suggest corrected code."
    send_long_answer(chat_id, ask_ai(chat_id, task, extra_context=context))


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
        send_text(chat_id, f"📊 <b>ZenoAI statistics</b>\nUsers: {s['users']}\nSubscribers: {s['subscribers']}\nBanned: {s['banned']}\nAdmins: {s['admins']}\nAI messages stored: {s['messages']}", parse_mode="HTML")
    elif command in {"/ban", "/unban"}:
        if not arg.isdigit():
            send_text(chat_id, f"Usage: {command} USER_ID")
            return
        target = int(arg)
        if target == config.owner_id:
            send_text(chat_id, "⛔ Owner account cannot be banned.")
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
    if text.split(maxsplit=1)[0].split("@", 1)[0].lower() in {
        "/admin", "/stats", "/ban", "/unban", "/broadcast", "/addadmin",
        "/removeadmin", "/model", "/prompt", "/maintenance", "/audit"
    }:
        if not is_admin(user_id):
            send_text(chat_id, "⛔ You do not have permission to use administrative commands.")
            return
        _admin_command(message, text, user_id)
        return
    if get_setting("maintenance", "off") == "on" and not is_admin(user_id):
        send_text(chat_id, "🛠 ZenoAI is temporarily under maintenance. Please try again later.")
        return
    document = message.get("document")
    if document:
        try:
            _analyze_upload(message, document)
        except Exception as exc:
            log.exception("Document analysis failed")
            send_text(chat_id, f"⚠️ I couldn't analyze that file: {str(exc)[:700]}")
        return
    if not text:
        send_text(chat_id, "Please send a text prompt or upload a ZIP/source file. Use /help for supported features.")
        return
    try:
        send_text(chat_id, "🧠 <b>ZenoAI is thinking…</b>", parse_mode="HTML")
        send_long_answer(chat_id, ask_ai(chat_id, text))
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
        if data == "home":
            send_text(chat_id, f"✨ <b>{html.escape(config.bot_name)}</b>\nChoose what you'd like to do.", home_keyboard(), "HTML")
        elif data == "help":
            _help(chat_id)
        elif data == "about":
            send_text(chat_id, f"✨ <b>{html.escape(config.bot_name)}</b>\nAI coding assistant powered by APInex.", home_keyboard(), "HTML")
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
