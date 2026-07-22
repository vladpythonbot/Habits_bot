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


def date_range(days: int) -> list[str]:
    today = datetime.now().date()
    return [
        (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(days - 1, -1, -1)
    ]


def parse_date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def normalize_habit_goal(goal_type: str | None, goal_value: int | None = None) -> tuple[str, int]:
    if goal_type == "weekdays":
        return "weekdays", 5
    if goal_type == "weekly":
        value = goal_value if isinstance(goal_value, int) else 3
        return "weekly", max(1, min(7, value))
    return "daily", 7


def expects_daily_check(goal_type: str | None, check_date) -> bool:
    if goal_type == "weekdays":
        return check_date.weekday() < 5
    if goal_type == "weekly":
        return False
    return True


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
            CREATE TABLE IF NOT EXISTS habit_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                group_name TEXT NOT NULL,
                emoji TEXT NOT NULL DEFAULT '🎯',
                created_at TEXT NOT NULL,
                UNIQUE(user_id, group_name)
            )
        """)

        cursor = await db.execute("PRAGMA table_info(habits)")
        habit_columns = {row[1] for row in await cursor.fetchall()}
        if "group_id" not in habit_columns:
            await db.execute("ALTER TABLE habits ADD COLUMN group_id INTEGER")
        if "goal_type" not in habit_columns:
            await db.execute("ALTER TABLE habits ADD COLUMN goal_type TEXT NOT NULL DEFAULT 'daily'")
        if "goal_value" not in habit_columns:
            await db.execute("ALTER TABLE habits ADD COLUMN goal_value INTEGER NOT NULL DEFAULT 7")
        if "archived_at" not in habit_columns:
            await db.execute("ALTER TABLE habits ADD COLUMN archived_at TEXT")

        cursor = await db.execute("PRAGMA table_info(habit_groups)")
        group_columns = {row[1] for row in await cursor.fetchall()}
        if "emoji" not in group_columns:
            await db.execute("ALTER TABLE habit_groups ADD COLUMN emoji TEXT NOT NULL DEFAULT '🎯'")

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
            CREATE TABLE IF NOT EXISTS habit_misses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                habit_id INTEGER NOT NULL,
                missed_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, habit_id, missed_date)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS habit_reminders (
                user_id INTEGER NOT NULL,
                habit_id INTEGER NOT NULL,
                enabled BOOLEAN DEFAULT 1,
                reminder_time TEXT NOT NULL,
                PRIMARY KEY(user_id, habit_id)
            )
        """)

        await db.execute("""
            INSERT OR IGNORE INTO habit_logs (user_id, habit_id, completed_date, created_at)
            SELECT user_id, id, last_completed_date, datetime('now')
            FROM habits
            WHERE last_completed_date IS NOT NULL
        """)

        await delete_archived_habits(db)

        await db.commit()


async def delete_archived_habits(db):
    await db.execute("""
        DELETE FROM habit_logs
        WHERE EXISTS (
            SELECT 1
            FROM habits
            WHERE habits.id = habit_logs.habit_id
              AND habits.user_id = habit_logs.user_id
              AND habits.archived_at IS NOT NULL
        )
    """)
    await db.execute("""
        DELETE FROM habit_misses
        WHERE EXISTS (
            SELECT 1
            FROM habits
            WHERE habits.id = habit_misses.habit_id
              AND habits.user_id = habit_misses.user_id
              AND habits.archived_at IS NOT NULL
        )
    """)
    await db.execute("""
        DELETE FROM habit_reminders
        WHERE EXISTS (
            SELECT 1
            FROM habits
            WHERE habits.id = habit_reminders.habit_id
              AND habits.user_id = habit_reminders.user_id
              AND habits.archived_at IS NOT NULL
        )
    """)
    await db.execute("DELETE FROM habits WHERE archived_at IS NOT NULL")


async def save_habit(
    user_id: int,
    habit_name: str,
    goal_days: int = 30,
    group_id: int | None = None,
):
    goal_type, goal_value = normalize_habit_goal("daily", 7)
    async with aiosqlite.connect(DB_NAME) as db:
        if group_id is not None:
            cursor = await db.execute("""
                SELECT 1
                FROM habit_groups
                WHERE id = ? AND user_id = ?
            """, (group_id, user_id))
            if not await cursor.fetchone():
                group_id = None

        await db.execute("""
            INSERT INTO habits
            (user_id, habit_name, created_date, last_completed_date, streak, total_completed, goal_days, group_id, goal_type, goal_value)
            VALUES (?, ?, ?, NULL, 0, 0, ?, ?, ?, ?)
        """, (user_id, habit_name, today_str(), goal_days, group_id, goal_type, goal_value))
        await db.commit()


