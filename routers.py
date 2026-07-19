# routers.py
import logging
import os
import re
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
)

from bot import bot
from db import (
    create_habit_group,
    date_range,
    delete_habit_group,
    delete_habit_from_db,
    disable_habit_reminder,
    get_due_habit_reminders,
    get_habit_group,
    get_habit_groups,
    get_habit_logs,
    get_habit_reminder,
    get_missed_habit_ids,
    get_user_habits,
    get_user_stats,
    is_habit_missed,
    mark_habit_completed,
    parse_date,
    parse_reminder_times,
    record_habit_miss,
    save_habit,
    set_habit_group,
    set_habit_reminder,
    today_str,
    unmark_habit_completed,
    update_habit_group_emoji,
    update_habit_name,
)

router = Router()
logger = logging.getLogger(__name__)
APP_VERSION = "2026.07.01.1"
RAILWAY_PUBLIC_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN")
MINI_APP_URL = os.getenv("MINI_APP_URL") or (
    f"https://{RAILWAY_PUBLIC_DOMAIN}/miniapp" if RAILWAY_PUBLIC_DOMAIN else None
)

REMINDER_PRESETS = {
    "morning": ("Утро", ["08:00"]),
    "day": ("День", ["13:00"]),
    "evening": ("Вечер", ["20:00"]),
    "often": ("Часто", ["08:00", "11:00", "14:00", "17:00", "20:00"]),
}


class Form(StatesGroup):
    waiting_habit_name = State()
    waiting_group_name = State()
    waiting_group_emoji = State()
    waiting_new_name = State()
    waiting_habit_reminder_time = State()


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟢 Сегодня")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
    is_persistent=True,
)


def progress_bar(percent: int, width: int = 10) -> str:
    filled = max(0, min(width, round(width * percent / 100)))
    color = "🟩" if percent >= 80 else "🟨" if percent >= 45 else "🟥"
    return color * filled + "⬜" * (width - filled)


def habit_name(habit) -> str:
    return escape(habit[1])


def habit_tracks_progress(habit) -> bool:
    return False


def group_name(group) -> str:
    return escape(group[1])


def group_emoji(group) -> str:
    if len(group) > 3 and group[3]:
        return escape(group[3])
    return "🎯"


def group_title(group) -> str:
    return f"{group_emoji(group)} {group_name(group)}"


