"""
Telegram diet-tracker bot.

Run it with:
    python bot.py

It does three things:
  1. Onboards a new user through a short Q&A and computes their daily calorie
     and macro targets.
  2. Logs meals: any plain message is read by Claude, turned into calories, and
     added to today's total. The bot replies with how much you have left.
  3. Proactively notifies you (a background scheduler) at set times of day with
     remaining calories and what you could still eat before bed.

Commands: /start /status /today /summary /undo /reset /help
"""

import threading
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
import database as db
import meal_ai
from nutrition import ACTIVITY_FACTORS, compute_targets
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
# Onboarding
# ---------------------------------------------------------------------------

# Ordered list of (key, prompt, keyboard, parser). parser returns the cleaned
# value or raises ValueError with a user-facing message.
ACTIVITY_LABELS = {
    "Sedentary (little exercise)": "sedentary",
    "Light (1-3 days/week)": "light",
    "Moderate (3-5 days/week)": "moderate",
    "Active (6-7 days/week)": "active",
    "Very active (physical job)": "very_active",
}
GOAL_LABELS = {
    "Lose weight": "lose",
    "Maintain weight": "maintain",
    "Gain weight": "gain",
}


def _parse_sex(text: str) -> str:
    t = text.strip().lower()
    if t in ("male", "m", "man"):
        return "male"
    if t in ("female", "f", "woman"):
        return "female"
    raise ValueError("Please choose *Male* or *Female*.")


def _parse_positive_number(text: str, lo: float, hi: float, label: str) -> float:
    try:
        val = float(text.strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"Please send {label} as a number.")
    if not (lo <= val <= hi):
        raise ValueError(f"{label} should be between {lo:g} and {hi:g}.")
    return val


def _start_onboarding(user_id: int):
    db.upsert_user(user_id, onboarded=0)
    db.set_onboarding_state(user_id, {"step": 0, "answers": {}})
    send_message(
        user_id,
        "👋 Welcome! I'm your personal diet tracker.\n\n"
        "I'll ask a few quick questions to work out how many calories you should "
        "eat each day. Then just text me what you eat and I'll keep score.\n\n"
        "First — what's your *name*?",
    )


def _ask_step(user_id: int, step: int):
    """Send the prompt for a given onboarding step."""
    if step == 1:
        send_message(user_id, "What's your *sex*? (used for the calorie formula)",
                     keyboard=[["Male", "Female"]])
    elif step == 2:
        send_message(user_id, "How *old* are you? (years)")
    elif step == 3:
        send_message(user_id, "What's your *height* in *cm*?")
    elif step == 4:
        send_message(user_id, "What's your current *weight* in *kg*?")
    elif step == 5:
        send_message(user_id, "How *active* are you?",
                     keyboard=[[k] for k in ACTIVITY_LABELS])
    elif step == 6:
        send_message(user_id, "What's your *goal*?",
                     keyboard=[[k] for k in GOAL_LABELS])
    elif step == 7:
        send_message(
            user_id,
            "How many *kg per week* do you want to change?\n"
            "_(0.5 is a healthy, sustainable pace)_",
            keyboard=[["0.25", "0.5", "0.75"]],
        )
    elif step == 8:
        send_message(
            user_id,
            "Last one — what *hour* do you usually go to *sleep*? (0-23, e.g. 23)",
            keyboard=[["22", "23", "0", "1"]],
        )


def _handle_onboarding(user_id: int, text: str, state: dict):
    step = state["step"]
    answers = state["answers"]

    try:
        if step == 0:  # name -> just received
            answers["name"] = text.strip()[:50]
        elif step == 1:
            answers["sex"] = _parse_sex(text)
        elif step == 2:
            answers["age"] = int(_parse_positive_number(text, 10, 100, "your age"))
        elif step == 3:
            answers["height_cm"] = _parse_positive_number(text, 100, 250, "height")
        elif step == 4:
            answers["weight_kg"] = _parse_positive_number(text, 30, 400, "weight")
        elif step == 5:
            level = ACTIVITY_LABELS.get(text.strip())
            if level is None:
                raise ValueError("Please pick one of the activity options.")
            answers["activity_level"] = level
        elif step == 6:
            goal = GOAL_LABELS.get(text.strip())
            if goal is None:
                raise ValueError("Please pick *Lose*, *Maintain* or *Gain*.")
            answers["goal"] = goal
        elif step == 7:
            answers["goal_rate"] = _parse_positive_number(text, 0.1, 1.5, "the rate")
        elif step == 8:
            answers["sleep_hour"] = int(_parse_positive_number(text, 0, 23, "the hour"))
    except ValueError as exc:
        send_message(user_id, f"⚠️ {exc}")
        _ask_step(user_id, step)  # re-ask same step
        return

    # Decide the next step. Maintain goal skips the rate question (step 7).
    next_step = step + 1
    if next_step == 7 and answers.get("goal") == "maintain":
        answers["goal_rate"] = 0.0
        next_step = 8

    if next_step <= 8:
        state["step"] = next_step
        state["answers"] = answers
        db.set_onboarding_state(user_id, state)
        _ask_step(user_id, next_step)
        return

    # Done — compute targets and persist.
    _finish_onboarding(user_id, answers)


