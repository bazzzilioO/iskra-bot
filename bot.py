import asyncio
import os
import json
import datetime as dt
import re
import aiosqlite
import smtplib
from email.mime.text import MIMEText

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    LabeledPrice, PreCheckoutQuery
)
from dotenv import load_dotenv

DB_PATH = "bot.db"
LABEL_EMAIL = "sreda.records@gmail.com"
REMINDER_INTERVAL_SECONDS = 300

# -------------------- CONFIG --------------------

LINKS = {
    "bandlink_home": "https://band.link/",
    "bandlink_login": "https://band.link/login",
    "spotify_for_artists": "https://artists.spotify.com/",
    "spotify_pitch_info": "https://support.spotify.com/us/artists/article/pitching-music-to-playlist-editors/",
    "yandex_artists_hub": "https://yandex.ru/support/music/ru/performers-and-copyright-holders",
    "yandex_pitch": "https://yandex.ru/support/music/ru/performers-and-copyright-holders/new-release",
    "kion_pitch": "https://music.mts.ru/pitch",  # КИОН (бывш. МТС Music)
    "zvuk_pitch": "https://help.zvuk.com/article/67859",
    "zvuk_studio": "https://studio.zvuk.com/",
    "vk_studio_info": "https://the-flow.ru/features/zachem-artistu-studiya-servis-vk-muzyki",
    "tiktok_for_artists": "https://artists.tiktok.com/",
}

ACCOUNTS = [
    ("spotify", "Spotify for Artists"),
    ("yandex", "Яндекс для артистов"),
    ("vk", "VK Studio"),
    ("zvuk", "Звук Studio"),
    ("tiktok", "TikTok (аккаунт + Artist/Music Tab)"),
]

def next_acc_status(v: int) -> int:
    return (v + 1) % 3

def task_mark(done: int) -> str:
    return "✅" if done else "▫️"

def build_export_text(tasks_state: dict[int, int]) -> str:
    done, total = count_progress(tasks_state)
    lines = [f"ИСКРА — экспорт плана релиза\nПрогресс задач: {done}/{total}\n"]
    for task_id, title in TASKS:
        lines.append(f"{task_mark(tasks_state.get(task_id, 0))} {title}")
    return "\n".join(lines)

async def send_export_invoice(message: Message):
    await message.answer(
        "📤 Экспорт плана — 25 ⭐\n\n"
        "Оплата через Telegram Stars. После оплаты пришлю чек-лист релиза.",
        reply_markup=menu_keyboard()
    )
    prices = [LabeledPrice(label="Экспорт плана", amount=25)]
    await message.answer_invoice(
        title="Экспорт плана",
        description="Чек-лист задач с прогрессом (25 ⭐)",
        payload="export_plan_25",
        provider_token="",
        currency="XTR",
        prices=prices
    )

# -------------------- DATE: RU format --------------------

def format_date_ru(d: dt.date) -> str:
    return d.strftime("%d.%m.%Y")

def parse_date(date_str: str) -> dt.date | None:
    """
    Понимает:
      - YYYY-MM-DD
      - DD.MM.YYYY
    """
    s = (date_str or "").strip()
    try:
        if "-" in s:
            y, m, d = s.split("-")
            return dt.date(int(y), int(m), int(d))
        if "." in s:
            d, m, y = s.split(".")
            return dt.date(int(y), int(m), int(d))
    except Exception:
        return None
    return None

# -------------------- TASKS --------------------

TASKS = [
    (1, "Цель релиза выбрана (зачем это выпускаю)"),
    (2, "Права/ownership: все участники согласны + семплы/биты легальны"),
    (3, "Единый нейминг: артист/трек/фиты везде одинаково"),
    (4, "Жанр + 1–2 референса определены (для питчинга/алгоритмов)"),
    (5, "Мини EPK: аватар + 1 фото + короткое био (для медиа/профилей)"),

    (6, "Мастер готов (WAV 24bit)"),
    (7, "Clean/Explicit версия (если нужно)"),
    (8, "Обложка 3000×3000 финальная"),
    (9, "Авторы и сплиты записаны"),

    (10, "Выбран дистрибьютор"),
    (11, "Релиз загружен в дистрибьютора"),
    (12, "Метаданные проверены (язык/explicit/жанр/написание)"),

    (13, "Получен UPC/ISRC и/или ссылки площадок (или подтверждение, что появятся)"),
    (14, "Лирика/синхронизация (опционально: Musixmatch/Genius)"),
    (15, "Сделана страница релиза в BandLink (Smartlink)"),
    (16, "Сделан пресейв (если доступно)"),

    (17, "Кабинеты артиста: Spotify / Яндекс / VK / Звук / TikTok (по возможности)"),
    (18, "Шаблон сообщения для плейлистов/медиа готов (5–7 строк)"),
    (19, "Питчинг: Spotify / Яндекс / VK / Звук / КИОН (если доступно)"),

    (20, "Контент-единицы минимум 3 (тизер/пост/сторис)"),
    (21, "Контент-спринт: 30 вертикалок ДО релиза (рекомендация)"),
    (22, "UGC/Content ID настройки проверены (чтобы не словить страйки)"),
    (23, "Контент-спринт: 30 вертикалок ПОСЛЕ релиза (рекомендация)"),

    (24, "Список плейлистов / медиа собран (10–30 точечных)"),
]

