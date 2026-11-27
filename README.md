# ZenoAI — Telegram AI Coding Assistant

ZenoAI is a Python Telegram bot powered by the APInex OpenAI-compatible API. It is designed for coding help, large code generation, debugging, and safe source-project/ZIP review.

## Features
- Emoji-rich inline dashboard, Help, Settings, About and chat reset.
- Configurable AI model and a separate editable behavior prompt.
- Code generation, explanations, debugging and refactoring.
- ZIP/source-file review with archive traversal, symlink, file-count and size protections. Uploaded code is inspected as text and is never executed.
- Long answers are delivered as a downloadable text file to avoid Telegram message limits.
- SQLite conversation history and a Flask health endpoint.
- Polling runner that removes any existing webhook before calling getUpdates.
- GitHub Actions checks for Python compilation and ZIP-reader tests.

## Render deployment
The included `render.yaml` is configured as an always-on **Starter Web Service** because a polling bot needs a continuously running process. Render's free web services can sleep when idle, which makes polling unreliable.

Create the service from this repository or use the Blueprint:
- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `python main.py`

Add these environment variables in Render:
- `TELEGRAM_BOT_TOKEN` — token from BotFather.
- `OWNER_ID` — your numeric Telegram user ID; required for the owner account and owner-only controls.
- `APINEX_API_KEY` — your APInex API key.
- `APINEX_MODEL` — optional; default is `deepseek-v4-flash`.
- `BOT_NAME` — optional; default is `ZenoAI`.
- `MAX_UPLOAD_MB` — optional; default is `15`.
- `DATABASE_PATH` — optional; default is `data/zenoai.db`.
- `AI_SYSTEM_PROMPT` — optional inline system instructions.

For persistent chat history on Render, attach a persistent disk and set `DATABASE_PATH` to a path on that disk, for example `/var/data/zenoai.db`. Without a persistent disk, SQLite data may be lost when the service is replaced.

**Important:** Run only one polling instance for this bot token. Do not run a second copy locally while the Render service is polling. Polling uses the Telegram Bot API and does not require a public webhook URL.

## Customize the AI's behavior
Open `prompts/behavior.txt` and paste your own behavior instructions in the marked section. You can edit this file directly on GitHub. The optional `AI_SYSTEM_PROMPT` environment variable takes precedence over the file.

## Local run
1. Copy `.env.example` to `.env` and fill in the two secret values.
2. Install dependencies: `pip install -r requirements.txt`
3. Run: `python main.py`

Never commit your Telegram token or APInex API key.

## Owner and admin controls

Open the bot and use `/admin`:
- Owner: `/addadmin USER_ID`, `/removeadmin USER_ID`, `/model MODEL_ID`, `/prompt YOUR_INSTRUCTIONS`, `/maintenance on|off`, `/audit`.
- Admins: `/stats`, `/ban USER_ID`, `/unban USER_ID`, `/broadcast MESSAGE`.
- Announcements are delivered only to users who explicitly use `/subscribe`; users can opt out with `/unsubscribe`.
- Admin actions are recorded in the audit log. The owner cannot be banned or demoted.
