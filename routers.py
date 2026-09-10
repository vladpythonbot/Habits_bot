import logging
import os
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo

from bot import bot
from db import (
    get_due_habit_reminders,
    get_missed_habit_ids,
    get_user_habits,
    is_habit_missed,
    mark_habit_completed,
    record_habit_miss,
    today_str,
    unmark_habit_completed,
)

router = Router()
logger = logging.getLogger(__name__)

APP_VERSION = "2026.09.10.2"
RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN")
MINI_APP_URL = os.getenv("MINI_APP_URL") or (
    f"https://{RAILWAY_PUBLIC_DOMAIN}/miniapp" if RAILWAY_PUBLIC_DOMAIN else None
)

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Блокнот на сегодня")]],
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
        if habit[5] == today:
            rows.append([InlineKeyboardButton(text=f"↩️ {habit[1][:22]}", callback_data=f"undo_{habit_id}")])
        elif habit_id not in missed_ids:
            rows.append([
                InlineKeyboardButton(text=f"✅ {habit[1][:18]}", callback_data=f"mark_{habit_id}"),
                InlineKeyboardButton(text="⚪", callback_data=f"miss_{habit_id}"),
            ])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


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


@router.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Открыл блокнот. Всё управление — в Mini App, здесь только самое нужное.",
        reply_markup=main_keyboard,
    )
    await show_today(message, message.from_user.id)


@router.message(Command("app"))
async def open_mini_app(message: types.Message, state: FSMContext):
    await state.clear()
    if MINI_APP_URL:
        await message.answer("Открываю твой блокнот.", reply_markup=mini_app_keyboard())
        return
    await message.answer("Mini App пока не настроен.", reply_markup=main_keyboard)


@router.message(Command("stats"))
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
    await message.answer(text, reply_markup=main_keyboard)


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


@router.callback_query(F.data.startswith("miss_"))
async def miss_habit(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    await record_habit_miss(callback.from_user.id, habit_id)
    await callback.answer("Ок, не сегодня")
    await show_today(callback, callback.from_user.id)


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
