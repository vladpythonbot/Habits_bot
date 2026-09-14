import asyncio
import contextlib
import logging

from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import bot, dp
from db import init_db
from routers import (
    APP_VERSION,
    HABIT_TABLE_TIME,
    MINI_APP_URL,
    SLEEP_RATE_TIME,
    ask_sleep_rate,
    daily_reminder,
    router,
    send_daily_habit_table,
)
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


def parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour, minute = [int(part) for part in value.split(":", 1)]
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Invalid time value: {value}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time value: {value}")
    return hour, minute


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
    habit_table_hour, habit_table_minute = parse_hhmm(HABIT_TABLE_TIME)
    scheduler.add_job(
        send_daily_habit_table,
        "cron",
        hour=habit_table_hour,
        minute=habit_table_minute,
        id="daily_habit_table",
        max_instances=1,
        coalesce=True,
    )
    sleep_rate_hour, sleep_rate_minute = parse_hhmm(SLEEP_RATE_TIME)
    scheduler.add_job(
        ask_sleep_rate,
        "cron",
        hour=sleep_rate_hour,
        minute=sleep_rate_minute,
        id="sleep_rate_prompt",
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
