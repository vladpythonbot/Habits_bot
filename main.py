import asyncio
import contextlib
import logging

from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import bot, dp
from db import init_db
from routers import APP_VERSION, MINI_APP_URL, daily_reminder, router
from webapp import start_web_app


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)


async def configure_menu_button() -> None:
    try:
        if MINI_APP_URL:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Mini App",
                    web_app=WebAppInfo(url=MINI_APP_URL),
                )
            )
        else:
            await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    except Exception as error:
        logging.warning("Menu button was not configured: %s", error)


async def main() -> None:
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

    runner = await start_web_app()
    scheduler.start()
    await configure_menu_button()
    logging.info("Bot started")

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await runner.cleanup()
        with contextlib.suppress(Exception):
            await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")
    except Exception as error:
        logging.exception("Critical error: %s", error)
