# routers.py
import logging
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot import bot
from db import (
    delete_habit_from_db,
    get_reminder_settings,
    get_user_habits,
    get_users_by_reminder_time,
    mark_habit_completed,
    parse_reminder_times,
    reset_habit_streak,
    save_habit,
    set_reminder_settings,
    today_str,
    update_habit_name,
    yesterday_str,
)

router = Router()
logger = logging.getLogger(__name__)

REMINDER_CHOICES = ["09:00", "12:00", "15:00", "18:00", "21:00"]


class Form(StatesGroup):
    waiting_habit_name = State()
    waiting_custom_goal = State()
    waiting_new_name = State()


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Сегодня"), KeyboardButton(text="Привычки")],
        [KeyboardButton(text="Настройки")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)


def days_word(value: int) -> str:
    if value % 10 == 1 and value % 100 != 11:
        return "день"
    if value % 10 in [2, 3, 4] and value % 100 not in [12, 13, 14]:
        return "дня"
    return "дней"


def progress_bar(done: int, total: int, width: int = 8) -> str:
    if total <= 0:
        return "—"

    filled = round(width * done / total)
    return "●" * filled + "○" * (width - filled)


def habit_name(habit) -> str:
    return escape(habit[1])


def main_summary(habits) -> str:
    if not habits:
        return "Пока привычек нет. Добавь первую — и начнём спокойно."

    today = today_str()
    yesterday = yesterday_str()
    done = sum(1 for h in habits if h[5] == today)
    best_streak = max((h[3] for h in habits), default=0)
    at_risk = [h for h in habits if h[5] == yesterday and h[3] > 0]

    lines = [
        f"<b>Сегодня</b>: {done}/{len(habits)}",
        progress_bar(done, len(habits)),
        f"Лучшая серия: <b>{best_streak} {days_word(best_streak)}</b>",
    ]

    if at_risk:
        lines.append(f"Под угрозой: <b>{len(at_risk)}</b>")

    return "\n".join(lines)


def goal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="7", callback_data="goal_7"),
            InlineKeyboardButton(text="14", callback_data="goal_14"),
            InlineKeyboardButton(text="21", callback_data="goal_21"),
        ],
        [
            InlineKeyboardButton(text="30", callback_data="goal_30"),
            InlineKeyboardButton(text="60", callback_data="goal_60"),
            InlineKeyboardButton(text="100", callback_data="goal_100"),
        ],
        [InlineKeyboardButton(text="Своя цель", callback_data="goal_custom")],
    ])


def habit_actions_keyboard(habits) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="+ Добавить", callback_data="add_habit")]]

    for habit in habits:
        habit_id = habit[0]
        rows.append([
            InlineKeyboardButton(text=f"✎ {habit[1]}", callback_data=f"edit_{habit_id}"),
            InlineKeyboardButton(text="Сброс", callback_data=f"reset_{habit_id}"),
            InlineKeyboardButton(text="Удалить", callback_data=f"delete_{habit_id}"),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def answer_or_edit(obj: types.Message | types.CallbackQuery, text: str, reply_markup=None):
    if isinstance(obj, types.CallbackQuery):
        await obj.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
        await obj.answer()
    else:
        await obj.answer(text, parse_mode="HTML", reply_markup=reply_markup)


@router.message(Command("start"))
async def start(message: types.Message):
    await get_reminder_settings(message.from_user.id)
    habits = await get_user_habits(message.from_user.id)

    await message.answer(
        "Привет. Я помогу держать привычки без шума.\n\n" + main_summary(habits),
        parse_mode="HTML",
        reply_markup=main_keyboard,
    )


@router.message(F.text == "Сегодня")
async def today(message: types.Message):
    await show_today(message, message.from_user.id)


async def show_today(obj: types.Message | types.CallbackQuery, user_id: int):
    habits = await get_user_habits(user_id)

    if not habits:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="+ Добавить привычку", callback_data="add_habit")]
        ])
        await answer_or_edit(obj, main_summary(habits), kb)
        return

    today = today_str()
    yesterday = yesterday_str()
    unmarked = [h for h in habits if h[5] != today]
    rows = []

    text = main_summary(habits)

    if not unmarked:
        text += "\n\nВсё отмечено. Хороший день."
    else:
        text += "\n\n<b>Осталось отметить:</b>"
        for habit in unmarked:
            habit_id, _, _, streak, _, last_date, goal_days = habit
            risk = " · серия под угрозой" if last_date == yesterday and streak > 0 else ""
            text += f"\n• <b>{habit_name(habit)}</b> — {streak}/{goal_days}{risk}"
            rows.append([
                InlineKeyboardButton(text=f"Отметить: {habit[1]}", callback_data=f"mark_{habit_id}")
            ])

    rows.append([InlineKeyboardButton(text="+ Добавить", callback_data="add_habit")])
    await answer_or_edit(obj, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("mark_"))
