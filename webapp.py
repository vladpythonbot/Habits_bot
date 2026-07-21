import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from bot import TOKEN
from db import (
    archive_habit,
    create_habit_group,
    date_range,
    delete_habit_group,
    delete_habit_from_db,
    delete_user_data,
    disable_habit_reminder,
    get_habit_groups,
    get_habit_reminder,
    get_habit_logs,
    get_missed_habit_ids,
    get_user_habits,
    mark_habit_completed,
    parse_date,
    record_habit_miss,
    restore_habit,
    save_habit,
    set_habit_group,
    set_habit_reminder,
    today_str,
    unmark_habit_completed,
    update_habit_group_emoji,
    update_habit_goal,
    update_habit_name,
)


BASE_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = BASE_DIR / "miniapp"


def verify_init_data(init_data: str) -> dict:
    if not init_data:
        raise web.HTTPUnauthorized(text="Telegram init data is required")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise web.HTTPUnauthorized(text="Telegram init data hash is missing")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise web.HTTPUnauthorized(text="Telegram init data is invalid")

    user_raw = pairs.get("user")
    if not user_raw:
        raise web.HTTPUnauthorized(text="Telegram user is missing")

    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise web.HTTPUnauthorized(text="Telegram user data is invalid") from exc

    if "id" not in user:
        raise web.HTTPUnauthorized(text="Telegram user id is missing")

    return user


async def habit_payload(user_id: int, habit, missed_ids: set[int]) -> dict:
    habit_id, name, created_date, streak, total_completed, last_completed, goal_days, group_id, *extra = habit
    goal_type = extra[0] if len(extra) > 0 else "daily"
    goal_value = int(extra[1] if len(extra) > 1 else 7)
    archived_at = extra[2] if len(extra) > 2 else None
    today = today_str()
    reminder = await get_habit_reminder(user_id, habit_id)
    return {
        "id": habit_id,
        "name": name,
        "created_date": created_date,
        "streak": streak,
        "total_completed": total_completed,
        "goal_days": goal_days,
        "goal_type": goal_type,
        "goal_value": goal_value,
        "goal_text": goal_label(goal_type, goal_value),
        "group_id": group_id,
        "archived_at": archived_at,
        "done_today": last_completed == today,
        "missed_today": habit_id in missed_ids,
        "reminder": reminder,
    }


def group_payload(group) -> dict:
    group_id, name, count, emoji = group
    return {
        "id": group_id,
        "name": name,
        "count": count,
        "emoji": emoji,
    }


def goal_label(goal_type: str | None, goal_value: int | None) -> str:
    if goal_type == "weekdays":
        return "По будням"
    if goal_type == "weekly":
        value = goal_value or 3
        return f"{value} раз в неделю"
    return "Каждый день"


def expected_dates_for_goal(created_date: str, goal_type: str | None, goal_value: int | None, dates: list[str]) -> set[str]:
    created = parse_date(created_date)
    active_dates = [date for date in dates if parse_date(date) >= created]
    if goal_type == "weekdays":
        return {date for date in active_dates if parse_date(date).weekday() < 5}
    if goal_type == "weekly":
        weekly_dates: dict[tuple[int, int], list[str]] = {}
        for date in active_dates:
            parsed = parse_date(date)
            iso_year, iso_week, _ = parsed.isocalendar()
            weekly_dates.setdefault((iso_year, iso_week), []).append(date)
        limit = max(1, min(7, int(goal_value or 3)))
        expected: set[str] = set()
        for week_dates in weekly_dates.values():
            expected.update(week_dates[:limit])
        return expected
    return set(active_dates)


def count_goal_completions(log_dates: set[str], expected_dates: set[str], goal_type: str | None, goal_value: int | None) -> int:
    if goal_type != "weekly":
        return len(log_dates.intersection(expected_dates))

    limit = max(1, min(7, int(goal_value or 3)))
    logs_by_week: dict[tuple[int, int], int] = {}
    expected_by_week: dict[tuple[int, int], int] = {}
    for date in log_dates:
        parsed = parse_date(date)
        iso_year, iso_week, _ = parsed.isocalendar()
        logs_by_week[(iso_year, iso_week)] = logs_by_week.get((iso_year, iso_week), 0) + 1
    for date in expected_dates:
        parsed = parse_date(date)
        iso_year, iso_week, _ = parsed.isocalendar()
        expected_by_week[(iso_year, iso_week)] = expected_by_week.get((iso_year, iso_week), 0) + 1
    return sum(min(logs_by_week.get(week, 0), possible, limit) for week, possible in expected_by_week.items())


