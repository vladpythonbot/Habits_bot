import logging
import os
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from bot import bot
from db import (
    clear_habit_day,
    get_due_habit_reminders,
    get_missed_habit_ids,
    get_users_for_habit_table_time,
    get_users_for_sleep_rate_time,
    get_sleep_stats,
    get_user_habit_stats,
    get_user_habits,
    is_habit_missed,
    mark_habit_completed,
    record_habit_miss,
    save_sleep_rate,
    today_str,
    unmark_habit_completed,
)
from sleep_chart import build_sleep_chart_png, sleep_caption, sleep_summary

router = Router()
logger = logging.getLogger(__name__)

APP_VERSION = "2026.09.14.4"
RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN")
MINI_APP_URL = os.getenv("MINI_APP_URL") or (
    f"https://{RAILWAY_PUBLIC_DOMAIN}/miniapp" if RAILWAY_PUBLIC_DOMAIN else None
)
HABIT_TABLE_TIME = os.getenv("HABIT_TABLE_TIME", "21:00")
SLEEP_RATE_TIME = os.getenv("SLEEP_RATE_TIME", "09:00")

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Блокнот на сегодня")],
        [KeyboardButton(text="📊 Статистика")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
    is_persistent=True,
)


def mini_app_keyboard() -> InlineKeyboardMarkup | None:
    if not MINI_APP_URL:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="Открыть Mini App",
        web_app=WebAppInfo(url=MINI_APP_URL),
    )]])


def habit_name(habit) -> str:
    return escape(habit[1])


def is_primary_habit(habit) -> bool:
    return bool(habit[10]) if len(habit) > 10 else False


def primary_time(habit) -> str:
    return habit[11] if len(habit) > 11 and habit[11] else "09:00"


def today_keyboard(habits, missed_ids: set[int]) -> InlineKeyboardMarkup | None:
    rows = []
    today = today_str()
    for habit in sorted(habits, key=lambda item: not is_primary_habit(item)):
        habit_id = habit[0]
        if habit[5] == today or habit_id in missed_ids:
            rows.append([InlineKeyboardButton(text=f"Очистить {habit[1][:18]}", callback_data=f"clear_{habit_id}")])
        else:
            rows.append([
                InlineKeyboardButton(text=f"✅ {habit[1][:18]}", callback_data=f"mark_{habit_id}"),
                InlineKeyboardButton(text="⚪", callback_data=f"miss_{habit_id}"),
            ])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def habit_status_icon(habit, missed_ids: set[int]) -> str:
    if habit[5] == today_str():
        return "✅"
    if habit[0] in missed_ids:
        return "⚪"
    return "▫️"


async def build_habit_table_text(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    habits = await get_user_habits(user_id)
    missed_ids = set(await get_missed_habit_ids(user_id))
    if not habits:
        return "📓 <b>Блокнот на сегодня</b>\n\nПока пусто. Добавь одну строку в Mini App.", None

    ordered = sorted(habits, key=lambda item: not is_primary_habit(item))
    lines = ["📓 <b>Таблица привычек на сегодня</b>", ""]
    for habit in ordered:
        star = "★ " if is_primary_habit(habit) else ""
        lines.append(f"{habit_status_icon(habit, missed_ids)} {star}<b>{habit_name(habit)}</b>")
    lines.append("\n✅ сделано · ⚪ не сегодня · ▫️ ждёт")
    return "\n".join(lines), today_keyboard(ordered, missed_ids)


async def answer_or_edit(obj: types.Message | types.CallbackQuery, text: str, reply_markup=None):
    if isinstance(obj, types.CallbackQuery):
        await obj.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
        await obj.answer()
        return
    await obj.answer(text, parse_mode="HTML", reply_markup=reply_markup)


async def show_today(obj: types.Message | types.CallbackQuery, user_id: int):
    habits = await get_user_habits(user_id)
    if not habits:
        await answer_or_edit(
            obj,
            "📓 <b>Мой блокнот</b>\n\nПока пусто. Добавь одну привычку в Mini App — без героизма, просто чтобы день имел опору.",
        )
        return

    today = today_str()
    missed_ids = set(await get_missed_habit_ids(user_id))
    ordered = sorted(habits, key=lambda item: not is_primary_habit(item))
    primary = next((habit for habit in ordered if is_primary_habit(habit)), None)
    pending = [habit for habit in ordered if habit[5] != today and habit[0] not in missed_ids]
    completed = [habit for habit in ordered if habit[5] == today]

    text = "📓 <b>Мой блокнот на сегодня</b>"
    if primary:
        text += f"\n\nГлавная строка дня: <b>{habit_name(primary)}</b> · {escape(primary_time(primary))}"

    if pending:
        text += "\n\n<b>Ещё ждёт:</b>"
        text += "".join(f"\n• <b>{habit_name(habit)}</b>" for habit in pending)
    else:
        text += "\n\nНа сегодня всё закрыто. Достаточно."

    if completed:
        text += "\n\n<b>Уже сделал:</b>"
        text += "".join(f"\n• <b>{habit_name(habit)}</b>" for habit in completed)

    await answer_or_edit(obj, text, today_keyboard(habits, missed_ids))


async def send_sleep_statistics(message: types.Message) -> None:
    items = await get_sleep_stats(message.from_user.id, days=7)
    summary = sleep_summary(items)
    caption = sleep_caption(items)
    if summary.recorded_days == 0 and summary.avg_rate is None:
        await message.answer(caption, parse_mode="HTML", reply_markup=main_keyboard)
        return

    image = build_sleep_chart_png(items)
    await message.answer_photo(
        BufferedInputFile(image, filename="sleep-week.png"),
        caption=caption,
        parse_mode="HTML",
        reply_markup=main_keyboard,
    )


async def send_daily_habit_table():
    current_time = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%H:%M")
    try:
        for user_id in await get_users_for_habit_table_time(current_time, HABIT_TABLE_TIME):
            text, keyboard = await build_habit_table_text(user_id)
            await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as error:
        logger.error("Ошибка send_daily_habit_table: %s", error, exc_info=True)


async def ask_sleep_rate():
    current_time = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%H:%M")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=str(value), callback_data=f"sleep_rate_{value}")
        for value in range(1, 6)
    ]])
    try:
        for user_id in await get_users_for_sleep_rate_time(current_time, SLEEP_RATE_TIME):
            await bot.send_message(
                user_id,
                "Как спал? Оцени качество сна от 1 до 5.",
                reply_markup=keyboard,
            )
    except Exception as error:
        logger.error("Ошибка ask_sleep_rate: %s", error, exc_info=True)


