"""
Central configuration for the Telegram diet tracker.

Every setting is read from an environment variable so nothing secret lives in
the repo. The only two values you *must* set are:

    TELEGRAM_BOT_TOKEN   - from @BotFather on Telegram
    ANTHROPIC_API_KEY    - from https://console.anthropic.com (used to read meals)

Everything else has a sensible default.
"""

import os

# --- Secrets -----------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# ANTHROPIC_API_KEY is read directly by the anthropic SDK from the environment.

# --- Storage -----------------------------------------------------------------

# Where the SQLite database lives. The bot and the dashboard both point here.
DB_PATH = os.environ.get(
    "DIET_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "diet_tracker.db"),
)

# --- Time --------------------------------------------------------------------

# IANA timezone used for "today", sleep time, and notification slots.
# e.g. "Asia/Jerusalem", "America/New_York", "Europe/London".
TIMEZONE = os.environ.get("DIET_TZ", "UTC")

# --- AI model ----------------------------------------------------------------

# Claude model used to turn a free-text meal ("2 eggs and toast") into calories.
# Defaults to the most capable model; set DIET_AI_MODEL=claude-haiku-4-5 to make
# it cheaper/faster if you log many meals a day.
AI_MODEL = os.environ.get("DIET_AI_MODEL", "claude-opus-4-8")

# --- Notification schedule ---------------------------------------------------

# Local hours (24h) at which the bot proactively nudges you with how many
# calories you have left and what you could still eat before bed.
NUDGE_HOURS = [int(h) for h in os.environ.get("DIET_NUDGE_HOURS", "12,16,20").split(",")]

# How often (seconds) the background scheduler wakes up to check the clock.
SCHEDULER_INTERVAL_SECONDS = int(os.environ.get("DIET_SCHEDULER_INTERVAL", "60"))
