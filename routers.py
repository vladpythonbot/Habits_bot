
import logging
from datetime import datetime

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db import save_habit, get_user_habits, mark_habit_completed, delete_habit_from_db, reset_habit_streak

router = Router()
logger = logging.getLogger(__name__)


class Form(StatesGroup):
    waiting_habit_name = State()
    waiting_goal_days = State()


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Отметить сегодня")],
        [KeyboardButton(text="📋 Мои привычки")],
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🗑 Удалить привычку")],
        [KeyboardButton(text="🔄 Обнулить цепочку")],
        [KeyboardButton(text="🌟 Добавить привычку")]],
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
async def process_goal(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    habit_name = data.get("habit_name")

    if callback.data == "goal_custom":
        await callback.message.edit_text("Напиши количество дней цели (например: 45):")

        await callback.answer()
        return

    goal_days = int(callback.data.split("_")[1])

    await save_habit(callback.from_user.id, habit_name, goal_days)

    await callback.message.edit_text(
        f"✅ Привычка создана!\n\n"
        f"Название: <b>{habit_name}</b>\n"
        f"Цель: <b>{goal_days} дней</b>",
        parse_mode="HTML",
        reply_markup=None)

    await my_habits(callback.message)
    await state.clear()
    await callback.answer()


@router.message(F.text == "✅ Отметить сегодня")
async def mark_today(message: types.Message):
    habits = await get_user_habits(message.from_user.id)

    if not habits:
        await message.answer("У тебя пока нет привычек.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[])

    for habit in habits:
        habit_id, habit_name, created_date, streak, total, last_date, goal_days = habit
        button_text = f"{habit_name} ({streak}/{goal_days})"

        kb.inline_keyboard.append([
            InlineKeyboardButton(text=button_text, callback_data=f"mark_{habit_id}")
        ])

    await message.answer("Отметь выполненные сегодня привычки:", reply_markup=kb)


@router.callback_query(F.data.startswith("mark_"))
async def process_mark_callback(callback: types.CallbackQuery):
    try:
        habit_id = int(callback.data.split("_")[1])
        user_id = callback.from_user.id

        success = await mark_habit_completed(user_id, habit_id)

        if success:
            await callback.message.edit_text("✅ Привычка отмечена сегодня!")
        else:
            await callback.message.edit_text("⚠️ Эта привычка уже отмечена сегодня.")

    except Exception as e:
        logger.error(f"Ошибка отметки: {e}")
        await callback.message.edit_text("❌ Ошибка при обработке.")

    await callback.answer()



@router.message(F.text == "📋 Мои привычки")
async def my_habits(message: types.Message):
    habits = await get_user_habits(message.from_user.id)

    if not habits:
        await message.answer("У тебя пока нет привычек.", reply_markup=empty_keyboard)
        return

    text = "📋 <b>Твои привычки:</b>\n\n"

    for habit in habits:
        habit_id,habit_name, created_date, streak, total, last_date, goal_days = habit

        try:
            created = datetime.strptime(created_date, "%Y-%m-%d")
            days_since = (datetime.now() - created).days
            days_text = f"уже {days_since} дней с создания"
        except:
            days_text = ""

        text += f"• <b>{habit_name}</b> ({streak}/{goal_days})\n"
        text += f"   {days_text}\n\n"

    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "📊 Статистика")
async def statistics(message: types.Message):
    await message.answer("Функция статистики в процессе обновления.")

@router.message(F.text == "🗑 Удалить привычку")
async def delete_habit_start(message: types.Message):
    await message.answer("Функция удаления в процессе обновления.")

@router.message(F.text == "🔄 Обнулить цепочку")
async def reset_streak_start(message: types.Message):
    await message.answer("Функция обнуления в процессе обновления.")