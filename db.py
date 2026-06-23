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
                created_at TEXT NOT NULL,
                UNIQUE(user_id, group_name)
            )
        """)

        cursor = await db.execute("PRAGMA table_info(habits)")
        habit_columns = {row[1] for row in await cursor.fetchall()}
        if "group_id" not in habit_columns:
            await db.execute("ALTER TABLE habits ADD COLUMN group_id INTEGER")
        if "progress_unit" not in habit_columns:
            await db.execute("ALTER TABLE habits ADD COLUMN progress_unit TEXT")

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
            CREATE TABLE IF NOT EXISTS habit_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                habit_id INTEGER NOT NULL,
                progress_date TEXT NOT NULL,
                value REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, habit_id, progress_date)
            )
        """)

        await db.execute("""
            INSERT OR IGNORE INTO habit_logs (user_id, habit_id, completed_date, created_at)
            SELECT user_id, id, last_completed_date, datetime('now')
            FROM habits
            WHERE last_completed_date IS NOT NULL
        """)

        await db.commit()


async def save_habit(
    user_id: int,
    habit_name: str,
    goal_days: int = 30,
    group_id: int | None = None,
    progress_unit: str | None = None,
):
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
            (user_id, habit_name, created_date, last_completed_date, streak, total_completed, goal_days, group_id, progress_unit)
            VALUES (?, ?, ?, NULL, 0, 0, ?, ?, ?)
        """, (user_id, habit_name, today_str(), goal_days, group_id, progress_unit))
        await db.commit()


async def get_user_habits(user_id: int, group_id: int | None = None, ungrouped_only: bool = False):
    await refresh_missed_streaks(user_id)

    conditions = ["user_id = ?"]
    params: list[int] = [user_id]
    if group_id is not None:
        conditions.append("group_id = ?")
        params.append(group_id)
    elif ungrouped_only:
        conditions.append("group_id IS NULL")

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(f"""
            SELECT id, habit_name, created_date, streak, total_completed, last_completed_date, goal_days, group_id, progress_unit
            FROM habits
            WHERE {' AND '.join(conditions)}
            ORDER BY created_date ASC, id ASC
        """, params)
        return await cursor.fetchall()


async def create_habit_group(user_id: int, group_name: str) -> tuple[bool, int | None]:
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            cursor = await db.execute("""
                INSERT INTO habit_groups (user_id, group_name, created_at)
                VALUES (?, ?, ?)
            """, (user_id, group_name, datetime.now().isoformat(timespec="seconds")))
            await db.commit()
            return True, cursor.lastrowid
        except aiosqlite.IntegrityError:
            return False, None


async def get_habit_groups(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT g.id, g.group_name, COUNT(h.id)
            FROM habit_groups AS g
            LEFT JOIN habits AS h
                ON h.group_id = g.id AND h.user_id = g.user_id
            WHERE g.user_id = ?
            GROUP BY g.id, g.group_name, g.created_at
            ORDER BY g.created_at ASC, g.id ASC
        """, (user_id,))
        return await cursor.fetchall()


async def get_habit_group(user_id: int, group_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, group_name
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
            WHERE id = ? AND user_id = ?
        """, (group_id, habit_id, user_id))
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
            WHERE id = ? AND user_id = ?
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
            WHERE id = ? AND user_id = ?
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
        await db.execute("DELETE FROM habit_progress WHERE habit_id = ? AND user_id = ?", (habit_id, user_id))
        await db.execute("DELETE FROM habits WHERE id = ? AND user_id = ?", (habit_id, user_id))
        await db.commit()
    return True


async def save_habit_progress(
    user_id: int,
    habit_id: int,
    value: float,
    unit: str | None = None,
) -> bool:
    now = datetime.now().isoformat(timespec="seconds")

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT progress_unit
            FROM habits
            WHERE id = ? AND user_id = ?
        """, (habit_id, user_id))
        row = await cursor.fetchone()
        if not row:
            return False

        saved_unit = unit or row[0]
        if not saved_unit:
            return False

        if unit:
            await db.execute("""
                UPDATE habits
                SET progress_unit = ?
                WHERE id = ? AND user_id = ?
            """, (unit, habit_id, user_id))

        await db.execute("""
            INSERT INTO habit_progress
                (user_id, habit_id, progress_date, value, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, habit_id, progress_date) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
        """, (user_id, habit_id, today_str(), value, now, now))
        await db.commit()
        return True


