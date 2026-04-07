import logging
from datetime import datetime
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


from db import save_habit, get_user_habits, mark_habit_completed,delete_habit_from_db,reset_habit_streak

router = Router()
logger = logging.getLogger(__name__)


class Form(StatesGroup):
    waiting_habit_name = State()
    waiting_start_date = State()


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌟 Добавить привычку")],
        [KeyboardButton(text="✅ Отметить сегодня")],
        [KeyboardButton(text="📋 Мои привычки")],
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🗑 Удалить привычку")],
        [KeyboardButton(text="🔄 Обнулить цепочку")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

empty_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🌟 Добавить привычку")]],
    resize_keyboard=True,
    one_time_keyboard=True
)



@router.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    habits = await get_user_habits(message.from_user.id)
    keyboard = main_keyboard if habits else empty_keyboard

    await message.answer(
        f"Привет, {message.from_user.first_name}!\n\n"
        f"Я помогу тебе формировать полезные привычки.",
        reply_markup=main_keyboard,
    )


@router.message(F.text == "🌟 Добавить привычку")
async def new_habit_start(message: types.Message, state: FSMContext):
    await message.answer(
        "Напиши название новой привычки:\n"
        "Например: Пить 2 литра воды, Читать 20 минут, Делать зарядку",reply_markup=ReplyKeyboardRemove()
    )
    await state.set_state(Form.waiting_habit_name)



@router.message(Form.waiting_habit_name)
async def new_habit_save(message: types.Message, state: FSMContext):
    habit_name = message.text.strip()
    today=datetime.today().strftime("%Y-%m-%d")
    if len(habit_name) < 2:
        await message.answer("Название привычки слишком короткое. Попробуй ещё раз.")
        return

    await state.update_data(habit_name=habit_name)
    await message.answer(
        "Напиши дату начала привычки в формате 'ГГГГ-ММ-ДД'\n"
        f"Например {today}\n\n"
        "Или напиши 'сегодня', если начала сегодня",
        parse_mode="Markdown"
    )
    await state.set_state(Form.waiting_start_date)



@router.message(Form.waiting_start_date)
async def new_habit_start_date(message: types.Message, state: FSMContext):
    data = await state.get_data()
    habit_name = data.get("habit_name")

    if message.text.lower() == "сегодня":
        start_date = datetime.now().strftime("%Y-%m-%d")
    else:

        try:
            datetime.strptime(message.text.strip(), "%Y-%m-%d")
            start_date = message.text.strip()
        except ValueError:
            await message.answer("Неправильний формат даты!\nИспользуй формат `ГГГГ-ММ-ДД` или напиши `сегодня`.")
            return
    await state.set_state(start_date)
    await save_habit(message.from_user.id, habit_name, start_date)

    await message.answer(
        f"✅ Привычка успешна добавлена!\n\n"
        f"Название: <b>{habit_name}</b>\n"
        f"Дата начала: <b>{start_date}</b>",
        parse_mode="HTML",
        reply_markup=main_keyboard
    )

    await state.clear()

@router.message(F.text == "🔄 Обнулить цепочку")
async def reset_streak_start(message: types.Message):
    habits = await get_user_habits(message.from_user.id)

    if not habits:
        await message.answer("У тебя пока нет привычек.",reply_markup=empty_keyboard)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    for habit in habits:
        habit_id, habit_name, streak, _, _ = habit
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"🔄 {habit_name} ({streak} дней)",
                callback_data=f"reset_{habit_id}"
            )
        ])

    await message.answer("Выбери привычку, цепочку которой хочешь обнулить:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("reset_"))
async def process_reset_callback(callback: types.CallbackQuery):
    habit_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    success = await reset_habit_streak(user_id, habit_id)

    if success:
        await callback.message.edit_text("🔄 Цепочка успешно обнулена.")
    else:
        await callback.message.edit_text("❌ Не удалось обнулить цепочку.")

    await callback.answer()

@router.message(F.text == "✅ Отметить сегодня")
async def mark_today(message: types.Message):
    habits = await get_user_habits(message.from_user.id)

    if not habits:
        await message.answer("У тебя пока нет привычек.\nДобавь первую через кнопку '🌟 Добавить привычку'")
        return

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    for habit in habits:
        habit_id, habit_name,start_date, streak, total, last_date = habit
        button_text = f"{habit_name} ({streak} 🔥)"

        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=button_text, callback_data=f"mark_{habit_id}")
        ])

    await message.answer(
        "✅ Отметь, какие привычки ты выполнил сегодня:",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("mark_"))
