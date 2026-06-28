"""
SQLite persistence layer shared by the Streamlit dashboard and the Telegram bot.

Design:
  profiles  - your diet profile + targets. Created and edited in the DASHBOARD.
              A profile is connected to Telegram by a one-time `link_code`.
  meals     - one row per logged meal (added by the bot from your Telegram texts).
  notif_log - remembers which proactive nudges were already sent today.

Telegram is only used to log food; all profile details are set in the dashboard.
The bot finds the right profile from the Telegram chat that sent the message
(after you connect them once with `/link <code>`).
"""

import json
import secrets
import sqlite3
import string
import threading
from contextlib import contextmanager
from datetime import datetime

from config import DB_PATH

_lock = threading.Lock()


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _lock, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                name               TEXT,
                sex                TEXT,
                age                INTEGER,
                height_cm          REAL,
                weight_kg          REAL,
                activity_level     TEXT,
                goal               TEXT,
                goal_rate          REAL,
                sleep_hour         INTEGER,
                daily_calories     INTEGER,
                protein_g          INTEGER,
                carbs_g            INTEGER,
                fat_g              INTEGER,
                telegram_chat_id   INTEGER UNIQUE,
                link_code          TEXT,
                created_at         TEXT
            );

            CREATE TABLE IF NOT EXISTS meals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id      INTEGER NOT NULL,
                description     TEXT,
                calories        INTEGER,
                protein_g       REAL,
                carbs_g         REAL,
                fat_g           REAL,
                items_json      TEXT,
                local_date      TEXT,
                logged_at       TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_meals_profile_date
                ON meals (profile_id, local_date);

            CREATE TABLE IF NOT EXISTS notif_log (
                profile_id  INTEGER NOT NULL,
                local_date  TEXT NOT NULL,
                slot        TEXT NOT NULL,
                sent_at     TEXT,
                PRIMARY KEY (profile_id, local_date, slot)
            );
            """
        )


def _new_link_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


# --- Profiles (managed by the dashboard) -------------------------------------

PROFILE_FIELDS = (
    "name", "sex", "age", "height_cm", "weight_kg", "activity_level",
    "goal", "goal_rate", "sleep_hour", "daily_calories", "protein_g",
    "carbs_g", "fat_g",
)


def create_profile(**fields) -> int:
    """Create a new profile and return its id. Generates a Telegram link code."""
    cols = [k for k in fields if k in PROFILE_FIELDS]
    with _lock, _connect() as conn:
        placeholders = ", ".join(["?"] * (len(cols) + 2))
        conn.execute(
            f"INSERT INTO profiles ({', '.join(cols)}, link_code, created_at) "
            f"VALUES ({placeholders})",
            (*[fields[c] for c in cols], _new_link_code(), datetime.utcnow().isoformat()),
        )
        return conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def update_profile(profile_id: int, **fields):
    cols = [k for k in fields if k in PROFILE_FIELDS]
    if not cols:
        return
    with _lock, _connect() as conn:
        sets = ", ".join(f"{c} = ?" for c in cols)
        conn.execute(
            f"UPDATE profiles SET {sets} WHERE id = ?",
            (*[fields[c] for c in cols], profile_id),
        )


def get_profile(profile_id: int):
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        return dict(row) if row else None


def list_profiles():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM profiles ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


# --- Telegram linking --------------------------------------------------------

def get_profile_by_chat(chat_id: int):
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE telegram_chat_id = ?", (chat_id,)
        ).fetchone()
        return dict(row) if row else None


def link_chat(code: str, chat_id: int):
    """Connect a Telegram chat to the profile holding `code`. Returns it or None."""
    code = code.strip().upper()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM profiles WHERE link_code = ?", (code,)
        ).fetchone()
        if not row:
            return None
        # Detach this chat from any other profile, then attach it here.
        conn.execute(
            "UPDATE profiles SET telegram_chat_id = NULL WHERE telegram_chat_id = ?",
            (chat_id,),
        )
        conn.execute(
            "UPDATE profiles SET telegram_chat_id = ? WHERE id = ?",
            (chat_id, row["id"]),
        )
        return dict(conn.execute(
            "SELECT * FROM profiles WHERE id = ?", (row["id"],)
        ).fetchone())


def active_profiles():
    """Profiles connected to Telegram and ready for notifications."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM profiles WHERE telegram_chat_id IS NOT NULL "
            "AND daily_calories IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]


# --- Meals -------------------------------------------------------------------

def add_meal(profile_id: int, description: str, estimate: dict, local_date: str):
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO meals
                (profile_id, description, calories, protein_g, carbs_g, fat_g,
                 items_json, local_date, logged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                description,
                estimate["total_calories"],
                estimate["total_protein_g"],
                estimate["total_carbs_g"],
                estimate["total_fat_g"],
                json.dumps(estimate.get("items", [])),
                local_date,
                datetime.utcnow().isoformat(),
            ),
        )


def meals_for_date(profile_id: int, local_date: str):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM meals WHERE profile_id = ? AND local_date = ? ORDER BY logged_at",
            (profile_id, local_date),
        ).fetchall()
        return [dict(r) for r in rows]


def day_totals(profile_id: int, local_date: str) -> dict:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(calories), 0)  AS calories,
                COALESCE(SUM(protein_g), 0) AS protein_g,
                COALESCE(SUM(carbs_g), 0)   AS carbs_g,
                COALESCE(SUM(fat_g), 0)     AS fat_g,
                COUNT(*)                    AS meal_count
            FROM meals WHERE profile_id = ? AND local_date = ?
            """,
            (profile_id, local_date),
        ).fetchone()
        return dict(row)


def delete_last_meal(profile_id: int, local_date: str) -> bool:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT id FROM meals WHERE profile_id = ? AND local_date = ? "
            "ORDER BY logged_at DESC LIMIT 1",
            (profile_id, local_date),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM meals WHERE id = ?", (row["id"],))
        return True


def history(profile_id: int, days: int = 30):
    """Per-day calorie totals for the last `days`, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT local_date,
                   SUM(calories)  AS calories,
                   SUM(protein_g) AS protein_g,
                   COUNT(*)       AS meal_count
            FROM meals
            WHERE profile_id = ?
            GROUP BY local_date
            ORDER BY local_date DESC
            LIMIT ?
            """,
            (profile_id, days),
        ).fetchall()
        return [dict(r) for r in rows]


# --- Notification log --------------------------------------------------------

def was_notified(profile_id: int, local_date: str, slot: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM notif_log WHERE profile_id = ? AND local_date = ? AND slot = ?",
            (profile_id, local_date, slot),
        ).fetchone()
        return row is not None


def mark_notified(profile_id: int, local_date: str, slot: str):
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO notif_log (profile_id, local_date, slot, sent_at) "
            "VALUES (?, ?, ?, ?)",
            (profile_id, local_date, slot, datetime.utcnow().isoformat()),
        )
