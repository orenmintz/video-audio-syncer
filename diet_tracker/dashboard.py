"""
Streamlit dashboard for the diet tracker.

Run alongside the bot with:
    streamlit run dashboard.py

This is where you set up your profile (all your personal details) and review
your progress. Telegram is only used to log food.

Two pages (pick in the sidebar):
  • Setup     - create/edit your profile and get the code to connect Telegram.
  • Progress  - charts, streaks, and days under target.
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import config
import database as db
from nutrition import compute_targets

st.set_page_config(page_title="Diet Tracker", page_icon="🥗", layout="wide")
db.init_db()


def _require_password():
    """Gate the whole dashboard behind a password when DASH_PASSWORD is set."""
    if not config.DASH_PASSWORD:
        return  # no password configured (local use)
    if st.session_state.get("auth_ok"):
        return
    st.title("🔒 Diet Tracker")
    entered = st.text_input("Password", type="password")
    if not entered:
        st.stop()
    if entered == config.DASH_PASSWORD:
        st.session_state["auth_ok"] = True
        st.rerun()
    else:
        st.error("Wrong password.")
        st.stop()


_require_password()

ACTIVITY_LABELS = {
    "sedentary": "Sedentary (little/no exercise)",
    "light": "Light (1-3 days/week)",
    "moderate": "Moderate (3-5 days/week)",
    "active": "Active (6-7 days/week)",
    "very_active": "Very active (physical job)",
}
GOAL_LABELS = {"lose": "Lose weight", "maintain": "Maintain weight", "gain": "Gain weight"}


# ===========================================================================
# Setup page
# ===========================================================================

def setup_page():
    st.title("⚙️ Setup your profile")
    st.caption("Enter all your details here. Telegram is only for logging meals.")

    profiles = db.list_profiles()
    options = {"➕ Create a new profile": None}
    options.update({f"{p['name']} (#{p['id']})": p["id"] for p in profiles})
    choice = st.selectbox("Profile", list(options.keys()))
    profile_id = options[choice]
    existing = db.get_profile(profile_id) if profile_id else {}

    def cur(key, default):
        val = existing.get(key)
        return default if val is None else val

    with st.form("profile_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Name", value=cur("name", ""))
            sex = st.selectbox(
                "Sex", ["male", "female"],
                index=0 if cur("sex", "male") == "male" else 1,
                format_func=lambda s: s.title(),
            )
            age = st.number_input("Age (years)", 10, 100, int(cur("age", 30)))
            height = st.number_input("Height (cm)", 100.0, 250.0, float(cur("height_cm", 175.0)))
            weight = st.number_input("Weight (kg)", 30.0, 400.0, float(cur("weight_kg", 75.0)))
        with c2:
            activity = st.selectbox(
                "Activity level", list(ACTIVITY_LABELS.keys()),
                index=list(ACTIVITY_LABELS).index(cur("activity_level", "moderate")),
                format_func=lambda k: ACTIVITY_LABELS[k],
            )
            goal = st.selectbox(
                "Goal", list(GOAL_LABELS.keys()),
                index=list(GOAL_LABELS).index(cur("goal", "lose")),
                format_func=lambda k: GOAL_LABELS[k],
            )
            goal_rate = st.number_input(
                "Target rate (kg per week)", 0.0, 1.5, float(cur("goal_rate", 0.5)), step=0.05,
                help="How fast to lose/gain. 0.5 is a healthy pace. Ignored for 'maintain'.",
            )
            sleep_hour = st.number_input(
                "Usual bedtime (hour, 0-23)", 0, 23, int(cur("sleep_hour", 23)),
                help="Used for the 'before bed' reminders and the nightly summary.",
            )
        submitted = st.form_submit_button("💾 Save profile", type="primary")

    if submitted:
        if not name.strip():
            st.error("Please enter a name.")
            return
        targets = compute_targets(
            sex=sex, age=int(age), height_cm=float(height), weight_kg=float(weight),
            activity_level=activity, goal=goal,
            goal_rate_kg_per_week=0.0 if goal == "maintain" else float(goal_rate),
        )
        fields = dict(
            name=name.strip(), sex=sex, age=int(age), height_cm=float(height),
            weight_kg=float(weight), activity_level=activity, goal=goal,
            goal_rate=0.0 if goal == "maintain" else float(goal_rate),
            sleep_hour=int(sleep_hour), daily_calories=targets.daily_calories,
            protein_g=targets.protein_g, carbs_g=targets.carbs_g, fat_g=targets.fat_g,
        )
        if profile_id:
            db.update_profile(profile_id, **fields)
            st.success("Profile updated.")
        else:
            profile_id = db.create_profile(**fields)
            st.success("Profile created!")
        existing = db.get_profile(profile_id)

        st.markdown(
            f"### 🎯 Daily target: **{targets.daily_calories} kcal**\n"
            f"- Protein: **{targets.protein_g} g**\n"
            f"- Carbs: **{targets.carbs_g} g**\n"
            f"- Fat: **{targets.fat_g} g**\n\n"
            f"_Maintenance ≈ {targets.tdee} kcal · resting burn ≈ {targets.bmr} kcal_"
        )

    # Telegram connection box (shown whenever an existing profile is selected).
    if existing:
        st.divider()
        st.subheader("📱 Connect Telegram")
        if existing.get("telegram_chat_id"):
            st.success(
                f"This profile is connected to Telegram (chat id "
                f"{existing['telegram_chat_id']}). Just text the bot what you eat!"
            )
            st.caption("To move it to a different Telegram account, send the code below from that account.")
        else:
            st.info("Not connected yet.")
        st.markdown(
            f"In Telegram, open your bot and send:\n\n"
            f"### `/link {existing['link_code']}`"
        )


# ===========================================================================
# Progress page
# ===========================================================================

def progress_page():
    st.title("🥗 Your progress")
    profiles = db.list_profiles()
    if not profiles:
        st.info("No profile yet. Go to the **Setup** page (sidebar) to create one.")
        return

    names = {f"{p['name']} (#{p['id']})": p for p in profiles}
    choice = st.sidebar.selectbox("Profile", list(names.keys()))
    user = names[choice]
    window = st.sidebar.slider("History window (days)", 7, 90, 30)

    if user.get("daily_calories") is None:
        st.warning("This profile has no target yet — finish it on the Setup page.")
        return

    st.sidebar.markdown(
        f"**Goal:** {user['goal'].title()}  \n"
        f"**Daily target:** {user['daily_calories']} kcal  \n"
        f"**Protein:** {user['protein_g']} g  \n"
        f"**Weight:** {user['weight_kg']:g} kg  \n"
        f"**Sleeps at:** {user['sleep_hour']:02d}:00"
    )

    target = user["daily_calories"]
    rows = db.history(user["id"], days=window)
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
    frame["logged"] = frame["meal_count"] > 0

    logged_days = frame[frame["logged"]]
    under_days = int((logged_days["calories"] <= target).sum())
    avg_cal = int(logged_days["calories"].mean()) if not logged_days.empty else 0

    streak = 0
    for _, r in frame.iloc[::-1].iterrows():
        if r["logged"] and r["calories"] <= target:
            streak += 1
        else:
            break

    today_consumed = int(frame.iloc[-1]["calories"])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Today", f"{today_consumed} kcal", f"{target - today_consumed:+d} vs target")
    c2.metric("Days under target", f"{under_days} / {len(logged_days)}")
    c3.metric("Avg (logged days)", f"{avg_cal} kcal")
    c4.metric("🔥 Current streak", f"{streak} days")

    st.subheader("Daily calories vs target")
    chart_df = frame.set_index("local_date")[["calories"]].copy()
    st.bar_chart(chart_df["calories"], color="#4CAF50", height=280)
    st.caption(f"Target: {target} kcal/day. Bars above it are days you went over.")

    st.subheader("Daily deficit / surplus (logged days)")
    deficit = logged_days.set_index("local_date")["calories"] - target
    if not deficit.empty:
        st.bar_chart(deficit, height=240)
        st.caption("Below zero = under target (good for losing weight).")
    else:
        st.write("No logged days in this window yet.")

    st.subheader("Today's meals")
    meals = db.meals_for_date(user["id"], today.isoformat())
    if meals:
        mdf = pd.DataFrame(meals)[
            ["description", "calories", "protein_g", "carbs_g", "fat_g", "logged_at"]
        ]
        st.dataframe(mdf, use_container_width=True, hide_index=True)
    else:
        st.write("Nothing logged today yet.")

    with st.expander("Full day-by-day history"):
        show = logged_days[["local_date", "calories", "protein_g", "meal_count"]].copy()
        show = show.sort_values("local_date", ascending=False)
        show["status"] = show["calories"].apply(lambda c: "✅ under" if c <= target else "🔴 over")
        st.dataframe(show, use_container_width=True, hide_index=True)


# ===========================================================================
# Router
# ===========================================================================

page = st.sidebar.radio("Page", ["Setup", "Progress"])
st.sidebar.markdown("---")
if page == "Setup":
    setup_page()
else:
    progress_page()
