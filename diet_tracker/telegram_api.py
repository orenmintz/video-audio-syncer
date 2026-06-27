"""
Minimal Telegram Bot API client built on `requests` long-polling.

Kept dependency-light on purpose: no framework, just the two endpoints we need
(getUpdates + sendMessage) plus a tiny reply-keyboard helper.
"""

import requests

from config import TELEGRAM_BOT_TOKEN

_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


class TelegramError(RuntimeError):
    pass


def _call(method: str, timeout: int = 35, **params):
    resp = requests.post(f"{_BASE}/{method}", json=params, timeout=timeout)
    data = resp.json()
    if not data.get("ok"):
        raise TelegramError(data.get("description", "unknown Telegram error"))
    return data["result"]


def get_updates(offset: int | None = None, timeout: int = 30):
    """Long-poll for new updates. Returns a list of update objects."""
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    # The HTTP read timeout must be longer than the long-poll timeout.
    return _call("getUpdates", timeout=timeout + 5, **params)


def send_message(chat_id: int, text: str, keyboard: list[list[str]] | None = None):
    """Send a Markdown message, optionally with a one-time reply keyboard."""
    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if keyboard is not None:
        params["reply_markup"] = {
            "keyboard": [[{"text": btn} for btn in row] for row in keyboard],
            "one_time_keyboard": True,
            "resize_keyboard": True,
        }
    else:
        # Remove any keyboard left over from a previous step.
        params["reply_markup"] = {"remove_keyboard": True}
    return _call("sendMessage", timeout=20, **params)
