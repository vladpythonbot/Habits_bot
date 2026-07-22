# main.py
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo

from bot import bot, dp
from routers import APP_VERSION, MINI_APP_URL, daily_reminder, router
from db import init_db
from webapp import start_web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)


async def configure_menu_button():
    if MINI_APP_URL:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Mini App",
                web_app=WebAppInfo(url=MINI_APP_URL),
            )
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())


async def main():
    await init_db()
    logging.info("HabitFlow version: %s", APP_VERSION)

    dp.include_router(router)

    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")

    scheduler.add_job(
        daily_reminder,
        "interval",
        minutes=1,
        id="daily_reminder",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    await start_web_app()
    await configure_menu_button()

    logging.info("Бот успешно запущен")

    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен")
    except Exception as e:
        logging.exception("Критическая ошибка: %s", e)