SECTIONS = [
    ("prep", "1) Подготовка", [1, 2, 3, 4, 5]),
    ("assets", "2) Материалы релиза", [6, 7, 8, 9]),
    ("dist", "3) Дистрибуция", [10, 11, 12]),
    ("links", "4) UPC / BandLink / Лирика", [13, 14, 15, 16]),
    ("accounts", "5) Кабинеты / Питчинг", [17, 18, 19]),
    ("content", "6) Контент", [20, 21, 22, 23, 24]),
]

DEADLINES = [
    {"key": "pitching", "title": "Pitching (Spotify / Яндекс / VK / Звук / МТС-КИОН)", "offset": -14},
    {"key": "presave", "title": "Pre-save", "offset": -7},
    {"key": "bandlink", "title": "BandLink / Smartlink", "offset": -7},
    {"key": "content_sprint", "title": "Контент-спринт ДО — старт", "offset": -14},
    {"key": "post_1", "title": "Пост-релиз план (+1)", "offset": 1},
    {"key": "post_3", "title": "Пост-релиз план (+3)", "offset": 3},
    {"key": "post_7", "title": "Пост-релиз план (+7)", "offset": 7},
]

HELP = {
    1: "Определи 1 цель: подписчики / плейлисты / медиа / деньги / проверка гипотезы. Это задаёт весь план.",
    2: "Проверь права: кто автор текста/музыки, кому принадлежит бит, есть ли разрешение на семплы.",
    3: "Одинаковое написание артиста/трека/фитов везде (обложка, дистрибьютор, BandLink, соцсети) — иначе карточки разъедутся.",
    4: "Жанр и 1–2 референса нужны для питчинга и алгоритмов (куда ставить на полку).",
    5: "Мини-EPK: аватар, 1 фотка, 3–5 предложений био. Это для медиа/плейлистов/кабинетов.",

    6: "Экспорт мастера: WAV 24bit (44.1k/48k), без клиппинга. Финальный файл держи отдельно.",
    7: "Если есть мат/жёсткий контент — ставь Explicit. Иногда полезно иметь Clean-версию.",
    8: "3000×3000, без мелкого текста. Без запрещённого/чужих логотипов.",
    9: "Запиши сплиты: кто что написал и в каких долях. Даже если «по дружбе».",

    10: "Выбери дистрибьютора: комиссия, выплаты, доступ к UPC/ISRC, саппорт, сроки модерации.",
    11: "Загрузи заранее (лучше 2–4 недели), чтобы успеть получить ссылки и сделать пресейв/питчинг.",
    12: "Проверь: язык, explicit, жанр, авторы, фиты, обложка. Ошибка = отказ/двойные карточки.",

    13: "UPC/ISRC часто нужны для smartlink и верификаций. Если не видишь — запроси у дистрибьютора.",
    14: "Опционально: Musixmatch/Genius. Помогает поиску и карточке трека, но не критично.",
    15: "BandLink/Smartlink — единая ссылка на релиз. Делай, когда появились ссылки/пресейв.",
    16: "Пресейв возможен, когда площадки/интеграции доступны. Если нет — просто делай smartlink.",

    17: "Кабинеты Spotify/Яндекс/VK/Звук/TikTok. Иногда доступны только после 1 релиза — ставь «⏳» и вернись позже.",
    18: "Сделай шаблон: 5–7 строк о треке + 1 ссылка + почему вы им подходите. Экономит часы.",
    19: "Питчинг: Spotify/Яндекс/VK/Звук/КИОН (бывш. МТС). Рекомендуем подавать до релиза (−14 дней).",

    20: "Минимум 3 контент-единицы: тизер, пост, сторис. Главное — движение.",
    21: "30 вертикалок ДО — рекомендация: тестируешь разные хуки/моменты. Объём важнее идеальности.",
    22: "Проверь Content ID/UGC, чтобы твой трек не сносил твои же видео и не ловил ложные страйки.",
    23: "30 вертикалок ПОСЛЕ — реакции, лайвы, история трека, ответы на комменты, новые куски.",
    24: "Собери 10–30 плейлистов/медиа и пиши точечно. Адресно конвертит лучше массовых рассылок.",
}

def expectations_text() -> str:
    return (
        "🧠 Ожидания / реальность\n\n"
        "1) Первый релиз почти никогда не «взлетает». Это нормально.\n"
        "2) Цель — система: процесс, контент, кабинеты.\n"
        "3) Алгоритмы любят регулярность.\n"
        "4) Мерь себя качеством процесса, не цифрами первого релиза.\n"
    )

def experience_prompt() -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Первый релиз", callback_data="exp:first")],
        [InlineKeyboardButton(text="🎧 Уже выпускал(а)", callback_data="exp:old")],
    ])
    text = (
        "Я ИСКРА — помощник по релизу.\n\n"
        "Это твой первый релиз или ты уже выпускал музыку?"
    )
    return text, kb

def menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎯 План"), KeyboardButton(text="📋 Задачи по разделам")],
            [KeyboardButton(text="🧾 Кабинеты"), KeyboardButton(text="📅 Таймлайн")],
            [KeyboardButton(text="🗓️ Установить дату"), KeyboardButton(text="🔗 Ссылки")],
            [KeyboardButton(text="📩 Запросить дистрибуцию"), KeyboardButton(text="📤 Экспорт")],
            [KeyboardButton(text="💫 Поддержать ИСКРУ"), KeyboardButton(text="🧠 Ожидания")],
            [KeyboardButton(text="🧹 Сброс")],
        ],
        resize_keyboard=True
    )

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID = os.getenv("ADMIN_TG_ID")

