import asyncio
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
import aiosqlite

DB_PATH = "bot.db"

TASKS = [
    (1, "Мастер готов (WAV 24bit)"),
    (2, "Clean / Explicit версия (если нужно)"),
    (3, "Обложка 3000×3000"),
    (4, "Название артиста и трека финализировано"),
    (5, "Авторы и сплиты записаны"),
    (6, "Выбран дистрибьютор"),
    (7, "Релиз загружен в дистрибьютора"),
    (8, "Метаданные проверены"),
    (9, "Сделан BandLink / Smartlink"),
    (10, "Сделан пресейв (если доступно)"),
    (11, "Текст о треке (5–7 строк)"),
    (12, "Подготовлены 3 контент-единицы"),
    (13, "Список плейлистов / медиа"),
    (14, "План пострелиза на 7 дней"),
]

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_tasks (
            tg_id INTEGER,
            task_id INTEGER,
            done INTEGER DEFAULT 0,
            PRIMARY KEY (tg_id, task_id)
        )
        """)
        await db.commit()

async def ensure_user(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (tg_id,))
        for task_id, _ in TASKS:
            await db.execute(
                "INSERT OR IGNORE INTO user_tasks (tg_id, task_id) VALUES (?, ?)",
                (tg_id, task_id)
            )
        await db.commit()

async def get_tasks(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
        SELECT task_id, done FROM user_tasks
        WHERE tg_id = ?
        """, (tg_id,))
        return {row[0]: row[1] for row in await cur.fetchall()}

async def toggle_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE user_tasks
        SET done = 1 - done
        WHERE tg_id = ? AND task_id = ?
        """, (tg_id, task_id))
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
        "/plan — мой план релиза\n"
        "/help — помощь"
    )

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "Я помогаю тебе выпустить релиз по шагам.\n"
        "Начни с команды /plan."
    )

@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks(tg_id)

    text = "🚀 Твой план релиза:\n\n"
    keyboard = []

    for task_id, title in TASKS:
        done = tasks_state.get(task_id, 0)
        status = "✅" if done else "⬜"
        text += f"{status} {title}\n"
        keyboard.append([
            InlineKeyboardButton(
                text=f"{'Снять' if done else 'Готово'}: {title}",
                callback_data=f"toggle:{task_id}"
            )
        ])

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

@dp.callback_query(F.data.startswith("toggle:"))
async def toggle_task_handler(callback):
    task_id = int(callback.data.split(":")[1])
    tg_id = callback.from_user.id

    await toggle_task(tg_id, task_id)
    await plan_cmd(callback.message)
    await callback.answer()

async def main():
    await init_db()
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
