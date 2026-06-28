"""
Telegram diet-tracker bot.

Run it with:
    python bot.py

Telegram is used ONLY to log food. You create and edit your profile (age,
weight, goal, sleep time, ...) in the Streamlit dashboard, which gives you a
short code. You connect this chat to that profile once with:

    /link ABC123

After that, just text the bot what you eat and it tracks your calories, replies
with what's left, and proactively nudges you during the day.

Commands: /link /status /today /summary /undo /help
"""

import threading
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import database as db
import meal_ai
from telegram_api import get_updates, send_message

TZ = ZoneInfo(config.TIMEZONE)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_local() -> datetime:
    return datetime.now(TZ)


def local_date_str(dt: datetime | None = None) -> str:
    return (dt or now_local()).strftime("%Y-%m-%d")


def hours_until_sleep(sleep_hour: int) -> float:
    """Hours from now until the user's bedtime (handles after-midnight wrap)."""
    now = now_local()
    bedtime = now.replace(hour=sleep_hour % 24, minute=0, second=0, microsecond=0)
    if bedtime <= now:
        bedtime += timedelta(days=1)
    return (bedtime - now).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Status / reporting
# ---------------------------------------------------------------------------

def _progress_bar(consumed: int, target: int, width: int = 10) -> str:
    if target <= 0:
        return ""
    filled = max(0, min(width, round(width * consumed / target)))
    return "▰" * filled + "▱" * (width - filled)


def _status_text(profile: dict, include_suggestion: bool = True) -> str:
    date = local_date_str()
    totals = db.day_totals(profile["id"], date)
    consumed = int(totals["calories"])
    target = profile["daily_calories"]
    remaining = target - consumed
    protein_left = max(0, profile["protein_g"] - round(totals["protein_g"]))
    h2s = hours_until_sleep(profile["sleep_hour"])

    lines = [
        f"📊 *Today* ({totals['meal_count']} meals logged)",
        f"{_progress_bar(consumed, target)}",
        f"Eaten: *{consumed}* / {target} kcal",
    ]
    if remaining >= 0:
        lines.append(f"Left: *{remaining} kcal* before bed (~{h2s:.0f}h away)")
    else:
        lines.append(f"⚠️ *{abs(remaining)} kcal over* target")
    lines.append(f"Protein: {round(totals['protein_g'])}/{profile['protein_g']} g")

    text = "\n".join(lines)

    if include_suggestion:
        try:
            tip = meal_ai.suggest_foods(remaining, protein_left, h2s, profile["goal"])
            text += f"\n\n💡 *What you can still eat:*\n{tip}"
        except Exception:
            pass  # suggestions are best-effort; never block the status

    return text


def _today_text(profile: dict) -> str:
    meals = db.meals_for_date(profile["id"], local_date_str())
    if not meals:
        return "You haven't logged anything today yet. Text me what you eat!"
    lines = ["🍽 *Today's meals:*"]
    for m in meals:
        lines.append(f"• {m['description']} — *{m['calories']} kcal*")
    return "\n".join(lines)


def _summary_text(profile: dict) -> str:
    rows = db.history(profile["id"], days=7)
    if not rows:
        return "No history yet — log a few meals first."
    target = profile["daily_calories"]
    under = sum(1 for r in rows if r["calories"] <= target)
    lines = [f"📅 *Last {len(rows)} days* (target {target} kcal):"]
    for r in rows:
        cal = int(r["calories"])
        mark = "✅" if cal <= target else "🔴"
        lines.append(f"{mark} {r['local_date']}: *{cal}* kcal ({r['meal_count']} meals)")
    lines.append(f"\nYou hit your target on *{under}/{len(rows)}* days. Keep it up! 💪")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Meal logging
# ---------------------------------------------------------------------------

def _log_meal(profile: dict, text: str):
    chat_id = profile["telegram_chat_id"]
    send_message(chat_id, "🔎 Reading your meal…")
    try:
        est = meal_ai.estimate_meal(text)
    except Exception:
        traceback.print_exc()
        send_message(
            chat_id,
            "😕 I couldn't work out the calories for that. Try describing it a "
            "bit more plainly (e.g. \"grilled chicken breast with rice\").",
        )
        return

    estimate = est.model_dump()
    db.add_meal(profile["id"], text, estimate, local_date_str())

    totals = db.day_totals(profile["id"], local_date_str())
    remaining = profile["daily_calories"] - int(totals["calories"])

    item_lines = "\n".join(
        f"  • {i['name']} ({i['quantity']}): {i['calories']} kcal"
        for i in estimate["items"]
    )
    msg = (
        f"✅ Logged *{estimate['total_calories']} kcal* "
        f"(P {estimate['total_protein_g']:.0f} / C {estimate['total_carbs_g']:.0f} / "
        f"F {estimate['total_fat_g']:.0f} g)\n{item_lines}\n\n"
    )
    if remaining >= 0:
        msg += f"You have *{remaining} kcal* left today."
    else:
        msg += f"⚠️ You're now *{abs(remaining)} kcal over* target."
    send_message(chat_id, msg)


