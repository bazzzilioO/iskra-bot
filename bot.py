import asyncio
import os

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

DB_PATH = "bot.db"

LINKS = {
    "bandlink_home": "https://band.link/",
    "bandlink_login": "https://band.link/login",

    "spotify_pitch_info": "https://support.spotify.com/us/artists/article/pitching-music-to-playlist-editors/",
    "spotify_for_artists": "https://artists.spotify.com/",

    "yandex_artists_hub": "https://yandex.ru/support/music/ru/performers-and-copyright-holders",
    "yandex_pitch": "https://yandex.ru/support/music/ru/performers-and-copyright-holders/new-release",

    "apple_pitch_guide": "https://itunespartner.apple.com/music/support/5391-apple-music-pitch-user-guide",

    # KION Музыка (бывш. МТС Музыка)
    "kion_pitch": "https://music.mts.ru/pitch",

    # Звук
    "zvuk_pitch": "https://help.zvuk.com/article/67859",
    "zvuk_studio": "https://studio.zvuk.com/",

    # VK (общая инфа — ссылка на вход/кабинет может отличаться; даём понятное направление)
    "vk_studio_info": "https://the-flow.ru/features/zachem-artistu-studiya-servis-vk-muzyki",
}

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
    (9, "Получен UPC/ISRC и ссылки площадок (или подтверждение, что появятся)"),
    (10, "Сделана страница релиза в BandLink (Smartlink)"),
    (11, "Сделан пресейв (если доступно)"),
    (12, "Кабинеты артиста: Spotify / Яндекс / VK / Звук"),
    (13, "Текст о треке (5–7 строк)"),
    (14, "Подготовлены 3 контент-единицы"),
    (15, "Список плейлистов / медиа"),
    (16, "Питчинг: Spotify / Яндекс / VK / Звук / КИОН"),
]

HELP = {
    1: "Что нужно: финальный мастер в WAV (обычно 24-bit, 44.1k/48k). Без клиппинга.\n"
       "Частая ошибка: залить mp3 вместо WAV.",

    2: "Если в треке мат/жёсткий контент — некоторые площадки требуют пометку Explicit.\n"
       "Иногда полезно иметь Clean-версию (без мата), если хочешь больше плейлистов/радио.\n"
       "Если мата нет — можно пропустить.",

    3: "Обложка: квадрат 3000×3000 (JPG/PNG), без мелкого текста.\n"
       "Проверь: нет чужих логотипов/брендов/чужих лиц без прав.",

    4: "Название лучше не менять после загрузки — можно сломать ссылки/ID у площадок.\n"
       "Проверь написание, чтобы везде было одинаково.",

    5: "Запиши: кто автор музыки/текста/аранжа, доли (сплиты).\n"
       "Это нужно, чтобы потом не было конфликтов.",

    6: "Дистрибьютор — сервис, который доставляет релиз на площадки.\n"
       "Для MVP выбери одного и не прыгай между ними ради 'лучше'.",

    7: "Загрузить релиз: WAV, обложка, дата релиза, авторы.\n"
       "Сделай заранее (лучше 2–3 недели), чтобы площадки успели принять релиз.",

    8: "Метаданные — имя артиста/трек, жанр, язык, explicit, авторы.\n"
       "Частая ошибка: разные написания артиста в разных релизах.",

    9: "Перед BandLink часто нужно дождаться, чтобы релиз «доехал» до площадок.\n"
       "Что сделать:\n"
       "1) После загрузки в дистрибьютора найди/запроси UPC (и ISRC)\n"
       "2) Попроси у дистрибьютора ссылки на будущий релиз (если он выдаёт)\n"
       "3) Либо дождись появления релиза в системах площадок (появятся ссылки)\n\n"
       "Зачем: BandLink проще и надёжнее собирать, когда есть UPC/ссылки.",

    10: "BandLink/Smartlink — одна страница релиза со ссылками на все площадки.\n"
        f"Сайт: {LINKS['bandlink_home']}\n"
        f"Вход: {LINKS['bandlink_login']}\n\n"
        "Минимум: обложка + короткий текст + кнопки площадок + соцсети.\n"
        "Идея: один линк вместо 10 ссылок.",

    11: "Пресейв — 'сохранить релиз заранее'. Не всегда доступен.\n"
        "Если доступен — веди трафик на пресейв через страницу релиза BandLink.\n"
        "Если недоступен — делай smartlink и прогрев контентом.",

    12: "Кабинеты артиста нужны для:\n"
        "- оформления профиля (фото/био)\n"
        "- статистики\n"
        "- питчинга/редакций\n\n"
        "База:\n"
        f"- Spotify for Artists: {LINKS['spotify_for_artists']}\n"
        f"- Яндекс (раздел для артистов): {LINKS['yandex_artists_hub']}\n"
        f"- Звук Studio: {LINKS['zvuk_studio']}\n"
        f"- VK Студия (инфа): {LINKS['vk_studio_info']}\n\n"
        "Даже если ты уже выпускал — кабинеты могут быть не настроены. Это нормально.",

    13: "Короткий текст: что за трек, настроение, 1–2 референса, чем цепляет.\n"
        "Нужен для постов, питчинга и рассылок.",

    14: "Минимум 3 штуки: тизер (10–15 сек), пост, сторис.\n"
        "Цель: в день релиза у тебя уже был контент.",

    15: "Собери 10–30 контактов: плейлисты, паблики, блоги, редакторы (по твоему жанру).\n"
        "Лучше меньше, но точнее.\n"
        "Сделай короткий шаблон сообщения и персонализируй 1–2 строки.",

    16: "Питчинг (ориентир): подавай минимум за 14 дней до релиза.\n\n"
        "Spotify:\n"
        f"- Инфа: {LINKS['spotify_pitch_info']}\n"
        f"- Кабинет: {LINKS['spotify_for_artists']}\n\n"
        "Яндекс Музыка:\n"
        f"- Официально: {LINKS['yandex_pitch']}\n"
        "Важно: питчинг отправляет верифицированный артист/менеджер; будущий релиз появляется в BandLink.\n\n"
        "Звук:\n"
        f"- Питчинг из приложения/кабинета Звук Studio: {LINKS['zvuk_studio']}\n"
        f"- Инструкция: {LINKS['zvuk_pitch']}\n\n"
        "КИОН Музыка (бывш. МТС Музыка):\n"
        f"- Форма: {LINKS['kion_pitch']}\n\n"
        "VK Музыка:\n"
        "- Питчинг делается из VK Студии (внутри экосистемы VK Музыки).\n"
        f"- Инфа: {LINKS['vk_studio_info']}\n",
}

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()