def normalize_group_emoji(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    first = value.split()[0]
    if len(first) > 8:
        return None
    if re.search(r"[A-Za-zА-Яа-яЁё0-9]", first):
        return None
    return first


def daily_status(done: int, total: int) -> tuple[str, int]:
    if total == 0:
        return "⚪", 0

    percent = round(done / total * 100)
    if percent == 100:
        return "🟢", percent
    if percent >= 50:
        return "🟡", percent
    return "🔴", percent


def normalize_reminder_time(value: str) -> str | None:
    value = value.strip().replace(".", ":")
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError:
        return None
    return parsed.strftime("%H:%M")


def normalize_reminder_times(value: str) -> list[str]:
    preset_aliases = {
        "утро": REMINDER_PRESETS["morning"][1],
        "день": REMINDER_PRESETS["day"][1],
        "вечер": REMINDER_PRESETS["evening"][1],
        "часто": REMINDER_PRESETS["often"][1],
        "несколько": REMINDER_PRESETS["often"][1],
    }
    preset = preset_aliases.get(value.strip().lower())
    if preset:
        return preset

    raw_items = value.replace(",", " ").replace(";", " ").split()
    times = []

    for item in raw_items:
        reminder_time = normalize_reminder_time(item)
        if not reminder_time:
            return []
        times.append(reminder_time)

    return sorted(set(times))



def completed_analysis_dates(dates: list[str]) -> list[str]:
    today = today_str()
    return [date for date in dates if date != today]


def single_habit_completion(completed_dates: set[str], available_dates: list[str], dates: list[str]) -> tuple[int, int, int]:
    period_dates = [date for date in dates if date in available_dates]
    possible = len(period_dates)
    done = sum(1 for date in period_dates if date in completed_dates)
    rate = round(done / possible * 100) if possible else 0
    return done, possible, rate


def completion_for_dates(stats: dict, dates: list[str]) -> tuple[int, int, int]:
    possible = 0
    done = 0

    for habit in stats["habits"]:
        created = parse_date(habit[2])
        habit_dates = [date for date in dates if parse_date(date) >= created]
        possible += len(habit_dates)

    for date in dates:
        done += stats["daily_done"].get(date, 0)

    rate = round(done / possible * 100) if possible else 0
    return done, possible, rate


def week_comparison(stats: dict) -> dict:
    dates = completed_analysis_dates(stats["dates"])
    current_dates = dates[-7:]
    previous_dates = dates[-14:-7]
    current_done, current_possible, current_rate = completion_for_dates(stats, current_dates)
    previous_done, previous_possible, previous_rate = completion_for_dates(stats, previous_dates)
    diff = current_rate - previous_rate

    if current_possible == 0:
        note = "Пока нет завершённых дней для анализа."
    elif previous_possible == 0:
        note = "Предыдущего отрезка ещё нет для сравнения."
    elif diff >= 10:
        note = "Последние дни стали заметно сильнее."
    elif diff > 0:
        note = "Есть лёгкий рост."
    elif diff <= -10:
        note = "В последние дни стало тяжелее."
    elif diff < 0:
        note = "Небольшой спад, без драмы."
    else:
        note = "Темп держится ровно."

    return {
        "current_done": current_done,
        "current_possible": current_possible,
        "current_rate": current_rate,
        "previous_done": previous_done,
        "previous_possible": previous_possible,
        "previous_rate": previous_rate,
        "diff": diff,
        "has_current": current_possible > 0,
        "has_previous": previous_possible > 0,
        "note": note,
    }


async def habit_breakdown(user_id: int, days: int = 14, group_id: int | None = None) -> list[dict]:
    habits = await get_user_habits(user_id, group_id=group_id, ungrouped_only=group_id is None)
    logs = await get_habit_logs(user_id, days=days)
    dates = completed_analysis_dates(date_range(days))
    completed_by_habit: dict[int, set[str]] = {}

    for habit_id, completed_date in logs:
        completed_by_habit.setdefault(habit_id, set()).add(completed_date)

    result = []
    for habit in habits:
        habit_id = habit[0]
        created = parse_date(habit[2])
        available_dates = [date for date in dates if parse_date(date) >= created]
        completed_dates = completed_by_habit.get(habit_id, set())
        visible_dates = available_dates[-7:]
        done = sum(1 for date in visible_dates if date in completed_dates)
        possible = len(visible_dates)
        missed = max(possible - done, 0)
        rate = round(done / possible * 100) if possible else 0
        heatmap = "".join("🟢" if date in completed_dates else "⚪" for date in visible_dates)

        result.append({
            "habit": habit,
            "done": done,
            "possible": possible,
            "missed": missed,
            "rate": rate,
            "heatmap": heatmap or "⚪",
        })

    return result


async def habit_diary(user_id: int, habit_id: int, days: int = 30) -> dict | None:
    habits = await get_user_habits(user_id)
    habit = next((item for item in habits if item[0] == habit_id), None)
    if not habit:
        return None

    dates = date_range(days)
    created = parse_date(habit[2])
    available_dates = [date for date in dates if parse_date(date) >= created]
    closed_available_dates = completed_analysis_dates(available_dates)
    logs = await get_habit_logs(user_id, habit_id=habit_id, days=days)
    completed_dates = {completed_date for _, completed_date in logs}
    done = sum(1 for date in closed_available_dates if date in completed_dates)
    possible = len(closed_available_dates)
    not_marked = max(possible - done, 0)
    rate = round(done / possible * 100) if possible else 0
    current_done, current_possible, current_rate = single_habit_completion(
        completed_dates,
        closed_available_dates,
        closed_available_dates[-7:],
    )

    calendar = []
    for date in available_dates[-30:]:
        mark = "🟢" if date in completed_dates else "⚪"
        day = datetime.strptime(date, "%Y-%m-%d").strftime("%d")
        calendar.append(f"{mark}{day}")

    weeks = [" ".join(calendar[index:index + 7]) for index in range(0, len(calendar), 7)]
    today_done = habit[5] == today_str()
    today_missed = await is_habit_missed(user_id, habit_id)
    reminder = await get_habit_reminder(user_id, habit_id)
    group = await get_habit_group(user_id, habit[7]) if habit[7] is not None else None
    progress = None

    return {
        "habit": habit,
        "done": done,
        "possible": possible,
        "not_marked": not_marked,
        "rate": rate,
        "current_done": current_done,
        "current_possible": current_possible,
        "current_rate": current_rate,
        "calendar": "\n".join(weeks) if weeks else "Пока нет дней для анализа.",
        "today_done": today_done,
        "today_missed": today_missed,
        "reminder": reminder,
        "group": group,
        "progress": progress,
    }


def format_habit_diary_text(item: dict) -> str:
    habit = item["habit"]
    today_status = "выполнено" if item["today_done"] else ("не сегодня" if item["today_missed"] else "не отмечено")
    reminder = item["reminder"]
    reminder_text = (
        ", ".join(parse_reminder_times(reminder["reminder_time"]))
        if reminder and reminder["enabled"]
        else "нет"
    )
    return (
        f"📖 <b>{habit_name(habit)}</b>\n\n"
        f"Сегодня: <b>{today_status}</b>\n"
        f"Напоминание: <b>{reminder_text}</b>\n"
        f"30 дней: <b>{item['done']}/{item['possible']} · {item['rate']}%</b>\n"
        f"7 дней: <b>{item['current_done']}/{item['current_possible']} · {item['current_rate']}%</b>\n\n"
        f"🗓 <b>Календарь</b>\n<code>{item['calendar']}</code>\n\n"
        "Сегодня не входит в проценты до конца дня."
    )

def compact_stats_text(
    stats: dict,
    breakdown: list[dict],
    comparison: dict,
    title: str = "Статистика",
    scope_note: str = "",
) -> str:
    current_text = (
        f"{comparison['current_done']}/{comparison['current_possible']} · {comparison['current_rate']}%"
        if comparison["has_current"]
        else "нет данных"
    )
    previous_text = (
        f"{comparison['previous_done']}/{comparison['previous_possible']} · {comparison['previous_rate']}%"
        if comparison["has_previous"]
        else "нет данных"
    )
    diff_text = f"{comparison['diff']:+d}%" if comparison["has_current"] and comparison["has_previous"] else "пока нет"
    lines = [
        f"🔵 <b>{title}</b>",
        scope_note or "За 30 дней, сегодня не входит в проценты.",
        "",
        f"Сегодня: <b>{stats['today_done']}/{stats['habits_count']}</b>",
        f"30 дней: <b>{stats['period_completed']}/{stats['possible']} · {stats['completion_rate']}%</b>",
        f"Последние 7 дней: <b>{current_text}</b>",
        f"7 дней до этого: <b>{previous_text}</b>",
        f"Разница: <b>{diff_text}</b>",
        f"Вывод: {comparison['note']}",
        "",
        "🟣 <b>Привычки за 7 дней</b>",
    ]

    if not breakdown:
        lines.append("Пока нет данных.")
        return "\n".join(lines)

    for item in breakdown:
        lines.append(
            f"• <b>{habit_name(item['habit'])}</b> — {item['done']}/{item['possible']} · {item['rate']}%"
        )
        lines.append(format_heatmap(item["heatmap"]))

    return "\n".join(lines)


def format_heatmap(heatmap: str) -> str:
    return " ".join(list(heatmap))



async def answer_or_edit(obj: types.Message | types.CallbackQuery, text: str, reply_markup=None):
    if isinstance(obj, types.CallbackQuery):
        await obj.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
        await obj.answer()
    else:
        await obj.answer(text, parse_mode="HTML", reply_markup=reply_markup)


async def main_summary(user_id: int) -> str:
    habits = await get_user_habits(user_id)

    if not habits:
        return (
            "🟣 <b>HabitFlow</b>\n"
            "Пока привычек нет. Добавь одно маленькое действие."
        )

    today = today_str()
    done = sum(1 for h in habits if h[5] == today)
    status, percent = daily_status(done, len(habits))

    lines = [
        "🟣 <b>HabitFlow</b>",
        f"{status} Сегодня: <b>{done}/{len(habits)}</b> · {percent}%",
        progress_bar(percent),
    ]

    return "\n".join(lines)


def habit_actions_keyboard(habits, groups) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Привычка", callback_data="add_habit")]]

    for habit in habits:
        habit_id = habit[0]
        rows.append([
            InlineKeyboardButton(text=f"📖 {habit[1][:24]}", callback_data=f"habit_diary_{habit_id}"),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def reminder_button_text(reminder: dict | None) -> str:
    if reminder and reminder["enabled"]:
        times = parse_reminder_times(reminder["reminder_time"])
        return f"⏰ {', '.join(times[:2])}" + ("…" if len(times) > 2 else "")
    return "⏰ Напомнить"



def group_emoji_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🍳", callback_data="group_emoji_🍳"),
            InlineKeyboardButton(text="💪", callback_data="group_emoji_💪"),
            InlineKeyboardButton(text="📚", callback_data="group_emoji_📚"),
            InlineKeyboardButton(text="🧘", callback_data="group_emoji_🧘"),
        ],
        [
            InlineKeyboardButton(text="🥗", callback_data="group_emoji_🥗"),
            InlineKeyboardButton(text="💧", callback_data="group_emoji_💧"),
            InlineKeyboardButton(text="☀️", callback_data="group_emoji_☀️"),
            InlineKeyboardButton(text="🌙", callback_data="group_emoji_🌙"),
        ],
        [InlineKeyboardButton(text="🎯 По умолчанию", callback_data="group_emoji_🎯")],
    ])