async def get_user_habits(
    user_id: int,
    group_id: int | None = None,
    ungrouped_only: bool = False,
):
    await refresh_missed_streaks(user_id)

    conditions = ["user_id = ?", "archived_at IS NULL"]
    params: list[int] = [user_id]
    if group_id is not None:
        conditions.append("group_id = ?")
        params.append(group_id)
    elif ungrouped_only:
        conditions.append("group_id IS NULL")

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(f"""
            SELECT id, habit_name, created_date, streak, total_completed, last_completed_date, goal_days, group_id,
                   goal_type, goal_value
            FROM habits
            WHERE {' AND '.join(conditions)}
            ORDER BY created_date ASC, id ASC
        """, params)
        return await cursor.fetchall()


async def create_habit_group(user_id: int, group_name: str, emoji: str = "🎯") -> tuple[bool, int | None]:
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            cursor = await db.execute("""
                INSERT INTO habit_groups (user_id, group_name, emoji, created_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, group_name, emoji, datetime.now().isoformat(timespec="seconds")))
            await db.commit()
            return True, cursor.lastrowid
        except aiosqlite.IntegrityError:
            return False, None


async def get_habit_groups(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT g.id, g.group_name, COUNT(h.id), g.emoji
            FROM habit_groups AS g
            LEFT JOIN habits AS h
                ON h.group_id = g.id AND h.user_id = g.user_id AND h.archived_at IS NULL
            WHERE g.user_id = ?
            GROUP BY g.id, g.group_name, g.emoji, g.created_at
            ORDER BY g.created_at ASC, g.id ASC
        """, (user_id,))
        return await cursor.fetchall()


async def get_habit_group(user_id: int, group_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, group_name, 0, emoji
            FROM habit_groups
            WHERE id = ? AND user_id = ?
        """, (group_id, user_id))
        return await cursor.fetchone()


async def set_habit_group(user_id: int, habit_id: int, group_id: int | None) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        if group_id is not None:
            cursor = await db.execute("""
                SELECT 1
                FROM habit_groups
                WHERE id = ? AND user_id = ?
            """, (group_id, user_id))
            if not await cursor.fetchone():
                return False

        cursor = await db.execute("""
            UPDATE habits
            SET group_id = ?
            WHERE id = ? AND user_id = ? AND archived_at IS NULL
        """, (group_id, habit_id, user_id))
        await db.commit()
        return cursor.rowcount > 0


async def update_habit_group_emoji(user_id: int, group_id: int, emoji: str) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            UPDATE habit_groups
            SET emoji = ?
            WHERE id = ? AND user_id = ?
        """, (emoji, group_id, user_id))
        await db.commit()
        return cursor.rowcount > 0


async def delete_habit_group(user_id: int, group_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            UPDATE habits
            SET group_id = NULL
            WHERE group_id = ? AND user_id = ?
        """, (group_id, user_id))
        cursor = await db.execute("""
            DELETE FROM habit_groups
            WHERE id = ? AND user_id = ?
        """, (group_id, user_id))
        await db.commit()
        return cursor.rowcount > 0


async def mark_habit_completed(user_id: int, habit_id: int):
    today = today_str()

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT last_completed_date, streak, habit_name
            FROM habits
            WHERE id = ? AND user_id = ? AND archived_at IS NULL
        """, (habit_id, user_id))
        result = await cursor.fetchone()

        if not result:
            return False, None

        last_completed, current_streak, habit_name = result

        if last_completed == today:
            return False, None

        new_streak = current_streak + 1 if last_completed == yesterday_str() else 1
        await db.execute("""
            INSERT OR IGNORE INTO habit_logs (user_id, habit_id, completed_date, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, habit_id, today, datetime.now().isoformat(timespec="seconds")))

        await db.execute("""
            DELETE FROM habit_misses
            WHERE user_id = ? AND habit_id = ? AND missed_date = ?
        """, (user_id, habit_id, today))

        await db.execute("""
            UPDATE habits
            SET last_completed_date = ?,
                streak = ?,
                total_completed = total_completed + 1
            WHERE id = ? AND user_id = ?
        """, (today, new_streak, habit_id, user_id))

        await db.commit()

    return True, {
        "habit_name": habit_name,
        "streak": new_streak,
    }


async def unmark_habit_completed(user_id: int, habit_id: int):
    today = today_str()

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT habit_name
            FROM habits
            WHERE id = ? AND user_id = ? AND archived_at IS NULL
        """, (habit_id, user_id))
        row = await cursor.fetchone()
        if not row:
            return False, None

        habit_name = row[0]
        cursor = await db.execute("""
            DELETE FROM habit_logs
            WHERE user_id = ? AND habit_id = ? AND completed_date = ?
        """, (user_id, habit_id, today))
        if cursor.rowcount == 0:
            return False, None

        cursor = await db.execute("""
            SELECT completed_date
            FROM habit_logs
            WHERE user_id = ? AND habit_id = ?
            ORDER BY completed_date DESC
        """, (user_id, habit_id))
        completed_dates = [item[0] for item in await cursor.fetchall()]

        last_completed_date = completed_dates[0] if completed_dates else None
        streak = 0
        if completed_dates:
            expected = parse_date(completed_dates[0])
            for completed_date in completed_dates:
                if parse_date(completed_date) != expected:
                    break
                streak += 1
                expected -= timedelta(days=1)

        await db.execute("""
            UPDATE habits
            SET last_completed_date = ?,
                streak = ?,
                total_completed = ?
            WHERE id = ? AND user_id = ?
        """, (last_completed_date, streak, len(completed_dates), habit_id, user_id))

        await db.commit()

    return True, {"habit_name": habit_name}


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
        await db.execute("DELETE FROM habit_misses WHERE habit_id = ? AND user_id = ?", (habit_id, user_id))
        await db.execute("DELETE FROM habit_reminders WHERE habit_id = ? AND user_id = ?", (habit_id, user_id))
        await db.execute("DELETE FROM habits WHERE id = ? AND user_id = ?", (habit_id, user_id))
        await db.commit()
    return True


