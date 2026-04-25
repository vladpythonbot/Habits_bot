import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage


from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("BOT_TOKEN не указан в .env")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
