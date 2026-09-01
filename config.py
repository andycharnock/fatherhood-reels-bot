"""Configuration. Every value comes from an environment variable.

Nothing secret is ever written in the code. Set these in Render under
Environment. For local testing, put them in a .env file (see .env.example).
"""

import hashlib
import os
import sys

# Load .env if present. Harmless on Render, where real env vars take priority.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _get(name, default=None, required=False):
    value = os.environ.get(name, default)
    if required and not value:
        print(f"FATAL: missing required environment variable {name}", file=sys.stderr)
        sys.exit(1)
    return value


# --- Telegram -------------------------------------------------------------
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN", required=True)
ALLOWED_CHAT_ID = int(_get("ALLOWED_CHAT_ID", required=True))

# --- Supabase -------------------------------------------------------------
SUPABASE_URL = _get("SUPABASE_URL", required=True).rstrip("/")
SUPABASE_SERVICE_KEY = _get("SUPABASE_SERVICE_KEY", required=True)

# --- Claude ---------------------------------------------------------------
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY", required=True)
CLAUDE_MODEL = _get("CLAUDE_MODEL", "claude-sonnet-5")

# --- ElevenLabs -----------------------------------------------------------
ELEVENLABS_API_KEY = _get("ELEVENLABS_API_KEY", required=True)
ELEVENLABS_VOICE_ID = _get("ELEVENLABS_VOICE_ID", required=True)
ELEVENLABS_MODEL_ID = _get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

# --- Shotstack ------------------------------------------------------------
SHOTSTACK_API_KEY = _get("SHOTSTACK_API_KEY", required=True)
# "stage" renders free with a watermark. "v1" renders for real and costs money.
SHOTSTACK_ENV = _get("SHOTSTACK_ENV", "stage")

# --- Behaviour ------------------------------------------------------------
# Seconds of background footage used per tile before it repeats.
BACKGROUND_TILE_SECONDS = float(_get("BACKGROUND_TILE_SECONDS", "20"))
# Optional caption font. Must be a font Shotstack supports. Blank = their default.
CAPTION_FONT = _get("CAPTION_FONT", "")
CAPTION_SIZE = int(_get("CAPTION_SIZE", "34"))
# Safety net against a bug looping and spending money. Set to 0 to disable.
DAILY_JOB_CAP = int(_get("DAILY_JOB_CAP", "5"))
POSTING_TIME_LINE = _get("POSTING_TIME_LINE", "Post at 7pm UAE.")

# --- Hosting --------------------------------------------------------------
PORT = int(_get("PORT", "10000"))
# Render sets RENDER_EXTERNAL_URL automatically. No action needed.
BASE_URL = _get("RENDER_EXTERNAL_URL", "").rstrip("/")

# Webhook path derived from the bot token so it is unguessable but stable.
WEBHOOK_PATH = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).hexdigest()[:32]
WEBHOOK_SECRET = hashlib.sha256(
    (TELEGRAM_BOT_TOKEN + "secret").encode()
).hexdigest()[:32]

SHOTSTACK_EDIT_URL = f"https://api.shotstack.io/edit/{SHOTSTACK_ENV}"
SHOTSTACK_INGEST_URL = f"https://api.shotstack.io/ingest/{SHOTSTACK_ENV}"