async def update_habit_goal(user_id: int, habit_id: int, goal_type: str, goal_value: int | None = None) -> bool:
    normalized_type, normalized_value = normalize_habit_goal(goal_type, goal_value)
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            UPDATE habits
            SET goal_type = ?,
                goal_value = ?
            WHERE id = ? AND user_id = ? AND archived_at IS NULL
        """, (normalized_type, normalized_value, habit_id, user_id))
        await db.commit()
        return cursor.rowcount > 0


async def record_habit_miss(user_id: int, habit_id: int, missed_date: str | None = None) -> bool:
    missed_date = missed_date or today_str()

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            INSERT OR IGNORE INTO habit_misses (user_id, habit_id, missed_date, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, habit_id, missed_date, datetime.now().isoformat(timespec="seconds")))
        await db.commit()
        return cursor.rowcount > 0


async def get_missed_habit_ids(user_id: int, missed_date: str | None = None) -> set[int]:
    missed_date = missed_date or today_str()

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT habit_id
            FROM habit_misses
            WHERE user_id = ? AND missed_date = ?
        """, (user_id, missed_date))
        rows = await cursor.fetchall()
        return {row[0] for row in rows}


async def is_habit_missed(user_id: int, habit_id: int, missed_date: str | None = None) -> bool:
    missed_date = missed_date or today_str()

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT 1
            FROM habit_misses
            WHERE user_id = ? AND habit_id = ? AND missed_date = ?
        """, (user_id, habit_id, missed_date))
        return await cursor.fetchone() is not None


async def refresh_missed_streaks(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, created_date, last_completed_date, streak, reset_date, goal_type
            FROM habits
            WHERE user_id = ? AND archived_at IS NULL
        """, (user_id,))
        habits = await cursor.fetchall()

        yesterday = parse_date(yesterday_str())
        now = datetime.now().isoformat(timespec="seconds")

        for habit_id, created_date, last_completed_date, streak, reset_date, goal_type in habits:
            start = parse_date(created_date)

            if reset_date:
                start = max(start, parse_date(reset_date) + timedelta(days=1))

            if last_completed_date:
                start = max(start, parse_date(last_completed_date) + timedelta(days=1))

            current = start
            has_expected_miss = False
            while current <= yesterday:
                if not expects_daily_check(goal_type, current):
                    current += timedelta(days=1)
                    continue
                missed_date = current.strftime("%Y-%m-%d")
                await db.execute("""
                    INSERT OR IGNORE INTO habit_misses (user_id, habit_id, missed_date, created_at)
                    VALUES (?, ?, ?, ?)
                """, (user_id, habit_id, missed_date, now))
                has_expected_miss = True
                current += timedelta(days=1)

            if streak > 0 and last_completed_date and has_expected_miss:
                await db.execute("""
                    UPDATE habits
                    SET streak = 0,
                        reset_date = ?
                    WHERE id = ? AND user_id = ?
                """, (today_str(), habit_id, user_id))

        await db.commit()


