from flask import Flask, jsonify
from datetime import datetime, timezone

app = Flask(__name__)


@app.get("/")
def index():
    return jsonify({"name": "ZenoAI", "status": "online", "service": "telegram-ai-bot"})


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "ZenoAI",
        "time": datetime.now(timezone.utc).isoformat()
    }), 200