SMTP_USER = os.getenv("SMTP_USER")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD")
SMTP_TO = os.getenv("SMTP_TO") or LABEL_EMAIL

dp = Dispatcher()

# -------------------- DB --------------------

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("PRAGMA temp_store=MEMORY;")
        await db.execute("PRAGMA cache_size=-20000;")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            experience TEXT DEFAULT 'unknown',
            username TEXT,
            release_date TEXT DEFAULT NULL,
            reminders_enabled INTEGER DEFAULT 1
        )
        """)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN reminders_enabled INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN release_date TEXT")
        except Exception:
            pass
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reminder_log (
            tg_id INTEGER,
            key TEXT,
            "when" TEXT,
            PRIMARY KEY (tg_id, key, "when")
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
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_accounts (
            tg_id INTEGER,
            key TEXT,
            status INTEGER DEFAULT 0,
            PRIMARY KEY (tg_id, key)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_forms (
            tg_id INTEGER PRIMARY KEY,
            form_name TEXT,
            step INTEGER DEFAULT 0,
            data_json TEXT DEFAULT '{}'
        )
        """)
        await db.commit()

async def ensure_user(tg_id: int, username: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (tg_id,))
        if username is not None:
            await db.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
        for task_id, _ in TASKS:
            await db.execute("INSERT OR IGNORE INTO user_tasks (tg_id, task_id) VALUES (?, ?)", (tg_id, task_id))
        for key, _ in ACCOUNTS:
            await db.execute("INSERT OR IGNORE INTO user_accounts (tg_id, key) VALUES (?, ?)", (tg_id, key))
        await db.commit()