# ---------------------------------------------------------------------------
# Command + message dispatch
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "🤖 *Diet Tracker*\n"
    "Set up your profile in the *dashboard*, then text me what you eat to log it.\n\n"
    "/link `CODE` — connect this chat to your dashboard profile\n"
    "/status — calories left + what you can eat\n"
    "/today — list today's meals\n"
    "/summary — your last 7 days\n"
    "/undo — remove your last logged meal\n"
    "/help — show this message"
)

NOT_LINKED_TEXT = (
    "👋 Welcome! First, set up your profile in the *dashboard* "
    "(your name, weight, goal, etc.).\n\n"
    "It will show you a 6-character code. Send it to me here like this:\n"
    "`/link ABC123`\n\n"
    "After that, just text me what you eat and I'll track your calories."
)


def _handle_link(chat_id: int, text: str):
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        send_message(
            chat_id,
            "Send the code from your dashboard, e.g. `/link ABC123`.",
        )
        return
    profile = db.link_chat(parts[1], chat_id)
    if not profile:
        send_message(
            chat_id,
            "❌ I couldn't find that code. Double-check it in the dashboard "
            "(it's 6 characters) and try `/link YOURCODE` again.",
        )
        return
    send_message(
        chat_id,
        f"✅ Connected to *{profile['name']}*'s profile!\n"
        f"Daily target: *{profile['daily_calories']} kcal*.\n\n"
        "Now just text me what you eat. Try /status anytime.",
    )


def handle_update(update: dict):
    message = update.get("message") or update.get("edited_message")
    if not message or "text" not in message:
        return
    chat_id = message["chat"]["id"]
    text = message["text"].strip()

    # /link and /help/start work before a profile is connected.
    if text.startswith("/link"):
        _handle_link(chat_id, text)
        return
    if text.startswith(("/help", "/start")):
        profile = db.get_profile_by_chat(chat_id)
        send_message(chat_id, HELP_TEXT if profile else NOT_LINKED_TEXT)
        return

    profile = db.get_profile_by_chat(chat_id)
    if not profile:
        send_message(chat_id, NOT_LINKED_TEXT)
        return
    if profile.get("daily_calories") is None:
        send_message(
            chat_id,
            "Your profile isn't finished yet — open the dashboard and save your "
            "details first.",
        )
        return

    if text.startswith("/status"):
        send_message(chat_id, _status_text(profile))
    elif text.startswith("/today"):
        send_message(chat_id, _today_text(profile))
    elif text.startswith("/summary"):
        send_message(chat_id, _summary_text(profile))
    elif text.startswith("/undo"):
        if db.delete_last_meal(profile["id"], local_date_str()):
            send_message(
                chat_id,
                "↩️ Removed your last meal. "
                + _status_text(profile, include_suggestion=False),
            )
        else:
            send_message(chat_id, "Nothing to undo today.")
    elif text.startswith("/"):
        send_message(chat_id, "Unknown command.\n" + HELP_TEXT)
    else:
        _log_meal(profile, text)


# ---------------------------------------------------------------------------
# Proactive notification scheduler (background thread)
# ---------------------------------------------------------------------------

def _send_nudge(profile: dict, slot: str):
    send_message(
        profile["telegram_chat_id"],
        f"⏰ Just checking in!\n\n{_status_text(profile)}",
    )
    db.mark_notified(profile["id"], local_date_str(), slot)


def _send_bedtime_summary(profile: dict):
    chat_id = profile["telegram_chat_id"]
    totals = db.day_totals(profile["id"], local_date_str())
    consumed = int(totals["calories"])
    target = profile["daily_calories"]
    if consumed == 0:
        msg = "😴 Heading to bed? You didn't log anything today — try again tomorrow!"
    elif consumed <= target:
        msg = (
            f"🌙 Great day! You finished on *{consumed}/{target} kcal* — "
            f"*{target - consumed} kcal under* target. Sleep well! 😴"
        )
    else:
        msg = (
            f"🌙 You finished on *{consumed}/{target} kcal*, "
            f"*{consumed - target} over* target. Tomorrow's a fresh start! 💪"
        )
    send_message(chat_id, msg)
    db.mark_notified(profile["id"], local_date_str(), "bedtime")


def scheduler_loop():
    """Wake periodically and fire any due nudges / bedtime summaries."""
    while True:
        try:
            now = now_local()
            date = local_date_str(now)
            for profile in db.active_profiles():
                pid = profile["id"]
                for hour in config.NUDGE_HOURS:
                    if now.hour == hour:
                        slot = f"nudge_{hour}"
                        if not db.was_notified(pid, date, slot):
                            _send_nudge(profile, slot)
                if profile.get("sleep_hour") is not None and now.hour == profile["sleep_hour"]:
                    if not db.was_notified(pid, date, "bedtime"):
                        _send_bedtime_summary(profile)
        except Exception:
            traceback.print_exc()
        time.sleep(config.SCHEDULER_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Main long-poll loop
# ---------------------------------------------------------------------------

def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN before starting the bot.")
    db.init_db()
    print(f"Diet tracker bot started (tz={config.TIMEZONE}, model={config.AI_MODEL}).")

    threading.Thread(target=scheduler_loop, daemon=True).start()

    offset = None
    while True:
        try:
            updates = get_updates(offset=offset, timeout=30)
        except Exception:
            traceback.print_exc()
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            try:
                handle_update(update)
            except Exception:
                traceback.print_exc()


if __name__ == "__main__":
    main()
