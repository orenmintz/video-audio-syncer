"""
Streamlit dashboard for the diet tracker.

Run alongside the bot with:
    streamlit run dashboard.py

Reads the same SQLite database the bot writes to, so it always reflects what
you've been logging on Telegram.
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import database as db

st.set_page_config(page_title="Diet Tracker", page_icon="🥗", layout="wide")

db.init_db()


# ---------------------------------------------------------------------------
# User selection
# ---------------------------------------------------------------------------

users = db.all_users()

st.title("🥗 Diet Tracker Dashboard")

if not users:
    st.info(
        "No onboarded users yet. Open Telegram, message your bot, and send "
        "**/start** to set up your profile. Then refresh this page."
    )
    st.stop()

names = {f"{u['name']} (#{u['user_id']})": u for u in users}
choice = st.sidebar.selectbox("Who are we looking at?", list(names.keys()))
user = names[choice]
window = st.sidebar.slider("History window (days)", 7, 90, 30)

st.sidebar.markdown("---")
st.sidebar.markdown(
    f"**Goal:** {user['goal'].title()}  \n"
    f"**Daily target:** {user['daily_calories']} kcal  \n"
    f"**Protein:** {user['protein_g']} g  \n"
    f"**Weight:** {user['weight_kg']:g} kg  \n"
    f"**Sleeps at:** {user['sleep_hour']:02d}:00"
)


# ---------------------------------------------------------------------------
# Build a continuous date-indexed dataframe (fill gaps with 0)
# ---------------------------------------------------------------------------

target = user["daily_calories"]
rows = db.history(user["user_id"], days=window)
hist = pd.DataFrame(rows)

today = datetime.now().date()
date_range = pd.date_range(end=today, periods=window).date
frame = pd.DataFrame({"local_date": [d.isoformat() for d in date_range]})

if not hist.empty:
    frame = frame.merge(hist, on="local_date", how="left")
else:
    frame["calories"] = 0
    frame["protein_g"] = 0
    frame["meal_count"] = 0

frame[["calories", "protein_g", "meal_count"]] = (
    frame[["calories", "protein_g", "meal_count"]].fillna(0)
)
frame["calories"] = frame["calories"].astype(int)
frame["over_target"] = frame["calories"] > target
frame["logged"] = frame["meal_count"] > 0


# ---------------------------------------------------------------------------
# Headline metrics
# ---------------------------------------------------------------------------

logged_days = frame[frame["logged"]]
under_days = int((logged_days["calories"] <= target).sum())
avg_cal = int(logged_days["calories"].mean()) if not logged_days.empty else 0

# Current streak of consecutive logged days ending today, all at/under target.
streak = 0
for _, r in frame.iloc[::-1].iterrows():
    if r["logged"] and r["calories"] <= target:
        streak += 1
    else:
        break

today_row = frame.iloc[-1]
today_consumed = int(today_row["calories"])

c1, c2, c3, c4 = st.columns(4)
c1.metric("Today", f"{today_consumed} kcal", f"{target - today_consumed:+d} vs target")
c2.metric("Days under target", f"{under_days} / {len(logged_days)}")
c3.metric("Avg (logged days)", f"{avg_cal} kcal")
c4.metric("🔥 Current streak", f"{streak} days")


# ---------------------------------------------------------------------------
# Calories over time
# ---------------------------------------------------------------------------

st.subheader("Daily calories vs target")

chart_df = frame.set_index("local_date")[["calories"]].copy()
chart_df["target"] = target
st.bar_chart(chart_df["calories"], color="#4CAF50", height=280)
st.caption(
    f"Target line: {target} kcal/day. "
    f"Bars above the target are days you went over."
)

# Surplus/deficit relative to target, only for logged days.
st.subheader("Daily deficit / surplus (logged days)")
deficit = logged_days.set_index("local_date")["calories"] - target
if not deficit.empty:
    st.bar_chart(deficit, height=240)
    st.caption("Below zero = under target (good for losing weight).")
else:
    st.write("No logged days in this window yet.")


# ---------------------------------------------------------------------------
# Today's meals
# ---------------------------------------------------------------------------

st.subheader("Today's meals")
meals = db.meals_for_date(user["user_id"], today.isoformat())
if meals:
    mdf = pd.DataFrame(meals)[
        ["description", "calories", "protein_g", "carbs_g", "fat_g", "logged_at"]
    ]
    st.dataframe(mdf, use_container_width=True, hide_index=True)
else:
    st.write("Nothing logged today yet.")


# ---------------------------------------------------------------------------
# Full history table
# ---------------------------------------------------------------------------

with st.expander("Full day-by-day history"):
    show = frame[frame["logged"]][["local_date", "calories", "protein_g", "meal_count"]]
    show = show.sort_values("local_date", ascending=False)
    show["status"] = show["calories"].apply(lambda c: "✅ under" if c <= target else "🔴 over")
    st.dataframe(show, use_container_width=True, hide_index=True)