async def get_experience(tg_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT experience FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else "unknown"

async def set_experience(tg_id: int, exp: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET experience=? WHERE tg_id=?", (exp, tg_id))
        await db.commit()

async def set_release_date(tg_id: int, date_str: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET release_date=? WHERE tg_id=?", (date_str, tg_id))
        await db.execute("DELETE FROM reminder_log WHERE tg_id=?", (tg_id,))
        await db.commit()

async def get_release_date(tg_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT release_date FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else None

async def set_reminders_enabled(tg_id: int, enabled: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET reminders_enabled=? WHERE tg_id=?", (1 if enabled else 0, tg_id))
        await db.commit()

async def get_reminders_enabled(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT reminders_enabled FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else True

async def get_tasks_state(tg_id: int) -> dict[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT task_id, done FROM user_tasks WHERE tg_id=?", (tg_id,))
        rows = await cur.fetchall()
        return {tid: done for tid, done in rows}

async def toggle_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done = 1 - done WHERE tg_id=? AND task_id=?", (tg_id, task_id))
        await db.commit()

async def set_task_done(tg_id: int, task_id: int, done: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done=? WHERE tg_id=? AND task_id=?", (done, tg_id, task_id))
        await db.commit()

async def get_accounts_state(tg_id: int) -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT key, status FROM user_accounts WHERE tg_id=?", (tg_id,))
        rows = await cur.fetchall()
        return {k: (s if s is not None else 0) for k, s in rows}

async def cycle_account_status(tg_id: int, key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT status FROM user_accounts WHERE tg_id=? AND key=?", (tg_id, key))
        row = await cur.fetchone()
        current = row[0] if row and row[0] is not None else 0
        new = next_acc_status(current)
        await db.execute("UPDATE user_accounts SET status=? WHERE tg_id=? AND key=?", (new, tg_id, key))
        await db.commit()

async def reset_progress_only(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done=0 WHERE tg_id=?", (tg_id,))
        await db.execute("UPDATE user_accounts SET status=0 WHERE tg_id=?", (tg_id,))
        await db.commit()

# -------------------- Forms --------------------

async def form_start(tg_id: int, form_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_forms (tg_id, form_name, step, data_json) VALUES (?, ?, 0, ?)",
            (tg_id, form_name, "{}")
        )
        await db.commit()

async def form_get(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT form_name, step, data_json FROM user_forms WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        if not row:
            return None
        form_name, step, data_json = row
        try:
            data = json.loads(data_json or "{}")
        except Exception:
            data = {}
        return {"form_name": form_name, "step": step, "data": data}

async def form_set(tg_id: int, step: int, data: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_forms SET step=?, data_json=? WHERE tg_id=?",
            (step, json.dumps(data, ensure_ascii=False), tg_id)
        )
        await db.commit()

async def form_clear(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM user_forms WHERE tg_id=?", (tg_id,))
        await db.commit()

# -------------------- UX helpers --------------------

def count_progress(tasks_state: dict[int, int]) -> tuple[int, int]:
    total = len(TASKS)
    done = sum(1 for task_id, _ in TASKS if tasks_state.get(task_id, 0) == 1)
    return done, total

def get_next_task(tasks_state: dict[int, int]):
    for task_id, title in TASKS:
        if tasks_state.get(task_id, 0) == 0:
            return task_id, title
    return None

def get_task_title(task_id: int) -> str:
    for tid, t in TASKS:
        if tid == task_id:
            return t
    return "Задача"

def find_section_for_task(task_id: int) -> tuple[str, str] | None:
    for sid, stitle, ids in SECTIONS:
        if task_id in ids:
            return sid, stitle
    return None

def build_focus(tasks_state: dict[int, int], experience: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    done, total = count_progress(tasks_state)
    next_task = get_next_task(tasks_state)

    lines = []
    lines.append("🎯 Фокус-режим")
    if experience == "first":
        lines.append("Тип релиза: первый")
    elif experience == "old":
        lines.append("Тип релиза: не первый")
    lines.append(f"Прогресс: {done}/{total}\n")

    rows: list[list[InlineKeyboardButton]] = []

    if not next_task:
        lines.append("✨ Всё выполнено. Поздравляю с закрытием релиза.")
        rows.append([InlineKeyboardButton(text="🧹 Сброс", callback_data="reset_menu")])
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    task_id, title = next_task
    sec = find_section_for_task(task_id)
    if sec:
        _, stitle = sec
        lines.append(f"Раздел: {stitle}")
    lines.append(f"Следующая задача:\n▫️ {title}\n")

    upcoming = []
    for tid, t in TASKS:
        if tid == task_id:
            continue
        if tasks_state.get(tid, 0) == 0:
            upcoming.append(t)
        if len(upcoming) >= 3:
            break
    if upcoming:
        lines.append("Дальше по очереди:")
        for t in upcoming:
            lines.append(f"▫️ {t}")

    rows.append([InlineKeyboardButton(text=f"✅ Сделано: {title}", callback_data=f"focus_done:{task_id}")])
    rows.append([InlineKeyboardButton(text="❓ Пояснение", callback_data=f"help:{task_id}")])
    rows.append([
        InlineKeyboardButton(text="📋 Задачи по разделам", callback_data="sections:open"),
        InlineKeyboardButton(text="🧾 Кабинеты", callback_data="accounts:open"),
    ])
    rows.append([
        InlineKeyboardButton(text="📅 Таймлайн", callback_data="timeline"),
        InlineKeyboardButton(text="🔗 Ссылки", callback_data="links"),
    ])
    rows.append([InlineKeyboardButton(text="📩 Запросить дистрибуцию", callback_data="label:start")])
    rows.append([InlineKeyboardButton(text="💫 Поддержать ИСКРУ", callback_data="donate:menu")])
    rows.append([InlineKeyboardButton(text="🧹 Сброс", callback_data="reset_menu")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

def build_sections_menu(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    done, total = count_progress(tasks_state)
    text = f"📋 Задачи по разделам\nПрогресс: {done}/{total}\n\nВыбери раздел:"
    inline = []
    for sid, title, ids in SECTIONS:
        section_done = sum(1 for tid in ids if tasks_state.get(tid, 0) == 1)
        inline.append([InlineKeyboardButton(text=f"{title} ({section_done}/{len(ids)})", callback_data=f"section:{sid}:0")])
    inline.append([InlineKeyboardButton(text="↩️ Назад в фокус", callback_data="back_to_focus")])
    return text, InlineKeyboardMarkup(inline_keyboard=inline)

def build_section_page(tasks_state: dict[int, int], section_id: str, page: int, page_size: int = 6) -> tuple[str, InlineKeyboardMarkup]:
    sec = next((s for s in SECTIONS if s[0] == section_id), None)
    if not sec:
        return "Раздел не найден.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="sections:open")]])

    _, title, ids = sec
    items = [(tid, get_task_title(tid)) for tid in ids]

    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    start = page * page_size
    chunk = items[start:start + page_size]

    done, total = count_progress(tasks_state)
    header = f"{title}\nПрогресс общий: {done}/{total}\nСтраница: {page+1}/{total_pages}\n"
    text_lines = [header]

    inline = []

    for tid, t in chunk:
        is_done = tasks_state.get(tid, 0) == 1
        text_lines.append(f"{task_mark(1 if is_done else 0)} {t}")

        btn = "✅ Снять" if is_done else "▫️ Отметить"
        inline.append([
            InlineKeyboardButton(text=f"{btn}", callback_data=f"sec_toggle:{section_id}:{page}:{tid}"),
            InlineKeyboardButton(text="❓", callback_data=f"help:{tid}")
        ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"section:{section_id}:{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"section:{section_id}:{page+1}"))
    if nav_row:
        inline.append(nav_row)

    inline.append([
        InlineKeyboardButton(text="📋 К разделам", callback_data="sections:open"),
        InlineKeyboardButton(text="🎯 В фокус", callback_data="back_to_focus"),
    ])

    return "\n".join(text_lines), InlineKeyboardMarkup(inline_keyboard=inline)

def build_accounts_checklist(accounts_state: dict[str, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = "🧾 Кабинеты артиста\nСостояния: ▫️ → ⏳ → ✅\n\n"
    for key, name in ACCOUNTS:
        v = accounts_state.get(key, 0)
        emoji = "▫️" if v == 0 else ("⏳" if v == 1 else "✅")
        text += f"{emoji} {name}\n"
    inline = []
    for key, name in ACCOUNTS:
        inline.append([InlineKeyboardButton(text=f"{name}", callback_data=f"accounts:cycle:{key}")])
    inline.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")])
    return text, InlineKeyboardMarkup(inline_keyboard=inline)

def build_links_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="BandLink", url=LINKS["bandlink_home"])],
        [InlineKeyboardButton(text="Spotify for Artists", url=LINKS["spotify_for_artists"])],
        [InlineKeyboardButton(text="Яндекс (артистам)", url=LINKS["yandex_artists_hub"])],
        [InlineKeyboardButton(text="Звук Studio", url=LINKS["zvuk_studio"])],
        [InlineKeyboardButton(text="КИОН (бывш. МТС) питчинг", url=LINKS["kion_pitch"])],
        [InlineKeyboardButton(text="TikTok for Artists", url=LINKS["tiktok_for_artists"])],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]
    ])

def build_timeline_kb(reminders_enabled: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔔 Напоминания: Вкл" if reminders_enabled else "🔔 Напоминания: Выкл"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data="reminders:toggle")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")],
        ]
    )

def build_deadlines(release_date: dt.date) -> list[tuple[str, str, dt.date]]:
    items: list[tuple[str, str, dt.date]] = []
    for d in DEADLINES:
        items.append((d["key"], d["title"], release_date + dt.timedelta(days=d["offset"])))
    return sorted(items, key=lambda x: x[2])


def timeline_text(release_date: dt.date | None, reminders_enabled: bool = True) -> str:
    if not release_date:
        return "📅 Таймлайн\n\nДата релиза не задана.\nУстанови: /set_date ДД.ММ.ГГГГ\nПример: /set_date 31.12.2025"

    lines = ["📅 Таймлайн", "", f"Дата релиза: {format_date_ru(release_date)}"]
    lines.append(f"Напоминания: {'включены' if reminders_enabled else 'выключены'}")
    lines.append("")
    lines.append("Ближайшие дедлайны:")

    today = dt.date.today()
    for _, title, d in build_deadlines(release_date):
        delta = (d - today).days
        delta_text = " (сегодня)" if delta == 0 else (f" (через {delta} дн)" if delta > 0 else f" ({abs(delta)} дн назад)")
        lines.append(f"▫️ {format_date_ru(d)} — {title}{delta_text}")

    return "\n".join(lines)

def build_reset_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧹 Сбросить прогресс", callback_data="reset_progress_yes")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")],
    ])

def build_donate_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 10", callback_data="donate:10"),
         InlineKeyboardButton(text="⭐ 25", callback_data="donate:25"),
         InlineKeyboardButton(text="⭐ 50", callback_data="donate:50")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]
    ])

async def safe_edit(message: Message, text: str, kb: InlineKeyboardMarkup | None) -> Message | None:
    try:
        await message.edit_text(text, reply_markup=kb)
        return message
    except Exception as edit_err:
        try:
            return await message.answer(text, reply_markup=kb)
        except Exception as answer_err:
            print(f"[safe_edit] edit failed: {edit_err}; answer failed: {answer_err}")
            return None

# -------------------- Reminders --------------------

async def was_reminder_sent(db: aiosqlite.Connection, tg_id: int, key: str, when: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM reminder_log WHERE tg_id=? AND key=? AND \"when\"=?",
        (tg_id, key, when)
    )
    row = await cur.fetchone()
    return row is not None


async def mark_reminder_sent(db: aiosqlite.Connection, tg_id: int, key: str, when: str):
    await db.execute(
        "INSERT OR IGNORE INTO reminder_log (tg_id, key, \"when\") VALUES (?, ?, ?)",
        (tg_id, key, when)
    )


def build_deadline_messages(release_date: dt.date) -> list[tuple[str, str, dt.date]]:
    messages: list[tuple[str, str, dt.date]] = []
    for key, title, d in build_deadlines(release_date):
        messages.append((key, title, d))
    return messages


async def process_reminders(bot: Bot):
    today = dt.date.today()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT tg_id, username, release_date FROM users WHERE reminders_enabled=1 AND release_date IS NOT NULL"
        )
        users = await cur.fetchall()

        for tg_id, _username, rd_s in users:
            rd = parse_date(rd_s)
            if not rd:
                continue
            deadlines = build_deadline_messages(rd)
            for key, title, ddate in deadlines:
                for when_label, send_date, prefix in (
                    ("pre2", ddate - dt.timedelta(days=2), "⏳ Через 2 дня дедлайн: " + title),
                    ("day0", ddate, "🚨 Сегодня дедлайн: " + title),
                ):
                    if today != send_date:
                        continue
                    if await was_reminder_sent(db, tg_id, key, when_label):
                        continue
                    try:
                        await bot.send_message(tg_id, prefix)
                        await mark_reminder_sent(db, tg_id, key, when_label)
                    except TelegramForbiddenError:
                        continue
                    except Exception:
                        continue
        await db.commit()


async def reminder_scheduler(bot: Bot):
    while True:
        try:
            await process_reminders(bot)
        except Exception as e:
            print(f"[reminder_scheduler] error: {e}")
        await asyncio.sleep(REMINDER_INTERVAL_SECONDS)

# -------------------- Email send (optional) --------------------

def _send_email_sync(subject: str, body: str) -> bool:
    if not SMTP_USER or not SMTP_APP_PASSWORD:
        return False
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = SMTP_TO
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=8) as server:
        server.login(SMTP_USER, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_USER, [SMTP_TO], msg.as_string())
    return True

async def try_send_email(subject: str, body: str) -> bool:
    if not SMTP_USER or not SMTP_APP_PASSWORD:
        return False
    try:
        return await asyncio.wait_for(asyncio.to_thread(_send_email_sync, subject, body), timeout=10)
    except Exception:
        return False

# -------------------- Label form --------------------

LABEL_FORM_STEPS = [
    ("name", "Шаг 1/6: Как тебя зовут (имя/ник)?"),
    ("artist_name", "Шаг 2/6: Название проекта/артиста (как будет на площадках)?"),
    ("contact", "Шаг 3/6: Контакт для связи (Telegram @... или email)?"),
    ("genre", "Шаг 4/6: Жанр + 1–2 референса (через запятую)?"),
    ("links", "Шаг 5/6: Ссылки на материал (приватная ссылка/облако/SoundCloud)."),
    ("release_date", "Шаг 6/6: Планируемая дата релиза (если есть) или «нет»."),
]

def render_label_summary(data: dict) -> str:
    return (
        "📩 Заявка на дистрибуцию\n\n"
        f"Кто: {data.get('name','')}\n"
        f"Артист/проект: {data.get('artist_name','')}\n"
        f"Контакт: {data.get('contact','')}\n"
        f"Жанр/референсы: {data.get('genre','')}\n"
        f"Ссылки: {data.get('links','')}\n"
        f"Дата релиза: {data.get('release_date','')}\n"
    )

def validate_label_input(key: str, raw: str) -> tuple[bool, str | None, str | None]:
    value = (raw or "").strip()

    def fail(msg: str) -> tuple[bool, None, str]:
        return False, None, msg

    if key in {"name", "artist_name", "genre"}:
        if len(value) < 2:
            return fail("Слишком коротко. Напиши минимум пару символов.")
        return True, value, None

    if key == "contact":
        email_ok = bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", value))
        tg_ok = value.startswith("@") or "t.me/" in value.lower()
        phone_ok = value.startswith("+") and len(value) >= 8
        if not (email_ok or tg_ok or phone_ok):
            return fail("Нужен контакт: @username, t.me/ссылка или email.")
        return True, value, None

    if key == "links":
        has_link = any(part.startswith("http") for part in value.replace("\n", " ").split())
        if not has_link:
            return fail("Добавь хотя бы одну ссылку вида https://...")
        return True, value, None

    if key == "release_date":
        lower = value.lower()
        if lower in {"нет", "не знаю", "unknown", "no"}:
            return True, "нет", None
        parsed = parse_date(value)
        if not parsed:
            return fail("Формат даты: ДД.ММ.ГГГГ или YYYY-MM-DD, либо напиши «нет»." )
        return True, format_date_ru(parsed), None

    return True, value, None

# -------------------- Commands & buttons --------------------

@dp.message(CommandStart())
async def start(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)

    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await message.answer(text, reply_markup=kb)
        return

    await message.answer("ИСКРА активна. Жми кнопки меню снизу 👇", reply_markup=menu_keyboard())

    tasks_state = await get_tasks_state(tg_id)
    focus_text, kb = build_focus(tasks_state, exp)
    await message.answer(focus_text, reply_markup=kb)

@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await message.answer(text, reply_markup=kb)
        return
    tasks_state = await get_tasks_state(tg_id)
    await message.answer("Меню снизу, держу фокус здесь:", reply_markup=menu_keyboard())
    text, kb = build_focus(tasks_state, exp)
    await message.answer(text, reply_markup=kb)

@dp.message(Command("set_date"))
async def set_date_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) != 2:
        await form_start(tg_id, "release_date")
        await message.answer(
            "Введи дату релиза в формате ДД.ММ.ГГГГ.\nПример: 31.12.2025\n\nОтмена: /cancel",
            reply_markup=menu_keyboard(),
        )
        return
    d = parse_date(parts[1])
    if not d:
        await message.answer("Не понял дату. Пример: /set_date 31.12.2025", reply_markup=menu_keyboard())
        return
    await set_release_date(tg_id, d.isoformat())
    await form_clear(tg_id)
    reminders = await get_reminders_enabled(tg_id)
    await message.answer(f"Ок. Дата релиза: {format_date_ru(d)}", reply_markup=build_timeline_kb(reminders))
    await message.answer(timeline_text(d, reminders), reply_markup=menu_keyboard())

@dp.message(Command("cancel"))
async def cancel(message: Message):
    tg_id = message.from_user.id
    await form_clear(tg_id)
    await message.answer("Ок, отменил.", reply_markup=menu_keyboard())

# Reply keyboard actions
@dp.message(F.text == "🎯 План")
async def rb_plan(message: Message):
    await plan_cmd(message)

@dp.message(F.text == "📋 Задачи по разделам")
async def rb_sections(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_sections_menu(tasks_state)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == "🧾 Кабинеты")
async def rb_accounts(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    acc = await get_accounts_state(tg_id)
    text, kb = build_accounts_checklist(acc)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == "📅 Таймлайн")
async def rb_timeline(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    rd = await get_release_date(tg_id)
    d = parse_date(rd) if rd else None
    reminders = await get_reminders_enabled(tg_id)
    await message.answer(timeline_text(d, reminders), reply_markup=build_timeline_kb(reminders))

@dp.message(F.text == "🗓️ Установить дату")
async def rb_set_date_hint(message: Message):
    await message.answer("Команда:\n/set_date ДД.ММ.ГГГГ\nПример:\n/set_date 31.12.2025", reply_markup=menu_keyboard())

@dp.message(F.text == "🔗 Ссылки")
async def rb_links(message: Message):
    await message.answer("🔗 Быстрые ссылки:", reply_markup=build_links_kb())

@dp.message(F.text == "🧠 Ожидания")
async def rb_expectations(message: Message):
    await message.answer(expectations_text(), reply_markup=menu_keyboard())

@dp.message(F.text == "🧹 Сброс")
async def rb_reset(message: Message):
    await message.answer("🧹 Сброс", reply_markup=build_reset_menu_kb())

@dp.message(F.text == "📤 Экспорт")
async def rb_export(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    await send_export_invoice(message)

@dp.message(F.text == "📩 Запросить дистрибуцию")
async def rb_label(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "label_submit")
    await message.answer(
        "📩 Заявка на дистрибуцию.\n\n"
        f"{LABEL_FORM_STEPS[0][1]}\n\n"
        "Отмена: /cancel",
        reply_markup=menu_keyboard()
    )

# -------------------- Stars: DONATE --------------------

@dp.message(F.text == "💫 Поддержать ИСКРУ")
async def rb_donate(message: Message):
    await message.answer(
        "💫 Поддержать ИСКРУ звёздами\n\n"
        "Если бот помог — можешь поддержать проект.\n"
        "Выбери сумму:",
        reply_markup=build_donate_menu_kb()
    )

@dp.callback_query(F.data == "donate:menu")
async def donate_menu_cb(callback):
    await safe_edit(
        callback.message,
        "💫 Поддержать ИСКРУ звёздами\n\nВыбери сумму:",
        build_donate_menu_kb()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("donate:"))
async def donate_send_invoice_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    amount_s = callback.data.split(":")[1]
    if amount_s not in {"10", "25", "50"}:
        await callback.answer("Не понял сумму", show_alert=True)
        return

    stars = int(amount_s)

    prices = [LabeledPrice(label=f"Поддержка ИСКРЫ ({stars} ⭐)", amount=stars)]
    # Для цифровых товаров/услуг в Telegram Stars используется валюта XTR.
    # provider_token для Stars можно передавать пустой строкой. :contentReference[oaicite:1]{index=1}
    await callback.message.answer_invoice(
        title="Поддержать ИСКРУ",
        description="Спасибо! Это помогает развивать бота и добавлять функции.",
        payload=f"donate_iskra_{stars}",
        provider_token="",
        currency="XTR",
        prices=prices
    )
    await callback.answer("Ок")

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery, bot: Bot):
    # обязательный шаг: без этого Telegram будет “крутить” оплату и ругаться, что бот не ответил
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    sp = message.successful_payment
    # sp.currency для Stars будет "XTR" :contentReference[oaicite:2]{index=2}
    if (sp.invoice_payload or "").startswith("donate_iskra_"):
        await message.answer("💫 Принято! Спасибо за поддержку ИСКРЫ 🤝", reply_markup=menu_keyboard())
    elif sp.invoice_payload == "export_plan_25":
        tg_id = message.from_user.id
        await ensure_user(tg_id)
        tasks_state = await get_tasks_state(tg_id)
        await message.answer(build_export_text(tasks_state), reply_markup=menu_keyboard())

# -------------------- Inline callbacks --------------------

@dp.callback_query(F.data == "export:inline")
async def export_inline_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await send_export_invoice(callback.message)
    await callback.answer("Счёт на экспорт плана")

@dp.callback_query(F.data.startswith("exp:"))
async def set_exp_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = callback.data.split(":")[1]
    await set_experience(tg_id, "first" if exp == "first" else "old")
    await callback.message.answer("Ок. Меню снизу, держу фокус здесь:", reply_markup=menu_keyboard())
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state, "first" if exp == "first" else "old")

    await safe_edit(callback.message, text, kb)
    await callback.answer("Готово")

@dp.callback_query(F.data.startswith("focus_done:"))
async def focus_done_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    task_id = int(callback.data.split(":")[1])
    await set_task_done(tg_id, task_id, 1)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state, exp)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Ок")

@dp.callback_query(F.data.startswith("help:"))
async def help_cb(callback):
    task_id = int(callback.data.split(":")[1])
    title = get_task_title(task_id)
    body = HELP.get(task_id, "Пояснение пока не добавлено.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]])
    await safe_edit(callback.message, f"❓ {title}\n\n{body}", kb)
    await callback.answer()

@dp.callback_query(F.data == "sections:open")
async def sections_open_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_sections_menu(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("section:"))
async def section_page_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    _, sid, page_s = callback.data.split(":")
    page = int(page_s)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_section_page(tasks_state, sid, page)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("sec_toggle:"))
async def section_toggle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    _, sid, page_s, tid_s = callback.data.split(":")
    page = int(page_s)
    task_id = int(tid_s)

    await toggle_task(tg_id, task_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_section_page(tasks_state, sid, page)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Ок")

@dp.callback_query(F.data == "accounts:open")
async def accounts_open_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    state = await get_accounts_state(tg_id)
    text, kb = build_accounts_checklist(state)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("accounts:cycle:"))
async def accounts_cycle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    key = callback.data.split(":")[2]
    if key not in [k for k, _ in ACCOUNTS]:
        await callback.answer("Неизвестный пункт", show_alert=True)
        return
    await cycle_account_status(tg_id, key)
    state = await get_accounts_state(tg_id)
    text, kb = build_accounts_checklist(state)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Ок")

@dp.callback_query(F.data == "timeline")
async def timeline_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    rd = await get_release_date(tg_id)
    d = parse_date(rd) if rd else None
    reminders = await get_reminders_enabled(tg_id)
    kb = build_timeline_kb(reminders)
    await safe_edit(callback.message, timeline_text(d, reminders), kb)
    await callback.answer()


@dp.callback_query(F.data == "reminders:toggle")
async def reminders_toggle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    current = await get_reminders_enabled(tg_id)
    await set_reminders_enabled(tg_id, not current)
    rd = await get_release_date(tg_id)
    d = parse_date(rd) if rd else None
    kb = build_timeline_kb(not current)
    await safe_edit(callback.message, timeline_text(d, not current), kb)
    await callback.answer("Напоминания обновлены")

@dp.callback_query(F.data == "links")
async def links_cb(callback):
    await safe_edit(callback.message, "🔗 Быстрые ссылки:", build_links_kb())
    await callback.answer()

@dp.callback_query(F.data == "reset_menu")
async def reset_menu_cb(callback):
    await safe_edit(callback.message, "🧹 Сброс", build_reset_menu_kb())
    await callback.answer()

@dp.callback_query(F.data == "reset_progress_yes")
async def reset_progress_yes_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    await reset_progress_only(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state, exp)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Сбросил")

@dp.callback_query(F.data == "back_to_focus")
async def back_to_focus_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state, exp)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data == "label:start")
async def label_start_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "label_submit")
    await callback.message.answer(
        "📩 Заявка на дистрибуцию.\n\n"
        f"{LABEL_FORM_STEPS[0][1]}\n\n"
        "Отмена: /cancel",
        reply_markup=menu_keyboard()
    )
    await callback.answer()

