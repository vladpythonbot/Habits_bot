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
    date_range,
    delete_habit_from_db,
    get_habit_logs,
    get_reminder_settings,
    get_user_habits,
    get_user_stats,
    get_users_by_reminder_time,
    mark_habit_completed,
    parse_date,
    parse_reminder_times,
    save_habit,
    set_reminder_settings,
    today_str,
    update_habit_name,
)

router = Router()
logger = logging.getLogger(__name__)

REMINDER_CHOICES = ["09:00", "12:00", "15:00", "18:00", "21:00"]
WEEKDAY_NAMES = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


class Form(StatesGroup):
    waiting_habit_name = State()
    waiting_new_name = State()


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟢 Сегодня"), KeyboardButton(text="🔵 Статистика")],
        [KeyboardButton(text="🟣 Привычки"), KeyboardButton(text="⚙️ Настройки")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)


def progress_bar(percent: int, width: int = 10) -> str:
    filled = max(0, min(width, round(width * percent / 100)))
    color = "🟩" if percent >= 80 else "🟨" if percent >= 45 else "🟥"
    return color * filled + "⬜" * (width - filled)


def habit_name(habit) -> str:
    return escape(habit[1])


def daily_status(done: int, total: int) -> tuple[str, int]:
    if total == 0:
        return "⚪", 0

    percent = round(done / total * 100)
    if percent == 100:
        return "🟢", percent
    if percent >= 50:
        return "🟡", percent
    return "🔴", percent


def rate_color(rate: int) -> str:
    if rate >= 80:
        return "🟢"
    if rate >= 50:
        return "🟡"
    return "🔴"


def habit_status(rate: int, last_date: str | None) -> str:
    if rate >= 80:
        return "прижилась"
    if rate >= 50:
        return "закрепляется"
    return "на старте"


def render_heatmap(stats: dict) -> str:
    habits_count = max(stats["habits_count"], 1)
    cells = []

    for date in stats["dates"]:
        done = stats["daily_done"].get(date, 0)
        ratio = done / habits_count
        if done == 0:
            cells.append("⬜")
        elif ratio < 0.34:
            cells.append("🟥")
        elif ratio < 0.67:
            cells.append("🟨")
        else:
            cells.append("🟩")

    return "\n".join("".join(cells[i:i + 7]) for i in range(0, len(cells), 7))


def render_week_graph(stats: dict) -> str:
    dates = stats["dates"][-7:]
    habits_count = max(stats["habits_count"], 1)
    lines = []

    for date in dates:
        done = stats["daily_done"].get(date, 0)
        percent = round(done / habits_count * 100)
        day = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m")
        lines.append(f"{day} {progress_bar(percent, 6)} {done}/{habits_count}")

    return "\n".join(lines)


def render_month_calendar(stats: dict) -> str:
    habits_count = max(stats["habits_count"], 1)
    lines = []

    for index in range(0, len(stats["dates"]), 7):
        week = stats["dates"][index:index + 7]
        cells = []
        for date in week:
            done = stats["daily_done"].get(date, 0)
            ratio = done / habits_count
            if done == 0:
                cells.append("⬜")
            elif ratio < 0.5:
                cells.append("🟥")
            elif ratio < 1:
                cells.append("🟨")
            else:
                cells.append("🟩")
        lines.append("".join(cells))

    return "\n".join(lines)


def completion_for_dates(stats: dict, dates: list[str]) -> tuple[int, int, int]:
    possible = 0
    completed = 0

    for habit in stats["habits"]:
        created = parse_date(habit[2])
        for date in dates:
            if parse_date(date) >= created:
                possible += 1

    for date in dates:
        completed += stats["daily_done"].get(date, 0)

    rate = round(completed / possible * 100) if possible else 0
    return completed, possible, rate


def single_habit_completion(completed_dates: set[str], available_dates: list[str], dates: list[str]) -> tuple[int, int, int]:
    period_dates = [date for date in dates if date in available_dates]
    possible = len(period_dates)
    done = sum(1 for date in period_dates if date in completed_dates)
    rate = round(done / possible * 100) if possible else 0
    return done, possible, rate


