# Habits Bot

Minimal Telegram bot for tracking daily habits without pressure. The bot focuses on simple daily check-ins, reminders, and lightweight statistics.

## Features

- Create and manage personal habits
- Mark a habit as completed today
- Mark a habit as "not today"
- Per-habit diary with 30-day history
- Personal reminders for specific habits
- Quick reminder presets and custom reminder times
- 30-day completion statistics
- Comparison of the latest 7 completed days with the previous 7 days
- Compact 7-day habit chart with emoji markers
- SQLite storage for habits, logs, misses, and reminders

## Tech Stack

- Python 3.11+
- aiogram 3
- aiosqlite
- APScheduler
- python-dotenv

## Project Structure

```text
.
├── main.py              # App entry point and scheduler setup
├── bot.py               # Bot and dispatcher initialization
├── routers.py           # Telegram handlers and UI logic
├── db.py                # SQLite database layer
├── tasks.py             # Reminder task helpers
└── requirements.txt     # Python dependencies
```

## Environment Variables

Create a `.env` file in the project root:

```env
BOT_TOKEN=your_telegram_bot_token
DB_PATH=habits.db
```

`DB_PATH` is optional. If it is not set, the bot creates `habits.db` in the project folder.

## Run Locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Deployment Notes

- Store `BOT_TOKEN` only as an environment variable.
- Use persistent storage for SQLite if deploying on Railway or another cloud platform.
- Run only one active instance of the bot per Telegram token.
