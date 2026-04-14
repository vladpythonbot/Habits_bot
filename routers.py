# routers.py
import logging
from datetime import datetime

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from bot import bot
from db import save_habit, get_user_habits, mark_habit_completed, delete_habit_from_db, reset_habit_streak, \
    init_reminder_table,get_reminder_settings,set_reminder_settings

router = Router()
logger = logging.getLogger(__name__)


class Form(StatesGroup):
    waiting_habit_name = State()
    waiting_goal_days = State()
    waiting_new_name = State()
    waiting_reminder_time = State()


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌟 Добавить привычку")],
        [KeyboardButton(text="✅ Отметить сегодня")],
        [KeyboardButton(text="📋 Мои привычки")],
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🔔 Напоминания")],
        [KeyboardButton(text="🗑 Удалить привычку")],
        [KeyboardButton(text="🔄 Обнулить цепочку")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

empty_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🌟 Добавить привычку")]],
    resize_keyboard=True,
    one_time_keyboard=False
)


@router.message(Command("start"))
async def start(message: types.Message):
    habits = await get_user_habits(message.from_user.id)
    keyboard = main_keyboard if habits else empty_keyboard

    await message.answer(
        f"Привет, {message.from_user.first_name}!\n\n"
        f"Я помогу тебе формировать полезные привычки.",
        reply_markup=keyboard
    )


@router.message(F.text == "🌟 Добавить привычку")
async def new_habit_start(message: types.Message, state: FSMContext):
    await message.answer(
        "Напиши название новой привычки:",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(Form.waiting_habit_name)


@router.message(Form.waiting_habit_name)
async def new_habit_save(message: types.Message, state: FSMContext):
    habit_name = message.text.strip()

    if len(habit_name) < 2:
        await message.answer("Название привычки слишком короткое. Минимум 2 символа.")
        return

    await state.update_data(habit_name=habit_name)

    goal_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="7 дней", callback_data="goal_7")],
        [InlineKeyboardButton(text="14 дней", callback_data="goal_14")],
        [InlineKeyboardButton(text="21 день", callback_data="goal_21")],
        [InlineKeyboardButton(text="30 дней", callback_data="goal_30")],
        [InlineKeyboardButton(text="60 дней", callback_data="goal_60")],
        [InlineKeyboardButton(text="100 дней", callback_data="goal_100")],
        [InlineKeyboardButton(text="Своя цель", callback_data="goal_custom")]
    ])

    await message.answer(
        f"Привычка: <b>{habit_name}</b>\n\n"
        "Выбери цель — сколько дней подряд хочешь держать привычку:",
        parse_mode="HTML",
        reply_markup=goal_kb
    )

    await state.set_state(Form.waiting_goal_days)


@router.callback_query(F.data.startswith("goal_"))
async def process_goal_callback(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    habit_name = data.get("habit_name")

    if not habit_name:
        await callback.message.edit_text("❌ Ошибка. Начни заново.")
        await state.clear()
        await callback.answer()
        return

    if callback.data == "goal_custom":
        await callback.message.edit_text("Напиши свою цель в днях (например: 45):")
        await callback.answer()
        return

    try:
        goal_days = int(callback.data.split("_")[1])
    except:
        goal_days = 30

    await save_habit(callback.from_user.id, habit_name, goal_days)

    await callback.message.edit_text(
        f"✅ Привычка успешно создана!\n\n"
        f"Название: <b>{habit_name}</b>\n"
        f"Цель: <b>{goal_days} дней</b>",
        parse_mode="HTML",
        reply_markup=None
    )

    await bot.send_message(
        chat_id=callback.from_user.id,
        text="✅ Готово! Что делаем дальше?",
        reply_markup=main_keyboard
    )

    await state.clear()
    await callback.answer()



@router.message(F.text == "✅ Отметить сегодня")
async def mark_today(message: types.Message):
    habits = await get_user_habits(message.from_user.id)

    if not habits:
        await message.answer("У тебя пока нет привычек.\nДобавь первую через кнопку '🌟 Добавить привычку'")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    unmarked_habits = []

    for habit in habits:
        habit_id, habit_name, created_date, streak, total_completed, last_date, goal_days = habit

        if last_date != today:
            unmarked_habits.append(habit)

    if not unmarked_habits:
        await message.answer("🎉 Все привычки на сегодня уже отмечены!\nМолодец!")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])

    for habit in unmarked_habits:
        habit_id, habit_name, created_date, streak, total_completed, last_date, goal_days = habit
        button_text = f"{habit_name} ({streak}/{goal_days} 🔥)"

        kb.inline_keyboard.append([
            InlineKeyboardButton(text=button_text, callback_data=f"mark_{habit_id}")
        ])

    await message.answer(
        "✅ Отметь привычки, которые ты выполнил сегодня:",
        reply_markup=kb
    )

