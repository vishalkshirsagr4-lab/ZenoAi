import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    apinex_api_key: str = os.getenv("APINEX_API_KEY", "").strip()
    apinex_base_url: str = os.getenv("APINEX_BASE_URL", "https://api.apinex.bond/v1").strip().rstrip("/")
    apinex_model: str = os.getenv("APINEX_MODEL", "deepseek-v4-flash").strip()
    bot_name: str = os.getenv("BOT_NAME", "ZenoAI").strip() or "ZenoAI"
    database_path: str = os.getenv("DATABASE_PATH", "data/zenoai.db")
    system_prompt: str = os.getenv("AI_SYSTEM_PROMPT", "").strip()
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "15"))
    max_archive_unpacked_mb: int = int(os.getenv("MAX_ARCHIVE_UNPACKED_MB", "30"))
    max_archive_files: int = int(os.getenv("MAX_ARCHIVE_FILES", "300"))
    max_file_text_kb: int = int(os.getenv("MAX_FILE_TEXT_KB", "256"))
    max_context_messages: int = int(os.getenv("MAX_CONTEXT_MESSAGES", "12"))
    ai_max_tokens: int = int(os.getenv("AI_MAX_TOKENS", "8192"))


config = Config()


def validate_config():
    missing = []
    if not config.bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not config.apinex_api_key:
        missing.append("APINEX_API_KEY")
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    if config.max_upload_mb < 1 or config.max_upload_mb > 20:
        raise RuntimeError("MAX_UPLOAD_MB must be between 1 and 20.")
