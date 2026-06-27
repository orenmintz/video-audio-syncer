"""
SQLite persistence layer shared by the Telegram bot and the Streamlit dashboard.

Three tables:
  users       - one row per Telegram user, including their diet targets and the
                in-progress onboarding state.
  meals       - one row per logged meal, with AI-estimated calories/macros.
  notif_log   - remembers which proactive nudges were already sent (so the
                scheduler doesn't spam the same slot twice in a day).
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from config import DB_PATH

# SQLite connections can't be shared across threads, so we serialise writes with
# a lock and open short-lived connections per operation.
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
            CREATE TABLE IF NOT EXISTS users (
                user_id            INTEGER PRIMARY KEY,
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
                onboarding_state   TEXT,
                onboarded          INTEGER DEFAULT 0,
                created_at         TEXT
            );

            CREATE TABLE IF NOT EXISTS meals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                description     TEXT,
                calories        INTEGER,
                protein_g       REAL,
                carbs_g         REAL,
                fat_g           REAL,
                items_json      TEXT,
                local_date      TEXT,
                logged_at       TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_meals_user_date
                ON meals (user_id, local_date);

            CREATE TABLE IF NOT EXISTS notif_log (
                user_id     INTEGER NOT NULL,
                local_date  TEXT NOT NULL,
                slot        TEXT NOT NULL,
                sent_at     TEXT,
                PRIMARY KEY (user_id, local_date, slot)
            );
            """
        )


# --- Users -------------------------------------------------------------------

def get_user(user_id: int):
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def upsert_user(user_id: int, **fields):
    """Insert the user if new, otherwise update only the provided columns."""
    with _lock, _connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO users (user_id, created_at) VALUES (?, ?)",
                (user_id, datetime.utcnow().isoformat()),
            )
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE users SET {cols} WHERE user_id = ?",
                (*fields.values(), user_id),
            )


def set_onboarding_state(user_id: int, state: dict | None):
    upsert_user(user_id, onboarding_state=json.dumps(state) if state else None)


def get_onboarding_state(user_id: int) -> dict | None:
    user = get_user(user_id)
    if user and user.get("onboarding_state"):
        return json.loads(user["onboarding_state"])
    return None


def all_users():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE onboarded = 1"
        ).fetchall()
        return [dict(r) for r in rows]


# --- Meals -------------------------------------------------------------------

def add_meal(user_id: int, description: str, estimate: dict, local_date: str):
    with _lock, _connect() as conn:
        conn.execute(
            """
            INSERT INTO meals
                (user_id, description, calories, protein_g, carbs_g, fat_g,
                 items_json, local_date, logged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
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


def meals_for_date(user_id: int, local_date: str):
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM meals WHERE user_id = ? AND local_date = ? ORDER BY logged_at",
            (user_id, local_date),
        ).fetchall()
        return [dict(r) for r in rows]


def day_totals(user_id: int, local_date: str) -> dict:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(calories), 0)  AS calories,
                COALESCE(SUM(protein_g), 0) AS protein_g,
                COALESCE(SUM(carbs_g), 0)   AS carbs_g,
                COALESCE(SUM(fat_g), 0)     AS fat_g,
                COUNT(*)                    AS meal_count
            FROM meals WHERE user_id = ? AND local_date = ?
            """,
            (user_id, local_date),
        ).fetchone()
        return dict(row)


def delete_last_meal(user_id: int, local_date: str) -> bool:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT id FROM meals WHERE user_id = ? AND local_date = ? ORDER BY logged_at DESC LIMIT 1",
            (user_id, local_date),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM meals WHERE id = ?", (row["id"],))
        return True


def history(user_id: int, days: int = 30):
    """Per-day calorie totals for the last `days`, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT local_date,
                   SUM(calories)  AS calories,
                   SUM(protein_g) AS protein_g,
                   COUNT(*)       AS meal_count
            FROM meals
            WHERE user_id = ?
            GROUP BY local_date
            ORDER BY local_date DESC
            LIMIT ?
            """,
            (user_id, days),
        ).fetchall()
        return [dict(r) for r in rows]


# --- Notification log --------------------------------------------------------

def was_notified(user_id: int, local_date: str, slot: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM notif_log WHERE user_id = ? AND local_date = ? AND slot = ?",
            (user_id, local_date, slot),
        ).fetchone()
        return row is not None


def mark_notified(user_id: int, local_date: str, slot: str):
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO notif_log (user_id, local_date, slot, sent_at) VALUES (?, ?, ?, ?)",
            (user_id, local_date, slot, datetime.utcnow().isoformat()),
        )
