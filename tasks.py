
import logging
from datetime import datetime
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot import bot
from db import get_all_users_with_habits, get_user_habits

logger = logging.getLogger(__name__)


async def daily_reminder_and_reset():

    users = await get_all_users_with_habits()

    if not users:
        logger.info("Нет пользователей с привычками")
        return

    today = datetime.now().strftime("%Y-%m-%d")

    for user_id in users:
        habits = await get_user_habits(user_id)
        if not habits:
            continue

        unmarked = []
        for habit in habits:
            habit_id, habit_name, created_date, streak, total_completed, last_date, goal_days = habit

            if last_date != today:
                unmarked.append((habit_id, habit_name, streak, goal_days))

        if not unmarked:
            continue

        text = "⏰ <b>Напоминание о привычках</b>\n\n"
        text += "Ты ещё не отметил сегодня:\n\n"

        kb = InlineKeyboardMarkup(inline_keyboard=[])

        for habit_id, habit_name, streak, goal_days in unmarked:
            text += f" <b>{habit_name}</b> ({streak}/{goal_days})\n"
            kb.inline_keyboard.append([
                InlineKeyboardButton(text=f"✅ {habit_name}", callback_data=f"mark_{habit_id}")
            ])

        try:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML",
                reply_markup=kb
            )
        except Exception as e:
            logger.error(f"Не удалось отправить напоминание пользователю {user_id}: {e}")

        logger.info(
            f"Напоминание отправлено пользователю {user_id}; "
            "пропуски записываются после окончания дня"
        )