def longest_empty_gap(completed_dates: set[str], available_dates: list[str]) -> int:
    longest = 0
    current = 0

    for date in available_dates:
        if date in completed_dates:
            current = 0
        else:
            current += 1
            longest = max(longest, current)

    return longest


def weekday_profile(completed_dates: set[str], available_dates: list[str]) -> tuple[str, str]:
    stats: dict[int, list[int]] = {index: [0, 0] for index in range(7)}

    for date in available_dates:
        weekday = parse_date(date).weekday()
        stats[weekday][1] += 1
        if date in completed_dates:
            stats[weekday][0] += 1

    rates = []
    for weekday, (done, possible) in stats.items():
        if possible:
            rates.append((weekday, round(done / possible * 100), done, possible))

    if not rates:
        return "пока нет", "пока нет"

    best = max(rates, key=lambda item: (item[1], item[2]))
    quiet = min(rates, key=lambda item: (item[1], item[2]))
    return (
        f"{WEEKDAY_NAMES[best[0]]} · {best[1]}%",
        f"{WEEKDAY_NAMES[quiet[0]]} · {quiet[1]}%",
    )


def stability_grade(rate: int, missed: int, habits_count: int) -> tuple[str, str]:
    if habits_count == 0:
        return "нет данных", "Добавь одну привычку и отметь её сегодня."
    if rate >= 85 and missed <= habits_count:
        return "хорошо прижилось", "Ничего не усложняй. Сохраняй тот же набор привычек."
    if rate >= 65:
        return "ритм формируется", "Лучше укрепить текущий набор, чем добавлять новые привычки."
    if rate >= 40:
        return "привычки ищут место", "Оставь самые лёгкие действия сверху и привяжи их к понятному моменту дня."
    return "нужно упростить", "На неделю оставь 1-2 самые лёгкие привычки, чтобы ритм появился без давления."


def weekly_trend(stats: dict) -> dict:
    current_dates = stats["dates"][-7:]
    previous_dates = stats["dates"][-14:-7]
    current_done, current_possible, current_rate = completion_for_dates(stats, current_dates)
    previous_done, previous_possible, previous_rate = completion_for_dates(stats, previous_dates)
    diff = current_rate - previous_rate

    if previous_possible == 0:
        label = "недостаточно данных для сравнения"
    elif diff > 0:
        label = f"+{diff}% к прошлой неделе"
    elif diff < 0:
        label = f"{diff}% к прошлой неделе"
    else:
        label = "без изменений"

    return {
        "current_done": current_done,
        "current_possible": current_possible,
        "current_rate": current_rate,
        "previous_done": previous_done,
        "previous_possible": previous_possible,
        "previous_rate": previous_rate,
        "has_previous": previous_possible > 0,
        "diff": diff,
        "label": label,
    }


def previous_week_line(trend: dict) -> str:
    if not trend["has_previous"]:
        return "Прошлая: <b>пока нет данных</b>\n"

    return f"Прошлая: <b>{trend['previous_rate']}%</b> ({trend['previous_done']}/{trend['previous_possible']})\n"


def previous_week_review_line(trend: dict) -> str:
    if not trend["has_previous"]:
        return "Прошлая неделя: <b>пока нет данных</b>\n"

    return f"Прошлая неделя: <b>{trend['previous_rate']}%</b> ({trend['previous_done']}/{trend['previous_possible']})\n"


def best_and_weak_days(stats: dict) -> tuple[str, str]:
    dates = stats["dates"][-7:]
    day_rates = []

    for date in dates:
        _, possible, rate = completion_for_dates(stats, [date])
        if possible:
            day_rates.append((date, rate))

    if not day_rates:
        return "пока нет", "пока нет"

    best = max(day_rates, key=lambda item: item[1])
    weak = min(day_rates, key=lambda item: item[1])
    best_text = f"{datetime.strptime(best[0], '%Y-%m-%d').strftime('%d.%m')} · {best[1]}%"
    weak_text = f"{datetime.strptime(weak[0], '%Y-%m-%d').strftime('%d.%m')} · {weak[1]}%"
    return best_text, weak_text