# -------------------- Form router --------------------

@dp.message()
async def any_message_router(message: Message):
    txt = (message.text or "").strip()
    if not txt or txt.startswith("/"):
        return

    tg_id = message.from_user.id
    form = await form_get(tg_id)
    if not form:
        return

    await ensure_user(tg_id)

    form_name = form.get("form_name")
    if form_name == "release_date":
        d = parse_date(txt)
        if not d:
            await message.answer(
                "Не понял дату. Формат: ДД.ММ.ГГГГ. Пример: 31.12.2025\n\nПопробуй ещё раз:",
                reply_markup=menu_keyboard(),
            )
            return
        await set_release_date(tg_id, d.isoformat())
        await form_clear(tg_id)
        reminders = await get_reminders_enabled(tg_id)
        await message.answer(
            f"Ок. Дата релиза: {format_date_ru(d)}",
            reply_markup=build_timeline_kb(reminders),
        )
        await message.answer(timeline_text(d, reminders), reply_markup=menu_keyboard())
        return

    if form_name != "label_submit":
        return

    step = int(form["step"])
    data = form["data"]

    if step < 0 or step >= len(LABEL_FORM_STEPS):
        await form_clear(tg_id)
        await message.answer("Форма сбросилась. Нажми «📩 Запросить дистрибуцию» ещё раз.", reply_markup=menu_keyboard())
        return

    key, _ = LABEL_FORM_STEPS[step]
    ok, normalized, err = validate_label_input(key, txt)
    if not ok:
        await message.answer(
            f"{err}\n\n{LABEL_FORM_STEPS[step][1]}\n\n(Отмена: /cancel)",
            reply_markup=menu_keyboard()
        )
        return

    data[key] = normalized

    step += 1
    if step < len(LABEL_FORM_STEPS):
        await form_set(tg_id, step, data)
        await message.answer(LABEL_FORM_STEPS[step][1] + "\n\n(Отмена: /cancel)", reply_markup=menu_keyboard())
        return

    summary = render_label_summary(data)
    subject = f"[SREDA / LABEL] Demo submission: {data.get('artist_name','')}".strip()

    sent_tg = False
    if ADMIN_TG_ID and ADMIN_TG_ID.isdigit():
        try:
            await message.bot.send_message(
                int(ADMIN_TG_ID),
                summary + f"\nОт: @{message.from_user.username or 'без_username'} (tg_id: {tg_id})"
            )
            sent_tg = True
        except Exception:
            sent_tg = False

    sent_email = await try_send_email(subject, summary)

    mailto = f"mailto:{LABEL_EMAIL}?subject={subject.replace(' ', '%20')}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Открыть почту", url=mailto)],
        [InlineKeyboardButton(text="🎯 Вернуться в фокус", callback_data="back_to_focus")],
    ])

    result_lines = ["✅ Заявка собрана."]
    result_lines.append("✓ Отправил в Telegram лейблу." if sent_tg else "⚠️ Не смог отправить в Telegram (проверь ADMIN_TG_ID).")
    result_lines.append("✓ И на почту отправил автоматически." if sent_email else "⧗ Авто-почта не настроена/не доступна — ниже шаблон письма.")
    await message.answer("\n".join(result_lines), reply_markup=menu_keyboard())

    if not sent_email:
        await message.answer(f"Почта: {LABEL_EMAIL}\n\nТекст письма (скопируй):\n\n{summary}", reply_markup=kb)

    await form_clear(tg_id)

# -------------------- Runner --------------------

async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан.")
    await init_db()
    bot = Bot(token=TOKEN)
    asyncio.create_task(reminder_scheduler(bot))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