@router.callback_query(F.data.startswith("mark_"))
async def process_mark_callback(callback: types.CallbackQuery):
    try:
        habit_id = int(callback.data.split("_")[1])
        user_id = callback.from_user.id

        success, goal_info = await mark_habit_completed(user_id, habit_id)

        if success:
            if goal_info and goal_info[0]:
                _, habit_name, new_streak, new_goal = goal_info
                await callback.message.edit_text(
                    f"🎉 <b>Поздравляем! Цель достигнута!</b>\n\n"
                    f"Привычка: <b>{habit_name}</b>\n"
                    f"Новая цепочка: <b>{new_streak} дней</b>\n"
                    f"Следующая цель: <b>{new_goal} дней</b> 🔥",
                    parse_mode="HTML"
                )
            else:
                await callback.message.edit_text("✅ Привычка успешно отмечена сегодня!")
        else:
            await callback.message.edit_text("⚠️ Эта привычка уже отмечена сегодня.")

    except Exception as e:
        logger.error(f"Ошибка отметки: {e}")
        await callback.message.edit_text("❌ Произошла ошибка.")

    await callback.answer()


@router.message(F.text == "📋 Мои привычки")
async def my_habits(message: types.Message):
    habits = await get_user_habits(message.from_user.id)

    if not habits:
        await message.answer("У тебя пока нет привычек.", reply_markup=empty_keyboard)
        return

    text = "📋 <b>Твои привычки:</b>\n\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    for habit in habits:
        habit_id, habit_name, created_date, streak, total_completed, last_date, goal_days = habit

        try:
            created = datetime.strptime(created_date, "%Y-%m-%d")
            days_since = (datetime.now() - created).days
            days_text = f"уже {days_since} дней"
        except:
            days_text = ""

        text += f"• <b>{habit_name}</b> ({streak}/{goal_days})\n"
        text += f"   {days_text}\n\n"

        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_name_{habit_id}"),
            InlineKeyboardButton(text="🔄 Обнулить", callback_data=f"reset_{habit_id}")
        ])

    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data.startswith("edit_name_"))
async def start_edit_name(callback: types.CallbackQuery, state: FSMContext):
    habit_id = int(callback.data.split("_")[2])

    await state.update_data(editing_habit_id=habit_id)

    await callback.message.edit_text(
        "Напиши новое название для этой привычки:",
        reply_markup=None
    )

    await state.set_state(Form.waiting_new_name)
    await callback.answer()


@router.message(Form.waiting_new_name)
async def save_new_name(message: types.Message, state: FSMContext):
    new_name = message.text.strip()

    if len(new_name) < 2:
        await message.answer("Название слишком короткое. Минимум 2 символа.")
        return

    data = await state.get_data()
    habit_id = data.get("editing_habit_id")

    if not habit_id:
        await message.answer("Ошибка. Попробуй заново.")
        await state.clear()
        return


    await message.answer(
        f"✅ Название изменено на:\n"
        f"<b>{new_name}</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard
    )

    await state.clear()


@router.callback_query(F.data.startswith("reset_"))
async def process_reset_callback(callback: types.CallbackQuery):
    try:
        habit_id = int(callback.data.split("_")[1])
        user_id = callback.from_user.id

        success = await reset_habit_streak(user_id, habit_id)

        if success:
            await callback.message.edit_text(
                "🔄 Цепочка успешно обнулена!\n"
                "Сегодня напоминание по этой привычке приходить не будет."
            )
        else:
            await callback.message.edit_text("❌ Не удалось обнулить цепочку.")

    except Exception as e:
        logger.error(f"Ошибка обнуления: {e}")
        await callback.message.edit_text("❌ Произошла ошибка.")

    await callback.answer()


@router.message(F.text == "📊 Статистика")
async def statistics(message: types.Message):
    await message.answer("Функция статистики в процессе обновления.")


@router.message(F.text == "🔔 Напоминания")
async def reminders_settings(message: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Включить напоминания", callback_data="reminder_on")],
        [InlineKeyboardButton(text="🔕 Выключить напоминания", callback_data="reminder_off")],
        [InlineKeyboardButton(text="⏰ Выбрать время напоминания", callback_data="reminder_time")]
    ])

    await message.answer(
        "Настройки напоминаний:",
        reply_markup=kb
    )

@router.callback_query(F.data == "reminder_on")
async def reminder_on(callback: types.CallbackQuery):
    # Пока просто заглушка
    await callback.message.edit_text("✅ Напоминания включены!")
    await callback.answer()


@router.callback_query(F.data == "reminder_off")
async def reminder_off(callback: types.CallbackQuery):
    await callback.message.edit_text("🔕 Напоминания выключены.")
    await callback.answer()


@router.callback_query(F.data == "reminder_time")
async def reminder_time_start(callback: types.CallbackQuery, state: FSMContext):
    time_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="9:00", callback_data="time_9")],
        [InlineKeyboardButton(text="12:00", callback_data="time_12")],
        [InlineKeyboardButton(text="15:00", callback_data="time_15")],
        [InlineKeyboardButton(text="18:00", callback_data="time_18")]
    ])

    await callback.message.edit_text(
        "Выбери время, в которое хочешь получать напоминания:",
        reply_markup=time_kb
    )
    await callback.answer()

@router.callback_query(F.data.startswith("time_"))
async def set_reminder_time(callback: types.CallbackQuery):
    hour = callback.data.split("_")[1]
    time_str=f"{hour}:00"

    await set_reminder_settings(callback.from_user.id, time_str)

    await callback.message.edit_text(f"✅ Время напоминания изменено на {time_str}")
    await callback.answer()

@router.message(F.text == "🗑 Удалить привычку")
async def delete_habit_start(message: types.Message):
    await message.answer("Функция удаления в процессе обновления.")