# db.py
import aiosqlite
from datetime import datetime

DB_NAME = "habits.db"


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE habits (
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
        await init_reminder_table()
        await db.commit()
    print("✅ Таблица habits успешно пересоздана")


async def save_habit(user_id: int, habit_name: str, goal_days: int = 30):

    created_date = datetime.now().strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO habits 
            (user_id, habit_name, created_date, last_completed_date, streak, total_completed, goal_days)
            VALUES (?, ?, ?, NULL, 0, 0, ?)
        """, (user_id, habit_name, created_date, goal_days))
        await db.commit()

    print(f"Привычка '{habit_name}' (цель: {goal_days} дней) добавлена для пользователя {user_id}")


async def get_user_habits(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, habit_name, created_date, streak, total_completed, last_completed_date, goal_days
            FROM habits 
            WHERE user_id = ?
            ORDER BY streak DESC
        """, (user_id,))
        return await cursor.fetchall()


async def mark_habit_completed(user_id: int, habit_id: int):
    today = datetime.now().strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT last_completed_date, streak 
            FROM habits 
            WHERE id = ? AND user_id = ?
        """, (habit_id, user_id))
        result = await cursor.fetchone()

        if not result:
            return False

        last_completed, current_streak = result

        if last_completed == today:
            return False

        from datetime import timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        new_streak = 1 if last_completed != yesterday else current_streak + 1

        await db.execute("""
            UPDATE habits 
            SET last_completed_date = ?,
                streak = ?,
                total_completed = total_completed + 1
            WHERE id = ? AND user_id = ?
        """, (today, new_streak, habit_id, user_id))

        await db.commit()
        return True


async def reset_habit_streak(user_id: int, habit_id: int):
    today = datetime.now().strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE habits 
            SET streak = 0,
            last_completed_date = NULL,
                reset_date = ?
            WHERE id = ? AND user_id = ?
        """, (today, habit_id, user_id))
        await db.commit()
    return True



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


async def set_reminder_settings(user_id: int, enabled: bool, reminder_time: str = "22:00"):
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
            return {"enabled": row[0], "reminder_time": row[1]}
        return {"enabled": False, "reminder_time": "15:00"}


async def init_reminder_table():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminder_settings (
                user_id INTEGER PRIMARY KEY,
                enabled BOOLEAN DEFAULT FALSE,
                reminder_time TEXT DEFAULT "15:00"
            )
        """)
        await db.commit()