async def process_mark_callback(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[1])
    success, goal_info = await mark_habit_completed(callback.from_user.id, habit_id)

    if not success:
        await callback.answer("Уже отмечено сегодня", show_alert=True)
        return

    if goal_info and goal_info[0]:
        _, name, streak, goal = goal_info
        await callback.answer(f"Цель достигнута: {name} — {streak}/{goal}", show_alert=True)
    else:
        await callback.answer("Отмечено")

    await show_today(callback, callback.from_user.id)


@router.message(F.text == "Привычки")
async def habits(message: types.Message):
    await show_habits(message, message.from_user.id)


async def show_habits(obj: types.Message | types.CallbackQuery, user_id: int):
    habits = await get_user_habits(user_id)

    if not habits:
        text = "Привычек пока нет. Начнём с одной."
    else:
        text = "<b>Привычки</b>\n"
        for habit in habits:
            _, _, created_date, streak, total_completed, last_date, goal_days = habit
            done_today = " · сегодня готово" if last_date == today_str() else ""
            text += (
                f"\n• <b>{habit_name(habit)}</b>"
                f"\n  серия {streak}/{goal_days}, всего {total_completed}{done_today}\n"
            )

    await answer_or_edit(obj, text, habit_actions_keyboard(habits))


@router.callback_query(F.data == "add_habit")
async def new_habit_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Как назовём привычку?",
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(Form.waiting_habit_name)
    await callback.answer()


@router.message(Form.waiting_habit_name)
async def new_habit_name(message: types.Message, state: FSMContext):
    name = message.text.strip()

    if len(name) < 2:
        await message.answer("Название слишком короткое. Нужно хотя бы 2 символа.")
        return

    if len(name) > 40:
        await message.answer("Давай короче: до 40 символов, чтобы кнопки были аккуратными.")
        return

    await state.update_data(habit_name=name)
    await message.answer(
        f"<b>{escape(name)}</b>\n\nВыбери цель в днях:",
        parse_mode="HTML",
        reply_markup=goal_keyboard(),
    )