def habit_has_progress(item: dict) -> bool:
    return False


def habit_diary_keyboard(
    habit_id: int,
    done_today: bool,
    missed_today: bool,
    reminder: dict | None = None,
    show_progress: bool = False,
) -> InlineKeyboardMarkup:
    rows = []
    if done_today:
        rows.append([
            InlineKeyboardButton(text="↩️ Отменить выполнение", callback_data=f"undo_diary_{habit_id}"),
        ])
    elif not missed_today:
        rows.append([
            InlineKeyboardButton(text="✅ Выполнил", callback_data=f"mark_diary_{habit_id}"),
            InlineKeyboardButton(text="⚪ Не сегодня", callback_data=f"miss_diary_{habit_id}"),
        ])
    elif missed_today:
        rows.append([InlineKeyboardButton(text="✅ Всё-таки выполнил", callback_data=f"mark_diary_{habit_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def habit_settings_keyboard(habit_id: int, reminder: dict | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=reminder_button_text(reminder), callback_data=f"habit_reminder_custom_{habit_id}")],
        [
            InlineKeyboardButton(text="✏️ Название", callback_data=f"edit_{habit_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_ask_{habit_id}"),
        ],
        [InlineKeyboardButton(text="🔵 Назад", callback_data=f"habit_diary_{habit_id}")],
    ])


def habit_reminder_keyboard(habit_id: int, reminder: dict | None) -> InlineKeyboardMarkup:
    rows = []
    selected = bool(reminder and reminder["enabled"])

    rows.append([InlineKeyboardButton(text="⏰ Изменить время", callback_data=f"habit_reminder_custom_{habit_id}")])
    if selected:
        rows.append([InlineKeyboardButton(text="🔕 Отключить", callback_data=f"habit_reminder_off_{habit_id}")])
    rows.append([InlineKeyboardButton(text="🔵 Назад", callback_data=f"habit_diary_{habit_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quick_reminder_keyboard(habit_id: int, has_reminder: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="🌅 Утро", callback_data=f"habit_reminder_preset_{habit_id}_morning"),
            InlineKeyboardButton(text="☀️ День", callback_data=f"habit_reminder_preset_{habit_id}_day"),
        ],
        [
            InlineKeyboardButton(text="🌙 Вечер", callback_data=f"habit_reminder_preset_{habit_id}_evening"),
            InlineKeyboardButton(text="🔁 Часто", callback_data=f"habit_reminder_preset_{habit_id}_often"),
        ],
    ]
    if has_reminder:
        rows.append([InlineKeyboardButton(text="🔕 Отключить", callback_data=f"habit_reminder_off_{habit_id}")])
    rows.append([InlineKeyboardButton(text="🔵 Назад", callback_data=f"habit_diary_{habit_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stats_keyboard(groups=()) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="🟢 Сегодня", callback_data="open_today"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_keyboard(group_id: int, habits) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="➕ Новая", callback_data=f"add_habit_group_{group_id}"),
            InlineKeyboardButton(text="📥 Добавить", callback_data=f"group_add_existing_{group_id}"),
            InlineKeyboardButton(text="⚙️ Тема", callback_data=f"group_settings_{group_id}"),
        ],
    ]
    for habit in habits:
        rows.append([
            InlineKeyboardButton(text=f"📖 {habit[1][:24]}", callback_data=f"habit_diary_{habit[0]}"),
        ])
    rows.extend([
        [InlineKeyboardButton(text="🟣 Все привычки", callback_data="open_habits")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_settings_keyboard(group_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔵 Статистика", callback_data=f"group_stats_{group_id}")],
        [InlineKeyboardButton(text="🎨 Эмодзи", callback_data=f"group_emoji_edit_{group_id}")],
        [InlineKeyboardButton(text="🗑 Удалить тему", callback_data=f"group_delete_ask_{group_id}")],
        [InlineKeyboardButton(text="🔵 Назад", callback_data=f"group_open_{group_id}")],
    ])


def group_existing_habits_keyboard(group_id: int, habits) -> InlineKeyboardMarkup:
    rows = []
    for habit in habits:
        rows.append([
            InlineKeyboardButton(
                text=f"📥 {habit[1][:26]}",
                callback_data=f"group_add_existing_pick_{group_id}_{habit[0]}",
            ),
        ])
    rows.append([InlineKeyboardButton(text="🔵 Назад", callback_data=f"group_open_{group_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def habit_group_picker(habit_id: int, groups) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Основные", callback_data=f"habit_group_set_{habit_id}_none")]]
    for group in groups:
        rows.append([
            InlineKeyboardButton(
                text=f"{group_emoji(group)} {group[1][:24]}",
                callback_data=f"habit_group_set_{habit_id}_{group[0]}",
            ),
        ])
    rows.append([InlineKeyboardButton(text="🔵 Назад", callback_data=f"habit_diary_{habit_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Меню всегда под рукой.", reply_markup=main_keyboard)
    await show_today(message, message.from_user.id)


@router.message(Command("app"))
async def open_mini_app(message: types.Message, state: FSMContext):
    await state.clear()
    if not MINI_APP_URL:
        await message.answer("Mini App пока не настроен.", reply_markup=main_keyboard)
        return
    await message.answer("Mini App теперь в кнопке меню рядом с полем ввода.", reply_markup=main_keyboard)


@router.message(Command("version"))
async def version(message: types.Message):
    await message.answer(f"HabitFlow <b>{APP_VERSION}</b>", parse_mode="HTML")


@router.message(F.text.in_(["🟢 Сегодня", "Сегодня"]))
async def today(message: types.Message, state: FSMContext):
    await state.clear()
    await show_today(message, message.from_user.id)


@router.message(Command("stats"))
@router.message(F.text.in_(["🔵 Статистика", "Статистика"]))
async def statistics(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Статистика теперь в Mini App: открой кнопку меню рядом с полем ввода.", reply_markup=main_keyboard)


async def show_statistics(obj: types.Message | types.CallbackQuery, user_id: int):
    stats = await get_user_stats(user_id, days=30)
    groups = await get_habit_groups(user_id)

    if stats["habits_count"] == 0:
        text = (
            "🔵 <b>Основные привычки</b>\n\n"
            "Здесь считаются привычки без темы."
        )
        if groups:
            text += "\nТемы ниже считаются отдельно."
        else:
            text += "\nПока нечего считать. Добавь первую привычку."
        await answer_or_edit(
            obj,
            text,
            stats_keyboard(groups),
        )
        return

    breakdown = await habit_breakdown(user_id, days=8)
    comparison = week_comparison(stats)
    await answer_or_edit(
        obj,
        compact_stats_text(
            stats,
            breakdown,
            comparison,
            "Основные привычки",
            "За 30 дней. Темы считаются отдельно кнопками ниже.",
        ),
        stats_keyboard(groups),
    )


@router.callback_query(F.data == "open_stats")
async def open_stats(callback: types.CallbackQuery):
    await callback.answer("Статистика теперь в Mini App", show_alert=True)


@router.callback_query(F.data.startswith("group_stats_"))
async def show_group_statistics(callback: types.CallbackQuery):
    group_id = int(callback.data.split("_")[-1])
    group = await get_habit_group(callback.from_user.id, group_id)
    if not group:
        await callback.answer("Тема не найдена", show_alert=True)
        return

    stats = await get_user_stats(callback.from_user.id, days=30, group_id=group_id)
    if stats["habits_count"] == 0:
        await answer_or_edit(
            callback,
            f"🔵 <b>{group_name(group)}</b>\n\nВ этой теме пока нет привычек.",
            group_keyboard(group_id, []),
        )
        return

    breakdown = await habit_breakdown(callback.from_user.id, days=8, group_id=group_id)
    comparison = week_comparison(stats)
    await answer_or_edit(
        callback,
        compact_stats_text(
            stats,
            breakdown,
            comparison,
            f"Тема: {group_title(group)}",
            "Статистика только этой темы. Сегодня не входит в проценты.",
        ),
        group_keyboard(group_id, stats["habits"]),
    )


@router.callback_query(F.data.startswith("habit_diary_"))
async def show_habit_diary(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    habit_id = int(callback.data.split("_")[-1])
    item = await habit_diary(callback.from_user.id, habit_id, days=30)

    if not item:
        await callback.answer("Привычка не найдена", show_alert=True)
        return

    await answer_or_edit(
        callback,
        format_habit_diary_text(item),
        habit_diary_keyboard(habit_id, item["today_done"], item["today_missed"], item["reminder"], habit_has_progress(item)),
    )


@router.callback_query(F.data.startswith("habit_settings_"))
async def show_habit_settings(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Управление теперь в Mini App", show_alert=True)


@router.callback_query(F.data.startswith("mark_diary_"))
async def process_mark_diary_callback(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    success, info = await mark_habit_completed(callback.from_user.id, habit_id)

    if not success:
        await callback.answer("Уже отмечено сегодня", show_alert=True)
        return

    await callback.answer(f"Отмечено: {info['habit_name']}")
    item = await habit_diary(callback.from_user.id, habit_id, days=30)
    if not item:
        await show_habits(callback, callback.from_user.id)
        return

    await answer_or_edit(
        callback,
        format_habit_diary_text(item),
        habit_diary_keyboard(habit_id, item["today_done"], item["today_missed"], item["reminder"], habit_has_progress(item)),
    )


@router.callback_query(F.data.startswith("undo_diary_"))
async def process_undo_diary_callback(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    success, info = await unmark_habit_completed(callback.from_user.id, habit_id)

    if not success:
        await callback.answer("Сегодняшней отметки уже нет", show_alert=True)
        return

    await callback.answer(f"Отменено: {info['habit_name']}")
    item = await habit_diary(callback.from_user.id, habit_id, days=30)
    if not item:
        await show_habits(callback, callback.from_user.id)
        return

    await answer_or_edit(
        callback,
        format_habit_diary_text(item),
        habit_diary_keyboard(habit_id, item["today_done"], item["today_missed"], item["reminder"], habit_has_progress(item)),
    )


@router.callback_query(F.data.startswith("miss_diary_"))
async def process_miss_diary_callback(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    await record_habit_miss(callback.from_user.id, habit_id)
    await callback.answer("Отмечено: не сегодня")

    item = await habit_diary(callback.from_user.id, habit_id, days=30)
    if not item:
        await show_habits(callback, callback.from_user.id)
        return

    await answer_or_edit(
        callback,
        format_habit_diary_text(item),
        habit_diary_keyboard(habit_id, item["today_done"], item["today_missed"], item["reminder"], habit_has_progress(item)),
    )



@router.callback_query(F.data.startswith("habit_reminder_preset_"))
async def set_habit_reminder_preset(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    habit_id = int(parts[-2])
    preset_key = parts[-1]
    preset = REMINDER_PRESETS.get(preset_key)

    if not preset:
        await callback.answer("Вариант не найден", show_alert=True)
        return

    _, times = preset
    saved = await set_habit_reminder(callback.from_user.id, habit_id, ",".join(times), enabled=True)
    await state.clear()

    if not saved:
        await callback.answer("Привычка не найдена", show_alert=True)
        return

    item = await habit_diary(callback.from_user.id, habit_id, days=30)
    if not item:
        await show_habits(callback, callback.from_user.id)
        return

    await answer_or_edit(
        callback,
        format_habit_diary_text(item),
        habit_diary_keyboard(habit_id, item["today_done"], item["today_missed"], item["reminder"], habit_has_progress(item)),
    )


@router.callback_query(F.data.startswith("habit_reminder_custom_"))
async def custom_habit_reminder_time(callback: types.CallbackQuery, state: FSMContext):
    habit_id = int(callback.data.split("_")[-1])
    reminder = await get_habit_reminder(callback.from_user.id, habit_id)
    await state.update_data(habit_reminder_id=habit_id)
    await callback.message.answer(
        "Выбери готовый вариант или напиши время:\n\n"
        "<b>07:30</b>\n"
        "<b>09:00 12:00 15:00 18:00</b>\n\n"
        "Можно написать: <b>утро</b>, <b>день</b>, <b>вечер</b> или <b>часто</b>.",
        parse_mode="HTML",
        reply_markup=quick_reminder_keyboard(habit_id, bool(reminder and reminder["enabled"])),
    )
    await state.set_state(Form.waiting_habit_reminder_time)
    await callback.answer()


@router.callback_query(F.data.startswith("habit_reminder_off_"))
async def disable_habit_reminder_callback(callback: types.CallbackQuery, state: FSMContext):
    habit_id = int(callback.data.split("_")[-1])
    await disable_habit_reminder(callback.from_user.id, habit_id)
    await state.clear()

    item = await habit_diary(callback.from_user.id, habit_id, days=30)
    if not item:
        await show_habits(callback, callback.from_user.id)
        return

    await answer_or_edit(
        callback,
        format_habit_diary_text(item),
        habit_diary_keyboard(habit_id, item["today_done"], item["today_missed"], item["reminder"], habit_has_progress(item)),
    )


@router.callback_query(F.data.startswith("habit_reminder_"))
async def open_habit_reminder(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    await show_habit_reminder(callback, habit_id)


async def show_habit_reminder(callback: types.CallbackQuery, habit_id: int):
    habits = await get_user_habits(callback.from_user.id)
    habit = next((item for item in habits if item[0] == habit_id), None)

    if not habit:
        await callback.answer("Привычка не найдена", show_alert=True)
        return

    reminder = await get_habit_reminder(callback.from_user.id, habit_id)
    enabled = bool(reminder and reminder["enabled"])
    status = "🟢 включено" if enabled else "⚪ выключено"
    reminder_times = ", ".join(parse_reminder_times(reminder["reminder_time"])) if enabled else "не выбрано"
    text = (
        "⏰ <b>Напоминание о привычке</b>\n\n"
        f"📖 <b>{habit_name(habit)}</b>\n"
        f"Статус: <b>{status}</b>\n"
        f"Время: <b>{reminder_times}</b>\n\n"
        "Можно поставить одно время или несколько, например для воды."
    )
    await answer_or_edit(callback, text, habit_reminder_keyboard(habit_id, reminder))


@router.message(Form.waiting_habit_reminder_time)
async def save_custom_habit_reminder_time(message: types.Message, state: FSMContext):
    reminder_times = normalize_reminder_times(message.text)

    if not reminder_times:
        await message.answer(
            "Не понял время. Напиши так: <b>07:30</b> или <b>09:00 12:00 18:00</b>.",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    habit_id = data.get("habit_reminder_id")

    if not habit_id:
        await message.answer("Не нашёл привычку. Открой её ещё раз.", reply_markup=main_keyboard)
        await state.clear()
        return

    reminder_value = ",".join(reminder_times)
    saved = await set_habit_reminder(message.from_user.id, habit_id, reminder_value, enabled=True)
    await state.clear()

    if not saved:
        await message.answer("Привычка не найдена.", reply_markup=main_keyboard)
        return

    item = await habit_diary(message.from_user.id, habit_id, days=30)
    if not item:
        await message.answer(
            f"Готово. Напоминание: <b>{', '.join(reminder_times)}</b>",
            parse_mode="HTML",
            reply_markup=main_keyboard,
        )
        return

    await message.answer(
        format_habit_diary_text(item),
        parse_mode="HTML",
        reply_markup=habit_diary_keyboard(habit_id, item["today_done"], item["today_missed"], item["reminder"], habit_has_progress(item)),
    )


@router.callback_query(F.data == "open_today")
async def open_today(callback: types.CallbackQuery):
    await show_today(callback, callback.from_user.id)


async def show_today(obj: types.Message | types.CallbackQuery, user_id: int):
    habits = await get_user_habits(user_id)

    if not habits:
        await answer_or_edit(
            obj,
            f"{await main_summary(user_id)}\n\nДобавление и управление теперь в Mini App.",
        )
        return

    today = today_str()
    missed_today = await get_missed_habit_ids(user_id)
    unmarked = [h for h in habits if h[5] != today and h[0] not in missed_today]
    completed = [h for h in habits if h[5] == today]
    rows = []
    text = await main_summary(user_id)

    if not unmarked:
        if missed_today:
            text += "\n\n🟢 На сегодня всё решено."
        else:
            text += "\n\n🟢 Всё отмечено. Хороший день."
    else:
        text += "\n\n<b>Сегодня не отмечено:</b>"
        for habit in unmarked:
            habit_id = habit[0]
            text += f"\n• <b>{habit_name(habit)}</b>"
            rows.append([
                InlineKeyboardButton(text=f"✅ {habit[1][:18]}", callback_data=f"mark_{habit_id}"),
            ])

    if completed:
        text += "\n\n<b>Выполнено:</b>"
        for habit in completed:
            text += f"\n• <b>{habit_name(habit)}</b>"
            rows.append([
                InlineKeyboardButton(
                    text=f"↩️ Отменить: {habit[1][:18]}",
                    callback_data=f"undo_{habit[0]}",
                ),
            ])

    await answer_or_edit(obj, text, InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


@router.callback_query(F.data.startswith("mark_"))
async def process_mark_callback(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[1])
    success, info = await mark_habit_completed(callback.from_user.id, habit_id)

    if not success:
        await callback.answer("Уже отмечено сегодня", show_alert=True)
        return

    await callback.answer(f"Отмечено: {info['habit_name']}")
    await show_today(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("undo_"))
async def process_undo_callback(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    success, info = await unmark_habit_completed(callback.from_user.id, habit_id)

    if not success:
        await callback.answer("Сегодняшней отметки уже нет", show_alert=True)
        return

    await callback.answer(f"Отменено: {info['habit_name']}")
    await show_today(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("miss_"))
async def process_miss_callback(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    await record_habit_miss(callback.from_user.id, habit_id)
    await callback.answer("Отмечено: не сегодня")
    await show_today(callback, callback.from_user.id)


@router.message(F.text.in_(["🟣 Привычки", "Привычки"]))
async def habits(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Управление привычками теперь в Mini App: открой кнопку меню рядом с полем ввода.", reply_markup=main_keyboard)


@router.message(F.text.in_(["⚙️ Настройки", "Настройки"]))
async def old_settings_button(message: types.Message):
    await message.answer(
        "Настройки упростили. Напоминания теперь находятся внутри каждой привычки.",
        reply_markup=main_keyboard,
    )


async def show_habits(obj: types.Message | types.CallbackQuery, user_id: int):
    habits = await get_user_habits(user_id)

    if not habits:
        text = "🟣 <b>Дневник привычек</b>\n\nСписок пуст. Начнём с одной."
    else:
        text = "🟣 <b>Дневник привычек</b>\n\nСписок плоский. Папки убраны."
        for habit in habits:
            _, _, _, _, total_completed, last_date, _, _, *_ = habit
            done_today = " 🟢" if last_date == today_str() else ""
            text += (
                f"\n📖 <b>{habit_name(habit)}</b>{done_today}\n"
                f"{'Сегодня отмечено' if last_date == today_str() else 'Сегодня не отмечено'} · всего: {total_completed}\n"
            )

    await answer_or_edit(obj, text, habit_actions_keyboard(habits, ()))


@router.callback_query(F.data.startswith("group_open_"))
async def open_group(callback: types.CallbackQuery):
    group_id = int(callback.data.split("_")[-1])
    group = await get_habit_group(callback.from_user.id, group_id)
    if not group:
        await callback.answer("Тема не найдена", show_alert=True)
        return

    habits = await get_user_habits(callback.from_user.id, group_id=group_id)
    text = f"<b>{group_title(group)}</b>"
    if not habits:
        text += "\n\nВ этой теме пока пусто. Добавь первую привычку."
    else:
        today = today_str()
        done = sum(1 for habit in habits if habit[5] == today)
        text += f"\n\nСегодня: <b>{done}/{len(habits)}</b>"
        for habit in habits:
            status = "🟢" if habit[5] == today else "⚪"
            text += f"\n{status} <b>{habit_name(habit)}</b>"

    await answer_or_edit(callback, text, group_keyboard(group_id, habits))


@router.callback_query(F.data.startswith("group_settings_"))
async def open_group_settings(callback: types.CallbackQuery):
    group_id = int(callback.data.split("_")[-1])
    group = await get_habit_group(callback.from_user.id, group_id)
    if not group:
        await callback.answer("Тема не найдена", show_alert=True)
        return

    await answer_or_edit(
        callback,
        f"<b>{group_title(group)}</b>\n\nНастройки темы.",
        group_settings_keyboard(group_id),
    )


@router.callback_query(F.data.startswith("group_add_existing_"))
async def add_existing_habit_to_group(callback: types.CallbackQuery):
    if callback.data.startswith("group_add_existing_pick_"):
        parts = callback.data.split("_")
        group_id = int(parts[-2])
        habit_id = int(parts[-1])
        updated = await set_habit_group(callback.from_user.id, habit_id, group_id)
        if not updated:
            await callback.answer("Не удалось добавить привычку", show_alert=True)
            return

        group = await get_habit_group(callback.from_user.id, group_id)
        habits = await get_user_habits(callback.from_user.id, group_id=group_id)
        text = f"<b>{group_title(group)}</b>\n\nПривычка добавлена в тему."
        today = today_str()
        done = sum(1 for habit in habits if habit[5] == today)
        text += f"\n\nСегодня: <b>{done}/{len(habits)}</b>"
        for habit in habits:
            status = "🟢" if habit[5] == today else "⚪"
            text += f"\n{status} <b>{habit_name(habit)}</b>"

        await answer_or_edit(callback, text, group_keyboard(group_id, habits))
        return

    group_id = int(callback.data.split("_")[-1])
    group = await get_habit_group(callback.from_user.id, group_id)
    if not group:
        await callback.answer("Тема не найдена", show_alert=True)
        return

    habits = await get_user_habits(callback.from_user.id, ungrouped_only=True)
    if not habits:
        await answer_or_edit(
            callback,
            f"<b>{group_title(group)}</b>\n\nВ основных привычках сейчас пусто. Можно создать новую привычку сразу в этой теме.",
            group_keyboard(group_id, await get_user_habits(callback.from_user.id, group_id=group_id)),
        )
        return

    await answer_or_edit(
        callback,
        f"<b>{group_title(group)}</b>\n\nВыбери привычку из «Основных», которую нужно добавить в эту тему.",
        group_existing_habits_keyboard(group_id, habits),
    )


@router.callback_query(F.data == "add_group")
async def new_group_start(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Как назовём тему?", reply_markup=main_keyboard)
    await state.set_state(Form.waiting_group_name)
    await callback.answer()


@router.message(Form.waiting_group_name)
async def new_group_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Название слишком короткое. Нужно хотя бы 2 символа.")
        return
    if len(name) > 30:
        await message.answer("Давай короче: до 30 символов.")
        return

    await state.update_data(new_group_name=name)
    await message.answer(
        f"<b>{escape(name)}</b>\n\nВыбери эмодзи для темы или отправь свой.",
        parse_mode="HTML",
        reply_markup=group_emoji_keyboard(),
    )
    await state.set_state(Form.waiting_group_emoji)


async def finish_group_creation(user_id: int, name: str, emoji: str, state: FSMContext, message: types.Message):
    created, group_id = await create_habit_group(user_id, name, emoji)
    await state.clear()
    if not created:
        await message.answer("Тема с таким названием уже есть.", reply_markup=main_keyboard)
        return

    if group_id:
        habits = await get_user_habits(user_id, group_id=group_id)
        await message.answer(
            f"{escape(emoji)} <b>{escape(name)}</b>\n\nТема создана. Добавь первую привычку.",
            parse_mode="HTML",
            reply_markup=group_keyboard(group_id, habits),
        )


@router.callback_query(Form.waiting_group_emoji, F.data.startswith("group_emoji_"))
async def choose_group_emoji(callback: types.CallbackQuery, state: FSMContext):
    emoji = normalize_group_emoji(callback.data.removeprefix("group_emoji_")) or "🎯"
    data = await state.get_data()
    edit_group_id = data.get("edit_group_emoji_id")
    if edit_group_id:
        updated = await update_habit_group_emoji(callback.from_user.id, edit_group_id, emoji)
        await state.clear()
        if not updated:
            await callback.answer("Не удалось изменить эмодзи", show_alert=True)
            return
        group = await get_habit_group(callback.from_user.id, edit_group_id)
        habits = await get_user_habits(callback.from_user.id, group_id=edit_group_id)
        await answer_or_edit(callback, f"<b>{group_title(group)}</b>\n\nЭмодзи обновлён.", group_keyboard(edit_group_id, habits))
        return

    name = data.get("new_group_name")
    if not name:
        await state.clear()
        await callback.answer("Не нашёл название темы", show_alert=True)
        return

    await finish_group_creation(callback.from_user.id, name, emoji, state, callback.message)
    await callback.answer()


@router.message(Form.waiting_group_emoji)
async def new_group_emoji(message: types.Message, state: FSMContext):
    emoji = normalize_group_emoji(message.text)
    if not emoji:
        await message.answer("Отправь один эмодзи, например 🍳, 📚 или 💪.", reply_markup=group_emoji_keyboard())
        return

    data = await state.get_data()
    edit_group_id = data.get("edit_group_emoji_id")
    if edit_group_id:
        updated = await update_habit_group_emoji(message.from_user.id, edit_group_id, emoji)
        await state.clear()
        if not updated:
            await message.answer("Не удалось изменить эмодзи.", reply_markup=main_keyboard)
            return
        group = await get_habit_group(message.from_user.id, edit_group_id)
        habits = await get_user_habits(message.from_user.id, group_id=edit_group_id)
        await message.answer(
            f"<b>{group_title(group)}</b>\n\nЭмодзи обновлён.",
            parse_mode="HTML",
            reply_markup=group_keyboard(edit_group_id, habits),
        )
        return

    name = data.get("new_group_name")
    if not name:
        await state.clear()
        await message.answer("Не нашёл название темы. Создай её ещё раз.", reply_markup=main_keyboard)
        return

    await finish_group_creation(message.from_user.id, name, emoji, state, message)


@router.callback_query(F.data.startswith("group_emoji_edit_"))
async def edit_group_emoji(callback: types.CallbackQuery, state: FSMContext):
    group_id = int(callback.data.split("_")[-1])
    group = await get_habit_group(callback.from_user.id, group_id)
    if not group:
        await callback.answer("Тема не найдена", show_alert=True)
        return

    await state.clear()
    await state.update_data(edit_group_emoji_id=group_id)
    await state.set_state(Form.waiting_group_emoji)
    await answer_or_edit(
        callback,
        f"<b>{group_title(group)}</b>\n\nВыбери новый эмодзи или отправь свой.",
        group_emoji_keyboard(),
    )


@router.callback_query(F.data == "add_habit")
@router.callback_query(F.data.startswith("add_habit_group_"))
async def new_habit_start(callback: types.CallbackQuery, state: FSMContext):
    group_id = int(callback.data.split("_")[-1]) if callback.data.startswith("add_habit_group_") else None
    await state.clear()
    if group_id is not None:
        await state.update_data(new_habit_group=group_id)
    await callback.message.answer(
        "Напиши привычку как маленькое действие.\n\n"
        "Не «читать книги», а <b>прочитать 1 страницу</b>.\n"
        "Не «спорт», а <b>1 подход отжиманий</b>.\n"
        "Не «питание», а <b>съесть завтрак</b>.",
        parse_mode="HTML",
        reply_markup=main_keyboard,
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
        await message.answer("Давай короче: до 40 символов.")
        return

    data = await state.get_data()
    group_id = data.get("new_habit_group")
    await save_habit(message.from_user.id, name, group_id=group_id)
    await state.clear()
    await message.answer(
        f"🟢 Готово: <b>{escape(name)}</b>\n"
        "Теперь каждый день просто отмечай: выполнил или нет.",
        parse_mode="HTML",
        reply_markup=main_keyboard,
    )
    await show_today(message, message.from_user.id)


@router.callback_query(F.data.startswith("habit_type_"))
async def choose_habit_type(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    name = data.get("new_habit_name")
    group_id = data.get("new_habit_group")

    if not name:
        await callback.answer("Сначала напиши название привычки", show_alert=True)
        return

    await save_habit(callback.from_user.id, name, group_id=group_id)
    await state.clear()
    await callback.message.answer(
        f"🟢 Готово: <b>{escape(name)}</b>\n"
        "Измерения убраны. Теперь только простая отметка: выполнил или нет.",
        parse_mode="HTML",
        reply_markup=main_keyboard,
    )
    await show_today(callback.message, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data.startswith("habit_group_"))
async def habit_group_actions(callback: types.CallbackQuery):
    if callback.data.startswith("habit_group_set_"):
        parts = callback.data.split("_")
        habit_id = int(parts[-2])
        value = parts[-1]
        group_id = None if value == "none" else int(value)
        updated = await set_habit_group(callback.from_user.id, habit_id, group_id)
        if not updated:
            await callback.answer("Не удалось изменить тему", show_alert=True)
            return

        item = await habit_diary(callback.from_user.id, habit_id, days=30)
        if item:
            await callback.message.edit_text(
                format_habit_diary_text(item),
                parse_mode="HTML",
                reply_markup=habit_diary_keyboard(
                    habit_id,
                    item["today_done"],
                    item["today_missed"],
                    item["reminder"],
                    habit_has_progress(item),
                ),
            )
        await callback.answer("Тема обновлена")
        return

    habit_id = int(callback.data.split("_")[-1])
    groups = await get_habit_groups(callback.from_user.id)
    await answer_or_edit(
        callback,
        "🎯 <b>Тема привычки</b>\n\n"
        "Основные привычки идут в общую статистику. Темы считаются отдельно.",
        habit_group_picker(habit_id, groups),
    )


@router.callback_query(F.data.startswith("group_delete_ask_"))
async def ask_delete_group(callback: types.CallbackQuery):
    group_id = int(callback.data.split("_")[-1])
    group = await get_habit_group(callback.from_user.id, group_id)
    if not group:
        await callback.answer("Тема не найдена", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Удалить", callback_data=f"group_delete_yes_{group_id}"),
            InlineKeyboardButton(text="Отмена", callback_data=f"group_open_{group_id}"),
        ],
    ])
    await answer_or_edit(
        callback,
        f"🗑 Удалить тему <b>{group_name(group)}</b>?\n\n"
        "Привычки не удалятся, а вернутся в «Основные».",
        keyboard,
    )


@router.callback_query(F.data.startswith("group_delete_yes_"))
async def delete_group(callback: types.CallbackQuery):
    group_id = int(callback.data.split("_")[-1])
    deleted = await delete_habit_group(callback.from_user.id, group_id)
    if not deleted:
        await callback.answer("Тема не найдена", show_alert=True)
        return
    await show_habits(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("edit_"))
async def start_edit_name(callback: types.CallbackQuery, state: FSMContext):
    habit_id = int(callback.data.split("_")[1])
    await state.update_data(editing_habit_id=habit_id)
    await callback.message.answer("Новое название привычки:", reply_markup=main_keyboard)
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


@router.callback_query(F.data.startswith("delete_ask_"))
async def ask_delete_habit(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Удалить", callback_data=f"delete_yes_{habit_id}"),
            InlineKeyboardButton(text="Отмена", callback_data="open_habits"),
        ]
    ])
    await answer_or_edit(callback, "🔴 Удалить привычку? История по ней тоже исчезнет.", kb)


@router.callback_query(F.data == "open_habits")
async def open_habits(callback: types.CallbackQuery):
    await callback.answer("Управление теперь в Mini App", show_alert=True)


@router.callback_query(F.data.startswith("delete_yes_"))
async def delete_habit(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    await delete_habit_from_db(callback.from_user.id, habit_id)
    await callback.answer("Удалено")
    await show_habits(callback, callback.from_user.id)


async def send_habit_reminder_to_user(user_id: int, habit_id: int, habit_name_text: str, last_completed_date: str | None):
    today = today_str()
    if last_completed_date == today or await is_habit_missed(user_id, habit_id):
        return

    text = (
        "⏰ <b>Напоминание</b>\n\n"
        f"📖 <b>{escape(habit_name_text)}</b>\n"
        "Отметь, как сегодня."
    )
    rows = [[
        InlineKeyboardButton(text="✅ Выполнил", callback_data=f"mark_diary_{habit_id}"),
        InlineKeyboardButton(text="⚪ Не сегодня", callback_data=f"miss_diary_{habit_id}"),
    ]]

    try:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
    except Exception as e:
        logger.error("Не удалось отправить напоминание о привычке %s пользователю %s: %s", habit_id, user_id, e)


async def daily_reminder():
    now = datetime.now(ZoneInfo("Europe/Kyiv"))
    current_time = now.strftime("%H:%M")

    try:
        habit_reminders = await get_due_habit_reminders(current_time)
        for user_id, habit_id, habit_name_text, last_completed_date in habit_reminders:
            await send_habit_reminder_to_user(user_id, habit_id, habit_name_text, last_completed_date)

    except Exception as e:
        logger.error("Ошибка daily_reminder: %s", e, exc_info=True)