def _finish_onboarding(user_id: int, a: dict):
    targets = compute_targets(
        sex=a["sex"],
        age=a["age"],
        height_cm=a["height_cm"],
        weight_kg=a["weight_kg"],
        activity_level=a["activity_level"],
        goal=a["goal"],
        goal_rate_kg_per_week=a.get("goal_rate", 0.5),
    )
    db.upsert_user(
        user_id,
        name=a["name"],
        sex=a["sex"],
        age=a["age"],
        height_cm=a["height_cm"],
        weight_kg=a["weight_kg"],
        activity_level=a["activity_level"],
        goal=a["goal"],
        goal_rate=a.get("goal_rate", 0.0),
        sleep_hour=a["sleep_hour"],
        daily_calories=targets.daily_calories,
        protein_g=targets.protein_g,
        carbs_g=targets.carbs_g,
        fat_g=targets.fat_g,
        onboarded=1,
    )
    db.set_onboarding_state(user_id, None)
    send_message(
        user_id,
        f"✅ All set, *{a['name']}*!\n\n"
        f"Your daily target is *{targets.daily_calories} kcal*\n"
        f"• Protein: {targets.protein_g} g\n"
        f"• Carbs: {targets.carbs_g} g\n"
        f"• Fat: {targets.fat_g} g\n\n"
        f"_(maintenance is ~{targets.tdee} kcal; resting burn ~{targets.bmr} kcal)_\n\n"
        "Now just text me what you eat — e.g. *\"2 eggs and toast with butter\"* — "
        "and I'll track it. Use /status anytime to see what's left.",
    )


# ---------------------------------------------------------------------------
# Status / reporting
# ---------------------------------------------------------------------------

def _progress_bar(consumed: int, target: int, width: int = 10) -> str:
    if target <= 0:
        return ""
    filled = max(0, min(width, round(width * consumed / target)))
    return "▰" * filled + "▱" * (width - filled)


def _status_text(user: dict, include_suggestion: bool = True) -> str:
    date = local_date_str()
    totals = db.day_totals(user["user_id"], date)
    consumed = int(totals["calories"])
    target = user["daily_calories"]
    remaining = target - consumed
    protein_left = max(0, user["protein_g"] - round(totals["protein_g"]))
    h2s = hours_until_sleep(user["sleep_hour"])

    lines = [
        f"📊 *Today* ({totals['meal_count']} meals logged)",
        f"{_progress_bar(consumed, target)}",
        f"Eaten: *{consumed}* / {target} kcal",
    ]
    if remaining >= 0:
        lines.append(f"Left: *{remaining} kcal* before bed (~{h2s:.0f}h away)")
    else:
        lines.append(f"⚠️ *{abs(remaining)} kcal over* target")
    lines.append(
        f"Protein: {round(totals['protein_g'])}/{user['protein_g']} g"
    )

    text = "\n".join(lines)

    if include_suggestion:
        try:
            tip = meal_ai.suggest_foods(remaining, protein_left, h2s, user["goal"])
            text += f"\n\n💡 *What you can still eat:*\n{tip}"
        except Exception:
            pass  # suggestions are best-effort; never block the status

    return text


def _today_text(user: dict) -> str:
    meals = db.meals_for_date(user["user_id"], local_date_str())
    if not meals:
        return "You haven't logged anything today yet. Text me what you eat!"
    lines = ["🍽 *Today's meals:*"]
    for m in meals:
        lines.append(f"• {m['description']} — *{m['calories']} kcal*")
    return "\n".join(lines)


