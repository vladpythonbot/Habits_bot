# main.py
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import bot, dp
from routers import router
from db import init_db
from routers import daily_reminder
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

async def main():
    await init_db()

    dp.include_router(router)

    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")

    scheduler.add_job(daily_reminder,
                      "cron", hour="9,12,15,18", minute=0,
                      id="daily_reminder")

    scheduler.start()

    print("🚀 Бот успешно запущен")

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")