import logging
import os
import threading
import time
import traceback
import requests

from config import config, validate_config
from core.storage import boot
from routes.webhook import _handle_message, _handle_callback
from app import app as flask_app

API = f"https://api.telegram.org/bot{config.bot_token}"
ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member", "chat_member"]
HTTP = requests.Session()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("zeno.polling")


def delete_webhook():
    """Telegram rejects getUpdates while a webhook is registered."""
    try:
        response = HTTP.post(f"{API}/deleteWebhook", data={"drop_pending_updates": "false"}, timeout=15)
        response.raise_for_status()
        result = response.json()
        log.info("deleteWebhook: %s", result.get("description", result.get("ok")))
    except Exception as exc:
        log.warning("deleteWebhook failed: %s", exc)


def get_updates(offset=None, timeout=20):
    params = {"timeout": timeout, "allowed_updates": ALLOWED_UPDATES}
    if offset is not None:
        params["offset"] = offset
    try:
        response = HTTP.get(f"{API}/getUpdates", params=params, timeout=timeout + 10)
        data = response.json()
        if data.get("ok"):
            return data.get("result", [])
        log.error("getUpdates rejected: %s", data.get("description", data))
    except Exception as exc:
        log.warning("getUpdates error: %s", exc)
    return []


def run_polling():
    boot()
    delete_webhook()
    offset = None
    log.info("Polling started (pid=%s)", os.getpid())
    while True:
        try:
            with flask_app.app_context():
                updates = get_updates(offset)
                for update in updates:
                    offset = update["update_id"] + 1
                    try:
                        callback = update.get("callback_query")
                        if callback:
                            log.info("Callback received")
                            _handle_callback(callback)
                            continue
                        message = update.get("message")
                        if message:
                            text = message.get("text") or message.get("caption") or ""
                            log.info("Message received: %s", text[:40].replace("\n", " "))
                            _handle_message(message)
                    except Exception:
                        log.exception("Update handler failed")
        except KeyboardInterrupt:
            log.info("Stopping polling…")
            break
        except Exception:
            log.exception("Polling loop error")
            time.sleep(2)


def run_health_server():
    port = int(os.environ.get("PORT", "10000"))
    flask_app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    validate_config()
    boot()
    threading.Thread(target=run_health_server, name="health-server", daemon=True).start()
    run_polling()