# -------------------- DB --------------------

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            experience TEXT DEFAULT 'unknown'
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
        # миграция на случай, если users раньше был без колонки experience
        try:
            await db.execute("ALTER TABLE users ADD COLUMN experience TEXT DEFAULT 'unknown'")
        except Exception:
            pass
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


async def set_experience(tg_id: int, exp: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET experience=? WHERE tg_id=?", (exp, tg_id))
        await db.commit()


async def get_experience(tg_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT experience FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        if not row or not row[0]:
            return "unknown"
        return row[0]


async def get_tasks_state(tg_id: int) -> dict[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT task_id, done FROM user_tasks WHERE tg_id = ?",
            (tg_id,),
        )
        rows = await cur.fetchall()
        return {task_id: done for task_id, done in rows}


async def set_task_done(tg_id: int, task_id: int, done: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE user_tasks
        SET done = ?
        WHERE tg_id = ? AND task_id = ?
        """, (done, tg_id, task_id))
        await db.commit()


async def toggle_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE user_tasks
        SET done = 1 - done
        WHERE tg_id = ? AND task_id = ?
        """, (tg_id, task_id))
        await db.commit()


async def reset_progress(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        UPDATE user_tasks
        SET done = 0
        WHERE tg_id = ?
        """, (tg_id,))
        await db.commit()


# -------------------- Helpers --------------------

def count_progress(tasks_state: dict[int, int]) -> tuple[int, int]:
    total = len(TASKS)
    done = sum(1 for task_id, _ in TASKS if tasks_state.get(task_id, 0) == 1)
    return done, total


def get_next_task(tasks_state: dict[int, int]):
    for task_id, title in TASKS:
        if tasks_state.get(task_id, 0) == 0:
            return task_id, title
    return None


def get_last_done_task(tasks_state: dict[int, int]):
    last = None
    for task_id, title in TASKS:
        if tasks_state.get(task_id, 0) == 1:
            last = (task_id, title)
    return last


def render_list_text(tasks_state: dict[int, int], header: str) -> str:
    done, total = count_progress(tasks_state)
    text = f"{header}\nПрогресс: {done}/{total}\n\n"
    for task_id, title in TASKS:
        status = "✅" if tasks_state.get(task_id, 0) else "⬜"
        text += f"{status} {title}\n"
    return text


def postrelease_7days_text() -> str:
    return (
        "📅 7 дней после релиза — мини-план\n\n"
        "День 1: запуск\n"
        "- Пост со smartlink (BandLink) + 1 тезис: «что это за трек»\n"
        "- Сторис: 10–15 секунд самый цепляющий момент\n\n"
        "День 2: контекст\n"
        "- Мини-история: зачем трек / как родился (фактами)\n"
        "- Репосты слушателей (если есть)\n\n"
        "День 3: доказательство\n"
        "- Бэкстейдж/демка «до/после» или кусок из студии\n"
        "- Прямо попросить: «сохрани/добавь в плейлист»\n\n"
        "День 4: алгоритмы\n"
        "- Вертикальный ролик (тизер + текст поверх)\n"
        "- Снова smartlink (без стыда)\n\n"
        "День 5: коммуникации\n"
        "- Точечная рассылка по плейлистам/медиа (по жанру)\n"
        "- 1–2 персональных сообщения вместо спама\n\n"
        "День 6: вариация\n"
        "- Альтернативный контент: лайв/акустика/ремикс-тизер\n"
        "- Smartlink ещё раз\n\n"
        "День 7: закрепление\n"
        "- Итоговый пост: «спасибо/цифры/планы» + call-to-action\n\n"
        "Главное: 7 дней — это вторая попытка. Не исчезай после релиза."
    )


def export_text(tasks_state: dict[int, int]) -> str:
    done, total = count_progress(tasks_state)
    lines = []
    lines.append("ИСКРА — экспорт плана релиза")
    lines.append(f"Прогресс: {done}/{total}")
    lines.append("")
    for task_id, title in TASKS:
        status = "✅" if tasks_state.get(task_id, 0) else "⬜"
        lines.append(f"{status} {title}")
    lines.append("")
    lines.append("Ссылки:")
    lines.append(f"- BandLink: {LINKS['bandlink_home']}")
    lines.append(f"- Spotify for Artists: {LINKS['spotify_for_artists']}")
    lines.append(f"- Яндекс (артистам): {LINKS['yandex_artists_hub']}")
    lines.append(f"- Яндекс питчинг: {LINKS['yandex_pitch']}")
    lines.append(f"- Звук Studio: {LINKS['zvuk_studio']}")
    lines.append(f"- Звук питчинг: {LINKS['zvuk_pitch']}")
    lines.append(f"- КИОН Музыка (бывш. МТС Музыка) питчинг: {LINKS['kion_pitch']}")
    lines.append(f"- VK Студия (инфа): {LINKS['vk_studio_info']}")
    lines.append("")
    lines.append("Напоминание:")
    lines.append("- Для BandLink часто нужны UPC/ISRC и/или ссылки площадок (спроси у дистрибьютора).")
    lines.append("- Питчинг лучше подавать минимум за 14 дней до релиза.")
    return "\n".join(lines)


async def safe_edit(message: Message, text: str, kb: InlineKeyboardMarkup | None):
    try:
        await message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


# -------------------- UI builders --------------------

def build_start_onboarding() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "Я ИСКРА — помощник по релизу.\n\n"
        "Быстрый вопрос, чтобы подстроиться:\n"
        "Это твой первый релиз или ты уже выпускал музыку?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Это мой первый релиз", callback_data="exp:first")],
        [InlineKeyboardButton(text="🎧 Я уже выпускал(а)", callback_data="exp:old")],
    ])
    return text, kb


def build_focus(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    done_count, total = count_progress(tasks_state)

    if done_count == total:
        text = (
            "🎉 Поздравляю с закрытием релиза.\n"
            "Теперь важное — не исчезнуть на следующий день.\n\n"
            "Выбери действие:"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📅 7 дней после релиза", callback_data="post7")],
            [InlineKeyboardButton(text="📤 Экспорт плана в текст", callback_data="export")],
            [InlineKeyboardButton(text="📋 Показать все задачи", callback_data="show_all")],
            [InlineKeyboardButton(text="🔁 Сбросить прогресс", callback_data="reset")],
        ])
        return text, kb

    text = render_list_text(tasks_state, "🎯 Фокус-режим")

    next_task = get_next_task(tasks_state)
    last_done = get_last_done_task(tasks_state)

    keyboard: list[list[InlineKeyboardButton]] = []

    if next_task:
        task_id, title = next_task
        keyboard.append([InlineKeyboardButton(text=f"✅ Сделано: {title}", callback_data=f"focus_done:{task_id}")])
        keyboard.append([InlineKeyboardButton(text="❓ Пояснение", callback_data=f"help:{task_id}")])

    if last_done:
        last_id, last_title = last_done
        keyboard.append([InlineKeyboardButton(text=f"↩️ Отменить последнее: {last_title}", callback_data=f"undo:{last_id}")])

    keyboard.append([InlineKeyboardButton(text="📤 Экспорт плана в текст", callback_data="export")])
    keyboard.append([InlineKeyboardButton(text="📅 7 дней после релиза", callback_data="post7")])
    keyboard.append([InlineKeyboardButton(text="📋 Показать все задачи", callback_data="show_all")])

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


def build_all_list(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = render_list_text(tasks_state, "📋 Все задачи (можно отметить любую)")

    inline = []
    for task_id, title in TASKS:
        done = tasks_state.get(task_id, 0)
        btn_text = f"{'✅ Снять' if done else '⬜ Отметить'}: {title}"
        inline.append([InlineKeyboardButton(text=btn_text, callback_data=f"all_toggle:{task_id}")])

    inline.append([InlineKeyboardButton(text="📤 Экспорт плана в текст", callback_data="export")])
    inline.append([InlineKeyboardButton(text="🎯 Вернуться в фокус-режим", callback_data="back_to_focus")])

    return text, InlineKeyboardMarkup(inline_keyboard=inline)


def build_help(task_id: int, title: str) -> tuple[str, InlineKeyboardMarkup]:
    body = HELP.get(task_id, "Пояснение пока не добавлено.")
    text = f"❓ {title}\n\n{body}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]
    ])
    return text, kb


def build_post7() -> tuple[str, InlineKeyboardMarkup]:
    text = postrelease_7days_text()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Назад", callback_data="back_to_focus")]
    ])
    return text, kb


# -------------------- Commands --------------------

@dp.message(CommandStart())
async def start(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    exp = await get_experience(tg_id)

    if exp == "unknown":
        text, kb = build_start_onboarding()
        await message.answer(text, reply_markup=kb)
        return

    await message.answer(
        "Я ИСКРА — помощник по релизу.\n\n"
        "Команды:\n"
        "/plan — план релиза (фокус-режим)\n"
        "/help — помощь\n"
        "/reset_profile — поменять «первый релиз / уже выпускал»"
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "Открой /plan.\n"
        "Фокус-режим ведёт по одной задаче + есть 'Отменить последнее'.\n"
        "В 'Показать все задачи' можно вручную отметить/снять любую.\n"
        "Есть экспорт плана и мини-план на 7 дней после релиза."
    )


@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await message.answer(text, reply_markup=kb)


@dp.message(Command("reset_profile"))
async def reset_profile(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    await set_experience(tg_id, "unknown")
    text, kb = build_start_onboarding()
    await message.answer(text, reply_markup=kb)


# -------------------- Callbacks --------------------

@dp.callback_query(F.data.startswith("exp:"))
async def set_exp(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    exp = callback.data.split(":")[1]
    if exp == "first":
        await set_experience(tg_id, "first")
        await callback.message.answer("Ок. Я буду объяснять чуть подробнее и не буду предполагать, что кабинеты уже есть.")
    else:
        await set_experience(tg_id, "old")
        await callback.message.answer("Ок. Но всё равно проверим кабинеты артиста — их часто забывают настроить.")

    await callback.message.answer(
        "Команды:\n"
        "/plan — план релиза (фокус-режим)\n"
        "/help — помощь"
    )
    await callback.answer("Готово")


@dp.callback_query(F.data.startswith("focus_done:"))
async def focus_done(callback):
    tg_id = callback.from_user.id
    task_id = int(callback.data.split(":")[1])

    await ensure_user(tg_id)
    await set_task_done(tg_id, task_id, 1)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Отмечено")


@dp.callback_query(F.data.startswith("undo:"))
async def undo_last(callback):
    tg_id = callback.from_user.id
    task_id = int(callback.data.split(":")[1])

    await ensure_user(tg_id)
    await set_task_done(tg_id, task_id, 0)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Откатил")


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


@dp.callback_query(F.data.startswith("all_toggle:"))
async def all_toggle(callback):
    tg_id = callback.from_user.id
    task_id = int(callback.data.split(":")[1])

    await ensure_user(tg_id)
    await toggle_task(tg_id, task_id)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_all_list(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Ок")


@dp.callback_query(F.data == "back_to_focus")
async def back_to_focus(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data == "export")
async def export_plan(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)

    text = export_text(tasks_state)
    await callback.message.answer(text)
    await callback.answer("Экспортировал")


@dp.callback_query(F.data == "post7")
async def post7(callback):
    text, kb = build_post7()
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data == "reset")
async def reset(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    await reset_progress(tg_id)

    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Сбросил")


# -------------------- Runner --------------------

async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Добавь его в переменные окружения Railway/Render.")

    await init_db()

    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
