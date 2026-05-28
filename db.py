# db.py
import os
from pathlib import Path
from datetime import datetime, timedelta

import aiosqlite


DB_NAME = os.getenv("DB_PATH", str(Path(__file__).with_name("habits.db")))


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def yesterday_str() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


async def init_db():
    Path(DB_NAME).parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                habit_name TEXT NOT NULL,
                created_date TEXT NOT NULL,
                last_completed_date TEXT,
                streak INTEGER DEFAULT 0,
                total_completed INTEGER DEFAULT 0,
                goal_days INTEGER DEFAULT 30,
                reset_date TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminder_settings (
                user_id INTEGER PRIMARY KEY,
                enabled BOOLEAN DEFAULT 0,
                reminder_time TEXT DEFAULT "15:00"
            )
        """)

        await db.commit()


async def save_habit(user_id: int, habit_name: str, goal_days: int = 30):

    created_date = datetime.now().strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO habits 
            (user_id, habit_name, created_date, last_completed_date, streak, total_completed, goal_days)
            VALUES (?, ?, ?, NULL, 0, 0, ?)
        """, (user_id, habit_name, created_date, goal_days))
        await db.commit()


async def get_user_habits(user_id: int):
    await refresh_missed_streaks(user_id)

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, habit_name, created_date, streak, total_completed, last_completed_date, goal_days
            FROM habits 
            WHERE user_id = ?
            ORDER BY streak DESC
        """, (user_id,))
        return await cursor.fetchall()


async def mark_habit_completed(user_id: int, habit_id: int):
    today = today_str()

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT last_completed_date, streak, goal_days, habit_name 
            FROM habits 
            WHERE id = ? AND user_id = ?
        """, (habit_id, user_id))
        result = await cursor.fetchone()

        if not result:
            return False, None

        last_completed, current_streak, goal_days, habit_name = result

        if last_completed == today:
            return False, None

        yesterday = yesterday_str()

        new_streak = 1 if last_completed != yesterday else current_streak + 1

        achieved_goal = new_streak >= goal_days

        await db.execute("""
            UPDATE habits 
            SET last_completed_date = ?,
                streak = ?,
                total_completed = total_completed + 1,
                goal_days = ?
            WHERE id = ? AND user_id = ?
        """, (today, new_streak, goal_days, habit_id, user_id))

        await db.commit()

        return True, (achieved_goal, habit_name, new_streak, goal_days)


async def reset_habit_streak(user_id: int, habit_id: int):
    today = today_str()

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE habits 
            SET streak = 0,
            total_completed = 0,
            last_completed_date = NULL,
                reset_date = ?
            WHERE id = ? AND user_id = ?
        """, (today, habit_id, user_id))
        await db.commit()
    return True


async def update_habit_name(user_id: int, habit_id: int, new_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor=await db.execute("""
            UPDATE habits 
            SET habit_name = ?
            WHERE id = ? AND user_id = ?
        """, (new_name, habit_id, user_id))
        await db.commit()

        rowcount=cursor.rowcount
        return rowcount>0


async def get_all_users_with_habits():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT DISTINCT user_id FROM habits")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def delete_habit_from_db(user_id: int, habit_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "DELETE FROM habits WHERE id = ? AND user_id = ?",
            (habit_id, user_id)
        )
        await db.commit()
    return True


async def refresh_missed_streaks(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE habits
            SET streak = 0,
                reset_date = ?
            WHERE user_id = ?
              AND streak > 0
              AND last_completed_date IS NOT NULL
              AND last_completed_date < ?
        """, (today_str(), user_id, yesterday_str()))
        await db.commit()


async def set_reminder_settings(user_id: int, enabled: bool, reminder_time: str = "15:00"):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO reminder_settings (user_id, enabled, reminder_time)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                enabled = excluded.enabled,
                reminder_time = excluded.reminder_time
        """, (user_id, enabled, reminder_time))
        await db.commit()


async def get_reminder_settings(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT enabled, reminder_time 
            FROM reminder_settings 
            WHERE user_id = ?
        """, (user_id,))
        row = await cursor.fetchone()
        if row:
            return {"enabled": bool(row[0]), "reminder_time": row[1] or "15:00"}

        await set_reminder_settings(user_id, True, reminder_time="15:00")
        return {"enabled": True, "reminder_time": "15:00"}


async def get_users_by_reminder_time(reminder_time: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT user_id, reminder_time
            FROM reminder_settings
            WHERE enabled = 1
        """)
        rows = await cursor.fetchall()

    return [
        user_id
        for user_id, times in rows
        if reminder_time in parse_reminder_times(times)
    ]


def parse_reminder_times(value: str | None) -> list[str]:
    if not value:
        return ["15:00"]

    times = []
    for item in value.split(","):
        item = item.strip()
        if item:
            times.append(item)

    return sorted(set(times)) or ["15:00"]


async def init_reminder_table():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminder_settings (
                user_id INTEGER PRIMARY KEY,
                enabled BOOLEAN DEFAULT 0,
                reminder_time TEXT DEFAULT "15:00"
            )
        """)
        await db.commit()