async def process_mark_callback(callback: types.CallbackQuery):
    try:
        habit_id = int(callback.data.split("_")[1])
        user_id = callback.from_user.id

        success = await mark_habit_completed(user_id, habit_id)

        if success:
            await callback.message.edit_text(
                "✅ Привычка успешно отмечена как выполненная сегодня!",
                reply_markup=None
            )
        else:
            await callback.message.edit_text(
                "⚠️ Эта привычка уже отмечена сегодня.",
            reply_markup=None
            )

    except Exception as e:
        logger.error(f"Ошибка при отметке привычки: {e}")
        await callback.message.edit_text("❌ Произошла ошибка при обработке.")

    await callback.answer()

@router.message(F.text == "📋 Мои привычки")
async def my_habits(message: types.Message):
    habits = await get_user_habits(message.from_user.id)

    if not habits:
        await message.answer(
            "У тебя пока нет привычек.\nДобавь первую через кнопку '🌟 Добавить привычку'",
            reply_markup=empty_keyboard
        )
        return

    text = "📋 <b>Твои привычки:</b>\n\n"

    for habit in habits:
        habit_id, habit_name,start_date, streak, total, last_date = habit
        text += f"• {habit_name} — 🔥 {streak} дней подряд\n"

        await message.answer(text, parse_mode="HTML")


@router.message(F.text == "📊 Статистика")
async def statistics(message: types.Message):
    habits = await get_user_habits(message.from_user.id)

    if not habits:
        await message.answer("У тебя пока нет привычек.\nДобавь первую через кнопку '🌟 Добавить привычку'")
        return

    total_habits = len(habits)
    total_completed_days = sum(habit[3] for habit in habits)
    max_streak = max((habit[2] for habit in habits), default=0)

    text = (
        f"📊 <b>Твоя статистика</b>\n\n"
        f"Привычек всего: <b>{total_habits}</b>\n"
        f"Дней выполнено всего: <b>{total_completed_days}</b>\n"
        f"Лучшая цепочка: <b>{max_streak} дней</b>\n\n"
        f"<b>По привычкам:</b>\n\n"
    )

    for habit in habits:
        habit_id, habit_name, start_date, streak, total_completed, last_date = habit
        percent = round((total_completed / 30) * 100) if total_completed > 0 else 0

        text += f"{habit_name}\n"
        text += f" Цепочка: <b>{streak}</b> дней 🔥\n"
        text += f" Дата начала: {start_date}"
        text += f" Выполнено: {total_completed} раз ({percent}%)\n\n"
#Сделать чтобы можно было выбирать цель 10 дней/месяц/и т.д
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == "🗑 Удалить привычку")
async def delete_habit(message: types.Message):
    habits = await get_user_habits(message.from_user.id)

    if not habits:
        await message.answer("У тебя пока нет привычек для удаления.")
        return


    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    for habit in habits:
        habit_id, habit_name, streak, _, _ = habit
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=f"🗑 {habit_name}", callback_data=f"delete_{habit_id}")
        ])

    await message.answer("Выбери привычку, которую хочешь удалить:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("delete_"))
async def process_delete_callback(callback: types.CallbackQuery):
    try:

        habit_id = int(callback.data.split("_")[1])
        user_id = callback.from_user.id

        success = await delete_habit_from_db(user_id, habit_id)

        if success:
            await callback.message.edit_text("🗑 Привычка успешно удалена.")
        else:
            await callback.message.edit_text("❌ Не удалось удалить привычку.")
    except Exception as e:
        logger.error(f"Ошибка при удалении привычки: {e}")
        await callback.message.edit_text("❌ Произошла ошибка.")
        await callback.answer()