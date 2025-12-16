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

# tasks: (id, title)
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

# help text per task_id
HELP = {
    1: "Что нужно: финальный мастер в WAV (обычно 24-bit, 44.1k/48k). Без клиппинга.\n"
       "Где взять: от звукорежа/студии или сам экспорт из проекта.\n"
       "Частая ошибка: залить mp3 вместо WAV.",
    2: "Если в треке мат/жёсткий контент — некоторые площадки требуют пометку Explicit.\n"
       "Иногда полезно иметь Clean-версию (без мата), если хочешь больше плейлистов/радио.\n"
       "Если мата нет — можно пропустить.",
    3: "Обложка: квадрат 3000×3000 (часто JPG/PNG), без мелкого текста.\n"
       "Проверь: нет чужих логотипов, брендов, чужих лиц без прав.\n"
       "Частая ошибка: слишком тёмная/мыльная картинка или маленькое разрешение.",
    4: "Название лучше не менять после загрузки — можно сломать ссылки/ID у площадок.\n"
       "Проверь транслит/символы/капс, чтобы везде было одинаково.",
    5: "Запиши: кто автор музыки/текста/аранжа, доли (сплиты), псевдонимы.\n"
       "Это нужно, чтобы потом не было конфликтов и чтобы всё корректно монетизировалось.",
    6: "Дистрибьютор — сервис, который доставляет релиз на площадки.\n"
       "Для MVP просто выбери одного и не прыгай между ними ради 'лучше'.",
    7: "Загрузить релиз: аудио WAV, обложка, дата релиза, авторы.\n"
       "Сделай это заранее (хотя бы за 2–3 недели), чтобы всё успело разъехаться.",
    8: "Метаданные — это имя артиста/трек, жанр, язык, explicit, авторы.\n"
       "Частая ошибка: разные написания артиста в разных релизах.",
    9: "BandLink/Smartlink — страница со ссылками на все площадки.\n"
       "Нужно: чтобы одним линком вести людей на Spotify/YM/VK и т.д.",
    10: "Пресейв — подписка 'сохранить релиз заранее' (если площадки/сервис поддерживают).\n"
        "Не обязателен, но помогает собрать ранний интерес.",
    11: "Сделай короткий текст: что за трек, настроение, 1–2 референса, чем цепляет.\n"
        "Это пригодится для постов, питчинга и рассылок.",
    12: "Минимум 3 штуки: тизер (10–15 сек), пост/карусель, сторис.\n"
        "Цель: чтобы в день релиза у тебя уже был контент, а не паника.",
    13: "Собери 10–30 контактов: плейлисты, паблики, блоги, редакторы (где реально твой жанр).\n"
        "Не спамь всем подряд — лучше меньше, но точнее.",
    14: "Пострелиз: 7 дней — это второй шанс, не 'конец'.\n"
        "Запланируй 2 инфоповода: лайв-кусок, бэкстейдж, ремикс/акустика, клип-тизер.",
}

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


# -------------------- Helpers --------------------

def get_next_task(tasks_state: dict[int, int]):
    for task_id, title in TASKS:
        if tasks_state.get(task_id, 0) == 0:
            return task_id, title
    return None


async def safe_edit(message: Message, text: str, kb: InlineKeyboardMarkup | None):
    try:
        await message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


def render_list_text(tasks_state: dict[int, int], header: str) -> str:
    text = f"{header}\n\n"
    for task_id, title in TASKS:
        done = tasks_state.get(task_id, 0)
        status = "✅" if done else "⬜"
        text += f"{status} {title}\n"
    return text


# -------------------- UI builders --------------------

def build_focus(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = render_list_text(tasks_state, "🎯 Фокус-режим")

    next_task = get_next_task(tasks_state)
    keyboard: list[list[InlineKeyboardButton]] = []

    if next_task:
        task_id, title = next_task
        keyboard.append([
            InlineKeyboardButton(text=f"✅ Сделано: {title}", callback_data=f"focus_done:{task_id}")
        ])
        keyboard.append([
            InlineKeyboardButton(text="❓ Пояснение", callback_data=f"help:{task_id}")
        ])
    else:
        keyboard.append([InlineKeyboardButton(text="✨ Всё выполнено", callback_data="noop")])

    keyboard.append([InlineKeyboardButton(text="📋 Показать все задачи", callback_data="show_all")])

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_all_list(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = render_list_text(tasks_state, "📋 Все задачи (без кнопок на каждую)")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Вернуться в фокус-режим", callback_data="back_to_focus")]
    ])
    return text, kb


def build_help(task_id: int, title: str) -> tuple[str, InlineKeyboardMarkup]:
    body = HELP.get(task_id, "Пояснение пока не добавлено.")
    text = f"❓ {title}\n\n{body}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]
    ])
    return text, kb


# -------------------- Commands --------------------

@dp.message(CommandStart())
async def start(message: Message):
    await ensure_user(message.from_user.id)
    await message.answer(
        "Я ИСКРА — помощник по релизу.\n\n"
        "Команды:\n"
        "/plan — план релиза (фокус-режим)\n"
        "/help — помощь"
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "Открой /plan.\n"
        "В фокус-режиме ты закрываешь задачи по одной, и можешь читать подсказки."
    )


@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await message.answer(text, reply_markup=kb)


# -------------------- Callbacks --------------------

@dp.callback_query(F.data.startswith("focus_done:"))
async def focus_done(callback):
    tg_id = callback.from_user.id
    task_id = int(callback.data.split(":")[1])

    await ensure_user(tg_id)
    await toggle_task(tg_id, task_id)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)

    await safe_edit(callback.message, text, kb)
    await callback.answer("Отмечено")


@dp.callback_query(F.data.startswith("help:"))
async def show_help(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    task_id = int(callback.data.split(":")[1])
    title = next((t for tid, t in TASKS if tid == task_id), "Задача")

    text, kb = build_help(task_id, title)
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data == "show_all")
async def show_all(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_all_list(tasks_state)

    await safe_edit(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data == "back_to_focus")
async def back_to_focus(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)

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