async def get_user_stats(user_id: int, days: int = 30, group_id: int | None = None):
    habits = await get_user_habits(user_id, group_id=group_id, ungrouped_only=group_id is None)
    dates = date_range(days)
    today = today_str()
    completed_dates = [date for date in dates if date != today]
    first_date = dates[0]
    group_condition = "h.group_id = ?" if group_id is not None else "h.group_id IS NULL"
    group_params = [group_id] if group_id is not None else []

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(f"""
            SELECT l.completed_date, COUNT(*)
            FROM habit_logs AS l
            JOIN habits AS h
                ON h.id = l.habit_id AND h.user_id = l.user_id
            WHERE l.user_id = ?
              AND l.completed_date >= ?
              AND {group_condition}
            GROUP BY l.completed_date
        """, [user_id, first_date, *group_params])
        daily_rows = await cursor.fetchall()

        cursor = await db.execute(f"""
            SELECT COUNT(*)
            FROM habit_logs AS l
            JOIN habits AS h
                ON h.id = l.habit_id AND h.user_id = l.user_id
            WHERE l.user_id = ?
              AND {group_condition}
        """, [user_id, *group_params])
        total_completed = (await cursor.fetchone())[0]

        cursor = await db.execute(f"""
            SELECT COUNT(*)
            FROM habit_misses AS m
            JOIN habits AS h
                ON h.id = m.habit_id AND h.user_id = m.user_id
            WHERE m.user_id = ?
              AND m.missed_date >= ?
              AND {group_condition}
        """, [user_id, first_date, *group_params])
        recorded_misses = (await cursor.fetchone())[0]

    daily_done = {date: count for date, count in daily_rows}
    possible = 0

    for habit in habits:
        created = parse_date(habit[2])
        for date in completed_dates:
            if parse_date(date) >= created:
                possible += 1

    period_completed = sum(count for date, count in daily_done.items() if date in completed_dates)
    completion_rate = round(period_completed / possible * 100) if possible else 0
    missed_days = recorded_misses
    today_done = daily_done.get(today, 0)

    return {
        "habits_count": len(habits),
        "total_completed": total_completed,
        "period_completed": period_completed,
        "possible": possible,
        "completion_rate": completion_rate,
        "missed_days": missed_days,
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
        cursor = await db.execute("SELECT DISTINCT user_id FROM habits WHERE archived_at IS NULL")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def set_habit_reminder(user_id: int, habit_id: int, reminder_time: str, enabled: bool = True) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT 1
            FROM habits
            WHERE user_id = ? AND id = ? AND archived_at IS NULL
        """, (user_id, habit_id))
        if not await cursor.fetchone():
            return False

        await db.execute("""
            INSERT INTO habit_reminders (user_id, habit_id, enabled, reminder_time)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, habit_id) DO UPDATE SET
                enabled = excluded.enabled,
                reminder_time = excluded.reminder_time
        """, (user_id, habit_id, enabled, reminder_time))
        await db.commit()
        return True


async def disable_habit_reminder(user_id: int, habit_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            UPDATE habit_reminders
            SET enabled = 0
            WHERE user_id = ? AND habit_id = ?
        """, (user_id, habit_id))
        await db.commit()
        return cursor.rowcount > 0


async def get_habit_reminder(user_id: int, habit_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT enabled, reminder_time
            FROM habit_reminders
            WHERE user_id = ? AND habit_id = ?
        """, (user_id, habit_id))
        row = await cursor.fetchone()
        if not row:
            return None

        return {"enabled": bool(row[0]), "reminder_time": row[1]}


async def get_due_habit_reminders(reminder_time: str):
    today = datetime.now().date()
    today_value = today.strftime("%Y-%m-%d")
    week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT h.user_id, h.id, h.habit_name, h.last_completed_date, r.reminder_time, h.goal_type, h.goal_value
            FROM habit_reminders AS r
            JOIN habits AS h
                ON h.user_id = r.user_id AND h.id = r.habit_id
            WHERE r.enabled = 1
              AND h.archived_at IS NULL
        """)
        rows = await cursor.fetchall()

        due = []
        for user_id, habit_id, habit_name, last_completed_date, times, goal_type, goal_value in rows:
            if reminder_time not in parse_reminder_times(times):
                continue
            if last_completed_date == today_value:
                continue
            if goal_type == "weekdays" and today.weekday() >= 5:
                continue
            if goal_type == "weekly":
                cursor = await db.execute("""
                    SELECT COUNT(*)
                    FROM habit_logs
                    WHERE user_id = ? AND habit_id = ? AND completed_date >= ?
                """, (user_id, habit_id, week_start))
                completed_this_week = (await cursor.fetchone())[0]
                if completed_this_week >= max(1, min(7, int(goal_value or 3))):
                    continue
            due.append((user_id, habit_id, habit_name, last_completed_date))

    return due


def parse_reminder_times(value: str | None) -> list[str]:
    if not value:
        return ["15:00"]

    times = []
    for item in value.split(","):
        item = item.strip()
        if item:
            times.append(item)

    return sorted(set(times)) or ["15:00"]
