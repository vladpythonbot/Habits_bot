import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from bot import TOKEN
from db import (
    create_habit_group,
    date_range,
    delete_habit_group,
    delete_habit_from_db,
    disable_habit_reminder,
    get_habit_groups,
    get_habit_reminder,
    get_habit_logs,
    get_missed_habit_ids,
    get_user_habits,
    mark_habit_completed,
    parse_date,
    record_habit_miss,
    save_habit,
    set_habit_group,
    set_habit_reminder,
    today_str,
    unmark_habit_completed,
    update_habit_group_emoji,
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
    habit_id, name, created_date, streak, total_completed, last_completed, goal_days, group_id = habit
    today = today_str()
    reminder = await get_habit_reminder(user_id, habit_id)
    return {
        "id": habit_id,
        "name": name,
        "created_date": created_date,
        "streak": streak,
        "total_completed": total_completed,
        "goal_days": goal_days,
        "group_id": group_id,
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
    groups = await get_habit_groups(user_id)
    missed_ids = await get_missed_habit_ids(user_id)
    items = [await habit_payload(user_id, habit, missed_ids) for habit in habits]
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
    for _, completed_date in logs:
        if completed_date in daily_done:
            daily_done[completed_date] += 1

    possible = 0
    for habit in habits:
        created = parse_date(habit[2])
        for date in completed_dates:
            if parse_date(date) >= created:
                possible += 1

    period_completed = sum(daily_done[date] for date in completed_dates)
    completion_rate = round(period_completed / possible * 100) if possible else 0
    missed_today = await get_missed_habit_ids(user_id)

    return web.json_response({
        "today": today,
        "habits_count": len(habits),
        "total_completed": sum(habit[4] for habit in habits),
        "period_completed": period_completed,
        "possible": possible,
        "completion_rate": completion_rate,
        "missed_days": len(missed_today),
        "today_done": daily_done.get(today, 0),
        "dates": dates,
        "daily_done": daily_done,
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
    app.router.add_post("/api/habits/{habit_id:\\d+}/group", api_set_habit_group)
    app.router.add_post("/api/habits/{habit_id:\\d+}/reminder", api_set_reminder)
    app.router.add_post("/api/habits/{habit_id:\\d+}/reminder/off", api_disable_reminder)
    app.router.add_post("/api/groups", api_create_group)
    app.router.add_post("/api/groups/{group_id:\\d+}/emoji", api_update_group_emoji)
    app.router.add_post("/api/groups/{group_id:\\d+}/delete", api_delete_group)
    app.router.add_post("/api/habits/{habit_id:\\d+}/mark", api_mark)
    app.router.add_post("/api/habits/{habit_id:\\d+}/miss", api_miss)
    app.router.add_post("/api/habits/{habit_id:\\d+}/undo", api_undo)
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
