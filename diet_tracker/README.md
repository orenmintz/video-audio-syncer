# 🥗 Telegram Diet Tracker

A personal calorie tracker. You set up your profile in a web **dashboard**, and
use **Telegram** only to log what you eat.

- **Set up in the dashboard** — enter your details (age, weight, height, goal,
  bedtime, ...) and it works out how many calories you should eat per day
  (Mifflin–St Jeor BMR → activity → goal).
- **Log by texting** — write *"2 eggs and toast with butter"* to the bot and Claude
  estimates the calories and macros, then adds them to today's total.
- **Stay on track** — ask `/status` anytime, or let the bot proactively nudge you
  during the day with how many calories you have left and what you could still eat
  before bed.
- **Review progress** — the dashboard shows your last weeks: daily calories vs
  target, deficit/surplus, how many days you went to bed under target, and your
  current streak.

## How it works

```
   Dashboard (Streamlit)              Telegram  ──►  bot.py ──► Claude (meal → kcal)
   set profile, see charts                │              │
            │                             │              │
            └──────────►  diet_tracker.db (SQLite)  ◄─────┘
```

The bot and the dashboard are two separate processes that share one SQLite file.
You connect a Telegram chat to a dashboard profile **once** with `/link <code>`.

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

### 5. Start the dashboard and set up your profile
```bash
streamlit run dashboard.py
```
On the **Setup** page, fill in your details and click *Save profile*. It shows
your daily target and a 6-character **connect code**.

### 6. Run the bot (separate terminal, same folder + same env vars)
```bash
python bot.py
```
In Telegram, open your bot and send **`/link YOURCODE`** (the code from the
dashboard). After that, just text it what you eat.

## Telegram commands

| Command | What it does |
|---------|--------------|
| `/link CODE` | Connect this chat to your dashboard profile (one time) |
| *(any text)* | Log it as a meal — e.g. "big mac meal with a coke" |
| `/status` | Calories left today + what you can still eat before bed |
| `/today` | List the meals you logged today |
| `/summary` | Your last 7 days at a glance |
| `/undo` | Remove the last meal you logged |
| `/help` | Show the command list |

All profile changes (weight, goal, bedtime, ...) are made in the dashboard, never
in Telegram.

## Files

| File | Purpose |
|------|---------|
| `config.py` | Reads all settings from environment variables |
| `nutrition.py` | BMR / TDEE / target calorie math (pure, no I/O) |
| `meal_ai.py` | Claude calls: meal → calories, and food suggestions |
| `database.py` | SQLite storage shared by bot + dashboard |
| `telegram_api.py` | Tiny Telegram Bot API client (long polling) |
| `bot.py` | The bot: meal logging, commands, notifications |
| `dashboard.py` | Streamlit: profile setup + progress dashboard |

## Hosting it 24/7 (Railway)

Locally the app only works while your terminals are open. To run it around the
clock, deploy to [Railway](https://railway.app) — it runs the bot and dashboard
together in one service (`run_all.py`).

1. Push this repo to GitHub (already done if you're reading this there).
2. On Railway: **New Project → Deploy from GitHub repo** → pick this repo and the
   branch.
3. Service **Settings → Root Directory** = `diet_tracker`.
4. Add a **Volume** mounted at `/data` (so your database survives redeploys).
5. **Variables** tab — add:
   - `TELEGRAM_BOT_TOKEN`
   - `ANTHROPIC_API_KEY`
   - `DIET_TZ` = `Asia/Jerusalem` (your timezone)
   - `DIET_DB_PATH` = `/data/diet_tracker.db`
   - `DASH_PASSWORD` = a password of your choice (protects the public dashboard)
6. Deploy. Under **Settings → Networking → Generate Domain** to get your dashboard
   URL. Open it, enter your password, set up your profile, and `/link` it to Telegram.

`run_all.py` auto-restarts the bot or dashboard if either crashes.

## Notes

- The calorie estimates are AI approximations of typical portion sizes — great for
  tracking trends, not a substitute for a food scale or medical advice.
- Locally your data stays in `diet_tracker.db`. When hosted, it lives on the
  Railway volume you mounted.
- Set `DASH_PASSWORD` whenever the dashboard is reachable from the internet — it's
  the only thing standing between strangers and your profile/data.