def best_consecutive_days(log_dates: set[str]) -> int:
    if not log_dates:
        return 0
    ordered = sorted(parse_date(date) for date in log_dates)
    best = current = 1
    for index in range(1, len(ordered)):
        if (ordered[index] - ordered[index - 1]).days == 1:
            current += 1
        else:
            current = 1
        best = max(best, current)
    return best


async def get_telegram_user(request: web.Request) -> dict:
    payload = await get_json_payload(request)
    init_data = (
        request.headers.get("X-Telegram-Init-Data")
        or request.query.get("initData")
        or payload.get("_auth")
        or ""
    )
    return verify_init_data(init_data)


async def get_json_payload(request: web.Request) -> dict:
    if request.method not in {"POST", "PUT", "PATCH"}:
        return {}
    if "json_payload" in request:
        return request["json_payload"]
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}
    request["json_payload"] = payload if isinstance(payload, dict) else {}
    return request["json_payload"]


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html")


async def api_state(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    user_id = int(user["id"])
    habits = await get_user_habits(user_id)
    archived_habits = await get_user_habits(user_id, archived_only=True)
    groups = await get_habit_groups(user_id)
    missed_ids = await get_missed_habit_ids(user_id)
    items = [await habit_payload(user_id, habit, missed_ids) for habit in habits]
    archived_items = [await habit_payload(user_id, habit, set()) for habit in archived_habits]
    done_count = sum(1 for item in items if item["done_today"])

    return web.json_response({
        "user": {"id": user_id, "first_name": user.get("first_name", "")},
        "today": today_str(),
        "summary": {
            "total": len(items),
            "done": done_count,
            "open": len([item for item in items if not item["done_today"] and not item["missed_today"]]),
        },
        "habits": items,
        "archived_habits": archived_items,
        "groups": [group_payload(group) for group in groups],
    })


async def api_add_habit(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    payload = await get_json_payload(request)
    name = str(payload.get("name", "")).strip()
    if not name:
        raise web.HTTPBadRequest(text="Habit name is required")
    if len(name) > 80:
        raise web.HTTPBadRequest(text="Habit name is too long")

    await save_habit(int(user["id"]), name)
    return await api_state(request)


async def api_rename_habit(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    payload = await get_json_payload(request)
    habit_id = int(request.match_info["habit_id"])
    name = str(payload.get("name", "")).strip()
    if not name:
        raise web.HTTPBadRequest(text="Habit name is required")
    if len(name) > 80:
        raise web.HTTPBadRequest(text="Habit name is too long")

    updated = await update_habit_name(int(user["id"]), habit_id, name)
    if not updated:
        raise web.HTTPNotFound(text="Habit not found")
    return await api_state(request)


async def api_delete_habit(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    habit_id = int(request.match_info["habit_id"])
    await delete_habit_from_db(int(user["id"]), habit_id)
    return await api_state(request)


async def api_archive_habit(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    habit_id = int(request.match_info["habit_id"])
    archived = await archive_habit(int(user["id"]), habit_id)
    if not archived:
        raise web.HTTPNotFound(text="Habit not found")
    return await api_state(request)


async def api_restore_habit(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    habit_id = int(request.match_info["habit_id"])
    restored = await restore_habit(int(user["id"]), habit_id)
    if not restored:
        raise web.HTTPNotFound(text="Habit not found")
    return await api_state(request)


async def api_set_goal(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    payload = await get_json_payload(request)
    habit_id = int(request.match_info["habit_id"])
    goal_type = str(payload.get("goal_type", "daily")).strip()
    raw_value = payload.get("goal_value")
    try:
        goal_value = int(raw_value) if raw_value is not None else None
    except (TypeError, ValueError):
        goal_value = None
    updated = await update_habit_goal(int(user["id"]), habit_id, goal_type, goal_value)
    if not updated:
        raise web.HTTPNotFound(text="Habit not found")
    return await api_state(request)


async def api_set_habit_group(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    payload = await get_json_payload(request)
    habit_id = int(request.match_info["habit_id"])
    group_value = payload.get("group_id")
    group_id = None if group_value in (None, "", "none") else int(group_value)
    updated = await set_habit_group(int(user["id"]), habit_id, group_id)
    if not updated:
        raise web.HTTPNotFound(text="Habit or group not found")
    return await api_state(request)


async def api_set_reminder(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    payload = await get_json_payload(request)
    habit_id = int(request.match_info["habit_id"])
    reminder_time = str(payload.get("reminder_time", "")).strip()
    if not reminder_time:
        raise web.HTTPBadRequest(text="Reminder time is required")
    saved = await set_habit_reminder(int(user["id"]), habit_id, reminder_time, enabled=True)
    if not saved:
        raise web.HTTPNotFound(text="Habit not found")
    return await api_state(request)


async def api_disable_reminder(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    habit_id = int(request.match_info["habit_id"])
    await disable_habit_reminder(int(user["id"]), habit_id)
    return await api_state(request)


async def api_create_group(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    payload = await get_json_payload(request)
    name = str(payload.get("name", "")).strip()
    emoji = str(payload.get("emoji", "🎯")).strip()[:8] or "🎯"
    if not name:
        raise web.HTTPBadRequest(text="Group name is required")
    created, _ = await create_habit_group(int(user["id"]), name, emoji)
    if not created:
        raise web.HTTPBadRequest(text="Group already exists")
    return await api_state(request)


async def api_update_group_emoji(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    payload = await get_json_payload(request)
    group_id = int(request.match_info["group_id"])
    emoji = str(payload.get("emoji", "🎯")).strip()[:8] or "🎯"
    updated = await update_habit_group_emoji(int(user["id"]), group_id, emoji)
    if not updated:
        raise web.HTTPNotFound(text="Group not found")
    return await api_state(request)


async def api_delete_group(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    group_id = int(request.match_info["group_id"])
    await delete_habit_group(int(user["id"]), group_id)
    return await api_state(request)


async def api_stats(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    user_id = int(user["id"])
    dates = date_range(30)
    today = today_str()
    habits = await get_user_habits(user_id)
    logs = await get_habit_logs(user_id, days=30)
    completed_dates = [date for date in dates if date != today]
    daily_done = {date: 0 for date in dates}
    logs_by_habit: dict[int, set[str]] = {}
    for habit_id, completed_date in logs:
        if completed_date in daily_done:
            daily_done[completed_date] += 1
        logs_by_habit.setdefault(habit_id, set()).add(completed_date)

    possible = 0
    daily_possible = {date: 0 for date in completed_dates}
    today_possible = 0
    habit_rows = []
    last_7_dates = completed_dates[-7:]
    previous_7_dates = completed_dates[-14:-7]
    for habit in habits:
        habit_id, name, created_date, streak, total_completed, last_completed, goal_days, group_id, *extra = habit
        goal_type = extra[0] if len(extra) > 0 else "daily"
        goal_value = int(extra[1] if len(extra) > 1 else 7)
        expected_dates = expected_dates_for_goal(created_date, goal_type, goal_value, dates)
        completed_expected_dates = expected_dates.intersection(completed_dates)
        habit_possible = len(completed_expected_dates)
        for date in completed_dates:
            if date in expected_dates:
                daily_possible[date] += 1
                possible += 1
        if today in expected_dates:
            today_possible += 1
        habit_log_dates = logs_by_habit.get(habit_id, set())
        habit_period_done = count_goal_completions(
            habit_log_dates.intersection(completed_dates),
            completed_expected_dates,
            goal_type,
            goal_value,
        )
        habit_last_7_expected = expected_dates.intersection(last_7_dates)
        habit_last_7_possible = len(habit_last_7_expected)
        habit_last_7_done = count_goal_completions(
            habit_log_dates.intersection(last_7_dates),
            habit_last_7_expected,
            goal_type,
            goal_value,
        )
        habit_rate = round(habit_period_done / habit_possible * 100) if habit_possible else 0
        habit_rows.append({
            "id": habit_id,
            "name": name,
            "created_date": created_date,
            "streak": streak,
            "best_streak": best_consecutive_days(habit_log_dates),
            "total_completed": total_completed,
            "last_completed_date": last_completed,
            "goal_type": goal_type,
            "goal_value": goal_value,
            "goal_text": goal_label(goal_type, goal_value),
            "done_today": today in logs_by_habit.get(habit_id, set()),
            "period_completed": habit_period_done,
            "possible": habit_possible,
            "completion_rate": habit_rate,
            "last_7_completed": habit_last_7_done,
            "last_7_possible": habit_last_7_possible,
            "last_7_rate": round(habit_last_7_done / habit_last_7_possible * 100) if habit_last_7_possible else 0,
            "missed_count": max(habit_possible - habit_period_done, 0),
            "calendar": [
                {
                    "date": date,
                    "done": date in habit_log_dates,
                    "available": parse_date(date) >= parse_date(created_date),
                    "expected": date in expected_dates,
                }
                for date in dates
            ],
        })

    period_completed = sum(daily_done[date] for date in completed_dates)
    completion_rate = round(period_completed / possible * 100) if possible else 0
    missed_today = await get_missed_habit_ids(user_id)
    habit_rows.sort(key=lambda item: (-item["completion_rate"], -item["streak"], item["name"].lower()))
    week_possible = sum(daily_possible.get(date, 0) for date in last_7_dates)
    week_completed = sum(daily_done.get(date, 0) for date in last_7_dates)
    prev_week_possible = sum(daily_possible.get(date, 0) for date in previous_7_dates)
    prev_week_completed = sum(daily_done.get(date, 0) for date in previous_7_dates)
    week_rate = round(week_completed / week_possible * 100) if week_possible else 0
    prev_week_rate = round(prev_week_completed / prev_week_possible * 100) if prev_week_possible else 0
    trend = week_rate - prev_week_rate
    active_days = sum(1 for date in completed_dates if daily_done.get(date, 0) > 0)
    perfect_days = sum(
        1
        for date in completed_dates
        if daily_possible.get(date, 0) > 0 and daily_done.get(date, 0) >= daily_possible.get(date, 0)
    )
    today_rate = round(daily_done.get(today, 0) / today_possible * 100) if today_possible else 0
    best_streak = max((habit[3] for habit in habits), default=0)
    average_streak = round(sum(habit[3] for habit in habits) / len(habits), 1) if habits else 0
    best_habit = habit_rows[0] if habit_rows else None
    focus_habit = min(
        (habit for habit in habit_rows if habit["possible"] > 0),
        key=lambda item: (item["completion_rate"], -item["missed_count"], item["name"].lower()),
        default=None,
    )
    total_completed = sum(habit[4] for habit in habits)

    return web.json_response({
        "today": today,
        "habits_count": len(habits),
        "total_completed": total_completed,
        "period_completed": period_completed,
        "possible": possible,
        "completion_rate": completion_rate,
        "missed_days": len(missed_today),
        "today_done": daily_done.get(today, 0),
        "today_possible": today_possible,
        "today_rate": today_rate,
        "dates": dates,
        "daily_done": daily_done,
        "daily_possible": daily_possible,
        "week_completed": week_completed,
        "week_possible": week_possible,
        "week_rate": week_rate,
        "prev_week_rate": prev_week_rate,
        "trend": trend,
        "active_days": active_days,
        "perfect_days": perfect_days,
        "best_streak": best_streak,
        "average_streak": average_streak,
        "best_habit": best_habit,
        "focus_habit": focus_habit,
        "habit_rows": habit_rows,
    })


async def api_mark(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    habit_id = int(request.match_info["habit_id"])
    await mark_habit_completed(int(user["id"]), habit_id)
    return await api_state(request)


async def api_miss(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    habit_id = int(request.match_info["habit_id"])
    user_id = int(user["id"])
    await unmark_habit_completed(user_id, habit_id)
    await record_habit_miss(user_id, habit_id)
    return await api_state(request)


async def api_undo(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    habit_id = int(request.match_info["habit_id"])
    await unmark_habit_completed(int(user["id"]), habit_id)
    return await api_state(request)


async def api_delete_user_data(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    await delete_user_data(int(user["id"]))
    return web.json_response({
        "user": {"id": int(user["id"]), "first_name": user.get("first_name", "")},
        "today": today_str(),
        "summary": {"total": 0, "done": 0, "open": 0},
        "habits": [],
        "archived_habits": [],
        "groups": [],
    })


def create_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/miniapp", index)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/state", api_state)
    app.router.add_post("/api/stats", api_stats)
    app.router.add_post("/api/habits", api_add_habit)
    app.router.add_post("/api/habits/{habit_id:\\d+}/rename", api_rename_habit)
    app.router.add_post("/api/habits/{habit_id:\\d+}/delete", api_delete_habit)
    app.router.add_post("/api/habits/{habit_id:\\d+}/archive", api_archive_habit)
    app.router.add_post("/api/habits/{habit_id:\\d+}/restore", api_restore_habit)
    app.router.add_post("/api/habits/{habit_id:\\d+}/goal", api_set_goal)
    app.router.add_post("/api/habits/{habit_id:\\d+}/group", api_set_habit_group)
    app.router.add_post("/api/habits/{habit_id:\\d+}/reminder", api_set_reminder)
    app.router.add_post("/api/habits/{habit_id:\\d+}/reminder/off", api_disable_reminder)
    app.router.add_post("/api/groups", api_create_group)
    app.router.add_post("/api/groups/{group_id:\\d+}/emoji", api_update_group_emoji)
    app.router.add_post("/api/groups/{group_id:\\d+}/delete", api_delete_group)
    app.router.add_post("/api/habits/{habit_id:\\d+}/mark", api_mark)
    app.router.add_post("/api/habits/{habit_id:\\d+}/miss", api_miss)
    app.router.add_post("/api/habits/{habit_id:\\d+}/undo", api_undo)
    app.router.add_post("/api/privacy/delete-data", api_delete_user_data)
    app.router.add_static("/static", WEBAPP_DIR, show_index=False)
    return app


async def start_web_app() -> web.AppRunner:
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
