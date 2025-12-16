import asyncio
import os

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from dotenv import load_dotenv

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

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()


# -------------------- DB --------------------

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
                (tg_id, task_id),
            )
        await db.commit()


async def get_tasks_state(tg_id: int) -> dict[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT task_id, done FROM user_tasks WHERE tg_id = ?",
            (tg_id,),
        )
        rows = await cur.fetchall()
        return {task_id: done for task_id, done in rows}


async def toggle_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE user_tasks
        SET done = 1 - done
        WHERE tg_id = ? AND task_id = ?
        """, (tg_id, task_id))
        await db.commit()


# -------------------- UI builders --------------------

def build_plan_message(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = "🚀 Твой план релиза:\n\n"
    keyboard: list[list[InlineKeyboardButton]] = []

    for task_id, title in TASKS:
        done = tasks_state.get(task_id, 0)
        status = "✅" if done else "⬜"
        text += f"{status} {title}\n"

        keyboard.append([
            InlineKeyboardButton(
                text=f"{'Снять' if done else 'Готово'}: {title}",
                callback_data=f"toggle:{task_id}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="▶️ Режим: по одной задаче", callback_data="focus")
    ])

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_focus_message(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = "🎯 Фокус-режим (по одной задаче):\n\n"
    for task_id, title in TASKS:
        done = tasks_state.get(task_id, 0)
        status = "✅" if done else "⬜"
        text += f"{status} {title}\n"

    next_task = None
    for task_id, title in TASKS:
        if tasks_state.get(task_id, 0) == 0:
            next_task = (task_id, title)
            break

    keyboard: list[list[InlineKeyboardButton]] = []

    if next_task:
        task_id, title = next_task
        keyboard.append([
            InlineKeyboardButton(
                text=f"✅ Сделано: {title}",
                callback_data=f"focus_done:{task_id}",
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(text="✨ Всё выполнено", callback_data="noop")
        ])

    keyboard.append([
        InlineKeyboardButton(text="↩️ Назад к списку (все кнопки)", callback_data="back_to_plan")
    ])

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


async def safe_edit(message: Message, text: str, kb: InlineKeyboardMarkup):
    """
    Иногда Telegram ругается 'message is not modified', если текст/клава те же.
    Тогда просто молча игнорим.
    """
    try:
        await message.edit_text(text, reply_markup=kb)
    except Exception:
        # Не спамим пользователя ошибками — это UX-мелочь.
        pass


# -------------------- Commands --------------------

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
        "Я веду тебя по релизу шаг за шагом.\n"
        "Открой /plan и отмечай выполненное."
    )


@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_plan_message(tasks_state)
    await message.answer(text, reply_markup=kb)


# -------------------- Callbacks --------------------

@dp.callback_query(F.data.startswith("toggle:"))
async def toggle_task_handler(callback):
    tg_id = callback.from_user.id
    task_id = int(callback.data.split(":")[1])

    await ensure_user(tg_id)
    await toggle_task(tg_id, task_id)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_plan_message(tasks_state)

    await safe_edit(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data == "focus")
async def focus_mode(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus_message(tasks_state)

    await safe_edit(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("focus_done:"))
async def focus_done(callback):
    tg_id = callback.from_user.id
    task_id = int(callback.data.split(":")[1])

    await ensure_user(tg_id)
    await toggle_task(tg_id, task_id)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus_message(tasks_state)

    await safe_edit(callback.message, text, kb)
    await callback.answer("Отмечено")


@dp.callback_query(F.data == "back_to_plan")
async def back_to_plan(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_plan_message(tasks_state)

    await safe_edit(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def noop(callback):
    await callback.answer()


# -------------------- Runner --------------------

async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Добавь его в переменные окружения Railway.")

    await init_db()

    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