async def get_habit_progress_summary(user_id: int, habit_id: int) -> dict | None:
    dates_30 = date_range(30)
    dates_7 = set(dates_30[-7:])
    previous_dates_7 = set(dates_30[-14:-7])
    today = today_str()

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT progress_unit
            FROM habits
            WHERE id = ? AND user_id = ?
        """, (habit_id, user_id))
        habit = await cursor.fetchone()
        if not habit:
            return None

        cursor = await db.execute("""
            SELECT progress_date, value
            FROM habit_progress
            WHERE user_id = ? AND habit_id = ? AND progress_date >= ?
            ORDER BY progress_date ASC
        """, (user_id, habit_id, dates_30[0]))
        rows = await cursor.fetchall()

    values = {progress_date: value for progress_date, value in rows}
    seven_values = [value for date, value in values.items() if date in dates_7]
    previous_seven_values = [value for date, value in values.items() if date in previous_dates_7]
    thirty_values = list(values.values())
    best_date = None
    best_value = None
    if values:
        best_date, best_value = max(values.items(), key=lambda item: item[1])

    seven_days = sum(seven_values)
    previous_seven_days = sum(previous_seven_values)
    change_percent = None
    if previous_seven_days > 0:
        change_percent = round((seven_days - previous_seven_days) / previous_seven_days * 100)

    return {
        "unit": habit[0],
        "today": values.get(today),
        "seven_days": seven_days,
        "previous_seven_days": previous_seven_days,
        "thirty_days": sum(thirty_values),
        "days_recorded": len(values),
        "days_recorded_7": len(seven_values),
        "days_recorded_30": len(thirty_values),
        "average_7": seven_days / len(seven_values) if seven_values else None,
        "average_30": sum(thirty_values) / len(thirty_values) if thirty_values else None,
        "best_value": best_value,
        "best_date": best_date,
        "change_percent": change_percent,
    }


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
            SELECT id, created_date, last_completed_date, streak, reset_date
            FROM habits
            WHERE user_id = ?
        """, (user_id,))
        habits = await cursor.fetchall()

        yesterday = parse_date(yesterday_str())
        now = datetime.now().isoformat(timespec="seconds")

        for habit_id, created_date, last_completed_date, streak, reset_date in habits:
            start = parse_date(created_date)

            if reset_date:
                start = max(start, parse_date(reset_date) + timedelta(days=1))

            if last_completed_date:
                start = max(start, parse_date(last_completed_date) + timedelta(days=1))

            current = start
            while current <= yesterday:
                missed_date = current.strftime("%Y-%m-%d")
                await db.execute("""
                    INSERT OR IGNORE INTO habit_misses (user_id, habit_id, missed_date, created_at)
                    VALUES (?, ?, ?, ?)
                """, (user_id, habit_id, missed_date, now))
                current += timedelta(days=1)

            if streak > 0 and last_completed_date and parse_date(last_completed_date) < yesterday:
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
        cursor = await db.execute("SELECT DISTINCT user_id FROM habits")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def set_habit_reminder(user_id: int, habit_id: int, reminder_time: str, enabled: bool = True) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT 1
            FROM habits
            WHERE user_id = ? AND id = ?
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
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT h.user_id, h.id, h.habit_name, h.last_completed_date, r.reminder_time
            FROM habit_reminders AS r
            JOIN habits AS h
                ON h.user_id = r.user_id AND h.id = r.habit_id
            WHERE r.enabled = 1
        """)
        rows = await cursor.fetchall()

    return [
        (user_id, habit_id, habit_name, last_completed_date)
        for user_id, habit_id, habit_name, last_completed_date, times in rows
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