def _summary_text(user: dict) -> str:
    rows = db.history(user["user_id"], days=7)
    if not rows:
        return "No history yet — log a few meals first."
    target = user["daily_calories"]
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

def _log_meal(user: dict, text: str):
    user_id = user["user_id"]
    send_message(user_id, "🔎 Reading your meal…")
    try:
        est = meal_ai.estimate_meal(text)
    except Exception as exc:
        traceback.print_exc()
        send_message(
            user_id,
            "😕 I couldn't work out the calories for that. Try describing it a "
            "bit more plainly (e.g. \"grilled chicken breast with rice\").",
        )
        return

    estimate = est.model_dump()
    db.add_meal(user_id, text, estimate, local_date_str())

    totals = db.day_totals(user_id, local_date_str())
    remaining = user["daily_calories"] - int(totals["calories"])

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
    send_message(user_id, msg)


# ---------------------------------------------------------------------------
# Command + message dispatch
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "🤖 *Diet Tracker commands*\n"
    "• Just text me what you ate to log it\n"
    "/status — calories left + what you can eat\n"
    "/today — list today's meals\n"
    "/summary — your last 7 days\n"
    "/undo — remove your last logged meal\n"
    "/reset — redo the onboarding questions\n"
    "/help — show this message"
)


def handle_update(update: dict):
    message = update.get("message") or update.get("edited_message")
    if not message or "text" not in message:
        return
    user_id = message["chat"]["id"]
    text = message["text"].strip()

    # In-progress onboarding takes priority over everything except /reset.
    state = db.get_onboarding_state(user_id)
    if state is not None and not text.startswith("/reset"):
        _handle_onboarding(user_id, text, state)
        return

    user = db.get_user(user_id)

    if text.startswith("/start"):
        if user and user.get("onboarded"):
            send_message(user_id, f"Welcome back, *{user['name']}*! " + HELP_TEXT)
        else:
            _start_onboarding(user_id)
        return

    if text.startswith("/reset"):
        _start_onboarding(user_id)
        return

    if text.startswith("/help"):
        send_message(user_id, HELP_TEXT)
        return

    # All remaining actions require a finished profile.
    if not user or not user.get("onboarded"):
        _start_onboarding(user_id)
        return

    if text.startswith("/status"):
        send_message(user_id, _status_text(user))
    elif text.startswith("/today"):
        send_message(user_id, _today_text(user))
    elif text.startswith("/summary"):
        send_message(user_id, _summary_text(user))
    elif text.startswith("/undo"):
        if db.delete_last_meal(user_id, local_date_str()):
            send_message(user_id, "↩️ Removed your last meal. " + _status_text(user, include_suggestion=False))
        else:
            send_message(user_id, "Nothing to undo today.")
    elif text.startswith("/"):
        send_message(user_id, "Unknown command.\n" + HELP_TEXT)
    else:
        _log_meal(user, text)


# ---------------------------------------------------------------------------
# Proactive notification scheduler (background thread)
# ---------------------------------------------------------------------------

def _send_nudge(user: dict, slot: str):
    send_message(
        user["user_id"],
        f"⏰ Just checking in!\n\n{_status_text(user)}",
    )
    db.mark_notified(user["user_id"], local_date_str(), slot)


def _send_bedtime_summary(user: dict):
    user_id = user["user_id"]
    totals = db.day_totals(user_id, local_date_str())
    consumed = int(totals["calories"])
    target = user["daily_calories"]
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
    send_message(user_id, msg)
    db.mark_notified(user_id, local_date_str(), "bedtime")


def scheduler_loop():
    """Wake periodically and fire any due nudges / bedtime summaries."""
    while True:
        try:
            now = now_local()
            date = local_date_str(now)
            for user in db.all_users():
                uid = user["user_id"]
                # Hourly check-in slots.
                for hour in config.NUDGE_HOURS:
                    if now.hour == hour:
                        slot = f"nudge_{hour}"
                        if not db.was_notified(uid, date, slot):
                            _send_nudge(user, slot)
                # Bedtime wrap-up.
                if user.get("sleep_hour") is not None and now.hour == user["sleep_hour"]:
                    if not db.was_notified(uid, date, "bedtime"):
                        _send_bedtime_summary(user)
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
