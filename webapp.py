import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from bot import TOKEN
from db import (
    get_missed_habit_ids,
    get_user_habits,
    mark_habit_completed,
    record_habit_miss,
    save_habit,
    today_str,
    unmark_habit_completed,
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


def habit_payload(habit, missed_ids: set[int]) -> dict:
    habit_id, name, created_date, streak, total_completed, last_completed, goal_days, group_id = habit
    today = today_str()
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
    missed_ids = await get_missed_habit_ids(user_id)
    items = [habit_payload(habit, missed_ids) for habit in habits]
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
    app.router.add_post("/api/habits", api_add_habit)
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
