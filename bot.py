import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage


TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("BOT_TOKEN не указан в .env")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