@router.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Открыл блокнот. Всё управление — в Mini App, здесь только самое нужное.",
        reply_markup=main_keyboard,
    )
    await show_today(message, message.from_user.id)


@router.message(Command("app"))
@router.message(Command("settings"))
async def open_mini_app(message: types.Message, state: FSMContext):
    await state.clear()
    if MINI_APP_URL:
        await message.answer("Открываю твой блокнот.", reply_markup=mini_app_keyboard())
        return
    await message.answer("Mini App пока не настроен.", reply_markup=main_keyboard)


@router.message(Command("stats"))
@router.message(F.text == "📊 Статистика")
async def statistics(message: types.Message, state: FSMContext):
    await state.clear()
    habits = await get_user_habits(message.from_user.id)
    missed_ids = set(await get_missed_habit_ids(message.from_user.id))
    done = sum(1 for habit in habits if habit[5] == today_str())
    open_count = sum(1 for habit in habits if habit[5] != today_str() and habit[0] not in missed_ids)
    primary = next((habit for habit in habits if is_primary_habit(habit)), None)

    text = (
        f"В блокноте привычек: {len(habits)}\n"
        f"Сегодня уже сделано: {done}\n"
        f"Ещё ждёт: {open_count}"
    )
    if primary:
        text += f"\nГлавная строка: {habit_name(primary)} · {escape(primary_time(primary))}"

    stats = await get_user_habit_stats(message.from_user.id)
    if stats:
        text += "\n\n<b>Как прижились за 30 дней:</b>"
        for item in stats:
            text += (
                f"\n• <b>{escape(item['name'])}</b>: {item['percent']}%"
                f" · срывов {item['missed_days']}"
                f" · серия {item['streak']}"
            )
    await message.answer(text, parse_mode="HTML", reply_markup=main_keyboard)
    await send_sleep_statistics(message)


@router.message(Command("today"))
@router.message(F.text.in_(["Сегодня", "Блокнот на сегодня", "🟢 Сегодня"]))
async def today(message: types.Message, state: FSMContext):
    await state.clear()
    await show_today(message, message.from_user.id)


@router.callback_query(F.data.startswith("mark_"))
async def mark_habit(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    success, info = await mark_habit_completed(callback.from_user.id, habit_id)
    if not success:
        await callback.answer("Уже записано на сегодня", show_alert=True)
        return
    await callback.answer(f"Записал: {info['habit_name']}")
    await show_today(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("undo_"))
async def undo_habit(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    success, info = await unmark_habit_completed(callback.from_user.id, habit_id)
    if not success:
        await callback.answer("Этой отметки уже нет", show_alert=True)
        return
    await callback.answer(f"Убрал: {info['habit_name']}")
    await show_today(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("clear_"))
async def clear_habit(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    success, info = await clear_habit_day(callback.from_user.id, habit_id)
    if not success:
        await callback.answer("Не смог очистить", show_alert=True)
        return
    await callback.answer(f"Очистил: {info['habit_name']}")
    await show_today(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("miss_"))
async def miss_habit(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    await record_habit_miss(callback.from_user.id, habit_id)
    await callback.answer("Ок, не сегодня")
    await show_today(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("sleep_rate_"))
async def sleep_rate(callback: types.CallbackQuery):
    try:
        rate = int(callback.data.split("_")[-1])
    except (TypeError, ValueError):
        await callback.answer("Не понял оценку", show_alert=True)
        return

    saved = await save_sleep_rate(callback.from_user.id, today_str(), rate)
    if not saved:
        await callback.answer("Оценка должна быть от 1 до 5", show_alert=True)
        return
    await callback.message.edit_text(f"Сон записал: {rate}/5.")
    await callback.answer("Записал сон")


async def send_habit_reminder_to_user(
    user_id: int,
    habit_id: int,
    habit_name_text: str,
    last_completed_date: str | None,
):
    if last_completed_date == today_str() or await is_habit_missed(user_id, habit_id):
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Выполнил", callback_data=f"mark_{habit_id}"),
        InlineKeyboardButton(text="⚪ Не сегодня", callback_data=f"miss_{habit_id}"),
    ]])

    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"⏰ <b>Строка из блокнота</b>\n\n<b>{escape(habit_name_text)}</b>\nЕсли сегодня не день — просто отметь спокойно.",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception as error:
        logger.error("Не удалось отправить напоминание habit_id=%s user_id=%s: %s", habit_id, user_id, error)


async def daily_reminder():
    current_time = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%H:%M")
    try:
        for user_id, habit_id, name, last_completed in await get_due_habit_reminders(current_time):
            await send_habit_reminder_to_user(user_id, habit_id, name, last_completed)
    except Exception as error:
        logger.error("Ошибка daily_reminder: %s", error, exc_info=True)
