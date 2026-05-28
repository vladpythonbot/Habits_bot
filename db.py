# db.py
import os
from pathlib import Path
from datetime import datetime, timedelta

import aiosqlite


DB_NAME = os.getenv("DB_PATH", str(Path(__file__).with_name("habits.db")))

ACHIEVEMENTS = {
    "first_step": ("Первый шаг", "Отмечена первая привычка"),
    "streak_3": ("Три дня", "Серия 3 дня подряд"),
    "streak_7": ("Неделя огня", "Серия 7 дней подряд"),
    "goal_reached": ("Цель взята", "Достигнута цель привычки"),
    "collector_3": ("Система", "Добавлены 3 привычки"),
    "perfect_day": ("Чистый день", "Все привычки отмечены сегодня"),
}


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def yesterday_str() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def date_range(days: int) -> list[str]:
    today = datetime.now().date()
    return [
        (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(days - 1, -1, -1)
    ]


def parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def level_from_xp(xp: int) -> int:
    return max(1, xp // 100 + 1)


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
            CREATE TABLE IF NOT EXISTS habit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                habit_id INTEGER NOT NULL,
                completed_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, habit_id, completed_date)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                updated_at TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_achievements (
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                unlocked_at TEXT NOT NULL,
                PRIMARY KEY(user_id, code)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminder_settings (
                user_id INTEGER PRIMARY KEY,
                enabled BOOLEAN DEFAULT 0,
                reminder_time TEXT DEFAULT "15:00"
            )
        """)

        await db.execute("""
            INSERT OR IGNORE INTO habit_logs (user_id, habit_id, completed_date, created_at)
            SELECT user_id, id, last_completed_date, datetime('now')
            FROM habits
            WHERE last_completed_date IS NOT NULL
        """)

        await db.commit()


async def save_habit(user_id: int, habit_name: str, goal_days: int = 30):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO habits
            (user_id, habit_name, created_date, last_completed_date, streak, total_completed, goal_days)
            VALUES (?, ?, ?, NULL, 0, 0, ?)
        """, (user_id, habit_name, today_str(), goal_days))
        await db.commit()

    await unlock_achievement_if_needed(user_id, "collector_3")


async def get_user_habits(user_id: int):
    await refresh_missed_streaks(user_id)

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, habit_name, created_date, streak, total_completed, last_completed_date, goal_days
            FROM habits
            WHERE user_id = ?
            ORDER BY streak DESC, created_date ASC
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

        new_streak = current_streak + 1 if last_completed == yesterday_str() else 1
        achieved_goal = new_streak >= goal_days

        await db.execute("""
            INSERT OR IGNORE INTO habit_logs (user_id, habit_id, completed_date, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, habit_id, today, datetime.now().isoformat(timespec="seconds")))

        await db.execute("""
            UPDATE habits
            SET last_completed_date = ?,
                streak = ?,
                total_completed = total_completed + 1
            WHERE id = ? AND user_id = ?
        """, (today, new_streak, habit_id, user_id))

        await db.commit()

    xp_added, level_up = await add_xp(user_id, 10 + min(new_streak, 10))
    achievements = await evaluate_achievements(user_id, new_streak, achieved_goal)

    return True, {
        "habit_name": habit_name,
        "streak": new_streak,
        "goal_days": goal_days,
        "achieved_goal": achieved_goal,
        "xp_added": xp_added,
        "level_up": level_up,
        "achievements": achievements,
    }


async def reset_habit_streak(user_id: int, habit_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE habits
            SET streak = 0,
                last_completed_date = NULL,
                reset_date = ?
            WHERE id = ? AND user_id = ?
        """, (today_str(), habit_id, user_id))
        await db.commit()
    return True


async def update_habit_name(user_id: int, habit_id: int, new_name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            UPDATE habits
            SET habit_name = ?
            WHERE id = ? AND user_id = ?
        """, (new_name, habit_id, user_id))
        await db.commit()
        return cursor.rowcount > 0


async def delete_habit_from_db(user_id: int, habit_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM habit_logs WHERE habit_id = ? AND user_id = ?", (habit_id, user_id))
        await db.execute("DELETE FROM habits WHERE id = ? AND user_id = ?", (habit_id, user_id))
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


async def add_xp(user_id: int, amount: int):
    now = datetime.now().isoformat(timespec="seconds")

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT xp, level FROM user_stats WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()

        old_xp, old_level = row if row else (0, 1)
        new_xp = old_xp + amount
        new_level = level_from_xp(new_xp)

        await db.execute("""
            INSERT INTO user_stats (user_id, xp, level, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                xp = excluded.xp,
                level = excluded.level,
                updated_at = excluded.updated_at
        """, (user_id, new_xp, new_level, now))
        await db.commit()

    return amount, new_level if new_level > old_level else None


async def get_user_level(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT xp, level FROM user_stats WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()

    if not row:
        return {"xp": 0, "level": 1, "next_level_xp": 100}

    xp, level = row
    return {"xp": xp, "level": level, "next_level_xp": level * 100}


async def unlock_achievement_if_needed(user_id: int, code: str):
    if code not in ACHIEVEMENTS:
        return None

    now = datetime.now().isoformat(timespec="seconds")

    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("""
                INSERT INTO user_achievements (user_id, code, unlocked_at)
                VALUES (?, ?, ?)
            """, (user_id, code, now))
            await db.commit()
        except aiosqlite.IntegrityError:
            return None

    title, description = ACHIEVEMENTS[code]
    await add_xp(user_id, 25)
    return {"code": code, "title": title, "description": description}


async def evaluate_achievements(user_id: int, streak: int, achieved_goal: bool):
    unlocked = []

    for code, condition in [
        ("first_step", True),
        ("streak_3", streak >= 3),
        ("streak_7", streak >= 7),
        ("goal_reached", achieved_goal),
        ("collector_3", True),
        ("perfect_day", True),
    ]:
        if not condition:
            continue

        if code == "collector_3":
            habits = await get_user_habits(user_id)
            if len(habits) < 3:
                continue

        if code == "perfect_day":
            habits = await get_user_habits(user_id)
            if not habits or any(h[5] != today_str() for h in habits):
                continue

        achievement = await unlock_achievement_if_needed(user_id, code)
        if achievement:
            unlocked.append(achievement)

    return unlocked


async def get_achievements(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT code, unlocked_at
            FROM user_achievements
            WHERE user_id = ?
            ORDER BY unlocked_at DESC
        """, (user_id,))
        rows = await cursor.fetchall()

    result = []
    for code, unlocked_at in rows:
        title, description = ACHIEVEMENTS.get(code, (code, ""))
        result.append({"code": code, "title": title, "description": description, "unlocked_at": unlocked_at})

    return result


async def get_user_stats(user_id: int, days: int = 30):
    habits = await get_user_habits(user_id)
    dates = date_range(days)
    first_date = dates[0]

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT completed_date, COUNT(*)
            FROM habit_logs
            WHERE user_id = ? AND completed_date >= ?
            GROUP BY completed_date
        """, (user_id, first_date))
        daily_rows = await cursor.fetchall()

        cursor = await db.execute("""
            SELECT COUNT(*)
            FROM habit_logs
            WHERE user_id = ?
        """, (user_id,))
        total_completed = (await cursor.fetchone())[0]

    daily_done = {date: count for date, count in daily_rows}
    possible = 0

    for habit in habits:
        created = parse_date(habit[2])
        for date in dates:
            if parse_date(date) >= created:
                possible += 1

    period_completed = sum(daily_done.values())
    completion_rate = round(period_completed / possible * 100) if possible else 0
    missed_days = max(possible - period_completed, 0)
    best_streak = max((h[3] for h in habits), default=0)
    today_done = daily_done.get(today_str(), 0)

    return {
        "habits_count": len(habits),
        "total_completed": total_completed,
        "period_completed": period_completed,
        "possible": possible,
        "completion_rate": completion_rate,
        "missed_days": missed_days,
        "best_streak": best_streak,
        "today_done": today_done,
        "dates": dates,
        "daily_done": daily_done,
        "habits": habits,
    }


async def get_habit_logs(user_id: int, habit_id: int | None = None, days: int = 30):
    dates = date_range(days)
    params = [user_id, dates[0]]
    query = """
        SELECT habit_id, completed_date
        FROM habit_logs
        WHERE user_id = ? AND completed_date >= ?
    """

    if habit_id is not None:
        query += " AND habit_id = ?"
        params.append(habit_id)

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(query, params)
        return await cursor.fetchall()


async def get_all_users_with_habits():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT DISTINCT user_id FROM habits")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


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
    await init_db()
