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


def recalculate_progress(completed_dates: list[str], goal_type: str | None = None) -> tuple[str | None, int, int]:
    last_completed_date = completed_dates[0] if completed_dates else None
    streak = 0
    if completed_dates:
        completed = {parse_date(date) for date in completed_dates}
        expected = parse_date(completed_dates[0])
        while True:
            if goal_type == "weekdays" and not expects_daily_check(goal_type, expected):
                expected -= timedelta(days=1)
                continue
            if expected not in completed:
                break
            streak += 1
            expected -= timedelta(days=1)

    return last_completed_date, streak, len(completed_dates)


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


def sleep_hours(sleep_out: str | None, sleep_up: str | None) -> float:
    if not sleep_out or not sleep_up:
        return 0.0
    try:
        start = datetime.strptime(sleep_out, "%H:%M")
        end = datetime.strptime(sleep_up, "%H:%M")
    except ValueError:
        return 0.0
    if end <= start:
        end += timedelta(days=1)
    return round((end - start).total_seconds() / 3600, 1)


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
        if "habit_position" not in habit_columns:
            await db.execute("ALTER TABLE habits ADD COLUMN habit_position INTEGER NOT NULL DEFAULT 0")
            await db.execute("""
                UPDATE habits
                SET habit_position = id
                WHERE habit_position = 0
            """)

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
            CREATE TABLE IF NOT EXISTS sleep_logs (
                user_id INTEGER NOT NULL,
                sleep_date TEXT NOT NULL,
                sleep_out TEXT NOT NULL,
                sleep_up TEXT NOT NULL,
                rate INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id, sleep_date)
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

        cursor = await db.execute("""
            SELECT COALESCE(MAX(habit_position), 0) + 1
            FROM habits
            WHERE user_id = ?
        """, (user_id,))
        position = (await cursor.fetchone())[0]

        await db.execute("""
            INSERT INTO habits
            (user_id, habit_name, created_date, last_completed_date, streak, total_completed, goal_days, group_id, goal_type, goal_value, habit_position)
            VALUES (?, ?, ?, NULL, 0, 0, ?, ?, ?, ?, ?)
        """, (user_id, habit_name, today_str(), goal_days, group_id, goal_type, goal_value, position))
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
            ORDER BY habit_position ASC, created_date ASC, id ASC
        """, params)
        return await cursor.fetchall()


async def move_habit(user_id: int, habit_id: int, direction: str) -> bool:
    if direction not in {"up", "down"}:
        return False

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT id, habit_position
            FROM habits
            WHERE user_id = ? AND id = ? AND archived_at IS NULL
        """, (user_id, habit_id))
        current = await cursor.fetchone()
        if not current:
            return False

        operator = "<" if direction == "up" else ">"
        order = "DESC" if direction == "up" else "ASC"
        cursor = await db.execute(f"""
            SELECT id, habit_position
            FROM habits
            WHERE user_id = ? AND archived_at IS NULL AND habit_position {operator} ?
            ORDER BY habit_position {order}, id {order}
            LIMIT 1
        """, (user_id, current[1]))
        neighbor = await cursor.fetchone()
        if not neighbor:
            return True

        await db.execute("UPDATE habits SET habit_position = ? WHERE id = ? AND user_id = ?", (neighbor[1], current[0], user_id))
        await db.execute("UPDATE habits SET habit_position = ? WHERE id = ? AND user_id = ?", (current[1], neighbor[0], user_id))
        await db.commit()
        return True


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


async def mark_habit_completed(user_id: int, habit_id: int, completed_date: str | None = None):
    completed_date = completed_date or today_str()

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT habit_name, created_date, goal_type
            FROM habits
            WHERE id = ? AND user_id = ? AND archived_at IS NULL
        """, (habit_id, user_id))
        result = await cursor.fetchone()

        if not result:
            return False, None

        habit_name, created_date, goal_type = result
        if parse_date(completed_date) < parse_date(created_date):
            return False, None

        cursor = await db.execute("""
            INSERT OR IGNORE INTO habit_logs (user_id, habit_id, completed_date, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, habit_id, completed_date, datetime.now().isoformat(timespec="seconds")))

        if cursor.rowcount == 0:
            return False, None

        await db.execute("""
            DELETE FROM habit_misses
            WHERE user_id = ? AND habit_id = ? AND missed_date = ?
        """, (user_id, habit_id, completed_date))

        cursor = await db.execute("""
            SELECT completed_date
            FROM habit_logs
            WHERE user_id = ? AND habit_id = ?
            ORDER BY completed_date DESC
        """, (user_id, habit_id))
        completed_dates = [item[0] for item in await cursor.fetchall()]
        last_completed_date, new_streak, total_completed = recalculate_progress(completed_dates, goal_type)

        await db.execute("""
            UPDATE habits
            SET last_completed_date = ?,
                streak = ?,
                total_completed = ?
            WHERE id = ? AND user_id = ? AND archived_at IS NULL
        """, (last_completed_date, new_streak, total_completed, habit_id, user_id))

        await db.commit()

    return True, {
        "habit_name": habit_name,
        "streak": new_streak,
    }


