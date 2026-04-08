import aiosqlite
from datetime import datetime

DB_NAME = "habits.db"


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DROP TABLE IF EXISTS habits")

        await db.execute("""
            CREATE TABLE habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                habit_name TEXT NOT NULL,
                created_date TEXT NOT NULL,
                last_completed_date TEXT,
                streak INTEGER DEFAULT 0,
                total_completed INTEGER DEFAULT 0,
                reset_date TEXT,
                goal_days INTEGER DEFAULT 30
            )
        """)
        await db.commit()


async def save_habit(user_id: int, habit_name: str, start_date: str = None):
    if start_date is None:
        start_date = datetime.now().strftime("%Y-%m-%d")

    created_date = datetime.now().strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO habits 
            (user_id, habit_name, start_date, created_date, last_completed_date, streak, total_completed)
            VALUES (?, ?, ?, ?, NULL, 0, 0)
        """, (user_id, habit_name, start_date, created_date))
        await db.commit()

async def get_user_habits(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, habit_name,created_date, streak, total_completed, last_completed_date 
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

        if last_completed != yesterday:
            new_streak = 1
        else:
            new_streak = current_streak + 1

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
                reset_date = ?
            WHERE id = ? AND user_id = ?
        """, (today, habit_id, user_id))
        await db.commit()
    return True

async def delete_habit_from_db(user_id: int, habit_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "DELETE FROM habits WHERE id = ? AND user_id = ?",
            (habit_id, user_id)
        )
        await db.commit()
    return True

