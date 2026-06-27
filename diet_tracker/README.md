# 🥗 Telegram Diet Tracker

A personal calorie tracker you control entirely from Telegram, with a good-looking
web dashboard for reviewing your progress.

- **Onboarding** — a short Q&A in Telegram works out how many calories you should
  eat per day (Mifflin–St Jeor BMR → activity → goal).
- **Log by texting** — write *"2 eggs and toast with butter"* and Claude estimates
  the calories and macros, then adds them to today's total.
- **Stay on track** — ask `/status` anytime, or let the bot proactively nudge you
  during the day with how many calories you have left and what you could still eat
  before bed.
- **Dashboard** — a Streamlit page shows your last weeks: daily calories vs target,
  deficit/surplus, how many days you went to bed under target, and your current streak.

## How it works

```
        Telegram  ──►  bot.py  ──►  Claude (meal → calories)
                          │
                          ▼
                    diet_tracker.db  (SQLite)
                          ▲
                          │
                     dashboard.py  (Streamlit)
```

The bot and the dashboard are two separate processes that share one SQLite file.

## Setup

### 1. Create a Telegram bot
Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, follow the
prompts, and copy the **bot token** it gives you.

### 2. Get an Anthropic API key
Create one at <https://console.anthropic.com>. This is used to read your meals.

### 3. Install dependencies
```bash
cd diet_tracker
pip install -r requirements.txt
```

### 4. Set environment variables
```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-your-bot-token"
export ANTHROPIC_API_KEY="sk-ant-..."
export DIET_TZ="Asia/Jerusalem"        # your timezone (default UTC)
# optional:
# export DIET_AI_MODEL="claude-haiku-4-5"   # cheaper/faster meal reading
# export DIET_NUDGE_HOURS="12,16,20"        # when to get check-in reminders
```

### 5. Run the bot
```bash
python bot.py
```
Now open Telegram, find your bot, and send **/start**.

### 6. Run the dashboard (optional, separate terminal)
```bash
streamlit run dashboard.py
```

## Telegram commands

| Command | What it does |
|---------|--------------|
| *(any text)* | Log it as a meal — e.g. "big mac meal with a coke" |
| `/status` | Calories left today + what you can still eat before bed |
| `/today` | List the meals you logged today |
| `/summary` | Your last 7 days at a glance |
| `/undo` | Remove the last meal you logged |
| `/reset` | Redo the onboarding questions |
| `/help` | Show the command list |

## Files

| File | Purpose |
|------|---------|
| `config.py` | Reads all settings from environment variables |
| `nutrition.py` | BMR / TDEE / target calorie math (pure, no I/O) |
| `meal_ai.py` | Claude calls: meal → calories, and food suggestions |
| `database.py` | SQLite storage shared by bot + dashboard |
| `telegram_api.py` | Tiny Telegram Bot API client (long polling) |
| `bot.py` | The bot: onboarding, logging, commands, notifications |
| `dashboard.py` | Streamlit progress dashboard |

## Notes

- The calorie estimates are AI approximations of typical portion sizes — great for
  tracking trends, not a substitute for a food scale or medical advice.
- All your data stays in the local `diet_tracker.db` file.
- The bot must be running for it to receive messages and send notifications.
  For 24/7 use, run it on a small always-on server (or under `systemd`,
  `pm2`, `screen`, etc.).