async def unmark_habit_completed(user_id: int, habit_id: int, completed_date: str | None = None):
    completed_date = completed_date or today_str()

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT habit_name, goal_type
            FROM habits
            WHERE id = ? AND user_id = ? AND archived_at IS NULL
        """, (habit_id, user_id))
        row = await cursor.fetchone()
        if not row:
            return False, None

        habit_name, goal_type = row
        cursor = await db.execute("""
            DELETE FROM habit_logs
            WHERE user_id = ? AND habit_id = ? AND completed_date = ?
        """, (user_id, habit_id, completed_date))
        if cursor.rowcount == 0:
            return False, None

        cursor = await db.execute("""
            SELECT completed_date
            FROM habit_logs
            WHERE user_id = ? AND habit_id = ?
            ORDER BY completed_date DESC
        """, (user_id, habit_id))
        completed_dates = [item[0] for item in await cursor.fetchall()]
        last_completed_date, streak, total_completed = recalculate_progress(completed_dates, goal_type)

        await db.execute("""
            UPDATE habits
            SET last_completed_date = ?,
                streak = ?,
                total_completed = ?
            WHERE id = ? AND user_id = ?
        """, (last_completed_date, streak, total_completed, habit_id, user_id))

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




async def get_habit_misses(user_id: int, habit_id: int | None = None, days: int = 30):
    dates = date_range(days)
    params = [user_id, dates[0]]
    query = """
        SELECT habit_id, missed_date
        FROM habit_misses
        WHERE user_id = ? AND missed_date >= ?
    """

    if habit_id is not None:
        query += " AND habit_id = ?"
        params.append(habit_id)

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(query, params)
        return await cursor.fetchall()


async def save_sleep_log(user_id: int, sleep_date: str, sleep_out: str, sleep_up: str, rate: int) -> bool:
    if not 1 <= rate <= 5:
        return False
    if sleep_hours(sleep_out, sleep_up) <= 0:
        return False

    now = datetime.now().isoformat(timespec="seconds")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO sleep_logs (user_id, sleep_date, sleep_out, sleep_up, rate, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, sleep_date) DO UPDATE SET
                sleep_out = excluded.sleep_out,
                sleep_up = excluded.sleep_up,
                rate = excluded.rate,
                updated_at = excluded.updated_at
        """, (user_id, sleep_date, sleep_out, sleep_up, rate, now, now))
        await db.commit()
        return True


async def get_sleep_stats(user_id: int, days: int = 7) -> list[dict]:
    dates = date_range(days)
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            SELECT sleep_date, sleep_out, sleep_up, rate
            FROM sleep_logs
            WHERE user_id = ? AND sleep_date >= ?
        """, (user_id, dates[0]))
        rows = {row[0]: row for row in await cursor.fetchall()}

    weekdays = ["\u041f\u043d", "\u0412\u0442", "\u0421\u0440", "\u0427\u0442", "\u041f\u0442", "\u0421\u0431", "\u0412\u0441"]
    result = []
    for date in dates:
        row = rows.get(date)
        parsed = parse_date(date)
        label = weekdays[parsed.weekday()]
        if row:
            _, sleep_out, sleep_up, rate = row
            hours = sleep_hours(sleep_out, sleep_up)
        else:
            sleep_out = sleep_up = None
            rate = 0
            hours = 0.0
        result.append({
            "date": label,
            "sleep_date": date,
            "hours": hours,
            "rate": rate,
            "sleep_out": sleep_out,
            "sleep_up": sleep_up,
        })
    return result


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