async def habit_breakdown(user_id: int, days: int = 14) -> list[dict]:
    habits = await get_user_habits(user_id)
    logs = await get_habit_logs(user_id, days=days)
    dates = date_range(days)
    completed_by_habit: dict[int, set[str]] = {}

    for habit_id, completed_date in logs:
        completed_by_habit.setdefault(habit_id, set()).add(completed_date)

    result = []
    for habit in habits:
        habit_id = habit[0]
        created = parse_date(habit[2])
        available_dates = [date for date in dates if parse_date(date) >= created]
        completed_dates = completed_by_habit.get(habit_id, set())
        done = sum(1 for date in available_dates if date in completed_dates)
        possible = len(available_dates)
        missed = max(possible - done, 0)
        rate = round(done / possible * 100) if possible else 0
        heatmap = "".join("🟩" if date in completed_dates else "⬜" for date in available_dates[-14:])

        result.append({
            "habit": habit,
            "done": done,
            "possible": possible,
            "missed": missed,
            "rate": rate,
            "heatmap": heatmap or "⬜",
            "status": habit_status(rate, habit[5]),
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
    logs = await get_habit_logs(user_id, habit_id=habit_id, days=days)
    completed_dates = {completed_date for _, completed_date in logs}
    done = sum(1 for date in available_dates if date in completed_dates)
    possible = len(available_dates)
    not_marked = max(possible - done, 0)
    rate = round(done / possible * 100) if possible else 0
    current_done, current_possible, current_rate = single_habit_completion(
        completed_dates,
        available_dates,
        dates[-7:],
    )
    previous_done, previous_possible, previous_rate = single_habit_completion(
        completed_dates,
        available_dates,
        dates[-14:-7],
    )
    best_weekday, quiet_weekday = weekday_profile(completed_dates, available_dates)
    empty_gap = longest_empty_gap(completed_dates, available_dates)

    calendar = []
    for date in available_dates[-30:]:
        mark = "🟩" if date in completed_dates else "⬜"
        day = datetime.strptime(date, "%Y-%m-%d").strftime("%d")
        calendar.append(f"{mark}{day}")

    weeks = [" ".join(calendar[index:index + 7]) for index in range(0, len(calendar), 7)]
    today_done = habit[5] == today_str()

    return {
        "habit": habit,
        "done": done,
        "possible": possible,
        "not_marked": not_marked,
        "rate": rate,
        "current_done": current_done,
        "current_possible": current_possible,
        "current_rate": current_rate,
        "previous_done": previous_done,
        "previous_possible": previous_possible,
        "previous_rate": previous_rate,
        "best_weekday": best_weekday,
        "quiet_weekday": quiet_weekday,
        "empty_gap": empty_gap,
        "calendar": "\n".join(weeks) if weeks else "Пока нет дней для анализа.",
        "status": habit_status(rate, habit[5]),
        "today_done": today_done,
    }


def habit_diary_note(item: dict) -> str:
    rate = item["rate"]
    if item["possible"] < 7:
        return (
            "• Данных пока мало. Отмечай несколько дней, и анализ станет точнее.\n"
            "• На старте важнее простота: сделай действие настолько маленьким, чтобы его легко было выполнить даже в занятый день."
        )

    tips = []

    if item["previous_possible"]:
        diff = item["current_rate"] - item["previous_rate"]
        if diff >= 20:
            tips.append("Последние 7 дней заметно лучше прошлых. Не увеличивай сложность, дай привычке закрепиться.")
        elif diff <= -20:
            tips.append("Последние 7 дней тише прошлых. Проверь подсказку: где и после какого действия привычка должна начинаться.")
        else:
            tips.append("Ритм почти не изменился. Для роста лучше менять не цель, а условия: место, время или напоминание.")
    else:
        tips.append("Сравнение с прошлой неделей появится после ещё одной недели данных.")

    if item["empty_gap"] >= 4:
        tips.append("Есть длинный пустой участок. Поможет план формата: «если наступило X, то я делаю маленькую версию привычки».")
    elif item["empty_gap"] >= 2:
        tips.append("Иногда привычка выпадает на несколько дней. Сделай видимую подсказку рядом с местом, где действие начинается.")

    if rate >= 80:
        tips.append("Привычка хорошо приживается. Добавь маленькую награду после выполнения: галочка, приятная фраза, короткая пауза.")
    elif rate >= 50:
        tips.append("Привычка уже появляется в жизни. Лучший следующий шаг — сделать её заметнее и проще, а не требовательнее.")
    else:
        tips.append("Пока действие тяжеловато для текущего дня. Уменьши его до версии на 1-2 минуты.")

    tips.append(f"Лучше всего заходит: {item['best_weekday']}. Самый тихий день: {item['quiet_weekday']}.")
    return "\n".join(f"• {tip}" for tip in tips[:4])


def format_habit_diary_text(item: dict) -> str:
    habit = item["habit"]
    today_line = "отмечено" if item["today_done"] else "не отмечено"
    previous_line = (
        f"{item['previous_rate']}% ({item['previous_done']}/{item['previous_possible']})"
        if item["previous_possible"]
        else "пока нет данных"
    )

    return (
        f"📖 <b>{habit_name(habit)}</b>\n\n"
        f"Сегодня: <b>{today_line}</b>\n"
        f"За месяц: <b>{item['done']}/{item['possible']}</b> · {item['rate']}%\n"
        f"Последние 7 дней: <b>{item['current_rate']}%</b> ({item['current_done']}/{item['current_possible']})\n"
        f"Прошлые 7 дней: <b>{previous_line}</b>\n"
        f"Не отмечено: <b>{item['not_marked']}</b>\n"
        f"Самый длинный пустой участок: <b>{item['empty_gap']}</b>\n"
        f"Лучший день недели: <b>{item['best_weekday']}</b>\n"
        f"Тихий день: <b>{item['quiet_weekday']}</b>\n"
        f"Всего выполнений: <b>{habit[4]}</b>\n"
        f"Статус: <b>{item['status']}</b>\n\n"
        f"🗓 <b>Календарь</b>\n{item['calendar']}\n\n"
        f"🧠 <b>Анализ</b>\n{habit_diary_note(item)}"
    )


async def personal_records(user_id: int) -> dict:
    stats = await get_user_stats(user_id, days=30)
    breakdown = await habit_breakdown(user_id, days=30)

    best_habit = max(breakdown, key=lambda item: item["rate"], default=None)
    weak_habit = min(breakdown, key=lambda item: item["rate"], default=None)

    best_day_rate = 0
    best_day = "пока нет"
    for date in stats["dates"]:
        _, possible, rate = completion_for_dates(stats, [date])
        if possible and rate >= best_day_rate:
            best_day_rate = rate
            best_day = f"{datetime.strptime(date, '%Y-%m-%d').strftime('%d.%m')} · {rate}%"

    best_7_rate = 0
    for index in range(0, max(len(stats["dates"]) - 6, 0)):
        window = stats["dates"][index:index + 7]
        _, possible, rate = completion_for_dates(stats, window)
        if possible:
            best_7_rate = max(best_7_rate, rate)

    return {
        "best_habit": best_habit,
        "weak_habit": weak_habit,
        "best_day": best_day,
        "best_7_rate": best_7_rate,
    }


def insight_text(stats: dict) -> str:
    rate = stats["completion_rate"]
    missed = stats["missed_days"]
    trend = weekly_trend(stats)
    habits_count = stats["habits_count"]
    today_done = stats["today_done"]

    if stats["possible"] == 0:
        return "Данных пока мало. Дай привычкам пару дней, и анализ станет полезнее."

    tips = []

    if not trend["has_previous"]:
        tips.append("Сравнение с прошлой неделей появится, когда накопится ещё 7 дней данных.")
    elif trend["diff"] <= -15:
        tips.append("На этой неделе ритм стал тише. На завтра выбери одну самую лёгкую привычку и отметь её первой.")
    elif trend["diff"] >= 15:
        tips.append("Неделя заметно лучше прошлой. Сохрани тот же объём, пока ритм закрепляется.")
    elif abs(trend["diff"]) <= 5:
        tips.append("Темп почти не изменился. Маленькое улучшение даст не новая привычка, а более удобное время для текущих.")

    if rate >= 85:
        tips.append("Ритм устойчивый. Сейчас важнее беречь простоту и не добавлять лишнюю нагрузку.")
    elif rate >= 60:
        tips.append("База хорошая. Посмотри, какие привычки легче всего отмечаются, и закрепи их сценарий.")
    elif missed > stats["period_completed"]:
        tips.append("Похоже, часть привычек пока не встроилась в день. Стоит временно оставить 1-2 самые простые.")
    else:
        tips.append("Ритм формируется. Смотри на неделю целиком, а не на один отдельный день.")

    if habits_count >= 4 and rate < 70:
        tips.append("Привычек уже много для этапа закрепления. Можно оставить только те, которые правда помогают.")

    if today_done < habits_count:
        left = habits_count - today_done
        tips.append(f"Сегодня ещё не отмечено: {left}. Начни с самого короткого действия.")

    return "\n".join(f"• {tip}" for tip in tips[:4])


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
            "Пока привычек нет. Добавь первую — и начнём спокойно."
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


def habit_actions_keyboard(habits) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Добавить привычку", callback_data="add_habit")]]

    for habit in habits:
        habit_id = habit[0]
        rows.append([
            InlineKeyboardButton(text=f"📖 {habit[1][:24]}", callback_data=f"habit_diary_{habit_id}"),
        ])
        rows.append([
            InlineKeyboardButton(text=f"✏️ {habit[1][:18]}", callback_data=f"edit_{habit_id}"),
            InlineKeyboardButton(text="🗑", callback_data=f"delete_ask_{habit_id}"),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def habit_diary_keyboard(habit_id: int, done_today: bool) -> InlineKeyboardMarkup:
    rows = []
    if not done_today:
        rows.append([InlineKeyboardButton(text="✅ Отметить сегодня", callback_data=f"mark_diary_{habit_id}")])
    rows.extend([
        [
            InlineKeyboardButton(text="✏️ Название", callback_data=f"edit_{habit_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_ask_{habit_id}"),
        ],
        [InlineKeyboardButton(text="🟣 Все привычки", callback_data="open_habits")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟣 По привычкам", callback_data="stats_habits")],
        [InlineKeyboardButton(text="📅 Обзор недели", callback_data="stats_week")],
        [InlineKeyboardButton(text="🟢 Сегодня", callback_data="open_today")],
    ])


@router.message(Command("start"))
async def start(message: types.Message):
    await get_reminder_settings(message.from_user.id)
    await message.answer(
        await main_summary(message.from_user.id),
        parse_mode="HTML",
        reply_markup=main_keyboard,
    )


@router.message(F.text.in_(["🟢 Сегодня", "Сегодня"]))
async def today(message: types.Message):
    await show_today(message, message.from_user.id)


@router.message(Command("stats"))
@router.message(F.text.in_(["🔵 Статистика", "Статистика"]))
async def statistics(message: types.Message):
    await show_statistics(message, message.from_user.id)


async def show_statistics(obj: types.Message | types.CallbackQuery, user_id: int):
    stats = await get_user_stats(user_id, days=30)

    if stats["habits_count"] == 0:
        await answer_or_edit(
            obj,
            "🔵 <b>Статистика</b>\n\nПока нечего считать. Добавь первую привычку.",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Добавить", callback_data="add_habit")]]),
        )
        return

    trend = weekly_trend(stats)
    text = (
        "🔵 <b>Статистика за 30 дней</b>\n\n"
        f"✅ Выполнений: <b>{stats['period_completed']}</b> из {stats['possible']}\n"
        f"📈 Процент: <b>{stats['completion_rate']}%</b>\n"
        f"{progress_bar(stats['completion_rate'])}\n"
        f"⚪ Не отмечено: <b>{stats['missed_days']}</b>\n\n"
        "📊 <b>Сравнение недель</b>\n"
        f"Эта: <b>{trend['current_rate']}%</b> ({trend['current_done']}/{trend['current_possible']})\n"
        f"{previous_week_line(trend)}"
        f"Тренд: <b>{trend['label']}</b>\n\n"
        "🟡 <b>Месяц</b>\n"
        f"{render_month_calendar(stats)}\n\n"
        "🧠 <b>Как приживается</b>\n"
        f"{insight_text(stats)}"
    )

    await answer_or_edit(obj, text, stats_keyboard())


@router.callback_query(F.data == "open_stats")
async def open_stats(callback: types.CallbackQuery):
    await show_statistics(callback, callback.from_user.id)


@router.callback_query(F.data == "stats_habits")
async def show_habit_stats(callback: types.CallbackQuery):
    habits = await get_user_habits(callback.from_user.id)

    if not habits:
        await answer_or_edit(callback, "🟣 <b>По привычкам</b>\n\nПока нет данных.", stats_keyboard())
        return

    text = "🟣 <b>Дневник привычек</b>\n\nВыбери привычку, чтобы открыть её отдельный месяц."
    rows = [
        [InlineKeyboardButton(text=f"📖 {habit[1][:28]}", callback_data=f"habit_diary_{habit[0]}")]
        for habit in habits
    ]
    rows.append([InlineKeyboardButton(text="🔵 Назад", callback_data="open_stats")])

    await answer_or_edit(callback, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("habit_diary_"))
async def show_habit_diary(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    item = await habit_diary(callback.from_user.id, habit_id, days=30)

    if not item:
        await callback.answer("Привычка не найдена", show_alert=True)
        return

    await answer_or_edit(callback, format_habit_diary_text(item), habit_diary_keyboard(habit_id, item["today_done"]))


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

    await answer_or_edit(callback, format_habit_diary_text(item), habit_diary_keyboard(habit_id, item["today_done"]))


@router.callback_query(F.data == "stats_week")
async def show_week_review(callback: types.CallbackQuery):
    stats = await get_user_stats(callback.from_user.id, days=30)
    records = await personal_records(callback.from_user.id)
    trend = weekly_trend(stats)
    best_day, weak_day = best_and_weak_days(stats)

    if stats["habits_count"] == 0:
        await answer_or_edit(callback, "📅 <b>Обзор недели</b>\n\nПока нет данных.", stats_keyboard())
        return

    best_habit = records["best_habit"]
    weak_habit = records["weak_habit"]
    grade, recommendation = stability_grade(stats["completion_rate"], stats["missed_days"], stats["habits_count"])

    text = (
        "📅 <b>Обзор недели</b>\n\n"
        f"Эта неделя: <b>{trend['current_rate']}%</b> ({trend['current_done']}/{trend['current_possible']})\n"
        f"{previous_week_review_line(trend)}"
        f"Тренд: <b>{trend['label']}</b>\n\n"
        f"Самый активный день: <b>{best_day}</b>\n"
        f"Самый тихий день: <b>{weak_day}</b>\n\n"
        "🧭 <b>Ритм месяца</b>\n"
        f"<b>{grade}</b>\n{recommendation}\n\n"
        "🏁 <b>Лучшие периоды</b>\n"
        f"Лучший период 7 дней: <b>{records['best_7_rate']}%</b>\n"
        f"Самый активный день: <b>{records['best_day']}</b>\n"
    )

    if best_habit:
        text += f"Лучше всего прижилась: <b>{habit_name(best_habit['habit'])}</b> · {best_habit['rate']}%\n"
    if weak_habit:
        text += f"Просит упрощения: <b>{habit_name(weak_habit['habit'])}</b> · {weak_habit['rate']}%\n"

    text += f"\n🧠 <b>Мягкий вывод</b>\n{insight_text(stats)}"

    await answer_or_edit(callback, text, InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟣 По привычкам", callback_data="stats_habits")],
        [InlineKeyboardButton(text="🔵 Назад", callback_data="open_stats")],
    ]))


@router.callback_query(F.data == "open_today")
async def open_today(callback: types.CallbackQuery):
    await show_today(callback, callback.from_user.id)


async def show_today(obj: types.Message | types.CallbackQuery, user_id: int):
    habits = await get_user_habits(user_id)

    if not habits:
        await answer_or_edit(
            obj,
            await main_summary(user_id),
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Добавить привычку", callback_data="add_habit")]]),
        )
        return

    today = today_str()
    unmarked = [h for h in habits if h[5] != today]
    rows = []
    text = await main_summary(user_id)

    if not unmarked:
        text += "\n\n🟢 Всё отмечено. Хороший день."
    else:
        text += "\n\n<b>Сегодня не отмечено:</b>"
        for habit in unmarked:
            habit_id = habit[0]
            text += f"\n• <b>{habit_name(habit)}</b>"
            rows.append([
                InlineKeyboardButton(text=f"✅ {habit[1][:28]}", callback_data=f"mark_{habit_id}")
            ])

    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data="add_habit")])
    await answer_or_edit(obj, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("mark_"))
async def process_mark_callback(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[1])
    success, info = await mark_habit_completed(callback.from_user.id, habit_id)

    if not success:
        await callback.answer("Уже отмечено сегодня", show_alert=True)
        return

    await callback.answer(f"Отмечено: {info['habit_name']}")
    await show_today(callback, callback.from_user.id)


@router.message(F.text.in_(["🟣 Привычки", "Привычки"]))
async def habits(message: types.Message):
    await show_habits(message, message.from_user.id)


async def show_habits(obj: types.Message | types.CallbackQuery, user_id: int):
    habits = await get_user_habits(user_id)

    if not habits:
        text = "🟣 <b>Дневник привычек</b>\n\nСписок пуст. Начнём с одной."
    else:
        text = "🟣 <b>Дневник привычек</b>\n\nКаждая привычка ведётся отдельно."
        for habit in habits:
            _, _, _, _, total_completed, last_date, _ = habit
            done_today = " 🟢" if last_date == today_str() else ""
            text += (
                f"\n📖 <b>{habit_name(habit)}</b>{done_today}\n"
                f"{'Сегодня отмечено' if last_date == today_str() else 'Сегодня не отмечено'} · всего: {total_completed}\n"
            )

    await answer_or_edit(obj, text, habit_actions_keyboard(habits))


@router.callback_query(F.data == "add_habit")
async def new_habit_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Как назовём привычку?", reply_markup=ReplyKeyboardRemove())
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

    await save_habit(message.from_user.id, name)
    await state.clear()
    await message.answer(
        f"🟢 Готово: <b>{escape(name)}</b>\nТеперь просто отмечай: есть сегодня или нет.",
        parse_mode="HTML",
        reply_markup=main_keyboard,
    )


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
    await show_habits(callback, callback.from_user.id)


@router.callback_query(F.data.startswith("delete_yes_"))
async def delete_habit(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[-1])
    await delete_habit_from_db(callback.from_user.id, habit_id)
    await callback.answer("Удалено")
    await show_habits(callback, callback.from_user.id)


@router.message(F.text.in_(["⚙️ Настройки", "Настройки"]))
async def settings(message: types.Message):
    await show_settings(message, message.from_user.id)


async def show_settings(obj: types.Message | types.CallbackQuery, user_id: int):
    settings = await get_reminder_settings(user_id)
    enabled = settings["enabled"]
    times = parse_reminder_times(settings["reminder_time"])

    status = "🟢 включены" if enabled else "🔴 выключены"
    text = (
        "⚙️ <b>Настройки</b>\n\n"
        f"Напоминания: <b>{status}</b>\n"
        f"Время: <b>{', '.join(times)}</b>\n\n"
        "Можно выбрать несколько времён."
    )

    rows = [[
        InlineKeyboardButton(
            text="🔕 Выключить" if enabled else "🔔 Включить",
            callback_data="rem_toggle",
        )
    ]]

    for reminder_time in REMINDER_CHOICES:
        mark = "✅" if reminder_time in times else "➕"
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
    unmarked = [h for h in habits if h[5] != today]

    if not unmarked:
        return

    rows = []
    text = "🟡 <b>Мягкое напоминание</b>\n\nСегодня ещё не отмечено:\n\n"

    for habit in unmarked:
        habit_id = habit[0]
        text += f"• <b>{habit_name(habit)}</b>\n"
        rows.append([
            InlineKeyboardButton(text=f"✅ {habit[1][:28]}", callback_data=f"mark_{habit_id}")
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
