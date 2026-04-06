# main.py
import asyncio
import logging

from bot import bot, dp
from routers import router
from db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

async def main():
    await init_db()
    print("✅ База данных инициализирована")

    dp.include_router(router)

    print("🚀 Запуск бота...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
#задачник