@router.callback_query(F.data.startswith("goal_"))
async def process_goal(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    name = data.get("habit_name")

    if not name:
        await callback.message.edit_text("Не вижу название привычки. Начни заново.")
        await state.clear()
        await callback.answer()
        return

    if callback.data == "goal_custom":
        await callback.message.edit_text("Введи цель числом, от 1 до 366 дней.")
        await state.set_state(Form.waiting_custom_goal)
        await callback.answer()
        return

    goal_days = int(callback.data.split("_")[1])
    await save_habit(callback.from_user.id, name, goal_days)
    await state.clear()
    await callback.message.edit_text(
        f"Готово: <b>{escape(name)}</b>\n"
        f"Цель: <b>{goal_days} {days_word(goal_days)}</b>.",
        parse_mode="HTML",
    )
    await callback.message.answer("Меню рядом.", reply_markup=main_keyboard)
    await callback.answer()


@router.message(Form.waiting_custom_goal)
async def process_custom_goal(message: types.Message, state: FSMContext):
    try:
        goal_days = int(message.text.strip())
    except ValueError:
        await message.answer("Нужно число. Например: 45")
        return

    if not 1 <= goal_days <= 366:
        await message.answer("Цель должна быть от 1 до 366 дней.")
        return

    data = await state.get_data()
    name = data.get("habit_name")

    if not name:
        await message.answer("Не вижу название привычки. Начни заново.", reply_markup=main_keyboard)
        await state.clear()
        return

    await create_habit(message.from_user.id, name, goal_days, message, state)


async def create_habit(user_id: int, name: str, goal_days: int, obj, state: FSMContext):
    await save_habit(user_id, name, goal_days)
    await state.clear()

    text = (
        f"Готово: <b>{escape(name)}</b>\n"
        f"Цель: <b>{goal_days} {days_word(goal_days)}</b>."
    )

    await obj.answer(text, parse_mode="HTML", reply_markup=main_keyboard)


@router.callback_query(F.data.startswith("edit_"))
async def start_edit_name(callback: types.CallbackQuery, state: FSMContext):
    habit_id = int(callback.data.split("_")[1])
    await state.update_data(editing_habit_id=habit_id)
    await callback.message.answer("Новое название привычки:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(Form.waiting_new_name)
    await callback.answer()


@router.message(Form.waiting_new_name)
async def save_new_name(message: types.Message, state: FSMContext):
    new_name = message.text.strip()

    if len(new_name) < 2:
        await message.answer("Название слишком короткое.")
        return

    if len(new_name) > 40:
        await message.answer("Давай до 40 символов.")
        return

    data = await state.get_data()
    habit_id = data.get("editing_habit_id")

    if not habit_id:
        await message.answer("Не нашёл привычку. Попробуй ещё раз.", reply_markup=main_keyboard)
        await state.clear()
        return

    updated = await update_habit_name(message.from_user.id, habit_id, new_name)
    await state.clear()

    if updated:
        await message.answer(f"Переименовано: <b>{escape(new_name)}</b>", parse_mode="HTML", reply_markup=main_keyboard)
    else:
        await message.answer("Не получилось переименовать.", reply_markup=main_keyboard)


@router.callback_query(F.data.startswith("reset_"))
async def reset_habit(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[1])
    await reset_habit_streak(callback.from_user.id, habit_id)
    await callback.answer("Серия сброшена")
    await show_habits(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("delete_"))
async def delete_habit(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[1])
    await delete_habit_from_db(callback.from_user.id, habit_id)
    await callback.answer("Удалено")
    await show_habits(callback, callback.from_user.id)


@router.message(F.text == "Настройки")
async def settings(message: types.Message):
    await show_settings(message, message.from_user.id)


async def show_settings(obj: types.Message | types.CallbackQuery, user_id: int):
    settings = await get_reminder_settings(user_id)
    enabled = settings["enabled"]
    times = parse_reminder_times(settings["reminder_time"])

    status = "включены" if enabled else "выключены"
    text = (
        "<b>Настройки</b>\n\n"
        f"Напоминания: <b>{status}</b>\n"
        f"Время: <b>{', '.join(times)}</b>"
    )

    rows = [[
        InlineKeyboardButton(
            text="Выключить" if enabled else "Включить",
            callback_data="rem_toggle",
        )
    ]]

    for reminder_time in REMINDER_CHOICES:
        mark = "✓" if reminder_time in times else "+"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {reminder_time}",
                callback_data=f"rem_time_{reminder_time.replace(':', '')}",
            )
        ])

    await answer_or_edit(obj, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "rem_toggle")
async def toggle_reminders(callback: types.CallbackQuery):
    settings = await get_reminder_settings(callback.from_user.id)
    await set_reminder_settings(
        callback.from_user.id,
        enabled=not settings["enabled"],
        reminder_time=settings["reminder_time"],
    )
    await show_settings(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("rem_time_"))
async def toggle_reminder_time(callback: types.CallbackQuery):
    raw_time = callback.data.split("_")[-1]
    selected = f"{raw_time[:2]}:{raw_time[2:]}"
    settings = await get_reminder_settings(callback.from_user.id)
    times = set(parse_reminder_times(settings["reminder_time"]))

    if selected in times and len(times) > 1:
        times.remove(selected)
    else:
        times.add(selected)

    await set_reminder_settings(
        callback.from_user.id,
        enabled=True,
        reminder_time=",".join(sorted(times)),
    )
    await show_settings(callback, callback.from_user.id)


async def send_daily_reminder_to_user(user_id: int):
    habits = await get_user_habits(user_id)
    if not habits:
        return

    today = today_str()
    yesterday = yesterday_str()
    unmarked = [h for h in habits if h[5] != today]

    if not unmarked:
        return

    rows = []
    text = "<b>Мягкое напоминание</b>\n\n"

    for habit in unmarked:
        habit_id, _, _, streak, _, last_date, goal_days = habit
        risk = " · серия под угрозой" if last_date == yesterday and streak > 0 else ""
        text += f"• <b>{habit_name(habit)}</b> — {streak}/{goal_days}{risk}\n"
        rows.append([
            InlineKeyboardButton(text=f"Отметить: {habit[1]}", callback_data=f"mark_{habit_id}")
        ])

    try:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception as e:
        logger.error("Не удалось отправить напоминание пользователю %s: %s", user_id, e)


async def daily_reminder():
    now = datetime.now(ZoneInfo("Europe/Kyiv"))
    current_time = now.strftime("%H:%M")

    try:
        users = await get_users_by_reminder_time(current_time)

        for user_id in users:
            await send_daily_reminder_to_user(user_id)

    except Exception as e:
        logger.error("Ошибка daily_reminder: %s", e, exc_info=True)
