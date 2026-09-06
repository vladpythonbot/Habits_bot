import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from bot import TOKEN
from db import (
    date_range,
    delete_habit_from_db,
    disable_habit_reminder,
    get_habit_reminder,
    get_habit_logs,
    get_missed_habit_ids,
    get_user_habits,
    get_sleep_stats,
    move_habit,
    mark_habit_completed,
    parse_date,
    record_habit_miss,
    save_habit,
    save_sleep_log,
    set_habit_reminder,
    today_str,
    unmark_habit_completed,
    update_habit_goal,
    update_habit_name,
)


BASE_DIR = Path(__file__).resolve().parent
WEBAPP_DIR = BASE_DIR / "miniapp"
MAX_INIT_DATA_AGE = int(os.getenv("TELEGRAM_INIT_DATA_MAX_AGE", str(24 * 60 * 60)))
logger = logging.getLogger(__name__)


def verify_init_data(init_data: str) -> dict:
    if not init_data:
        raise web.HTTPUnauthorized(text="Telegram init data is required")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise web.HTTPUnauthorized(text="Telegram init data hash is missing")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise web.HTTPUnauthorized(text="Telegram auth_date is invalid") from exc

    if not auth_date or time.time() - auth_date > MAX_INIT_DATA_AGE:
        raise web.HTTPUnauthorized(text="Telegram init data is expired")

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
    habit_id, name, created_date, streak, total_completed, last_completed, goal_days, _, *extra = habit
    goal_type = extra[0] if len(extra) > 0 else "daily"
    goal_value = int(extra[1] if len(extra) > 1 else 7)
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
        "done_today": last_completed == today,
        "missed_today": habit_id in missed_ids,
        "reminder": reminder,
    }


async def get_action_date(request: web.Request) -> str:
    payload = await get_json_payload(request)
    value = payload.get("date") or today_str()
    try:
        selected = parse_date(value)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="Invalid date") from exc

    today = parse_date(today_str())
    first_available = parse_date(date_range(30)[0])
    if selected > today or selected < first_available:
        raise web.HTTPBadRequest(text="Date is outside the available calendar range")

    return selected.strftime("%Y-%m-%d")


def goal_label(goal_type: str | None, goal_value: int | None) -> str:
    if goal_type == "weekdays":
        return "По будням"
    if goal_type == "weekly":
        value = goal_value or 3
        return f"{value} раз в неделю"
    return "Каждый день"


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


async def api_move_habit(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    payload = await get_json_payload(request)
    habit_id = int(request.match_info["habit_id"])
    direction = str(payload.get("direction", "")).strip()
    if direction not in {"up", "down"}:
        raise web.HTTPBadRequest(text="Invalid direction")
    moved = await move_habit(int(user["id"]), habit_id, direction)
    if not moved:
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


async def api_stats(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    user_id = int(user["id"])
    today = today_str()
    habits = await get_user_habits(user_id)
    logs = await get_habit_logs(user_id, days=30)
    missed_today = set(await get_missed_habit_ids(user_id))

    daily_done = {date: 0 for date in date_range(30)}
    for _, completed_date in logs:
        if completed_date in daily_done:
            daily_done[completed_date] += 1

    today_done = sum(1 for habit in habits if habit[5] == today)
    today_open = sum(1 for habit in habits if habit[5] != today and habit[0] not in missed_today)

    return web.json_response({
        "today": today,
        "habits_count": len(habits),
        "total_completed": sum(habit[4] for habit in habits),
        "today_done": today_done,
        "today_open": today_open,
        "active_days": sum(1 for value in daily_done.values() if value > 0),
        "daily_done": daily_done,
    })


async def api_sleep_stats(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    user_id = int(user["id"])
    requested_user_id = request.query.get("user_id")
    if requested_user_id:
        try:
            requested_user_id_int = int(requested_user_id)
        except ValueError as exc:
            raise web.HTTPBadRequest(text="Invalid user_id") from exc
        if requested_user_id_int != user_id:
            raise web.HTTPForbidden(text="Forbidden user_id")
    return web.json_response({"items": await get_sleep_stats(user_id, days=7)})


async def api_save_sleep_today(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    payload = await get_json_payload(request)
    sleep_out = str(payload.get("sleep_out", "")).strip()
    sleep_up = str(payload.get("sleep_up", "")).strip()
    try:
        rate = int(payload.get("rate", 0))
    except (TypeError, ValueError):
        rate = 0
    saved = await save_sleep_log(int(user["id"]), today_str(), sleep_out, sleep_up, rate)
    if not saved:
        raise web.HTTPBadRequest(text="Invalid sleep data")
    return await api_sleep_stats(request)


async def api_mark(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    habit_id = int(request.match_info["habit_id"])
    completed_date = await get_action_date(request)
    await mark_habit_completed(int(user["id"]), habit_id, completed_date)
    return await api_state(request)


async def api_miss(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    habit_id = int(request.match_info["habit_id"])
    user_id = int(user["id"])
    missed_date = await get_action_date(request)
    await unmark_habit_completed(user_id, habit_id, missed_date)
    await record_habit_miss(user_id, habit_id, missed_date)
    return await api_state(request)


async def api_undo(request: web.Request) -> web.Response:
    user = await get_telegram_user(request)
    habit_id = int(request.match_info["habit_id"])
    completed_date = await get_action_date(request)
    await unmark_habit_completed(int(user["id"]), habit_id, completed_date)
    return await api_state(request)


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Web API request failed: %s %s", request.method, request.path)
        return web.json_response({"error": "internal_error"}, status=500)


def create_web_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app.router.add_get("/", index)
    app.router.add_get("/miniapp", index)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/state", api_state)
    app.router.add_post("/api/stats", api_stats)
    app.router.add_get("/api/sleep/stats", api_sleep_stats)
    app.router.add_post("/api/sleep/today", api_save_sleep_today)
    app.router.add_post("/api/habits", api_add_habit)
    app.router.add_post("/api/habits/{habit_id:\\d+}/rename", api_rename_habit)
    app.router.add_post("/api/habits/{habit_id:\\d+}/delete", api_delete_habit)
    app.router.add_post("/api/habits/{habit_id:\\d+}/move", api_move_habit)
    app.router.add_post("/api/habits/{habit_id:\\d+}/goal", api_set_goal)
    app.router.add_post("/api/habits/{habit_id:\\d+}/reminder", api_set_reminder)
    app.router.add_post("/api/habits/{habit_id:\\d+}/reminder/off", api_disable_reminder)
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
