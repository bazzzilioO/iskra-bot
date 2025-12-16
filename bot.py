import asyncio
import os
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from dotenv import load_dotenv
import aiosqlite

DB_PATH = "bot.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        await db.commit()

async def ensure_user(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (tg_id,))
        await db.commit()

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()

@dp.message(CommandStart())
async def start(message: Message):
    await ensure_user(message.from_user.id)
    await message.answer(
        "Я ИСКРА — помощник по релизу.\n\n"
        "Команды:\n"
        "/plan — показать план (пока заглушка)\n"
        "/help — помощь"
    )

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer("MVP жив. Дальше добавим онбординг и план релиза.")

@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    await message.answer(
        "План релиза (черновик):\n"
        "1) Мастер\n2) Обложка\n3) Метаданные\n4) Дистрибуция\n5) BandLink\n6) Питчинг"
    )

async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")

    await init_db()
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
