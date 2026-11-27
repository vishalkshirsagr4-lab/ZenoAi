import html
import io
import logging
import os
import requests
from config import config
from core.ai import ask_ai
from core.project_reader import read_zip_bytes, read_single_source
from core.storage import clear_history

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
    summary = "🧩 The complete answer is attached as a text file so the code and formatting stay intact."
    send_text(chat_id, summary)
    file_obj = io.BytesIO(answer.encode("utf-8"))
    file_obj.name = "zenoai_answer.txt"
    return _api(
        "sendDocument",
        {"chat_id": chat_id, "caption": "✨ ZenoAI complete response"},
        files={"document": ("zenoai_answer.txt", file_obj, "text/plain")},
        timeout=90,
    )


def home_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "💻 Coding", "callback_data": "mode_coding"},
                {"text": "🧩 Debug", "callback_data": "mode_debug"},
            ],
            [
                {"text": "📦 Analyze ZIP", "callback_data": "mode_zip"},
                {"text": "⚙️ Settings", "callback_data": "settings"},
            ],
            [
                {"text": "📚 Help", "callback_data": "help"},
                {"text": "ℹ️ About", "callback_data": "about"},
            ],
            [{"text": "🧹 Clear chat", "callback_data": "clear"}],
        ]
    }


def _welcome(message):
    user = message.get("from") or {}
    first_name = html.escape(user.get("first_name") or "there")
    text = (
        f"✨ <b>Welcome to {html.escape(config.bot_name)}, {first_name}!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🤖 Your AI workspace for coding, debugging and project analysis.\n\n"
        "💻 Generate code and complete project files\n"
        "🛠 Find bugs and understand errors\n"
        "📦 Upload a ZIP to inspect its source files\n"
        "🧠 Ask follow-up questions with chat context\n\n"
        "<i>Choose a tool below, or simply send me a message.</i>"
    )
    send_text(message["chat"]["id"], text, home_keyboard(), "HTML")


def _help(chat_id):
    text = (
        "📚 <b>ZenoAI Help</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💬 <b>Chat:</b> Send any question or coding task.\n"
        "💻 <b>Generate:</b> Describe the language, features and expected output.\n"
        "🛠 <b>Debug:</b> Paste the error and the related code.\n"
        "📦 <b>Project review:</b> Upload a .zip file and tell me what to inspect.\n"
        "📄 <b>Source files:</b> Send supported text/code files directly.\n\n"
        "<b>Commands</b>\n/start — open dashboard\n/help — show help\n/clear — clear chat context\n/about — bot information\n\n"
        "Large answers are sent as a text attachment. ZIP contents are inspected, never executed."
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
    lower = filename.lower()
    send_text(chat_id, "📥 <b>File received.</b> Checking its contents safely…", parse_mode="HTML")
    data = _download_telegram_file(document["file_id"])
    if lower.endswith(".zip"):
        context, file_count, text_count = read_zip_bytes(data)
        task = (
            "Review this uploaded software project. Start with a short architecture summary, then list "
            "confirmed issues by severity and file path, explain likely causes, and give concrete fixes. "
            "Mention which areas could not be verified. Do not claim tests were run. "
            f"Archive entries: {file_count}; text files included: {text_count}."
        )
    else:
        context, file_count, text_count = read_single_source(filename, data)
        task = f"Review the uploaded source file {filename}. Explain its purpose, identify concrete bugs or risks, and suggest corrected code where useful."
    answer = ask_ai(chat_id, task, extra_context=context)
    send_long_answer(chat_id, answer)


def _handle_message(message):
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return
    text = (message.get("text") or message.get("caption") or "").strip()
    if text.startswith("/start"):
        _welcome(message)
        return
    if text.startswith("/help"):
        _help(chat_id)
        return
    if text.startswith("/clear"):
        clear_history(chat_id)
        send_text(chat_id, "🧹 Chat context cleared. Start a fresh conversation!", home_keyboard())
        return
    if text.startswith("/about"):
        send_text(chat_id, f"✨ <b>{html.escape(config.bot_name)}</b>\nAI coding assistant powered by APInex.\nBuild, debug, understand and review code.", home_keyboard(), "HTML")
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
        send_text(chat_id, "Please send a text prompt or upload a ZIP/source file. Use /help to see supported features.")
        return

    try:
        send_text(chat_id, "🧠 <b>ZenoAI is thinking…</b>", parse_mode="HTML")
        answer = ask_ai(chat_id, text)
        send_long_answer(chat_id, answer)
    except Exception:
        log.exception("AI response failed")
        send_text(chat_id, "⚠️ I couldn't complete that request. Please try again in a moment. If it continues, check the APInex key, model ID and account limits.")


def _handle_callback(callback):
    callback_id = callback.get("id")
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    data = callback.get("data", "")
    try:
        if callback_id:
            _api("answerCallbackQuery", {"callback_query_id": callback_id})
        if not chat_id:
            return
        if data == "home":
            send_text(chat_id, f"✨ <b>{html.escape(config.bot_name)}</b>\nChoose what you'd like to do.", home_keyboard(), "HTML")
        elif data == "help":
            _help(chat_id)
        elif data == "about":
            send_text(chat_id, f"✨ <b>{html.escape(config.bot_name)}</b>\nAI coding assistant powered by APInex.\nGenerate code, debug errors and inspect project files.", home_keyboard(), "HTML")
        elif data == "settings":
            send_text(chat_id, f"⚙️ <b>Settings</b>\n━━━━━━━━━━━━━━━━━━\n🤖 Model: <code>{html.escape(config.apinex_model)}</code>\n🧠 Chat memory: last {config.max_context_messages} messages\n📦 Upload limit: {config.max_upload_mb} MB\n\nYour custom AI behavior can be pasted into <code>prompts/behavior.txt</code> in the GitHub repository.", {
                "inline_keyboard": [[{"text": "🧹 Clear chat", "callback_data": "clear"}], [{"text": "⬅️ Home", "callback_data": "home"}]]
            }, "HTML")
        elif data == "clear":
            clear_history(chat_id)
            send_text(chat_id, "🧹 Conversation context cleared.", home_keyboard())
        elif data == "mode_coding":
            send_text(chat_id, "💻 Describe the app, language, features and output you need. I can create code and project structures.")
        elif data == "mode_debug":
            send_text(chat_id, "🛠 Send the full error message and the related code. I'll trace the likely cause and propose a fix.")
        elif data == "mode_zip":
            send_text(chat_id, "📦 Upload your project as a .zip file. I'll inspect the source and report confirmed issues. I never execute uploaded files.")
        else:
            send_text(chat_id, "That menu option is not available. Use /start to open the dashboard.")
    except Exception:
        log.exception("Callback handling failed")
