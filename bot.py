import asyncio
import os
import json
import datetime as dt
from zoneinfo import ZoneInfo
import re
import html
from urllib.parse import parse_qsl, urlparse, urlunparse, urlencode

import aiohttp
import aiosqlite
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    LabeledPrice, PreCheckoutQuery,
    BufferedInputFile,
)
from dotenv import load_dotenv

DB_PATH = "bot.db"
LABEL_EMAIL = "sreda.records@gmail.com"
REMINDER_INTERVAL_SECONDS = 300
REMINDER_CLEAN_DAYS = 60
REMINDER_LAST_CLEAN: dt.date | None = None
DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_REMINDER_OFFSETS = "-7,-1,0,7"
DEFAULT_REMINDER_TIME = "12:00"

SMARTLINK_PLATFORMS = [
    ("yandex", "Яндекс Музыка"),
    ("vk", "VK Музыка"),
    ("apple", "Apple Music"),
    ("spotify", "Spotify"),
    ("itunes", "iTunes"),
    ("zvuk", "Звук"),
    ("youtubemusic", "YouTube Music"),
    ("youtube", "YouTube"),
    ("deezer", "Deezer"),
]

EXTRA_SMARTLINK_PLATFORMS = [
    ("kion", "MTS Music / КИОН"),
]

SMARTLINK_BUTTON_ORDER = [*SMARTLINK_PLATFORMS, *EXTRA_SMARTLINK_PLATFORMS]
KEY_PLATFORM_SET = {"yandex", "vk", "apple", "spotify"}

PLATFORM_LABELS = {
    **{k: v for k, v in SMARTLINK_PLATFORMS},
    **{k: v for k, v in EXTRA_SMARTLINK_PLATFORMS},
    "youtube": "YouTube",
    "youtubemusic": "YouTube Music",
    "bandlink": "BandLink",
}

EXPORT_LABELS: dict[str, tuple[str, str, str, str]] = {
    "yandex": ("Яндекс Музыка", "Яндекс Музыка", "Yandex Music", "Yandex"),
    "vk": ("VK Музыка", "VK Музыка", "VK Music", "VK"),
    "apple": ("Apple Music", "Apple Music", "Apple Music", "Apple"),
    "spotify": ("Spotify", "Spotify", "Spotify", "Spotify"),
    "itunes": ("iTunes", "iTunes", "iTunes", "iTunes"),
    "zvuk": ("Звук", "Звук", "Zvuk", "Zvuk"),
    "youtubemusic": ("YouTube Music", "YouTube Music", "YouTube Music", "YouTube Music"),
    "youtube": ("YouTube", "YouTube", "YouTube", "YouTube"),
    "deezer": ("Deezer", "Deezer", "Deezer", "Deezer"),
    "kion": ("MTS Music / КИОН", "MTS Music / КИОН", "MTS Music", "MTS Music"),
    "bandlink": ("BandLink", "BandLink", "BandLink", "BandLink"),
}

HUMAN_METADATA_PLATFORMS = {"apple", "spotify", "yandex", "vk"}


def smartlink_step_prompt(step: int) -> str:
    total = 5 + len(SMARTLINK_PLATFORMS)
    if step == 0:
        return f"🔗 Смартлинк. Шаг 1/{total}: артист? (можно «Пропустить»)."
    if step == 1:
        return f"Шаг 2/{total}: название трека? (можно «Пропустить»)."
    if step == 2:
        return f"Шаг 3/{total}: дата релиза (ДД.ММ.ГГГГ)? (можно «Пропустить»)."
    if step == 3:
        return f"Шаг 4/{total}: пришли обложку (фото). Можно «Пропустить»."
    if step == 4:
        return "✍️ Добавь короткий текст (необязательно). Отправь сообщением или нажми «Пропустить»."
    idx = step - 5
    if 0 <= idx < len(SMARTLINK_PLATFORMS):
        label = SMARTLINK_PLATFORMS[idx][1]
        return f"Шаг {step + 1}/{total}: ссылка на {label}? (можно «Пропустить»)."
    return ""

BANDLINK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SONGLINK_API_URL = "https://api.song.link/v1-alpha.1/links"
SONGLINK_PLATFORM_ALIASES = {
    "spotify": "spotify",
    "applemusic": "apple",
    "applemusicapp": "apple",
    "apple": "apple",
    "itunes": "itunes",
    "youtubemusic": "youtubemusic",
    "youtube": "youtube",
    "deezer": "deezer",
    "yandex": "yandex",
    "yandexmusic": "yandex",
    "vk": "vk",
    "zvuk": "zvuk",
    "kion": "kion",
    "mts": "kion",
}

RESOLVER_FALLBACK_TEXT = (
    "BandLink открывается через JS, поэтому бот иногда не видит кнопки.\n"
    "Сделай так:\n"
    "1) Открой BandLink в браузере\n"
    "2) Нажми кнопку 'Слушать' нужной платформы (Spotify / Apple / Яндекс)\n"
    "3) Нажми 'Копировать адрес ссылки'\n"
    "4) Пришли эту ссылку сюда — я подтяну остальные."
)

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

UPDATES_CHANNEL_URL = "https://t.me/sreda_music"
UPDATES_POST_URL = os.getenv("UPDATES_POST_URL", "")

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
        reply_markup=menu_keyboard(await get_updates_opt_in(message.from_user.id) if message.from_user else True)
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

QC_PROMPTS = {
    6: {
        "key": "master_wav24",
        "question": "WAV 24bit?",
        "tip": "Проверь формат мастера: WAV 24bit, без клиппинга и лимитера на мастер-шине.",
    },
    8: {
        "key": "cover_size",
        "question": "3000x3000 + без запрещённых логотипов?",
        "tip": "Держи обложку 3000x3000, без чужих/запрещённых логотипов и мелкого текста.",
    },
    12: {
        "key": "metadata_check",
        "question": "язык/explicit/жанр заполнены?",
        "tip": "Проверь язык, explicit, жанр, авторов и написание фитов — это частые причины отклонений.",
    },
}


async def maybe_send_qc_prompt(callback, tg_id: int, task_id: int):
    qc = QC_PROMPTS.get(task_id)
    if not qc:
        return
    if await was_qc_checked(tg_id, task_id, qc["key"]):
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=f"qc:{task_id}:yes"),
                InlineKeyboardButton(text="Нет", callback_data=f"qc:{task_id}:no"),
            ]
        ]
    )
    await callback.message.answer(f"Мини-проверка: {qc['question']}", reply_markup=kb)

def expectations_text() -> str:
    return (
        "🧠 Ожидания / реальность\n\n"
        "1) Первый релиз почти никогда не «взлетает». Это нормально.\n"
        "2) Цель — система: процесс, контент, кабинеты.\n"
        "3) Алгоритмы любят регулярность.\n"
        "4) Мерь себя качеством процесса, не цифрами первого релиза.\n"
    )


def lyrics_sync_text() -> str:
    return (
        "Лирика/синхронизация: Musixmatch / Genius\n\n"
        "Что подготовить: чистый текст песни, при наличии таймкоды и авторы.\n"
        "Где и когда: Musixmatch/Genius после появления релиза или ближе к релизу, чтобы карточка выглядела полно.\n"
        "Частые отказы: не тот текст, капслок/эмодзи, неуказанные авторы, дубликаты карточек."
    )


def ugc_tip_text() -> str:
    return (
        "UGC/Content ID — как не словить страйки\n\n"
        "• Если включён Content ID, свои же ролики могут уйти в блок или монетизацию.\n"
        "• При активных тиктоках/вертикалках иногда лучше временно отключать.\n"
        "• Уточни у дистрибьютора: где включено, можно ли гибко отключать/белить свои каналы."
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

def menu_keyboard(updates_enabled: bool | None = None) -> ReplyKeyboardMarkup:
    updates_text = "🔔 Обновления: Вкл" if updates_enabled is not False else "🔔 Обновления: Выкл"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎯 План"), KeyboardButton(text="📦 Задачи по разделам")],
            [KeyboardButton(text="📅 Таймлайн"), KeyboardButton(text="⏰ Дата релиза")],
            [KeyboardButton(text="🔗 Ссылки"), KeyboardButton(text="👤 Кабинеты")],
            [KeyboardButton(text="🧾 Экспорт"), KeyboardButton(text="📩 Запросить дистрибуцию")],
            [KeyboardButton(text="📰 Что нового"), KeyboardButton(text=updates_text)],
            [KeyboardButton(text="🔗 Смарт-линки")],
            [KeyboardButton(text="💫 Поддержать ИСКРУ")],
            [KeyboardButton(text="🔄 Сброс")],
        ],
        resize_keyboard=True
    )

async def user_menu_keyboard(tg_id: int) -> ReplyKeyboardMarkup:
    updates_enabled = await get_updates_opt_in(tg_id)
    return menu_keyboard(updates_enabled)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID = os.getenv("ADMIN_TG_ID")

SMTP_USER = os.getenv("SMTP_USER")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD")
SMTP_TO = os.getenv("SMTP_TO") or LABEL_EMAIL
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_UPC_ENABLED = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)

_SPOTIFY_ACCESS_TOKEN: str | None = None
_SPOTIFY_TOKEN_EXPIRES_AT: dt.datetime | None = None

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
            reminders_enabled INTEGER DEFAULT 1,
            reminder_offsets TEXT DEFAULT '-7,-1,0,7',
            reminder_time TEXT DEFAULT '12:00',
            timezone TEXT DEFAULT 'Europe/Moscow',
            export_unlocked INTEGER DEFAULT 0
        )
        """)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN reminder_offsets TEXT DEFAULT '-7,-1,0,7'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN reminder_time TEXT DEFAULT '12:00'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'Europe/Moscow'")
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
        try:
            await db.execute("ALTER TABLE users ADD COLUMN updates_opt_in INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_update_notified TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN export_unlocked INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reminder_log (
            tg_id INTEGER,
            key TEXT,
            "when" TEXT,
            sent_on TEXT,
            PRIMARY KEY (tg_id, key, "when")
        )
        """)
        try:
            await db.execute("ALTER TABLE reminder_log ADD COLUMN sent_on TEXT")
        except Exception:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reminder_log_sent_on ON reminder_log(sent_on)")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_tasks (
            tg_id INTEGER,
            task_id INTEGER,
            done INTEGER DEFAULT 0,
            PRIMARY KEY (tg_id, task_id)
        )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_tasks_tg ON user_tasks(tg_id)"
        )
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
        await db.execute("""
        CREATE TABLE IF NOT EXISTS smartlinks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_tg_id INTEGER,
            artist TEXT,
            title TEXT,
            release_date TEXT,
            pre_save_enabled INTEGER DEFAULT 1,
            reminders_enabled INTEGER DEFAULT 1,
            project_id INTEGER,
            cover_file_id TEXT,
            links_json TEXT,
            caption_text TEXT,
            branding_disabled INTEGER DEFAULT 0,
            branding_paid INTEGER DEFAULT 0,
            created_at TEXT
        )
        """)
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN caption_text TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN pre_save_enabled INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN reminders_enabled INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN project_id INTEGER")
        except Exception:
            pass
        try:
            await db.execute(
                "ALTER TABLE smartlinks ADD COLUMN branding_disabled INTEGER DEFAULT 0"
            )
        except Exception:
            pass
        try:
            await db.execute(
                "ALTER TABLE smartlinks ADD COLUMN branding_paid INTEGER DEFAULT 0"
            )
        except Exception:
            pass
        await db.execute("""
        CREATE TABLE IF NOT EXISTS smartlink_subscriptions (
            smartlink_id INTEGER,
            subscriber_tg_id INTEGER,
            notified INTEGER DEFAULT 0,
            PRIMARY KEY(smartlink_id, subscriber_tg_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS smartlink_reminder_log (
            smartlink_id INTEGER,
            subscriber_tg_id INTEGER,
            offset_days INTEGER,
            sent_on TEXT,
            PRIMARY KEY (smartlink_id, subscriber_tg_id, offset_days)
        )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_smartlink_reminder_sent ON smartlink_reminder_log(sent_on)")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_tg_id INTEGER,
            name TEXT NOT NULL,
            slug TEXT,
            created_at TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS important_tasks (
            tg_id INTEGER,
            task_id INTEGER,
            PRIMARY KEY (tg_id, task_id)
        )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_important_tasks_tg ON important_tasks(tg_id)"
        )
        await db.execute("""
        CREATE TABLE IF NOT EXISTS qc_checks (
            tg_id INTEGER,
            task_id INTEGER,
            key TEXT,
            value TEXT,
            PRIMARY KEY (tg_id, task_id, key)
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
        cur = await db.execute("SELECT release_date FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        current = row[0] if row else None
        if current == date_str:
            return
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
        cur = await db.execute("SELECT reminders_enabled FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        current = row[0] if row else 1
        if current == (1 if enabled else 0):
            return
        await db.execute("UPDATE users SET reminders_enabled=? WHERE tg_id=?", (1 if enabled else 0, tg_id))
        await db.commit()

async def get_reminders_enabled(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT reminders_enabled FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else True


def _parse_offsets(raw: str | None) -> list[int]:
    values: list[int] = []
    for part in (raw or DEFAULT_REMINDER_OFFSETS).split(","):
        try:
            values.append(int(part.strip()))
        except ValueError:
            continue
    return values or [-7, -1, 0, 7]


def _parse_reminder_time(raw: str | None) -> dt.time | None:
    if not raw:
        return None
    try:
        h, m = raw.split(":")
        return dt.time(int(h), int(m))
    except Exception:
        return None


async def get_user_reminder_prefs(db: aiosqlite.Connection, tg_id: int) -> tuple[str, list[int], dt.time | None]:
    cur = await db.execute(
        "SELECT timezone, reminder_offsets, reminder_time FROM users WHERE tg_id=?",
        (tg_id,),
    )
    row = await cur.fetchone()
    timezone = row[0] if row and row[0] else DEFAULT_TIMEZONE
    offsets_raw = row[1] if row else DEFAULT_REMINDER_OFFSETS
    reminder_time_raw = row[2] if row else DEFAULT_REMINDER_TIME
    return timezone, _parse_offsets(offsets_raw), _parse_reminder_time(reminder_time_raw) or _parse_reminder_time(DEFAULT_REMINDER_TIME)

async def get_updates_opt_in(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT updates_opt_in FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else True

async def set_updates_opt_in(tg_id: int, enabled: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET updates_opt_in=? WHERE tg_id=?", (1 if enabled else 0, tg_id))
        await db.commit()

async def set_export_unlocked(tg_id: int, unlocked: bool = True):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET export_unlocked=? WHERE tg_id=?",
            (1 if unlocked else 0, tg_id),
        )
        await db.commit()


async def get_export_unlocked(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT export_unlocked FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else False

async def toggle_updates_opt_in(tg_id: int) -> bool:
    enabled = await get_updates_opt_in(tg_id)
    await set_updates_opt_in(tg_id, not enabled)
    return not enabled

async def get_last_update_notified(tg_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT last_update_notified FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else None

async def set_last_update_notified(tg_id: int, value: str | None, db: aiosqlite.Connection | None = None):
    if db:
        await db.execute("UPDATE users SET last_update_notified=? WHERE tg_id=?", (value, tg_id))
        return
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute("UPDATE users SET last_update_notified=? WHERE tg_id=?", (value, tg_id))
        await db_conn.commit()

async def maybe_send_update_notice(message: Message, tg_id: int):
    if not UPDATES_POST_URL:
        return
    if not await get_updates_opt_in(tg_id):
        return
    last_notified = await get_last_update_notified(tg_id)
    if last_notified == UPDATES_POST_URL:
        return
    await message.answer(f"⚡️ Есть обновление ИСКРЫ. Подробнее: {UPDATES_POST_URL}")
    await set_last_update_notified(tg_id, UPDATES_POST_URL)

async def get_tasks_state(tg_id: int) -> dict[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT task_id, done FROM user_tasks WHERE tg_id=?", (tg_id,))
        rows = await cur.fetchall()
        return {tid: done for tid, done in rows}

async def toggle_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done = 1 - done WHERE tg_id=? AND task_id=?", (tg_id, task_id))
        await db.commit()

async def set_task_done(tg_id: int, task_id: int, done: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT done FROM user_tasks WHERE tg_id=? AND task_id=?", (tg_id, task_id))
        row = await cur.fetchone()
        current = row[0] if row else 0
        if current == done:
            return False
        await db.execute("UPDATE user_tasks SET done=? WHERE tg_id=? AND task_id=?", (done, tg_id, task_id))
        await db.commit()
        return True

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

async def add_important_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO important_tasks (tg_id, task_id) VALUES (?, ?)",
            (tg_id, task_id)
        )
        await db.commit()

async def remove_important_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM important_tasks WHERE tg_id=? AND task_id=?",
            (tg_id, task_id)
        )
        await db.commit()

async def get_important_tasks(tg_id: int) -> set[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT task_id FROM important_tasks WHERE tg_id=?",
            (tg_id,)
        )
        rows = await cur.fetchall()
        return {r[0] for r in rows}

async def save_qc_check(tg_id: int, task_id: int, key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO qc_checks (tg_id, task_id, key, value) VALUES (?, ?, ?, ?)",
            (tg_id, task_id, key, value)
        )
        await db.commit()

async def was_qc_checked(tg_id: int, task_id: int, key: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM qc_checks WHERE tg_id=? AND task_id=? AND key=?",
            (tg_id, task_id, key)
        )
        row = await cur.fetchone()
        return row is not None

async def reset_progress_only(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done=0 WHERE tg_id=?", (tg_id,))
        await db.execute("UPDATE user_accounts SET status=0 WHERE tg_id=?", (tg_id,))
        await db.commit()

async def reset_all_data(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done=0 WHERE tg_id=?", (tg_id,))
        await db.execute("UPDATE user_accounts SET status=0 WHERE tg_id=?", (tg_id,))
        await db.execute("DELETE FROM important_tasks WHERE tg_id=?", (tg_id,))
        await db.execute("DELETE FROM qc_checks WHERE tg_id=?", (tg_id,))
        await db.execute("DELETE FROM reminder_log WHERE tg_id=?", (tg_id,))
        await db.execute("DELETE FROM smartlink_subscriptions WHERE subscriber_tg_id=?", (tg_id,))
        await db.execute("DELETE FROM smartlinks WHERE owner_tg_id=?", (tg_id,))
        await db.execute(
            "UPDATE users SET release_date=NULL, reminders_enabled=1 WHERE tg_id=?",
            (tg_id,)
        )
        await db.execute("DELETE FROM user_forms WHERE tg_id=?", (tg_id,))
        await db.commit()

def _smartlink_row_to_dict(row) -> dict:
    if not row:
        return {}
    return {
        "id": row[0],
        "owner_tg_id": row[1],
        "artist": row[2] or "",
        "title": row[3] or "",
        "release_date": row[4],
        "pre_save_enabled": bool(row[5]) if len(row) > 5 else True,
        "reminders_enabled": bool(row[6]) if len(row) > 6 else True,
        "project_id": row[7] if len(row) > 7 else None,
        "cover_file_id": row[8] if len(row) > 8 else "",
        "links": json.loads(row[9] or "{}"),
        "caption_text": row[10] or "",
        "branding_disabled": bool(row[11]) if len(row) > 11 else False,
        "created_at": row[12] if len(row) > 12 else None,
        "branding_paid": bool(row[13]) if len(row) > 13 else False,
    }


async def save_smartlink(
    owner_tg_id: int,
    artist: str,
    title: str,
    release_date_iso: str,
    cover_file_id: str,
    links: dict,
    caption_text: str,
    branding_disabled: bool = False,
    pre_save_enabled: bool = True,
    reminders_enabled: bool = True,
    project_id: int | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO smartlinks (owner_tg_id, artist, title, release_date, pre_save_enabled, reminders_enabled, project_id, cover_file_id, links_json, caption_text, branding_disabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner_tg_id,
                artist,
                title,
                release_date_iso,
                1 if pre_save_enabled else 0,
                1 if reminders_enabled else 0,
                project_id,
                cover_file_id,
                json.dumps(links, ensure_ascii=False),
                caption_text,
                1 if branding_disabled else 0,
                dt.datetime.utcnow().isoformat(),
            ),
        )
        await db.commit()
        return cur.lastrowid


async def update_smartlink_caption(smartlink_id: int, caption_text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE smartlinks SET caption_text=? WHERE id=?",
            (caption_text, smartlink_id),
        )
        await db.commit()


async def get_latest_smartlink(owner_tg_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, owner_tg_id, artist, title, release_date, pre_save_enabled, reminders_enabled, project_id, cover_file_id, links_json, caption_text, branding_disabled, created_at, branding_paid FROM smartlinks WHERE owner_tg_id=? ORDER BY id DESC LIMIT 1",
            (owner_tg_id,),
        )
        row = await cur.fetchone()
        return _smartlink_row_to_dict(row) if row else None


async def get_smartlink_by_id(smartlink_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, owner_tg_id, artist, title, release_date, pre_save_enabled, reminders_enabled, project_id, cover_file_id, links_json, caption_text, branding_disabled, created_at, branding_paid FROM smartlinks WHERE id=?",
            (smartlink_id,),
        )
        row = await cur.fetchone()
        return _smartlink_row_to_dict(row) if row else None


async def list_smartlinks(owner_tg_id: int, limit: int = 5, offset: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, owner_tg_id, artist, title, release_date, pre_save_enabled, reminders_enabled, project_id, cover_file_id, links_json, caption_text, branding_disabled, created_at, branding_paid FROM smartlinks WHERE owner_tg_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (owner_tg_id, limit, offset),
        )
        return [_smartlink_row_to_dict(row) for row in await cur.fetchall()]


async def count_smartlinks(owner_tg_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM smartlinks WHERE owner_tg_id=?", (owner_tg_id,))
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def update_smartlink_data(smartlink_id: int, owner_tg_id: int, updates: dict) -> bool:
    allowed = {
        "artist",
        "title",
        "release_date",
        "cover_file_id",
        "links",
        "caption_text",
        "branding_disabled",
        "branding_paid",
        "pre_save_enabled",
        "reminders_enabled",
        "project_id",
    }
    fields: list[str] = []
    params: list = []

    for key, value in updates.items():
        if key not in allowed:
            continue
        if key == "links":
            fields.append("links_json=?")
            params.append(json.dumps(value or {}, ensure_ascii=False))
        elif key == "branding_disabled":
            fields.append("branding_disabled=?")
            params.append(1 if value else 0)
        elif key == "branding_paid":
            fields.append("branding_paid=?")
            params.append(1 if value else 0)
        else:
            fields.append(f"{key}=?")
            params.append(value)

    if not fields:
        return False

    params.extend([smartlink_id, owner_tg_id])

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE smartlinks SET {', '.join(fields)} WHERE id=? AND owner_tg_id=?",
            params,
        )
        await db.commit()
    return True


async def delete_smartlink(smartlink_id: int, owner_tg_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM smartlinks WHERE id=? AND owner_tg_id=?", (smartlink_id, owner_tg_id))
        await db.execute("DELETE FROM smartlink_subscriptions WHERE smartlink_id=?", (smartlink_id,))
        await db.commit()


async def set_smartlink_subscription(smartlink_id: int, subscriber_tg_id: int, subscribed: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        if subscribed:
            await db.execute(
                "INSERT OR REPLACE INTO smartlink_subscriptions (smartlink_id, subscriber_tg_id, notified) VALUES (?, ?, 0)",
                (smartlink_id, subscriber_tg_id),
            )
        else:
            await db.execute(
                "DELETE FROM smartlink_subscriptions WHERE smartlink_id=? AND subscriber_tg_id=?",
                (smartlink_id, subscriber_tg_id),
            )
        await db.commit()


async def is_smartlink_subscribed(smartlink_id: int, subscriber_tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM smartlink_subscriptions WHERE smartlink_id=? AND subscriber_tg_id=?",
            (smartlink_id, subscriber_tg_id),
        )
        row = await cur.fetchone()
        return row is not None


async def start_smartlink_form(
    message: Message,
    tg_id: int,
    initial_links: dict[str, str] | None = None,
    prefill: dict | None = None,
):
    data = {"links": initial_links or {}, "caption_text": "", "branding_disabled": False}
    if prefill:
        data.update(prefill)
    step = skip_prefilled_smartlink_steps(0, data)
    await form_start(tg_id, "smartlink")
    await form_set(tg_id, step, data)

    total_steps = 5 + len(SMARTLINK_PLATFORMS)
    if step >= total_steps:
        await finalize_smartlink_form(message, tg_id, data)
        return

    await message.answer(smartlink_step_prompt(step) + "\n\n(Отмена: /cancel)", reply_markup=smartlink_step_kb())


async def start_smartlink_import(message: Message, tg_id: int):
    await form_start(tg_id, "smartlink_import")
    await form_set(
        tg_id,
        0,
        {"links": {}, "metadata": {}, "bandlink_help_shown": False, "low_links_hint_shown": False},
    )
    await message.answer(
        "Пришли ссылку на релиз: BandLink / Spotify / Apple Music / Яндекс / VK / YouTube.\n"
        "Я попробую подтянуть площадки и данные автоматически.\n"
        "Если BandLink не отдаст ссылки — подскажу, как скопировать одну кнопку платформы.\n\n"
        "Отмена: /cancel",
        reply_markup=await user_menu_keyboard(tg_id),
    )


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

async def build_focus_for_user(tg_id: int, exp: str, focus_task_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    tasks_state = await get_tasks_state(tg_id)
    important = await get_important_tasks(tg_id)
    return build_focus(tasks_state, exp, important, focus_task_id)

def build_focus(
    tasks_state: dict[int, int],
    experience: str | None = None,
    important: set[int] | None = None,
    focus_task_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    done, total = count_progress(tasks_state)
    next_task = None
    if focus_task_id:
        next_task = (focus_task_id, get_task_title(focus_task_id))
    else:
        next_task = get_next_task(tasks_state)

    lines = []
    lines.append("🎯 Фокус-режим")
    if experience == "first":
        lines.append("Тип релиза: первый")
    elif experience == "old":
        lines.append("Тип релиза: не первый")
    lines.append(f"Прогресс общий: {done}/{total}\n")

    rows: list[list[InlineKeyboardButton]] = []

    if not next_task:
        lines.append("✨ Всё выполнено. Поздравляю с закрытием релиза.")
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    task_id, title = next_task
    sec = find_section_for_task(task_id)
    if sec:
        sid, stitle = sec
        idx = next((i for i, s in enumerate(SECTIONS) if s[0] == sid), 0) + 1
        sec_total = len(SECTIONS)
        section_ids = next((s[2] for s in SECTIONS if s[0] == sid), [])
        section_done = sum(1 for tid in section_ids if tasks_state.get(tid, 0) == 1)
        lines.append(f"Раздел: {idx}/{sec_total} — {stitle}")
        lines.append(f"Прогресс по разделу: {section_done}/{len(section_ids)}")
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

    is_done = tasks_state.get(task_id, 0) == 1
    mark_text = f"↩️ Отменить: {title}" if is_done else f"✅ Сделано: {title}"
    rows.append([
        InlineKeyboardButton(
            text=mark_text,
            callback_data=f"focus_done:{task_id}"
        )
    ])
    imp_set = important or set()
    imp_text = "🔥 Убрать из важных" if task_id in imp_set else "⭐ Важное"
    rows.append([InlineKeyboardButton(text=imp_text, callback_data=f"important:toggle:{task_id}")])
    rows.append([InlineKeyboardButton(text="❓ Пояснение", callback_data=f"help:{task_id}")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

def build_sections_menu(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    done, total = count_progress(tasks_state)
    text = f"📦 Задачи по разделам\nПрогресс: {done}/{total}\n\nВыбери раздел:"
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


def build_important_screen(tasks_state: dict[int, int], important_ids: set[int]) -> tuple[str, InlineKeyboardMarkup]:
    if not important_ids:
        text = "🔥 Важное\n\nПока ничего не закреплено. Отметь задачу кнопкой ⭐ Важное во фокусе."
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 В фокус", callback_data="back_to_focus")]])
        return text, kb

    text_lines = ["🔥 Важное"]
    inline: list[list[InlineKeyboardButton]] = []
    for tid in sorted(important_ids):
        title = get_task_title(tid)
        status = "✅" if tasks_state.get(tid, 0) == 1 else "▫️"
        text_lines.append(f"{status} {title}")
        inline.append(
            [
                InlineKeyboardButton(text="➡️ В фокус", callback_data=f"important:focus:{tid}"),
                InlineKeyboardButton(text="🔥 Снять", callback_data=f"important:toggle:{tid}"),
            ]
        )
    inline.append([InlineKeyboardButton(text="🎯 В фокус", callback_data="back_to_focus")])
    return "\n".join(text_lines), InlineKeyboardMarkup(inline_keyboard=inline)

def build_accounts_checklist(accounts_state: dict[str, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = "👤 Кабинеты артиста\nСостояния: ▫️ → ⏳ → ✅\n\n"
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
        [InlineKeyboardButton(text="🔥 Важное", callback_data="important:list")],
        [InlineKeyboardButton(text="🔗 Смартлинк", callback_data="smartlink:open")],
        [InlineKeyboardButton(text="✍️ Тексты", callback_data="texts:start")],
        [InlineKeyboardButton(text="BandLink", url=LINKS["bandlink_home"])],
        [InlineKeyboardButton(text="Spotify for Artists", url=LINKS["spotify_for_artists"])],
        [InlineKeyboardButton(text="Яндекс (артистам)", url=LINKS["yandex_artists_hub"])],
        [InlineKeyboardButton(text="Звук Studio", url=LINKS["zvuk_studio"])],
        [InlineKeyboardButton(text="КИОН (бывш. МТС) питчинг", url=LINKS["kion_pitch"])],
        [InlineKeyboardButton(text="TikTok for Artists", url=LINKS["tiktok_for_artists"])],
        [InlineKeyboardButton(text="Лирика/синхронизация", callback_data="links:lyrics")],
        [InlineKeyboardButton(text="UGC / Content ID", callback_data="links:ugc")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]
    ])


SMARTLINKS_PAGE_SIZE = 5
BRANDING_DISABLE_PRICE = 10
EXPORT_UNLOCK_PRICE = 25
SUPPORT_DONATE_PRICE = 50
DONATE_MIN_STARS = 10
DONATE_MAX_STARS = 5000


def smartlinks_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать смарт-линк", callback_data="smartlinks:create")],
            [InlineKeyboardButton(text="📂 Мои смарт-линки", callback_data="smartlinks:list:0")],
            [InlineKeyboardButton(text="✏️ Редактировать смарт-линк", callback_data="smartlinks:list:0")],
            [InlineKeyboardButton(text="📋 Скопировать ссылки", callback_data="smartlinks:list:0")],
            [InlineKeyboardButton(text="❓ Помощь по смарт-линкам", callback_data="smartlinks:help")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_focus")],
        ]
    )


def smartlinks_help_text() -> str:
    return (
        "🔗 Смарт-линки\n\n"
        "• Создавай ссылку по BandLink или площадке — подтяну остальные автоматически.\n"
        "• Можно обновлять обложку, описание и ссылки точечно через меню редактирования.\n"
        "• В карточке есть кнопка 📋 для быстрого копирования всех ссылок."
    )


def build_smartlink_list_text(items: list[dict], page: int, total_pages: int) -> str:
    if not items:
        return "Пока нет смарт-линков. Нажми «➕ Создать смарт-линк»."

    lines = [f"📂 Мои смарт-линки (страница {page + 1}/{total_pages})", ""]
    for idx, item in enumerate(items, start=1):
        artist = item.get("artist") or "Без артиста"
        title = item.get("title") or "Без названия"
        rd = parse_date(item.get("release_date") or "")
        rd_text = f"📅 {format_date_ru(rd)}" if rd else ""
        lines.append(f"{idx}. {artist} — {title} {rd_text}")
    return "\n".join(lines)


def build_smartlink_view_text(smartlink: dict) -> str:
    artist = smartlink.get("artist") or "Без артиста"
    title = smartlink.get("title") or "Без названия"
    rd = parse_date(smartlink.get("release_date") or "")
    lines = [f"{artist} — {title}"]
    if rd:
        lines.append(f"📅 {format_date_ru(rd)}")
    return "\n".join(lines)


def smartlink_view_kb(smartlink_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Открыть", callback_data=f"smartlinks:open:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"smartlinks:edit_menu:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text="📋 Скопировать ссылки", callback_data=f"smartlinks:copy:{smartlink_id}")],
            [InlineKeyboardButton(text=f"📤 Экспорт ⭐{EXPORT_UNLOCK_PRICE}", callback_data=f"smartlinks:export:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"smartlinks:delete:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"smartlinks:list:{page}")],
        ]
    )


def smartlink_edit_menu_kb(
    smartlink_id: int, page: int, branding_disabled: bool = False, branding_paid: bool = False
) -> InlineKeyboardMarkup:
    if branding_disabled:
        branding_text = "🏷 Брендинг ИСКРЫ: Выкл"
    elif branding_paid:
        branding_text = "🏷 Брендинг ИСКРЫ: Вкл"
    else:
        branding_text = "Убрать брендинг ⭐10"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Артист/Название", callback_data=f"smartlinks:edit_field:{smartlink_id}:{page}:title")],
            [InlineKeyboardButton(text="Дата релиза", callback_data=f"smartlinks:edit_field:{smartlink_id}:{page}:date")],
            [InlineKeyboardButton(text="Описание", callback_data=f"smartlinks:edit_field:{smartlink_id}:{page}:caption")],
            [InlineKeyboardButton(text="Обложка", callback_data=f"smartlinks:edit_field:{smartlink_id}:{page}:cover")],
            [InlineKeyboardButton(text="Ссылки", callback_data=f"smartlinks:edit_links:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text=branding_text, callback_data=f"smartlinks:branding_toggle:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"smartlinks:view:{smartlink_id}:{page}")],
        ]
    )


def smartlink_links_menu_kb(smartlink_id: int, page: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, label in SMARTLINK_BUTTON_ORDER:
        rows.append([InlineKeyboardButton(text=label, callback_data=f"smartlinks:edit_link:{smartlink_id}:{page}:{key}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"smartlinks:edit_menu:{smartlink_id}:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def smartlink_export_kb(smartlink_id: int, page: int | None = None) -> InlineKeyboardMarkup:
    page_marker = page if page is not None else -1
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Текст для Telegram", callback_data=f"smartlinks:exportfmt:{smartlink_id}:{page_marker}:tg")],
            [InlineKeyboardButton(text="🧱 Текст для VK", callback_data=f"smartlinks:exportfmt:{smartlink_id}:{page_marker}:vk")],
            [InlineKeyboardButton(text="🌐 Универсальный текст", callback_data=f"smartlinks:exportfmt:{smartlink_id}:{page_marker}:universal")],
            [InlineKeyboardButton(text="🔗 Только ссылки", callback_data=f"smartlinks:exportfmt:{smartlink_id}:{page_marker}:links")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"smartlinks:export_back:{smartlink_id}:{page_marker}")],
        ]
    )


def smartlink_export_paywall_kb(smartlink_id: int, page: int | None = None) -> InlineKeyboardMarkup:
    page_marker = page if page is not None else -1
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⭐ Оплатить {EXPORT_UNLOCK_PRICE} Stars",
                    callback_data=f"smartlinks:export_pay:{smartlink_id}:{page_marker}",
                )
            ],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"smartlinks:export_cancel:{smartlink_id}:{page_marker}")],
        ]
    )


def smartlink_branding_confirm_kb(smartlink_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⭐ Оплатить {BRANDING_DISABLE_PRICE} Stars",
                    callback_data=f"smartlinks:branding_pay:{smartlink_id}:{page}",
                )
            ],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"smartlinks:branding_cancel:{smartlink_id}:{page}")],
        ]
    )


async def send_smartlink_list(message: Message, tg_id: int, page: int = 0):
    total = await count_smartlinks(tg_id)
    total_pages = max(1, (total + SMARTLINKS_PAGE_SIZE - 1) // SMARTLINKS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    items = await list_smartlinks(tg_id, limit=SMARTLINKS_PAGE_SIZE, offset=page * SMARTLINKS_PAGE_SIZE)
    text = build_smartlink_list_text(items, page, total_pages)

    inline: list[list[InlineKeyboardButton]] = []
    for idx, item in enumerate(items, start=1):
        inline.append(
            [
                InlineKeyboardButton(text=f"{idx}. {item.get('artist') or 'Без артиста'} — {item.get('title') or 'Без названия'}", callback_data=f"smartlinks:view:{item.get('id')}:{page}")
            ]
        )
        inline.append(
            [
                InlineKeyboardButton(
                    text=f"📤 Экспорт ⭐{EXPORT_UNLOCK_PRICE}", callback_data=f"smartlinks:export:{item.get('id')}:{page}"
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"smartlinks:list:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"smartlinks:list:{page + 1}"))
    if nav_row:
        inline.append(nav_row)

    inline.append([InlineKeyboardButton(text="◀️ Назад", callback_data="smartlinks:menu")])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=inline))


async def show_smartlink_view(message: Message, tg_id: int, smartlink_id: int, page: int):
    smartlink = await get_smartlink_by_id(smartlink_id)
    if not smartlink or smartlink.get("owner_tg_id") != tg_id:
        await message.answer("Смартлинк не найден.", reply_markup=smartlinks_menu_kb())
        return
    text = build_smartlink_view_text(smartlink)
    await message.answer(text, reply_markup=smartlink_view_kb(smartlink_id, page))


async def resend_smartlink_card(message: Message, tg_id: int, smartlink: dict, page: int):
    allow_remind = smartlink_can_remind(smartlink)
    subscribed = await is_smartlink_subscribed(smartlink.get("id"), tg_id) if allow_remind else False
    await send_smartlink_photo(message.bot, tg_id, smartlink, subscribed=subscribed, allow_remind=allow_remind, page=page)
    await message.answer("Выбери действие:", reply_markup=smartlink_view_kb(smartlink.get("id"), page))


async def get_owned_smartlink(tg_id: int, smartlink_id: int) -> dict | None:
    smartlink = await get_smartlink_by_id(smartlink_id)
    if not smartlink or smartlink.get("owner_tg_id") != tg_id:
        return None
    return smartlink
async def get_spotify_access_token() -> str | None:
    global _SPOTIFY_ACCESS_TOKEN, _SPOTIFY_TOKEN_EXPIRES_AT

    if not SPOTIFY_UPC_ENABLED:
        return None

    now = dt.datetime.utcnow()
    if _SPOTIFY_ACCESS_TOKEN and _SPOTIFY_TOKEN_EXPIRES_AT and _SPOTIFY_TOKEN_EXPIRES_AT > now:
        return _SPOTIFY_ACCESS_TOKEN

    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://accounts.spotify.com/api/token",
                data={"grant_type": "client_credentials"},
                auth=aiohttp.BasicAuth(SPOTIFY_CLIENT_ID or "", SPOTIFY_CLIENT_SECRET or ""),
            ) as resp:
                if resp.status >= 400:
                    return None
                payload = await resp.json()
                token = payload.get("access_token")
                expires_in = int(payload.get("expires_in", 3600))
                if not token:
                    return None
                _SPOTIFY_ACCESS_TOKEN = token
                _SPOTIFY_TOKEN_EXPIRES_AT = now + dt.timedelta(seconds=max(expires_in - 30, 0))
                return token
    except Exception:
        return None

    return None


async def spotify_search_upc(upc: str) -> list[dict[str, str]]:
    token = await get_spotify_access_token()
    if not token:
        return []

    timeout = aiohttp.ClientTimeout(total=10)
    headers = {"Authorization": f"Bearer {token}"}
    params = {"q": f"upc:{upc}", "type": "album,track", "limit": 5}
    candidates: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    def add_candidate(title: str, artists: list[dict] | list[str], url: str | None):
        if not url or url in seen_urls:
            return
        artist_names_list: list[str] = []
        for a in artists:
            name = a.get("name") if isinstance(a, dict) else str(a)
            if name:
                artist_names_list.append(name)
        artist_names = ", ".join(artist_names_list)
        candidates.append({"artist": artist_names, "title": title, "spotify_url": url})
        seen_urls.add(url)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://api.spotify.com/v1/search", headers=headers, params=params) as resp:
                if resp.status >= 400:
                    return []
                data = await resp.json()
    except Exception:
        return []

    for item in data.get("albums", {}).get("items", []) or []:
        add_candidate(item.get("name", ""), item.get("artists", []), (item.get("external_urls") or {}).get("spotify"))

    for item in data.get("tracks", {}).get("items", []) or []:
        add_candidate(item.get("name", ""), item.get("artists", []), (item.get("external_urls") or {}).get("spotify"))

    return candidates


def _allowed_music_platform(host: str, path: str, query: dict[str, str]) -> str | None:
    if "band.link" in host:
        return "bandlink"
    if host.startswith("music.yandex.") and ("/track/" in path or "/album/" in path):
        if query.get("utm_source", "").lower() == "bandlink":
            return "bandlink"
        return "yandex"
    if host == "open.spotify.com":
        return "spotify"
    if host == "music.apple.com":
        return "apple"
    if host == "itunes.apple.com":
        return "itunes"
    if host in {"music.vk.com", "music.vk.ru"}:
        return "vk"
    if host == "vk.com" and (
        path.startswith("/music") or path.startswith("/link/")
    ) and not any(path.startswith(prefix) for prefix in {"/away", "/share", "/login", "/terms"}):
        return "vk"
    if host == "deezer.com" and any(path.startswith(prefix) for prefix in {"/track/", "/album/", "/playlist/", "/artist/"}):
        return "deezer"
    if host in {"youtube.com", "m.youtube.com"}:
        if path.startswith("/watch") and query.get("v"):
            return "youtube"
        if path.startswith("/shorts/"):
            return "youtube"
    if host == "youtu.be" and path.strip("/"):
        return "youtube"
    if host == "music.youtube.com":
        if path.startswith("/watch") and query.get("v"):
            return "youtubemusic"
        if path.startswith("/browse/MPRE"):
            return "youtubemusic"
    if host == "zvuk.com" and any(
        path.startswith(prefix)
        for prefix in {"/album/", "/artist/", "/track/", "/playlist/", "/release/"}
    ):
        return "zvuk"
    if host.startswith("kion.") or host == "kion.ru" or host.startswith("music.kion."):
        return "kion"
    return None


def _normalize_music_url(url: str, platform_hint: str | None = None) -> str:
    normalized, _ = normalize_music_url_with_platform(url, platform_hint)
    return normalized


def normalize_music_url_with_platform(url: str, platform_hint: str | None = None) -> tuple[str, str | None]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return "", None
    cleaned_query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
    ]
    query_dict = {k: v for k, v in cleaned_query_pairs}
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"

    platform = _normalize_platform_key(platform_hint) if platform_hint else None
    platform = platform or _allowed_music_platform(host, path, query_dict)
    if not platform:
        return "", None

    normalized_url = urlunparse(parsed._replace(netloc=host, query=urlencode(cleaned_query_pairs), fragment=""))
    return normalized_url, platform


def detect_platform(url: str) -> str | None:
    _, platform = normalize_music_url_with_platform(url)
    return platform


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def normalize_meta_value(value: str | None) -> str:
    cleaned = (value or "").lower().strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\b(ep|album|single)\b", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9а-яё]+", "", cleaned)
    return cleaned


def filter_human_sources(sources: dict[str, dict]) -> dict[str, dict]:
    filtered: dict[str, dict] = {}
    for key, meta in (sources or {}).items():
        normalized_key = SONGLINK_PLATFORM_ALIASES.get(key, key)
        if normalized_key not in HUMAN_METADATA_PLATFORMS:
            continue
        filtered.setdefault(normalized_key, meta or {})
    return filtered


def _normalize_platform_key(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^a-zA-Z]", "", value).lower()
    if not cleaned:
        return None
    if cleaned in {"youtubemusic", "ytmusic"}:
        cleaned = "youtubemusic"
    if cleaned == "deezer":
        return "deezer"
    return SONGLINK_PLATFORM_ALIASES.get(cleaned, cleaned)


def _collect_metadata_fields(candidate: dict, meta_acc: dict[str, set[str]]):
    for key, val in candidate.items():
        if not isinstance(val, str):
            continue
        lowered_key = key.lower()
        if lowered_key in {"artist", "artistname", "artist_name"} or lowered_key.endswith("artist"):
            if val.strip():
                meta_acc.setdefault("artist", set()).add(val.strip())
        if lowered_key in {"title", "track", "song", "name"} and not lowered_key.endswith("url"):
            if val.strip():
                meta_acc.setdefault("title", set()).add(val.strip())
        if "cover" in lowered_key or "image" in lowered_key or "thumbnail" in lowered_key or "artwork" in lowered_key:
            if val.strip().startswith("http"):
                meta_acc.setdefault("cover_url", set()).add(val.strip())


def parse_bandlink(html_content: str) -> tuple[dict[str, str], dict | None]:
    links: dict[str, str] = {}
    meta: dict | None = None
    meta_candidates: dict[str, set[str]] = {}

    soup = BeautifulSoup(html_content or "", "html.parser")

    next_script = soup.find("script", id="__NEXT_DATA__")
    if next_script and next_script.string:
        try:
            next_data_raw = html.unescape(next_script.string)
            next_data = json.loads(next_data_raw)
            print("[bandlink] __NEXT_DATA__ found")
        except Exception as e:
            print(f"[bandlink] failed to parse __NEXT_DATA__: {e}")
            next_data = None
    else:
        print("[bandlink] __NEXT_DATA__ not found")
        next_data = None

    def add_link(url: str | None, platform_hint: str | None = None):
        if not url:
            return
        normalized_url, platform = normalize_music_url_with_platform(url, platform_hint)
        if not normalized_url or not platform:
            return
        if platform and platform not in links:
            links[platform] = normalized_url

    def process_service(service: dict):
        if not isinstance(service, dict):
            return
        add_link(
            service.get("href")
            or service.get("url")
            or service.get("link")
            or (service.get("action") or {}).get("url"),
            service.get("platform")
            or service.get("service")
            or service.get("type")
            or service.get("id")
            or service.get("name"),
        )
        _collect_metadata_fields(service, meta_candidates)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = key.lower()
                if isinstance(value, list) and lowered in {"services", "links", "platforms", "buttons"}:
                    for item in value:
                        if isinstance(item, dict):
                            process_service(item)
                elif isinstance(value, dict):
                    process_service(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    if next_data:
        walk(next_data.get("props") or next_data.get("pageProps") or next_data)

    for script in soup.find_all("script"):
        content_type = (script.get("type") or "").lower()
        if "json" not in content_type and script.get("id") != "__NEXT_DATA__":
            continue
        raw = script.string or ""
        if not raw.strip():
            continue
        try:
            data_blob = json.loads(raw)
        except Exception:
            continue
        walk(data_blob)

    if not links or len(links) < 3:
        extracted_links = extract_links_from_bandlink(html_content, soup=soup)
        for platform_key, href in extracted_links.items():
            add_link(href, platform_key)

    if not links:
        legacy_links = extract_links_from_bandlink(html_content, soup=soup)
        if legacy_links:
            print(f"[bandlink] legacy href parser extracted {len(legacy_links)} platforms")
            links.update(legacy_links)

    og_title_match = re.search(r'<meta[^>]+property=\"og:title\"[^>]+content=\"([^\"]+)\"', html_content, re.IGNORECASE)
    og_image_match = re.search(r'<meta[^>]+property=\"og:image\"[^>]+content=\"([^\"]+)\"', html_content, re.IGNORECASE)
    if og_title_match:
        title_raw = html.unescape(og_title_match.group(1)).strip()
        if " - " in title_raw and not meta_candidates.get("artist"):
            artist_val, title_val = title_raw.split(" - ", 1)
            meta_candidates.setdefault("artist", set()).add(artist_val.strip())
            meta_candidates.setdefault("title", set()).add(title_val.strip())
        else:
            meta_candidates.setdefault("title", set()).add(title_raw)
    if og_image_match:
        image_val = html.unescape(og_image_match.group(1)).strip()
        meta_candidates.setdefault("cover_url", set()).add(image_val)

    artist = next(iter(meta_candidates.get("artist", [])), "")
    title = next(iter(meta_candidates.get("title", [])), "")
    cover_url = next(iter(meta_candidates.get("cover_url", [])), "")

    if artist or title or cover_url:
        meta = {
            "artist": artist,
            "title": title,
            "cover_url": cover_url,
            "source_platform": "bandlink",
            "preferred_source": "bandlink",
            "sources": {"bandlink": {"artist": artist, "title": title, "cover_url": cover_url}},
            "conflict": False,
        }

    print(f"[bandlink] extracted {len(links)} platforms; meta={'yes' if meta else 'no'}")
    return links, meta


async def resolve_links(url: str) -> tuple[dict[str, str], dict | None]:
    timeout = aiohttp.ClientTimeout(total=10)
    normalized_input_url = _normalize_music_url(url)

    async def resolve_via_songlink() -> tuple[dict[str, str], dict | None]:
        def collect(platforms: dict, acc: dict[str, str]):
            for platform_key, info in (platforms or {}).items():
                normalized_platform = SONGLINK_PLATFORM_ALIASES.get(platform_key.lower())
                normalized_url = _normalize_music_url((info or {}).get("url") or "")
                if normalized_platform and normalized_url and normalized_platform not in acc:
                    acc[normalized_platform] = normalized_url

        def extract_platform(entity_id: str | None, entity: dict | None) -> str | None:
            if not entity_id:
                return None
            parts = entity_id.split(":")
            if parts:
                candidate = parts[0].lower()
                return SONGLINK_PLATFORM_ALIASES.get(candidate, candidate)
            platform = (entity or {}).get("platform")
            return SONGLINK_PLATFORM_ALIASES.get(platform.lower()) if platform else None

        try:
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": BANDLINK_USER_AGENT}) as session:
                async with session.get(SONGLINK_API_URL, params={"url": url}) as resp:
                    if resp.status != 200:
                        return {}, {}
                    data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return {}, {}
        except Exception:
            return {}, {}

        links: dict[str, str] = {}
        entities = data.get("entitiesByUniqueId") or {}
        if not entities:
            print("[songlink] empty entities from resolver")
            return {}, {}

        collect(data.get("linksByPlatform") or {}, links)
        primary_entity_id = data.get("entityUniqueId")
        primary = entities.get(primary_entity_id) if primary_entity_id else None
        candidates = [primary] if primary else []
        candidates.extend([entity for entity in entities.values() if entity is not primary])

        meta_candidates: dict[str, dict[str, str]] = {}

        for entity in candidates:
            collect((entity or {}).get("linksByPlatform") or {}, links)

            artist_name = (entity or {}).get("artistName") or (entity or {}).get("artistNamePrimary")
            title = (entity or {}).get("title")
            cover_url = (entity or {}).get("thumbnailUrl") or (entity or {}).get("thumbnailUrlLarge")
            entity_platform = extract_platform((entity or {}).get("id") or (entity or {}).get("uniqueId"), entity)

            if entity_platform:
                meta_candidates[entity_platform] = {
                    "artist": artist_name or "",
                    "title": title or "",
                    "cover_url": cover_url or "",
                }

        if not meta_candidates:
            print("[songlink] no metadata from entities")
            return links, None

        priority = ["apple", "itunes", "spotify", "yandex", "vk", "zvuk", "youtube", "deezer", "kion", "youtubemusic"]
        preferred = None
        for p in priority:
            if p in meta_candidates:
                preferred = p
                break
        if not preferred:
            preferred = next(iter(meta_candidates.keys()))

        artists = {m.get("artist") for m in meta_candidates.values() if m.get("artist")}
        titles = {m.get("title") for m in meta_candidates.values() if m.get("title")}
        conflict = len(artists) > 1 or len(titles) > 1

        meta = {
            "artist": meta_candidates.get(preferred, {}).get("artist", ""),
            "title": meta_candidates.get(preferred, {}).get("title", ""),
            "cover_url": meta_candidates.get(preferred, {}).get("cover_url", ""),
            "source_platform": preferred,
            "preferred_source": preferred,
            "sources": meta_candidates,
            "conflict": conflict,
        }

        print(f"[songlink] meta source={preferred} conflict={conflict} candidates={list(meta_candidates.keys())}")
        print(f"[songlink] extracted {len(links)} platforms")

        return links, meta

    detected = detect_platform(url) or ""
    links: dict[str, str] = {}
    metadata: dict | None = None

    if detected == "bandlink":
        html_content = await fetch_bandlink_html(url) or ""
        band_links, band_meta = parse_bandlink(html_content)
        links.update(band_links)
        metadata = merge_metadata(metadata, band_meta)

        if len(links) < 2 or not ((metadata or {}).get("artist") and (metadata or {}).get("title")):
            song_links, song_meta = await resolve_via_songlink()
            links.update(song_links)
            metadata = merge_metadata(metadata, song_meta)
    else:
        song_links, song_meta = await resolve_via_songlink()
        links.update(song_links)
        metadata = merge_metadata(metadata, song_meta)

    if detected and normalized_input_url:
        platform_key = SONGLINK_PLATFORM_ALIASES.get(detected, detected)
        if platform_key not in links:
            links[platform_key] = normalized_input_url

    return links, metadata or {}


def merge_metadata(existing: dict | None, new: dict | None) -> dict:
    merged = dict(existing or {})
    if not new:
        return merged

    sources = merged.get("sources") or {}
    sources.update((new or {}).get("sources") or {})
    sources = filter_human_sources(sources)
    merged["sources"] = sources

    preferred = new.get("preferred_source") or merged.get("preferred_source")
    if preferred:
        preferred = SONGLINK_PLATFORM_ALIASES.get(preferred, preferred)
    if preferred and preferred not in sources and sources:
        preferred = next(iter(sources.keys()))
    if not preferred and sources:
        preferred = next(iter(sources.keys()))

    merged["preferred_source"] = preferred
    merged["source_platform"] = preferred or merged.get("source_platform")

    artists = {normalize_meta_value(s.get("artist")) for s in sources.values() if s.get("artist")}
    titles = {normalize_meta_value(s.get("title")) for s in sources.values() if s.get("title")}
    merged["conflict"] = len(artists) > 1 or len(titles) > 1

    def value_from_sources(field: str) -> str:
        if preferred and preferred in sources:
            return sources.get(preferred, {}).get(field, "")
        return merged.get(field, "") or ""

    merged["artist"] = new.get("artist") or value_from_sources("artist")
    merged["title"] = new.get("title") or value_from_sources("title")
    merged["cover_url"] = new.get("cover_url") or value_from_sources("cover_url")

    return merged


def extract_links_from_bandlink(html_content: str, soup: BeautifulSoup | None = None) -> dict[str, str]:
    links: dict[str, str] = {}
    soup = soup or BeautifulSoup(html_content or "", "html.parser")

    for a_tag in soup.find_all("a"):
        href = a_tag.get("href") or ""
        if not href:
            continue
        platform_hint = (
            a_tag.get("data-platform")
            or a_tag.get("data-service")
            or a_tag.get("data-provider")
            or None
        )
        class_tokens = {cls.lower() for cls in (a_tag.get("class") or []) if cls}
        if not platform_hint:
            for token in class_tokens:
                if token in {
                    "yandex",
                    "vk",
                    "spotify",
                    "apple",
                    "itunes",
                    "zvuk",
                    "kion",
                    "youtube",
                    "youtubemusic",
                    "deezer",
                }:
                    platform_hint = token
                    break
        normalized, platform = normalize_music_url_with_platform(html.unescape(href), platform_hint)
        if not normalized or not platform:
            continue
        if platform not in links:
            links[platform] = normalized
    return links


async def fetch_bandlink_html(url: str) -> str | None:
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {
        "User-Agent": BANDLINK_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
    }
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    return None
                return await resp.text()
    except Exception:
        return None


def skip_prefilled_smartlink_steps(step: int, data: dict) -> int:
    total_steps = 5 + len(SMARTLINK_PLATFORMS)
    links = data.get("links") or {}
    while step < total_steps:
        if step == 0 and data.get("artist"):
            step += 1
            continue
        if step == 1 and data.get("title"):
            step += 1
            continue
        if step == 2 and data.get("release_date"):
            step += 1
            continue
        if step == 3 and data.get("cover_file_id"):
            step += 1
            continue
        if step >= 5:
            idx = step - 5
            platform_key = SMARTLINK_PLATFORMS[idx][0]
            if links.get(platform_key):
                step += 1
                continue
        break
    return step


def smartlink_step_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="smartlink:skip")],
            [InlineKeyboardButton(text="Отмена", callback_data="smartlink:cancel")],
        ]
    )


async def finalize_smartlink_form(message: Message, tg_id: int, data: dict):
    links = data.get("links") or {}
    links_clean = {k: v for k, v in links.items() if v}
    release_iso = data.get("release_date")
    caption_text = data.get("caption_text", "") or ""
    smartlink_id = await save_smartlink(
        tg_id,
        data.get("artist", ""),
        data.get("title", ""),
        release_iso or "",
        data.get("cover_file_id", ""),
        links_clean,
        caption_text,
        bool(data.get("branding_disabled")),
    )
    smartlink = {
        "id": smartlink_id,
        "owner_tg_id": tg_id,
        "artist": data.get("artist", ""),
        "title": data.get("title", ""),
        "release_date": release_iso,
        "cover_file_id": data.get("cover_file_id", ""),
        "links": links_clean,
        "caption_text": caption_text,
        "branding_disabled": bool(data.get("branding_disabled")),
        "created_at": dt.datetime.utcnow().isoformat(),
    }
    allow_remind = smartlink_can_remind(smartlink)
    subscribed = await is_smartlink_subscribed(smartlink_id, tg_id) if allow_remind else False
    await send_smartlink_photo(message.bot, tg_id, smartlink, subscribed=subscribed, allow_remind=allow_remind)
    await message.answer("Готово. Смартлинк сохранён.", reply_markup=await user_menu_keyboard(tg_id))
    await form_clear(tg_id)


async def fetch_cover_file(cover_url: str) -> BufferedInputFile | None:
    if not cover_url:
        return None
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(cover_url) as resp:
                if resp.status >= 400:
                    print(f"[cover] failed to fetch {cover_url}: status {resp.status}")
                    return None
                data = await resp.read()
                if not data:
                    return None
                filename = cover_url.split("/")[-1] or "cover.jpg"
                return BufferedInputFile(data, filename=filename)
    except Exception as e:
        print(f"[cover] error fetching {cover_url}: {e}")
        return None


async def show_import_confirmation(
    message: Message,
    tg_id: int,
    links: dict[str, str],
    metadata: dict | None,
    latest: dict | None = None,
):
    sources = filter_human_sources((metadata or {}).get("sources") or {})
    preferred_source = (metadata or {}).get("preferred_source") or (metadata or {}).get("source_platform")
    if preferred_source:
        preferred_source = SONGLINK_PLATFORM_ALIASES.get(preferred_source, preferred_source)
    if preferred_source not in sources and sources:
        preferred_source = next(iter(sources.keys()))
    selected_meta = sources.get(preferred_source, metadata or {}) if metadata else {}

    artist = selected_meta.get("artist") or (latest.get("artist") if latest else "")
    title = selected_meta.get("title") or (latest.get("title") if latest else "")
    release_date = (latest.get("release_date") or "") if latest else ""
    caption_text = (latest.get("caption_text") or "") if latest else ""
    cover_file_id = (latest.get("cover_file_id") or "") if latest else ""

    platforms_text = ", ".join(sorted(links.keys())) if links else "—"
    caption_lines = [
        "Нашёл ссылки на релиз.",
        f"{artist or 'Без артиста'} — {title or 'Без названия'}",
        "",
        f"Площадки: {platforms_text}",
    ]
    if metadata and sources and preferred_source:
        label = platform_label(preferred_source)
        caption_lines.append(f"Источник: {label}")
    if metadata and metadata.get("conflict"):
        caption_lines.append("⚠️ Название/артист отличаются на площадках. Выбери источник или подтверди по умолчанию.")
    if len(links) < 2:
        caption_lines.append("Можно прислать ссылку другой платформы, чтобы добавить остальные площадки.")
    caption_lines.append("")
    caption_lines.append("Подтверди данные или измени вручную.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="smartlink:import_confirm")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="smartlink:import_edit")],
            [InlineKeyboardButton(text="Отмена", callback_data="smartlink:import_cancel")],
        ]
    )

    if metadata and len(sources) > 1:
        source_row = []
        for platform_key in sorted(sources.keys()):
            label = platform_label(platform_key)
            mark = "✅ " if platform_key == preferred_source else ""
            source_row.append(InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"smartlink:import_source:{platform_key}"))
        kb.inline_keyboard.insert(0, source_row)

    cover_source = selected_meta.get("cover_url") or cover_file_id
    preview_message: Message | None = None
    if cover_source:
        try:
            input_file = await fetch_cover_file(cover_source)
        except Exception:
            input_file = None
        try:
            preview_message = await message.answer_photo(
                photo=input_file or cover_source,
                caption="\n".join(caption_lines),
                reply_markup=kb,
            )
            if input_file:
                print(f"[cover] downloaded cover from {cover_source}")
        except Exception as e:
            print(f"[cover] failed to show preview: {e}")
            preview_message = None

    if not preview_message:
        preview_message = await message.answer("\n".join(caption_lines), reply_markup=kb)

    if preview_message.photo:
        cover_file_id = preview_message.photo[-1].file_id

    await form_start(tg_id, "smartlink_import_review")
    await form_set(
        tg_id,
        0,
        {
            "artist": artist,
            "title": title,
            "release_date": release_date,
            "cover_file_id": cover_file_id,
            "links": links,
            "caption_text": caption_text,
            "metadata": metadata or {},
            "preferred_source": preferred_source,
        },
    )


def pick_selected_metadata(data: dict) -> dict:
    metadata = data.get("metadata") or {}
    sources = metadata.get("sources") or {}
    preferred = data.get("preferred_source") or metadata.get("preferred_source") or metadata.get("source_platform")
    if preferred and preferred in sources:
        return sources.get(preferred) or {}
    if sources:
        first_key = next(iter(sources.keys()))
        return sources.get(first_key) or {}
    return metadata


async def start_prefill_editor(message: Message, tg_id: int, data: dict):
    selected_meta = pick_selected_metadata(data)
    artist = data.get("artist") or selected_meta.get("artist") or ""
    title = data.get("title") or selected_meta.get("title") or ""
    cover_file_id = data.get("cover_file_id") or selected_meta.get("cover_file_id") or ""

    display_lines = [
        "Проверь данные перед сохранением:",
        f"Артист: {artist or '—'}",
        f"Релиз: {title or '—'}",
        f"Площадки: {', '.join(sorted((data.get('links') or {}).keys())) or '—'}",
        "",
        "Можно поправить нужное поле и продолжить.",
    ]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить артиста", callback_data="smartlink:prefill_edit:artist")],
            [InlineKeyboardButton(text="Изменить релиз", callback_data="smartlink:prefill_edit:title")],
            [InlineKeyboardButton(text="Заменить обложку", callback_data="smartlink:prefill_edit:cover")],
            [InlineKeyboardButton(text="Продолжить", callback_data="smartlink:prefill_continue")],
            [InlineKeyboardButton(text="Отмена", callback_data="smartlink:import_cancel")],
        ]
    )

    if cover_file_id:
        try:
            await message.answer_photo(photo=cover_file_id, caption="\n".join(display_lines), reply_markup=kb)
        except Exception:
            await message.answer("\n".join(display_lines), reply_markup=kb)
    else:
        await message.answer("\n".join(display_lines), reply_markup=kb)

    data["artist"] = artist
    data["title"] = title
    data["cover_file_id"] = cover_file_id
    data.pop("pending", None)
    await form_start(tg_id, "smartlink_prefill_edit")
    await form_set(tg_id, 0, data)


async def apply_spotify_upc_selection(message: Message, tg_id: int, candidate: dict):
    await form_clear(tg_id)

    spotify_url = candidate.get("spotify_url")
    if not spotify_url:
        await message.answer("Не нашёл ссылку Spotify для этого UPC.", reply_markup=await user_menu_keyboard(tg_id))
        return

    latest = await get_latest_smartlink(tg_id)
    if latest and latest.get("artist") and latest.get("title") and latest.get("cover_file_id"):
        links = latest.get("links") or {}
        links["spotify"] = spotify_url
        smartlink_id = await save_smartlink(
            tg_id,
            latest.get("artist", ""),
            latest.get("title", ""),
            latest.get("release_date") or "",
            latest.get("cover_file_id", ""),
            links,
            latest.get("caption_text", "") or "",
            bool(latest.get("branding_disabled")),
        )
        smartlink = {
            "id": smartlink_id,
            "owner_tg_id": tg_id,
            "artist": latest.get("artist", ""),
            "title": latest.get("title", ""),
            "release_date": latest.get("release_date") or "",
            "cover_file_id": latest.get("cover_file_id", ""),
            "links": links,
            "caption_text": latest.get("caption_text", "") or "",
            "branding_disabled": bool(latest.get("branding_disabled")),
            "created_at": dt.datetime.utcnow().isoformat(),
        }
        allow_remind = smartlink_can_remind(smartlink)
        subscribed = await is_smartlink_subscribed(smartlink_id, tg_id) if allow_remind else False
        await send_smartlink_photo(message.bot, tg_id, smartlink, subscribed=subscribed, allow_remind=allow_remind)
        await message.answer("Добавил Spotify по UPC. Смартлинк обновлён.", reply_markup=await user_menu_keyboard(tg_id))
        return

    await message.answer(
        "Нашёл Spotify. Давай заполним смартлинк: ссылка на Spotify уже подставлена.",
        reply_markup=await user_menu_keyboard(tg_id),
    )
    await start_smartlink_form(message, tg_id, initial_links={"spotify": spotify_url})


async def apply_caption_update(message: Message, tg_id: int, smartlink_id: int, caption_text: str):
    await update_smartlink_caption(smartlink_id, caption_text)
    smartlink = await get_smartlink_by_id(smartlink_id)
    if not smartlink:
        await message.answer("Смартлинк не найден.", reply_markup=await user_menu_keyboard(tg_id))
        await form_clear(tg_id)
        return
    allow_remind = smartlink_can_remind(smartlink)
    subscribed = await is_smartlink_subscribed(smartlink_id, tg_id) if allow_remind else False
    await send_smartlink_photo(message.bot, tg_id, smartlink, subscribed=subscribed, allow_remind=allow_remind)
    await message.answer("Текст обновлён.", reply_markup=await user_menu_keyboard(tg_id))
    await form_clear(tg_id)


ATTRIBUTION_HTML = 'Сделано с помощью <a href="https://t.me/iskramusic_bot">ИСКРЫ</a>'


def build_smartlink_caption(
    smartlink: dict, release_today: bool = False, show_listen_label: bool | None = None
) -> str:
    artist = html.escape(smartlink.get("artist") or "")
    title = html.escape(smartlink.get("title") or "")
    caption_text = html.escape(smartlink.get("caption_text") or "")
    release_date = parse_date(smartlink.get("release_date")) if smartlink.get("release_date") else None
    show_branding = not smartlink.get("branding_disabled")
    presave_active = smartlink_pre_save_active(smartlink)

    links = smartlink.get("links") or {}
    has_platforms = any(links.get(key) for key, _ in SMARTLINK_BUTTON_ORDER)
    include_listen = False if presave_active else (show_listen_label if show_listen_label is not None else has_platforms)

    if release_today:
        lines = [f"{artist} — {title}"]
        lines.append("🎉 Сегодня релиз!")
        if release_date:
            lines.append(f"📅 Релиз: {format_date_ru(release_date)}")
        if caption_text:
            lines.append(caption_text)
        if show_branding:
            lines.append("")
            lines.append(ATTRIBUTION_HTML)
        if include_listen:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("▶️ Слушать:")
        return "\n".join(lines)

    lines = [f"{artist} — {title}"]
    if release_date:
        lines.append(f"📅 Релиз: {format_date_ru(release_date)}")
    if presave_active:
        lines.append("⏳ Скоро выйдет")
    if caption_text:
        lines.append(caption_text)
    if show_branding:
        lines.append("")
        lines.append(ATTRIBUTION_HTML)
    if include_listen:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append("▶️ Слушать:")
    return "\n".join(lines)


def build_smartlink_buttons(
    smartlink: dict,
    subscribed: bool = False,
    can_remind: bool = False,
    page: int | None = None,
) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    links = smartlink.get("links") or {}
    page_marker = page if page is not None else -1
    presave_active = smartlink_pre_save_active(smartlink)

    if not presave_active:
        platform_rows: list[list[InlineKeyboardButton]] = []
        for key, label in SMARTLINK_BUTTON_ORDER:
            url = links.get(key)
            if url:
                platform_rows.append([InlineKeyboardButton(text=label, url=url)])

        if platform_rows:
            rows.extend(platform_rows)

    if can_remind:
        toggle_text = "✅ Напоминание включено" if subscribed else "🔔 Напомнить о релизе"
        rows.append([InlineKeyboardButton(text=toggle_text, callback_data=f"smartlink:toggle:{smartlink.get('id')}")])

    rows.append([InlineKeyboardButton(text="📋 Скопировать ссылки", callback_data=f"smartlinks:copy:{smartlink.get('id')}")])
    rows.append([InlineKeyboardButton(text="📤 Экспорт", callback_data=f"smartlinks:export:{smartlink.get('id')}:{page_marker}")])

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def build_copy_links_text(smartlink: dict) -> str:
    artist = smartlink.get("artist") or ""
    title = smartlink.get("title") or ""
    links = smartlink.get("links") or {}

    lines = [f"{artist} — {title}"]

    link_lines: list[str] = []
    for key, label in SMARTLINK_BUTTON_ORDER:
        url = links.get(key)
        if url:
            display_label = "YouTube" if key == "youtube" else label
            link_lines.append(f"{display_label}: {url}")

    if link_lines:
        lines.append("")
        lines.extend(link_lines)

    return "\n".join(lines)


def _iter_smartlink_links(smartlink: dict) -> list[tuple[str, str]]:
    links = smartlink.get("links") or {}
    items: list[tuple[str, str]] = []
    for key, _ in SMARTLINK_BUTTON_ORDER:
        url = links.get(key)
        if url:
            items.append((key, url))
    return items


def _export_label(platform: str, variant: str) -> str:
    order = {"tg": 0, "vk": 1, "universal": 2, "links": 3}
    labels = EXPORT_LABELS.get(platform)
    if labels and variant in order:
        return labels[order[variant]]
    return platform_label(platform)


def build_smartlink_export_text(smartlink: dict, variant: str) -> str:
    artist = smartlink.get("artist") or "Без артиста"
    title = smartlink.get("title") or "Без названия"
    items = [(platform, url, _export_label(platform, variant)) for platform, url in _iter_smartlink_links(smartlink)]

    if variant == "tg":
        lines = [f"{artist} — {title}"]
        if items:
            lines.append("▶️ Слушать:")
            for _platform, url, label in items:
                lines.append(f"{label} — {url}")
        return "\n".join(lines)

    if variant == "vk":
        lines = [f"{artist} — {title}", "Новый релиз уже доступен 👇"]
        for _platform, url, label in items:
            lines.append(f"{label}: {url}")
        return "\n".join(lines)

    if variant == "universal":
        lines = [f"{artist} — {title}", "Release links:"]
        for _platform, url, label in items:
            lines.append(f"- {label}: {url}")
        return "\n".join(lines)

    if variant == "links":
        lines = [f"{label}: {url}" for _platform, url, label in items]
        return "\n".join(lines) if lines else "Ссылок пока нет"

    return ""


def smartlink_pre_save_active(smartlink: dict) -> bool:
    if not smartlink:
        return False
    rd = parse_date(smartlink.get("release_date") or "")
    return bool(rd and rd > dt.date.today() and smartlink.get("pre_save_enabled", True))


def smartlink_can_remind(smartlink: dict) -> bool:
    rd = parse_date(smartlink.get("release_date") or "") if smartlink else None
    return bool(rd and rd > dt.date.today() and smartlink.get("reminders_enabled", True))


async def send_smartlink_photo(
    bot: Bot,
    chat_id: int,
    smartlink: dict,
    release_today: bool = False,
    subscribed: bool = False,
    allow_remind: bool = False,
    page: int | None = None,
):
    caption = build_smartlink_caption(smartlink, release_today=release_today)
    kb = build_smartlink_buttons(smartlink, subscribed=subscribed, can_remind=allow_remind, page=page)
    return await bot.send_photo(
        chat_id,
        photo=smartlink.get("cover_file_id"),
        caption=caption,
        reply_markup=kb,
        parse_mode="HTML",
    )

def build_timeline_kb(reminders_enabled: bool, has_date: bool = True) -> InlineKeyboardMarkup:
    toggle_text = "🔔 Напоминания: Вкл" if reminders_enabled else "🔔 Напоминания: Выкл"
    rows = [[InlineKeyboardButton(text=toggle_text, callback_data="reminders:toggle")]]
    if not has_date:
        rows.append([InlineKeyboardButton(text="📅 Установить дату", callback_data="timeline:set_date")])
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_deadlines(release_date: dt.date) -> list[tuple[str, str, dt.date]]:
    items: list[tuple[str, str, dt.date]] = []
    for d in DEADLINES:
        items.append((d["key"], d["title"], release_date + dt.timedelta(days=d["offset"])))
    return sorted(items, key=lambda x: x[2])


def timeline_text(release_date: dt.date | None, reminders_enabled: bool = True) -> str:
    if not release_date:
        return (
            "📅 Таймлайн\n\nДата релиза не задана."
            "\nНажми «📅 Установить дату» или команду /set_date ДД.ММ.ГГГГ"
        )

    blocks: list[tuple[str, list[tuple[str, dt.date]]]] = []
    start_prep = release_date + dt.timedelta(days=-21)
    end_prep = release_date + dt.timedelta(days=-14)
    blocks.append(("−21…−14 (подготовка к питчингу)", [("Окно подготовки", start_prep), ("Конец окна", end_prep)]))

    deadlines = build_deadlines(release_date)
    events: list[tuple[str, dt.date]] = [("Релиз", release_date)]
    for _, title, d in deadlines:
        events.append((title, d))

    grouped: dict[str, list[tuple[str, dt.date]]] = {
        "pitch": [],
        "pre": [],
        "release": [],
        "post": [],
    }
    for title, d in events:
        offset = (d - release_date).days
        if -21 <= offset <= -15:
            grouped.setdefault("prep", []).append((title, d))
        if offset == -14:
            grouped["pitch"].append((title, d))
        if offset == -7:
            grouped["pre"].append((title, d))
        if offset == 0:
            grouped["release"].append((title, d))
        if offset in {1, 3, 7}:
            grouped["post"].append((title, d))

    blocks.append(("−14 Питчинг", grouped.get("pitch", [])))
    blocks.append(("−7 Пресейв/бендлинк", grouped.get("pre", [])))
    blocks.append(("0 Релиз", grouped.get("release", [])))
    blocks.append(("+1/+3/+7 пост-релиз", grouped.get("post", [])))

    lines = ["📅 Таймлайн", "", f"Дата релиза: {format_date_ru(release_date)}"]
    lines.append(f"Напоминания: {'включены' if reminders_enabled else 'выключены'}\n")

    today = dt.date.today()
    for title, items in blocks:
        if not items:
            continue
        lines.append(title)
        for item_title, d in sorted(items, key=lambda x: x[1]):
            delta = (d - today).days
            delta_text = " (сегодня)" if delta == 0 else (f" (через {delta} дн)" if delta > 0 else f" ({abs(delta)} дн назад)")
            lines.append(f"▫️ {format_date_ru(d)} — {item_title}{delta_text}")
        lines.append("")

    return "\n".join([l for l in lines if l is not None])

def build_reset_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, сбросить", callback_data="reset_progress_yes")],
        [InlineKeyboardButton(text="Сбросить всё (дата/настройки)", callback_data="reset_all_yes")],
        [InlineKeyboardButton(text="Отмена", callback_data="back_to_focus")],
    ])

def build_donate_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Спасибо ⭐10", callback_data="donate:10")],
        [InlineKeyboardButton(text="Поддержать ⭐25", callback_data="donate:25")],
        [InlineKeyboardButton(text="Сильно поддержать ⭐50", callback_data="donate:50")],
        [InlineKeyboardButton(text="Своя сумма", callback_data="donate:custom")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_focus")]
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


async def safe_edit_caption(message: Message, caption: str, kb: InlineKeyboardMarkup | None) -> Message | None:
    try:
        await message.edit_caption(caption=caption, reply_markup=kb, parse_mode="HTML")
        return message
    except Exception as edit_err:
        try:
            return await message.answer_photo(
                photo=message.photo[-1].file_id if message.photo else None,
                caption=caption,
                reply_markup=kb,
                parse_mode="HTML",
            )
        except Exception as answer_err:
            print(f"[safe_edit_caption] edit failed: {edit_err}; answer failed: {answer_err}")
            return None

# -------------------- Reminders --------------------

async def was_reminder_sent(db: aiosqlite.Connection, tg_id: int, key: str, when: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM reminder_log WHERE tg_id=? AND key=? AND \"when\"=?",
        (tg_id, key, when)
    )
    row = await cur.fetchone()
    return row is not None


async def mark_reminder_sent(db: aiosqlite.Connection, tg_id: int, key: str, when: str, sent_on: dt.date):
    await db.execute(
        "INSERT OR IGNORE INTO reminder_log (tg_id, key, \"when\", sent_on) VALUES (?, ?, ?, ?)",
        (tg_id, key, when, sent_on.isoformat())
    )


async def was_smartlink_day_sent(db: aiosqlite.Connection, smartlink_id: int, subscriber_tg_id: int, offset_days: int) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM smartlink_reminder_log WHERE smartlink_id=? AND subscriber_tg_id=? AND offset_days=?",
        (smartlink_id, subscriber_tg_id, offset_days),
    )
    return await cur.fetchone() is not None


async def mark_smartlink_day_sent(db: aiosqlite.Connection, smartlink_id: int, subscriber_tg_id: int, offset_days: int, sent_on: dt.date):
    await db.execute(
        "INSERT OR REPLACE INTO smartlink_reminder_log (smartlink_id, subscriber_tg_id, offset_days, sent_on) VALUES (?, ?, ?, ?)",
        (smartlink_id, subscriber_tg_id, offset_days, sent_on.isoformat()),
    )


async def cleanup_reminder_log(db: aiosqlite.Connection, today: dt.date):
    threshold = today - dt.timedelta(days=REMINDER_CLEAN_DAYS)
    await db.execute(
        "DELETE FROM reminder_log WHERE sent_on IS NOT NULL AND sent_on < ?",
        (threshold.isoformat(),)
    )


def build_deadline_messages(release_date: dt.date) -> list[tuple[str, str, dt.date]]:
    messages: list[tuple[str, str, dt.date]] = []
    for key, title, d in build_deadlines(release_date):
        messages.append((key, title, d))
    return messages


def smartlink_reminder_text(offset: int, artist: str, title: str) -> str:
    label = f"{artist} — {title}".strip(" —")
    if offset == -7:
        return f"Через 7 дней релиз: {label}. Проверь смарт-линк и материалы."
    if offset == -1:
        return f"Завтра релиз: {label}. Подготовь посты и рассылку."
    if offset == 0:
        return f"Сегодня релиз: {label}. Пора постить смарт-линк."
    if offset == 7:
        return f"Прошла неделя после релиза: {label}. Самое время допушить в плейлисты/медиа."
    return ""


async def process_reminders(bot: Bot):
    today = dt.date.today()
    async with aiosqlite.connect(DB_PATH) as db:
        global REMINDER_LAST_CLEAN
        if REMINDER_LAST_CLEAN != today:
            await cleanup_reminder_log(db, today)
            REMINDER_LAST_CLEAN = today
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
                        await mark_reminder_sent(db, tg_id, key, when_label, today)
                    except TelegramForbiddenError:
                        continue
                    except Exception:
                        continue
        await db.commit()


async def process_smartlink_notifications(bot: Bot):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, owner_tg_id, artist, title, release_date, pre_save_enabled, reminders_enabled, project_id, cover_file_id, links_json, caption_text, branding_disabled, created_at, branding_paid FROM smartlinks WHERE release_date IS NOT NULL",
        )
        smartlinks = [_smartlink_row_to_dict(row) for row in await cur.fetchall()]

        for smartlink in smartlinks:
            if not smartlink.get("reminders_enabled"):
                continue
            rd = parse_date(smartlink.get("release_date") or "")
            if not rd:
                continue
            sub_cur = await db.execute(
                "SELECT subscriber_tg_id FROM smartlink_subscriptions WHERE smartlink_id=?",
                (smartlink.get("id"),),
            )
            subscribers = [row[0] for row in await sub_cur.fetchall()]
            for subscriber_tg_id in subscribers:
                tz, offsets, reminder_time = await get_user_reminder_prefs(db, subscriber_tg_id)
                now_local = dt.datetime.now(ZoneInfo(tz))
                if reminder_time and (now_local.hour != reminder_time.hour or now_local.minute != reminder_time.minute):
                    continue
                for offset in offsets:
                    target_date = rd + dt.timedelta(days=offset)
                    if target_date != now_local.date():
                        continue
                    if await was_smartlink_day_sent(db, smartlink.get("id"), subscriber_tg_id, offset):
                        continue
                    try:
                        text = smartlink_reminder_text(offset, smartlink.get("artist") or "", smartlink.get("title") or "")
                        if text:
                            await bot.send_message(subscriber_tg_id, text)
                        await send_smartlink_photo(
                            bot,
                            subscriber_tg_id,
                            smartlink,
                            release_today=offset == 0,
                            subscribed=True,
                            allow_remind=False,
                        )
                        await mark_smartlink_day_sent(db, smartlink.get("id"), subscriber_tg_id, offset, now_local.date())
                        if offset == 0:
                            await db.execute(
                                "UPDATE smartlink_subscriptions SET notified=1 WHERE smartlink_id=? AND subscriber_tg_id=?",
                                (smartlink.get("id"), subscriber_tg_id),
                            )
                    except TelegramForbiddenError:
                        continue
                    except Exception:
                        continue
        await db.commit()


async def reminder_scheduler(bot: Bot):
    while True:
        try:
            await process_reminders(bot)
            await process_smartlink_notifications(bot)
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
    ("name", "Шаг 1/8: Как тебя зовут (имя/ник)?"),
    ("artist_name", "Шаг 2/8: Название проекта/артиста (как будет на площадках)?"),
    ("contact", "Шаг 3/8: Контакт для связи (Telegram @... или email)?"),
    ("genre", "Шаг 4/8: Жанр + 1–2 референса (через запятую)?"),
    ("links", "Шаг 5/8: Ссылки на материал (приватная ссылка/облако/SoundCloud)."),
    ("release_date", "Шаг 6/8: Планируемая дата релиза (если есть) или «нет»."),
    ("goal", "Шаг 7/8: Цель заявки (лейбл / дистрибуция / промо)?"),
    ("readiness", "Шаг 8/8: Готовность материала (демо / почти готов / готов)?"),
]

TEXT_FORM_STEPS = [
    ("genre", "Шаг 1/5: Жанр?"),
    ("refs", "Шаг 2/5: 1–2 референса (через запятую)?"),
    ("mood", "Шаг 3/5: Настроение/темы (1 строка)?"),
    ("city", "Шаг 4/5: Город/страна (опционально, можно пропустить)", True),
    ("link", "Шаг 5/5: Ссылка на трек/приват (опционально, можно пропустить)", True),
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
        f"Цель: {data.get('goal','')}\n"
        f"Готовность: {data.get('readiness','')}\n"
    )


def generate_pitch_texts(data: dict) -> list[str]:
    genre = data.get("genre", "жанр не указан")
    refs = data.get("refs") or data.get("ref") or data.get("reference") or data.get("genre")
    mood = data.get("mood", "настроение")
    city = data.get("city")
    link = data.get("link")

    base_lines = [
        f"Жанр: {genre}",
        f"Референсы: {refs}",
        f"Настроение/темы: {mood}",
    ]
    if city:
        base_lines.append(f"Город/страна: {city}")
    if link:
        base_lines.append(f"Ссылка: {link}")

    variants = []
    # короткий
    lines_short = [
        "Коротко о релизе:",
        *base_lines[:],
        "Готов к подборкам/редакторам",
    ]
    variants.append("\n".join(lines_short))

    # нейтральный
    lines_neutral = [
        "Новый трек для плейлистов:",
        *base_lines[:],
        "Фокус: чистый звук + понятная история",
        "Буду рад фидбеку/подборкам",
    ]
    variants.append("\n".join(lines_neutral))

    # дерзкий
    lines_bold = [
        "Чуть дерзкий питч:",
        f"{genre.capitalize()} с упором на вайб {mood}",
        f"Рефы: {refs}",
        "Хочу зайти в плейлисты и рекомендации",
    ]
    if city:
        lines_bold.append(f"Местная точка: {city}")
    if link:
        lines_bold.append(f"Слушать: {link}")
    lines_bold.append("Готов к ревью/подкастам")
    variants.append("\n".join(lines_bold))

    return variants

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

    if key == "goal":
        if len(value) < 3:
            return fail("Опиши цель: лейбл / дистрибуция / промо.")
        return True, value, None

    if key == "readiness":
        normalized = value.lower()
        allowed = {"демо", "почти готов", "готов"}
        if normalized not in allowed:
            return fail("Готовность: демо / почти готов / готов.")
        return True, normalized, None

    return True, value, None

# -------------------- Commands & buttons --------------------

@dp.message(CommandStart())
async def start(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    await maybe_send_update_notice(message, tg_id)

    exp = await get_experience(tg_id)
    menu_kb = await user_menu_keyboard(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await message.answer("ИСКРА активна. Жми кнопки меню снизу 👇", reply_markup=menu_kb)
        await message.answer(text, reply_markup=kb)
        return

    await message.answer("ИСКРА активна. Жми кнопки меню снизу 👇", reply_markup=menu_kb)

    focus_text, kb = await build_focus_for_user(tg_id, exp)
    await message.answer(focus_text, reply_markup=kb)

@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    await maybe_send_update_notice(message, tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await message.answer(text, reply_markup=await user_menu_keyboard(tg_id))
        return
    tasks_state = await get_tasks_state(tg_id)
    await message.answer("Меню снизу, держу фокус здесь:", reply_markup=await user_menu_keyboard(tg_id))
    important = await get_important_tasks(tg_id)
    text, kb = build_focus(tasks_state, exp, important)
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
            reply_markup=await user_menu_keyboard(tg_id),
        )
        return
    d = parse_date(parts[1])
    if not d:
        await message.answer("Не понял дату. Пример: /set_date 31.12.2025", reply_markup=await user_menu_keyboard(tg_id))
        return
    await set_release_date(tg_id, d.isoformat())
    await form_clear(tg_id)
    reminders = await get_reminders_enabled(tg_id)
    await message.answer(f"Ок. Дата релиза: {format_date_ru(d)}", reply_markup=build_timeline_kb(reminders, has_date=True))
    await message.answer(timeline_text(d, reminders), reply_markup=await user_menu_keyboard(tg_id))

@dp.message(Command("cancel"))
async def cancel(message: Message):
    tg_id = message.from_user.id
    await form_clear(tg_id)
    await message.answer("Ок, отменил.", reply_markup=await user_menu_keyboard(tg_id))

@dp.message(Command("broadcast_update"))
async def broadcast_update(message: Message, bot: Bot):
    if not ADMIN_TG_ID or str(message.from_user.id) != ADMIN_TG_ID:
        await message.answer("Нет доступа.")
        return
    await ensure_user(message.from_user.id, message.from_user.username)
    parts = message.text.split(maxsplit=1)
    url = (parts[1] if len(parts) == 2 else UPDATES_POST_URL).strip()
    if not url:
        await message.answer("Укажи ссылку: /broadcast_update <url> или задай UPDATES_POST_URL.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT tg_id, last_update_notified FROM users WHERE updates_opt_in=1"
        )
        users = await cur.fetchall()
        sent = skipped = errors = 0
        for tg_id, last_notified in users:
            if last_notified == url:
                skipped += 1
                continue
            try:
                await bot.send_message(tg_id, f"⚡️ Есть обновление ИСКРЫ. Подробнее: {url}")
                await set_last_update_notified(tg_id, url, db)
                sent += 1
            except TelegramForbiddenError:
                skipped += 1
            except Exception:
                errors += 1
            await asyncio.sleep(0.1)
        await db.commit()
    await message.answer(
        f"Рассылка завершена. Отправлено: {sent}. Пропущено/ошибок: {skipped + errors}.",
        reply_markup=await user_menu_keyboard(message.from_user.id)
    )

# Reply keyboard actions
@dp.message(F.text == "🎯 План")
async def rb_plan(message: Message):
    await plan_cmd(message)

@dp.message(F.text == "📦 Задачи по разделам")
async def rb_sections(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_sections_menu(tasks_state)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == "👤 Кабинеты")
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
    await message.answer(timeline_text(d, reminders), reply_markup=build_timeline_kb(reminders, has_date=bool(d)))

@dp.message(F.text == "⏰ Дата релиза")
async def rb_set_date_hint(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    await message.answer("Команда:\n/set_date ДД.ММ.ГГГГ\nПример:\n/set_date 31.12.2025", reply_markup=await user_menu_keyboard(tg_id))

@dp.message(F.text == "🔗 Ссылки")
async def rb_links(message: Message):
    await message.answer("🔗 Быстрые ссылки:", reply_markup=build_links_kb())


@dp.message(F.text == "🔗 Смарт-линки")
async def rb_smartlinks(message: Message):
    await message.answer("🔗 Смарт-линки — выбери действие:", reply_markup=smartlinks_menu_kb())


@dp.message(F.text == "🧠 Ожидания")
async def rb_expectations(message: Message):
    await message.answer(expectations_text(), reply_markup=await user_menu_keyboard(message.from_user.id))

@dp.message(F.text == "📰 Что нового")
async def rb_whats_new(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    if UPDATES_POST_URL:
        text = f"📰 Что нового: {UPDATES_POST_URL}"
    else:
        text = f"{UPDATES_CHANNEL_URL}\nПоследнее обновление — в закреплённом посте канала."
    await message.answer(text, reply_markup=await user_menu_keyboard(tg_id))

@dp.message(F.text.startswith("🔔 Обновления"))
async def rb_toggle_updates(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    enabled = await toggle_updates_opt_in(tg_id)
    reply = "Ок, обновления включены ✅" if enabled else "Ок, обновления выключены ❌"
    await message.answer(reply, reply_markup=await user_menu_keyboard(tg_id))

@dp.message(F.text == "🔄 Сброс")
async def rb_reset(message: Message):
    await message.answer("⚠️ Сбросить чеклист?", reply_markup=build_reset_menu_kb())

@dp.message(F.text == "🧾 Экспорт")
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
        reply_markup=await user_menu_keyboard(tg_id)
    )

# -------------------- Stars: DONATE --------------------


async def send_donate_invoice(message: Message, stars: int):
    prices = [LabeledPrice(label=f"Поддержка ИСКРЫ ({stars} ⭐)", amount=stars)]
    await message.answer_invoice(
        title="Поддержать ИСКРУ",
        description="Спасибо! Это помогает развивать бота и добавлять функции.",
        payload=f"donate_iskra_{stars}",
        provider_token="",
        currency="XTR",
        prices=prices,
    )

@dp.message(F.text == "💫 Поддержать ИСКРУ")
async def rb_donate(message: Message):
    await message.answer(
        "💫 Поддержать ИСКРУ звёздами\n\n"
        "Если бот помог — можешь поддержать проект.\n"
        "Выбери сумму (минимум 10 ⭐):",
        reply_markup=build_donate_menu_kb()
    )

@dp.callback_query(F.data == "donate:menu")
async def donate_menu_cb(callback):
    await safe_edit(
        callback.message,
        "💫 Поддержать ИСКРУ звёздами\n\nВыбери сумму (минимум 10 ⭐):",
        build_donate_menu_kb()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("donate:"))
async def donate_send_invoice_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    amount_s = callback.data.split(":")[1]
    allowed = {BRANDING_DISABLE_PRICE, EXPORT_UNLOCK_PRICE, SUPPORT_DONATE_PRICE}
    if not amount_s.isdigit() or int(amount_s) not in allowed:
        await callback.answer("Не понял сумму", show_alert=True)
        return

    stars = int(amount_s)
    await send_donate_invoice(callback.message, stars)
    await callback.answer("Ок")


@dp.callback_query(F.data == "donate:custom")
async def donate_custom_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "donate_custom")
    await callback.message.answer(
        f"Введи сумму доната в Stars (целое число от {DONATE_MIN_STARS} до {DONATE_MAX_STARS}).",
        reply_markup=await user_menu_keyboard(tg_id),
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery, bot: Bot):
    # обязательный шаг: без этого Telegram будет “крутить” оплату и ругаться, что бот не ответил
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    sp = message.successful_payment
    # sp.currency для Stars будет "XTR" :contentReference[oaicite:2]{index=2}
    if (sp.invoice_payload or "").startswith("donate_iskra_"):
        await message.answer("💫 Принято! Спасибо за поддержку ИСКРЫ 🤝", reply_markup=await user_menu_keyboard(message.from_user.id))
    elif sp.invoice_payload == "export_plan_25":
        tg_id = message.from_user.id
        await ensure_user(tg_id)
        tasks_state = await get_tasks_state(tg_id)
        await message.answer(build_export_text(tasks_state), reply_markup=await user_menu_keyboard(tg_id))
    elif sp.invoice_payload == "smartlink_export_unlock":
        tg_id = message.from_user.id
        await ensure_user(tg_id)
        await set_export_unlocked(tg_id, True)
        await message.answer(
            "Готово! Экспорт смарт-линков активирован для всех твоих ссылок.",
            reply_markup=await user_menu_keyboard(tg_id),
        )
    elif (sp.invoice_payload or "").startswith("smartlink_branding_"):
        tg_id = message.from_user.id
        await ensure_user(tg_id)
        payload = sp.invoice_payload or ""
        try:
            smartlink_id = int(payload.split("_")[-1])
        except Exception:
            smartlink_id = None
        if smartlink_id is not None:
            await update_smartlink_data(
                smartlink_id, tg_id, {"branding_disabled": True, "branding_paid": True}
            )
        await message.answer(
            "Готово! Брендинг ИСКРЫ отключён для этого смарт-линка. Если нужно — его можно снова включить бесплатно.",
            reply_markup=await user_menu_keyboard(tg_id),
        )

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
    await callback.message.answer("Ок. Меню снизу, держу фокус здесь:", reply_markup=await user_menu_keyboard(tg_id))
    text, kb = await build_focus_for_user(tg_id, "first" if exp == "first" else "old")

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
    tasks_state = await get_tasks_state(tg_id)
    was_done = tasks_state.get(task_id, 0) == 1
    await set_task_done(tg_id, task_id, 0 if was_done else 1)
    tasks_state = await get_tasks_state(tg_id)
    important = await get_important_tasks(tg_id)
    text, kb = build_focus(tasks_state, exp, important)
    await safe_edit(callback.message, text, kb)
    if not was_done:
        await maybe_send_qc_prompt(callback, tg_id, task_id)
    await callback.answer("Ок")

@dp.callback_query(F.data.startswith("help:"))
async def help_cb(callback):
    task_id = int(callback.data.split(":")[1])
    title = get_task_title(task_id)
    body = HELP.get(task_id, "Пояснение пока не добавлено.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]])
    await safe_edit(callback.message, f"❓ {title}\n\n{body}", kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("qc:"))
async def qc_answer_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    _, task_s, value = callback.data.split(":")
    task_id = int(task_s)
    qc = QC_PROMPTS.get(task_id)
    if not qc:
        await callback.answer("Не актуально")
        return
    await save_qc_check(tg_id, task_id, qc["key"], value)
    if value == "no":
        await callback.message.answer(f"Подсказка: {qc['tip']}", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer("Записал")

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
    kb = build_timeline_kb(reminders, has_date=bool(d))
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
    kb = build_timeline_kb(not current, has_date=bool(d))
    await safe_edit(callback.message, timeline_text(d, not current), kb)
    await callback.answer("Напоминания обновлены")

@dp.callback_query(F.data == "timeline:set_date")
async def timeline_set_date_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "release_date")
    await callback.message.answer(
        "Введи дату релиза в формате ДД.ММ.ГГГГ.\nПример: 31.12.2025\n\nОтмена: /cancel",
        reply_markup=await user_menu_keyboard(tg_id),
    )
    await callback.answer()

@dp.callback_query(F.data == "links")
async def links_cb(callback):
    await safe_edit(callback.message, "🔗 Быстрые ссылки:", build_links_kb())
    await callback.answer()


@dp.callback_query(F.data == "smartlinks:menu")
async def smartlinks_menu_cb(callback):
    await callback.message.answer("🔗 Смарт-линки — выбери действие:", reply_markup=smartlinks_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "smartlinks:create")
async def smartlinks_create_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await start_smartlink_import(callback.message, tg_id)
    await callback.answer()


@dp.callback_query(F.data == "smartlinks:help")
async def smartlinks_help_cb(callback):
    await callback.message.answer(smartlinks_help_text(), reply_markup=smartlinks_menu_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:list:"))
async def smartlinks_list_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    try:
        page = int(callback.data.split(":")[-1])
    except ValueError:
        page = 0
    await send_smartlink_list(callback.message, tg_id, page=page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:view:"))
async def smartlinks_view_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    page = int(parts[3])
    await show_smartlink_view(callback.message, tg_id, smartlink_id, page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:open:"))
async def smartlinks_open_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    page = int(parts[3])
    smartlink = await get_owned_smartlink(tg_id, smartlink_id)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    await resend_smartlink_card(callback.message, tg_id, smartlink, page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:delete:"))
async def smartlinks_delete_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    page = int(parts[3])
    await delete_smartlink(smartlink_id, tg_id)
    await callback.answer("Удалено")
    await send_smartlink_list(callback.message, tg_id, page=page)


@dp.callback_query(F.data.startswith("smartlinks:edit_menu:"))
async def smartlinks_edit_menu_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    page = int(parts[3])
    smartlink = await get_owned_smartlink(tg_id, smartlink_id)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    text = build_smartlink_view_text(smartlink)
    await callback.message.answer(
        text + "\n\nВыбери, что обновить:",
        reply_markup=smartlink_edit_menu_kb(
            smartlink_id, page, smartlink.get("branding_disabled"), smartlink.get("branding_paid")
        ),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:edit_field:"))
async def smartlinks_edit_field_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    page = int(parts[3])
    field = parts[4]
    smartlink = await get_owned_smartlink(tg_id, smartlink_id)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return

    await form_start(tg_id, "smartlink_edit")
    await form_set(tg_id, 0, {"smartlink_id": smartlink_id, "page": page, "field": field, "data": {}})

    if field == "title":
        await callback.message.answer(
            "Обновляем артиста и название.\nПришли артиста (минимум 2 символа).\n\n(Отмена: /cancel)",
            reply_markup=await user_menu_keyboard(tg_id),
        )
    elif field == "date":
        await callback.message.answer(
            "Пришли дату релиза в формате ДД.ММ.ГГГГ или напиши «нет».\n\n(Отмена: /cancel)",
            reply_markup=await user_menu_keyboard(tg_id),
        )
    elif field == "caption":
        await callback.message.answer(
            "Пришли новое описание (до 600 символов) или напиши «пропустить», чтобы очистить.\n\n(Отмена: /cancel)",
            reply_markup=await user_menu_keyboard(tg_id),
        )
    elif field == "cover":
        await callback.message.answer(
            "Пришли новую обложку (фото). Чтобы оставить без изменений — /cancel.",
            reply_markup=await user_menu_keyboard(tg_id),
        )
    else:
        await callback.answer("Не понял", show_alert=True)
        return
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:edit_links:"))
async def smartlinks_edit_links_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    page = int(parts[3])
    smartlink = await get_owned_smartlink(tg_id, smartlink_id)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    await callback.message.answer("Выбери платформу для обновления:", reply_markup=smartlink_links_menu_kb(smartlink_id, page))
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:branding_toggle:"))
async def smartlinks_branding_toggle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    page = int(parts[3])
    smartlink = await get_owned_smartlink(tg_id, smartlink_id)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    branding_paid = bool(smartlink.get("branding_paid"))

    if smartlink.get("branding_disabled"):
        await update_smartlink_data(smartlink_id, tg_id, {"branding_disabled": False})
        updated = await get_smartlink_by_id(smartlink_id)
        if updated:
            text = build_smartlink_view_text(updated)
            await callback.message.answer(
                text + "\n\nВыбери, что обновить:",
                reply_markup=smartlink_edit_menu_kb(
                    smartlink_id,
                    page,
                    updated.get("branding_disabled"),
                    updated.get("branding_paid"),
                ),
            )
        await callback.answer("Брендинг включён")
        return

    if branding_paid:
        await update_smartlink_data(smartlink_id, tg_id, {"branding_disabled": True})
        updated = await get_smartlink_by_id(smartlink_id)
        if updated:
            text = build_smartlink_view_text(updated)
            await callback.message.answer(
                text + "\n\nВыбери, что обновить:",
                reply_markup=smartlink_edit_menu_kb(
                    smartlink_id,
                    page,
                    updated.get("branding_disabled"),
                    updated.get("branding_paid"),
                ),
            )
        await callback.answer("Брендинг отключён")
        return

    await callback.message.answer(
        f"Отключить брендинг ИСКРЫ для этого смарт-линка?\nСтоимость: ⭐ {BRANDING_DISABLE_PRICE}",
        reply_markup=smartlink_branding_confirm_kb(smartlink_id, page),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:branding_cancel:"))
async def smartlinks_branding_cancel_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    smartlink_id = int(parts[2])
    page = int(parts[3])
    smartlink = await get_owned_smartlink(tg_id, smartlink_id)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    text = build_smartlink_view_text(smartlink)
    await callback.message.answer(
        text + "\n\nВыбери, что обновить:",
        reply_markup=smartlink_edit_menu_kb(
            smartlink_id,
            page,
            smartlink.get("branding_disabled"),
            smartlink.get("branding_paid"),
        ),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:branding_pay:"))
async def smartlinks_branding_pay_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    page = int(parts[3])
    smartlink = await get_owned_smartlink(tg_id, smartlink_id)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    if smartlink.get("branding_disabled"):
        await callback.answer("Брендинг уже отключён", show_alert=True)
        return
    if smartlink.get("branding_paid"):
        await update_smartlink_data(smartlink_id, tg_id, {"branding_disabled": True})
        updated = await get_smartlink_by_id(smartlink_id)
        if updated:
            text = build_smartlink_view_text(updated)
            await callback.message.answer(
                text + "\n\nВыбери, что обновить:",
                reply_markup=smartlink_edit_menu_kb(
                    smartlink_id,
                    page,
                    updated.get("branding_disabled"),
                    updated.get("branding_paid"),
                ),
            )
        await callback.answer("Брендинг уже оплачен")
        return

    prices = [LabeledPrice(label="Отключение брендинга ИСКРЫ", amount=BRANDING_DISABLE_PRICE)]
    await callback.message.answer_invoice(
        title="Отключить брендинг ИСКРЫ",
        description="Брендинг уберётся только у этого смарт-линка.",
        payload=f"smartlink_branding_{smartlink_id}",
        provider_token="",
        currency="XTR",
        prices=prices,
    )
    await callback.answer("Счёт на оплату")


@dp.callback_query(F.data.startswith("smartlinks:edit_link:"))
async def smartlinks_edit_link_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    page = int(parts[3])
    platform = parts[4]
    if platform not in {k for k, _ in SMARTLINK_BUTTON_ORDER}:
        await callback.answer("Платформа не поддерживается", show_alert=True)
        return
    smartlink = await get_owned_smartlink(tg_id, smartlink_id)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return

    await form_start(tg_id, "smartlink_edit")
    await form_set(
        tg_id,
        0,
        {"smartlink_id": smartlink_id, "page": page, "field": "link", "platform": platform, "data": {}},
    )
    label = platform_label(platform)
    await callback.message.answer(
        f"Пришли ссылку на {label}. Чтобы удалить площадку — напиши «удалить».\n\n(Отмена: /cancel)",
        reply_markup=await user_menu_keyboard(tg_id),
    )
    await callback.answer()
@dp.callback_query(F.data == "smartlink:open")
async def smartlink_open_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    existing = await get_latest_smartlink(tg_id)
    if not existing:
        inline_keyboard = []
        if SPOTIFY_UPC_ENABLED:
            inline_keyboard.append([InlineKeyboardButton(text="⚡ Автозаполнение по UPC", callback_data="smartlink:upc")])
        inline_keyboard.extend([
            [InlineKeyboardButton(text="⚡ Импорт по ссылке", callback_data="smartlink:import")],
            [InlineKeyboardButton(text="✏️ Создать вручную", callback_data="smartlink:new")],
            [InlineKeyboardButton(text="↩️ В фокус", callback_data="back_to_focus")],
        ])
        actions_kb = InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
        await callback.message.answer("Смартлинк не найден. Выбери действие:", reply_markup=actions_kb)
        await callback.answer()
        return

    allow_remind = smartlink_can_remind(existing)
    subscribed = await is_smartlink_subscribed(existing.get("id"), tg_id) if allow_remind else False
    await send_smartlink_photo(callback.message.bot, tg_id, existing, subscribed=subscribed, allow_remind=allow_remind)

    inline_keyboard = []
    if SPOTIFY_UPC_ENABLED:
        inline_keyboard.append([InlineKeyboardButton(text="⚡ Автозаполнение по UPC", callback_data="smartlink:upc")])
    inline_keyboard.extend([
        [InlineKeyboardButton(text="⚡ Импорт по ссылке", callback_data="smartlink:import")],
        [InlineKeyboardButton(text="✏️ Обновить", callback_data="smartlink:new")],
        [InlineKeyboardButton(text="✍️ Изменить текст", callback_data="smartlink:caption_edit")],
        [InlineKeyboardButton(text="↩️ В фокус", callback_data="back_to_focus")],
    ])
    manage_kb = InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
    await callback.message.answer("Можно обновить смартлинк:", reply_markup=manage_kb)
    await callback.answer()


@dp.callback_query(F.data == "smartlink:new")
async def smartlink_new_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await start_smartlink_form(callback.message, tg_id)
    await callback.answer()


@dp.callback_query(F.data == "smartlink:upc")
async def smartlink_upc_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    if not SPOTIFY_UPC_ENABLED:
        await callback.answer("Не задан SPOTIFY_CLIENT_ID/SECRET", show_alert=True)
        return

    await form_start(tg_id, "smartlink_upc")
    await callback.message.answer(
        "⚡ Автозаполнение по UPC. Пришли UPC (12–14 цифр).\n\n(Отмена: /cancel)",
        reply_markup=await user_menu_keyboard(tg_id),
    )
    await callback.answer()


@dp.callback_query(F.data == "smartlink:import")
async def smartlink_import_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await start_smartlink_import(callback.message, tg_id)
    await callback.answer()


@dp.callback_query(F.data == "smartlink:import_confirm")
async def smartlink_import_confirm_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    form = await form_get(tg_id)
    if not form or form.get("form_name") != "smartlink_import_review":
        await callback.answer("Нет данных для сохранения", show_alert=True)
        return
    data = form.get("data") or {}
    prefill = {
        "artist": data.get("artist") or pick_selected_metadata(data).get("artist"),
        "title": data.get("title") or pick_selected_metadata(data).get("title"),
        "cover_file_id": data.get("cover_file_id") or pick_selected_metadata(data).get("cover_file_id"),
        "release_date": data.get("release_date") or "",
        "caption_text": data.get("caption_text") or "",
    }
    links = data.get("links") or {}
    await start_smartlink_form(callback.message, tg_id, initial_links=links, prefill=prefill)
    await callback.answer("Проверь данные")


@dp.callback_query(F.data.startswith("smartlink:import_source:"))
async def smartlink_import_source_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    platform = callback.data.split(":")[-1]
    form = await form_get(tg_id)
    if not form or form.get("form_name") != "smartlink_import_review":
        await callback.answer("Нет данных", show_alert=True)
        return
    data = form.get("data") or {}
    metadata = data.get("metadata") or {}
    sources = metadata.get("sources") or {}
    if platform not in sources:
        await callback.answer("Нет такого источника", show_alert=True)
        return
    metadata["preferred_source"] = platform
    data["metadata"] = metadata
    data["preferred_source"] = platform
    await form_set(tg_id, 0, data)
    latest_stub = {
        "artist": data.get("artist", ""),
        "title": data.get("title", ""),
        "release_date": data.get("release_date", ""),
        "caption_text": data.get("caption_text", ""),
        "cover_file_id": data.get("cover_file_id", ""),
    }
    await show_import_confirmation(callback.message, tg_id, data.get("links") or {}, metadata, latest=latest_stub)
    await callback.answer("Источник обновлён")


@dp.callback_query(F.data == "smartlink:import_edit")
async def smartlink_import_edit_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    form = await form_get(tg_id)
    data = (form or {}).get("data") or {}
    if not data:
        await start_smartlink_form(callback.message, tg_id, initial_links={})
        await callback.answer()
        return
    await start_prefill_editor(callback.message, tg_id, data)
    await callback.answer()


@dp.callback_query(F.data == "smartlink:import_cancel")
async def smartlink_import_cancel_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_clear(tg_id)
    await callback.message.answer("Ок, отменил импорт.", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlink:prefill_edit:"))
async def smartlink_prefill_field_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    form = await form_get(tg_id)
    if not form or form.get("form_name") != "smartlink_prefill_edit":
        await callback.answer("Нет данных", show_alert=True)
        return
    field = callback.data.split(":")[-1]
    data = form.get("data") or {}
    if field not in {"artist", "title", "cover"}:
        await callback.answer("Неизвестно", show_alert=True)
        return
    data["pending"] = field
    await form_set(tg_id, 1, data)
    if field == "cover":
        await callback.message.answer("Пришли новую обложку фото.", reply_markup=await user_menu_keyboard(tg_id))
    elif field == "artist":
        await callback.message.answer("Введи артиста:", reply_markup=await user_menu_keyboard(tg_id))
    elif field == "title":
        await callback.message.answer("Введи название релиза:", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer()


@dp.callback_query(F.data == "smartlink:prefill_continue")
async def smartlink_prefill_continue_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    form = await form_get(tg_id)
    if not form or form.get("form_name") not in {"smartlink_prefill_edit", "smartlink_import_review"}:
        await callback.answer("Нет данных", show_alert=True)
        return
    data = form.get("data") or {}
    selected_meta = pick_selected_metadata(data)
    prefill = {
        "artist": data.get("artist") or selected_meta.get("artist"),
        "title": data.get("title") or selected_meta.get("title"),
        "cover_file_id": data.get("cover_file_id") or selected_meta.get("cover_file_id"),
        "release_date": data.get("release_date") or "",
        "caption_text": data.get("caption_text") or "",
    }
    await start_smartlink_form(callback.message, tg_id, initial_links=data.get("links") or {}, prefill=prefill)
    await callback.answer("Давай сохраним")


@dp.callback_query(F.data == "smartlink:caption_edit")
async def smartlink_caption_edit_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    existing = await get_latest_smartlink(tg_id)
    if not existing:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    await form_start(tg_id, "smartlink_caption_edit")
    await form_set(tg_id, 0, {"smartlink_id": existing.get("id"), "caption_text": existing.get("caption_text", "")})
    await callback.message.answer(
        smartlink_step_prompt(4) + "\n\n(Отмена: /cancel)",
        reply_markup=smartlink_step_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlink:upc_pick:"))
async def smartlink_upc_pick_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Не понял выбор", show_alert=True)
        return

    form = await form_get(tg_id)
    if not form or form.get("form_name") != "smartlink_upc":
        await callback.answer("Запрос устарел, пришли UPC снова", show_alert=True)
        return

    candidates = (form.get("data") or {}).get("candidates") or []
    idx = int(parts[2])
    if idx < 0 or idx >= len(candidates):
        await callback.answer("Запрос устарел, пришли UPC снова", show_alert=True)
        return

    await apply_spotify_upc_selection(callback.message, tg_id, candidates[idx])
    await callback.answer("Готово")


@dp.callback_query(F.data == "smartlink:upc_cancel")
async def smartlink_upc_cancel_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_clear(tg_id)
    await callback.message.answer("Ок, не сохраняю.", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlink:toggle:"))
async def smartlink_toggle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    smartlink = await get_smartlink_by_id(smartlink_id)
    if not smartlink:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return
    if not smartlink_can_remind(smartlink):
        await callback.answer("Релиз уже сегодня или прошёл", show_alert=True)
        return

    current = await is_smartlink_subscribed(smartlink_id, tg_id)
    await set_smartlink_subscription(smartlink_id, tg_id, not current)
    allow_remind = smartlink_can_remind(smartlink)
    kb = build_smartlink_buttons(smartlink, subscribed=not current, can_remind=allow_remind)
    caption = build_smartlink_caption(smartlink)
    await safe_edit_caption(callback.message, caption, kb)
    await callback.answer("Напомню" if not current else "Напоминание выключено")


@dp.callback_query(F.data.in_({"smartlink:caption_skip", "smartlink:skip"}))
async def smartlink_skip_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    form = await form_get(tg_id)
    if not form:
        await callback.answer("Нет шага", show_alert=True)
        return
    form_name = form.get("form_name")
    data = form.get("data") or {}
    if form_name == "smartlink":
        step = int(form.get("step", 0))
        data["links"] = data.get("links") or {}
        total_steps = 5 + len(SMARTLINK_PLATFORMS)
        if step >= total_steps:
            await callback.answer("Шагов больше нет", show_alert=True)
            return
        if step == 0:
            data["artist"] = ""
        elif step == 1:
            data["title"] = ""
        elif step == 2:
            data["release_date"] = ""
        elif step == 3:
            data["cover_file_id"] = ""
        elif step == 4:
            data["caption_text"] = ""
        else:
            idx = step - 5
            if idx < 0 or idx >= len(SMARTLINK_PLATFORMS):
                await form_clear(tg_id)
                await callback.answer("Нет шага", show_alert=True)
                return
            data["links"][SMARTLINK_PLATFORMS[idx][0]] = ""

        next_step = skip_prefilled_smartlink_steps(step + 1, data)
        total_steps = 5 + len(SMARTLINK_PLATFORMS)
        if next_step < total_steps:
            await form_set(tg_id, next_step, data)
            await callback.message.answer(
                smartlink_step_prompt(next_step) + "\n\n(Отмена: /cancel)",
                reply_markup=smartlink_step_kb(),
            )
        else:
            await finalize_smartlink_form(callback.message, tg_id, data)
        await callback.answer("Пропустил")
        return

    if form_name == "smartlink_caption_edit":
        smartlink_id = data.get("smartlink_id")
        if not smartlink_id:
            await callback.answer("Смартлинк не найден", show_alert=True)
            await form_clear(tg_id)
            return
        await apply_caption_update(callback.message, tg_id, smartlink_id, "")
        await callback.answer("Пропустил")
        return

    await callback.answer("Нет шага", show_alert=True)


@dp.callback_query(F.data == "smartlink:cancel")
async def smartlink_cancel_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_clear(tg_id)
    await callback.message.answer("Ок, отменил.", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:copy:"))
async def smartlinks_copy_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Не понял", show_alert=True)
        return

    smartlink_id = int(parts[2])
    smartlink = await get_smartlink_by_id(smartlink_id)
    if not smartlink:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return

    text = build_copy_links_text(smartlink)
    await callback.message.answer(text)
    await callback.answer("Готово")


@dp.callback_query(F.data.startswith("smartlinks:exportfmt:"))
async def smartlinks_export_format_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Не понял", show_alert=True)
        return

    smartlink_id = int(parts[2])
    page = int(parts[3]) if parts[3].lstrip("-").isdigit() else -1
    variant = parts[4]
    smartlink = await get_smartlink_by_id(smartlink_id)
    if not smartlink or smartlink.get("owner_tg_id") != tg_id:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    if not await get_export_unlocked(tg_id):
        await callback.message.answer(
            f"Открыть экспорт смарт-линка (Telegram/VK/PR/ссылки)?\nСтоимость: ⭐ {EXPORT_UNLOCK_PRICE}",
            reply_markup=smartlink_export_paywall_kb(smartlink_id, page),
        )
        await callback.answer()
        return

    export_text = build_smartlink_export_text(smartlink, variant)
    if not export_text.strip():
        await callback.message.answer("Нет данных для экспорта.")
        await callback.answer()
        return

    await callback.message.answer(export_text)
    await callback.answer("Готово")


@dp.callback_query(F.data.startswith("smartlinks:export_back:"))
async def smartlinks_export_back_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return

    smartlink_id = int(parts[2])
    page = int(parts[3]) if parts[3].lstrip("-").isdigit() else -1
    try:
        await callback.message.delete()
    except Exception:
        pass

    if page >= 0:
        await show_smartlink_view(callback.message, tg_id, smartlink_id, page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:export_pay:"))
async def smartlinks_export_pay_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_id = int(parts[2])
    page = int(parts[3]) if parts[3].lstrip("-").isdigit() else -1
    smartlink = await get_owned_smartlink(tg_id, smartlink_id)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    if await get_export_unlocked(tg_id):
        await callback.answer("Экспорт уже активирован", show_alert=True)
        return

    prices = [LabeledPrice(label="Экспорт смарт-линков", amount=EXPORT_UNLOCK_PRICE)]
    await callback.message.answer_invoice(
        title="Экспорт смарт-линка",
        description="Доступ к экспортам Telegram/VK/PR/ссылки для всех смарт-линков.",
        payload="smartlink_export_unlock",
        provider_token="",
        currency="XTR",
        prices=prices,
    )
    await callback.answer("Счёт на оплату")


@dp.callback_query(F.data.startswith("smartlinks:export_cancel:"))
async def smartlinks_export_cancel_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    smartlink_id = int(parts[2])
    page = int(parts[3]) if parts[3].lstrip("-").isdigit() else -1
    try:
        await callback.message.delete()
    except Exception:
        pass
    if page >= 0:
        await show_smartlink_view(callback.message, tg_id, smartlink_id, page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:export:"))
async def smartlinks_export_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) not in {3, 4}:
        await callback.answer("Не понял", show_alert=True)
        return

    smartlink_id = int(parts[2])
    page = int(parts[3]) if len(parts) == 4 and parts[3].lstrip("-").isdigit() else -1
    smartlink = await get_smartlink_by_id(smartlink_id)
    if not smartlink or smartlink.get("owner_tg_id") != tg_id:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    if not await get_export_unlocked(tg_id):
        await callback.message.answer(
            f"Открыть экспорт смарт-линка (Telegram/VK/PR/ссылки)?\nСтоимость: ⭐ {EXPORT_UNLOCK_PRICE}",
            reply_markup=smartlink_export_paywall_kb(smartlink_id, page),
        )
        await callback.answer()
        return

    header = build_smartlink_view_text(smartlink)
    await callback.message.answer(
        header + "\n\nВыбери формат:", reply_markup=smartlink_export_kb(smartlink_id, page)
    )
    await callback.answer()


@dp.callback_query(F.data == "links:lyrics")
async def links_lyrics_cb(callback):
    await safe_edit(callback.message, lyrics_sync_text(), build_links_kb())
    await callback.answer()

@dp.callback_query(F.data == "links:ugc")
async def links_ugc_cb(callback):
    await safe_edit(callback.message, ugc_tip_text(), build_links_kb())
    await callback.answer()

@dp.callback_query(F.data == "texts:start")
async def texts_start_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "pitch_texts")
    await form_set(tg_id, 0, {})
    await callback.message.answer("✍️ Тексты для питчинга.\n\n" + TEXT_FORM_STEPS[0][1] + "\n\n(Отмена: /cancel)", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer()

@dp.callback_query(F.data.startswith("texts:copy:"))
async def texts_copy_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    idx = int(callback.data.split(":")[2])
    form = await form_get(tg_id)
    if not form or form.get("form_name") not in {"pitch_texts_ready"}:
        await callback.answer("Нет готовых текстов", show_alert=True)
        return
    texts = form.get("data", {}).get("texts", [])
    if idx < 0 or idx >= len(texts):
        await callback.answer("Нет варианта", show_alert=True)
        return
    await callback.message.answer(texts[idx], reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer("Скопируй текст")

@dp.callback_query(F.data == "reset_menu")
async def reset_menu_cb(callback):
    await safe_edit(callback.message, "🔄 Сброс", build_reset_menu_kb())
    await callback.answer()

@dp.callback_query(F.data == "important:list")
async def important_list_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    important = await get_important_tasks(tg_id)
    text, kb = build_important_screen(tasks_state, important)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("important:toggle:"))
async def important_toggle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    task_id = int(callback.data.split(":")[2])
    important = await get_important_tasks(tg_id)
    if task_id in important:
        await remove_important_task(tg_id, task_id)
    else:
        await add_important_task(tg_id, task_id)
    important = await get_important_tasks(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    exp = await get_experience(tg_id)
    if callback.message.text and callback.message.text.startswith("🔥 Важное"):
        text, kb = build_important_screen(tasks_state, important)
    else:
        text, kb = build_focus(tasks_state, exp, important)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Обновил")

@dp.callback_query(F.data.startswith("important:focus:"))
async def important_focus_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    task_id = int(callback.data.split(":")[2])
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    text, kb = await build_focus_for_user(tg_id, exp, focus_task_id=task_id)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Готово")

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
    text, kb = await build_focus_for_user(tg_id, exp)
    await safe_edit(callback.message, text, kb)
    await callback.message.answer("Прогресс очищен.", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer("Сбросил")

@dp.callback_query(F.data == "reset_all_yes")
async def reset_all_yes_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    await reset_all_data(tg_id)
    text, kb = await build_focus_for_user(tg_id, exp)
    await safe_edit(callback.message, text, kb)
    await callback.message.answer("Сбросил всё: чеклист, дату и напоминания.", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer("Полный сброс")

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
    text, kb = await build_focus_for_user(tg_id, exp)
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
        reply_markup=await user_menu_keyboard(tg_id)
    )
    await callback.answer()

# -------------------- Form router --------------------

@dp.message()
async def any_message_router(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)

    form = await form_get(tg_id)
    txt = (message.text or "").strip()

    if not form:
        if not txt or txt.startswith("/"):
            return

        exp = await get_experience(tg_id)
        if exp == "unknown":
            lower = txt.lower()
            inferred: str | None = None
            if "уже" in lower or "не первый" in lower:
                inferred = "old"
            elif "перв" in lower:
                inferred = "first"

            if not inferred:
                text, kb = experience_prompt()
                await message.answer(text, reply_markup=kb)
                return

            await set_experience(tg_id, inferred)
            await message.answer("Ок. Меню снизу, держу фокус здесь:", reply_markup=await user_menu_keyboard(tg_id))
            focus_text, kb = await build_focus_for_user(tg_id, inferred)
            await message.answer(focus_text, reply_markup=kb)
            return
        return

    form_name = form.get("form_name")
    if form_name == "donate_custom":
        if not txt.isdigit():
            await message.answer(
                "Нужна целая сумма в Stars. Попробуй ещё раз.",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return
        stars = int(txt)
        if stars < DONATE_MIN_STARS or stars > DONATE_MAX_STARS:
            await message.answer(
                f"Минимум {DONATE_MIN_STARS} ⭐. Максимум {DONATE_MAX_STARS} ⭐.",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return
        await form_clear(tg_id)
        await send_donate_invoice(message, stars)
        return

    if form_name == "smartlink_upc":
        digits = re.sub(r"\D", "", txt)
        if not re.fullmatch(r"\d{12,14}", digits):
            await message.answer(
                "Нужен UPC: 12–14 цифр. Пришли номер ещё раз.\n\n(Отмена: /cancel)",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return

        results = await spotify_search_upc(digits)
        if not results:
            await message.answer(
                "Не нашёл, попробуй BandLink или вставь ссылки вручную. Можешь прислать другой UPC.",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return

        await form_set(tg_id, 1, {"upc": digits, "candidates": results})
        if len(results) == 1:
            candidate = results[0]
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Подтвердить", callback_data="smartlink:upc_pick:0")],
                    [InlineKeyboardButton(text="Отмена", callback_data="smartlink:upc_cancel")],
                ]
            )
            await message.answer(
                f"Нашёл: {candidate.get('artist') or 'Без артиста'} — {candidate.get('title') or ''}\n"
                f"{candidate.get('spotify_url', '')}\n\nПодтверждаешь?",
                reply_markup=kb,
            )
        else:
            rows = []
            for idx, candidate in enumerate(results):
                label = f"{candidate.get('artist') or ''} — {candidate.get('title') or ''}".strip(" —")
                if len(label) > 60:
                    label = label[:57] + "…"
                if not label:
                    label = f"Вариант {idx + 1}"
                rows.append([InlineKeyboardButton(text=label, callback_data=f"smartlink:upc_pick:{idx}")])
            rows.append([InlineKeyboardButton(text="Отмена", callback_data="smartlink:upc_cancel")])
            await message.answer(
                "Выбери релиз по UPC:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        return

    if form_name == "smartlink_import":
        if not re.match(r"https?://", txt):
            await message.answer(
                "Нужна ссылка (http/https).\n\nОтмена: /cancel",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return

        data = form.get("data") or {}
        existing_links = data.get("links") or {}
        existing_metadata = data.get("metadata") or {}
        bandlink_help_shown = bool(data.get("bandlink_help_shown"))
        low_links_hint_shown = bool(data.get("low_links_hint_shown"))

        detected_platform = detect_platform(txt) or ""
        if detected_platform and detected_platform != "bandlink":
            await message.answer("Принял ссылку, пытаюсь найти релиз…", reply_markup=await user_menu_keyboard(tg_id))

        links, metadata = await resolve_links(txt)

        merged_links = dict(existing_links)
        added_platforms: list[str] = []
        for platform_key, url in links.items():
            if platform_key not in merged_links or merged_links[platform_key] != url:
                merged_links[platform_key] = url
                added_platforms.append(platform_key)

        merged_metadata = merge_metadata(existing_metadata, metadata)

        key_links_count = sum(1 for p in KEY_PLATFORM_SET if merged_links.get(p))

        if added_platforms:
            added_labels = [platform_label(p) for p in added_platforms]
            total_added = len(merged_links)
            await message.answer(
                f"Добавил площадки: {', '.join(added_labels)}. Всего: {total_added}",
                reply_markup=await user_menu_keyboard(tg_id),
            )

        total = len(merged_links)
        latest = await get_latest_smartlink(tg_id)
        temp_data = {"metadata": merged_metadata, "preferred_source": merged_metadata.get("preferred_source")}
        selected_meta = pick_selected_metadata(temp_data)
        cover_source = selected_meta.get("cover_url") or (merged_metadata or {}).get("cover_url") or ""
        cover_file_id = ""
        if cover_source:
            try:
                input_file = await fetch_cover_file(cover_source)
                if input_file:
                    preview = await message.answer_photo(photo=input_file, caption="Загрузил обложку…")
                    cover_file_id = preview.photo[-1].file_id if preview.photo else ""
                    await preview.delete()
            except Exception as e:
                print(f"[cover] failed to auto download: {e}")

        ready_for_autofill = (
            not (merged_metadata or {}).get("conflict")
            and bool(selected_meta.get("artist"))
            and bool(selected_meta.get("title"))
            and bool(cover_file_id)
            and total >= 2
        )

        if ready_for_autofill:
            data.update(
                {
                    "artist": selected_meta.get("artist", ""),
                    "title": selected_meta.get("title", ""),
                    "cover_file_id": cover_file_id,
                    "links": merged_links,
                    "metadata": merged_metadata,
                    "preferred_source": merged_metadata.get("preferred_source"),
                    "release_date": (latest or {}).get("release_date", ""),
                    "caption_text": (latest or {}).get("caption_text", ""),
                }
            )
            await form_start(tg_id, "smartlink_prefill_edit")
            await form_set(tg_id, 0, data)
            platforms_text = ", ".join(sorted(merged_links.keys())) if merged_links else "—"
            summary_lines = [
                "Нашёл ссылки и данные релиза:",
                f"{data.get('artist') or 'Без артиста'} — {data.get('title') or 'Без названия'}",
                f"Площадки: {platforms_text}",
                "Карточку заполнил автоматически.",
            ]
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Продолжить", callback_data="smartlink:prefill_continue")],
                    [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="smartlink:import_edit")],
                    [InlineKeyboardButton(text="Отмена", callback_data="smartlink:import_cancel")],
                ]
            )
            try:
                await message.answer_photo(photo=cover_file_id, caption="\n".join(summary_lines), reply_markup=kb)
            except Exception:
                await message.answer("\n".join(summary_lines), reply_markup=kb)
            return

        meta_complete = bool((merged_metadata or {}).get("artist") and (merged_metadata or {}).get("title"))

        if key_links_count < 3 and not low_links_hint_shown:
            data["low_links_hint_shown"] = True
            await form_set(tg_id, form.get("step", 0) or 0, data)
            await message.answer(
                "Ссылок мало. Можешь прислать Яндекс или VK — доберу остальные.",
                reply_markup=await user_menu_keyboard(tg_id),
            )

        if total >= 2 and meta_complete:
            await show_import_confirmation(message, tg_id, merged_links, merged_metadata, latest)
            return

        if meta_complete:
            await show_import_confirmation(message, tg_id, merged_links, merged_metadata, latest)
            return

        if total >= 2:
            await show_import_confirmation(message, tg_id, merged_links, merged_metadata, latest)
            return

        data.update({
            "links": merged_links,
            "metadata": merged_metadata,
            "bandlink_help_shown": bandlink_help_shown,
            "low_links_hint_shown": data.get("low_links_hint_shown", False),
        })
        await form_set(tg_id, form.get("step", 0) or 0, data)

        failure = total <= 1 and not meta_complete
        if detected_platform == "bandlink" and not bandlink_help_shown and failure:
            data["bandlink_help_shown"] = True
            await form_set(tg_id, form.get("step", 0) or 0, data)
            await message.answer(
                RESOLVER_FALLBACK_TEXT,
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return

        await message.answer(
            "Не нашёл остальные площадки, пришли ссылку другой платформы.",
            reply_markup=await user_menu_keyboard(tg_id),
        )
        return

    if form_name == "smartlink":
        step = int(form.get("step", 0))
        data = form.get("data") or {}
        links = data.get("links") or {}
        data["links"] = links
        total_steps = 5 + len(SMARTLINK_PLATFORMS)
        skip_text = txt.lower() in {"пропустить", "skip"}

        if step == 0:
            if skip_text:
                data["artist"] = ""
            else:
                if len(txt) < 2:
                    await message.answer(
                        smartlink_step_prompt(step) + "\n\n(Отмена: /cancel)",
                        reply_markup=smartlink_step_kb(),
                    )
                    return
                data["artist"] = txt
        elif step == 1:
            if skip_text:
                data["title"] = ""
            else:
                if len(txt) < 1:
                    await message.answer(
                        smartlink_step_prompt(step) + "\n\n(Отмена: /cancel)",
                        reply_markup=smartlink_step_kb(),
                    )
                    return
                data["title"] = txt
        elif step == 2:
            if skip_text:
                data["release_date"] = ""
            else:
                d = parse_date(txt)
                if not d:
                    await message.answer(
                        "Не понял дату. Формат: ДД.ММ.ГГГГ\n\n" + smartlink_step_prompt(step),
                        reply_markup=smartlink_step_kb(),
                    )
                    return
                data["release_date"] = d.isoformat()
        elif step == 3:
            if skip_text:
                data["cover_file_id"] = ""
            else:
                if not message.photo:
                    await message.answer(
                        "Пришли фото для обложки.\n\n" + smartlink_step_prompt(step),
                        reply_markup=smartlink_step_kb(),
                    )
                    return
                data["cover_file_id"] = message.photo[-1].file_id
        elif step == 4:
            if skip_text:
                data["caption_text"] = ""
            else:
                if not txt:
                    await message.answer(
                        smartlink_step_prompt(step) + "\n\n(Отмена: /cancel)",
                        reply_markup=smartlink_step_kb(),
                    )
                    return
                if len(txt) > 600:
                    await message.answer(
                        "Максимум 600 символов. Сократи текст и отправь снова.\n\n" + smartlink_step_prompt(step),
                        reply_markup=smartlink_step_kb(),
                    )
                    return
                data["caption_text"] = txt
        else:
            idx = step - 5
            if idx < 0 or idx >= len(SMARTLINK_PLATFORMS):
                await form_clear(tg_id)
                return
            if skip_text:
                links[SMARTLINK_PLATFORMS[idx][0]] = ""
            else:
                if not txt:
                    await message.answer(
                        smartlink_step_prompt(step) + "\n\n(Отмена: /cancel)",
                        reply_markup=smartlink_step_kb(),
                    )
                    return
                if not re.match(r"https?://", txt):
                    await message.answer("Нужна ссылка или «Пропустить».", reply_markup=smartlink_step_kb())
                    return
                links[SMARTLINK_PLATFORMS[idx][0]] = txt

        step += 1
        step = skip_prefilled_smartlink_steps(step, data)
        if step < total_steps:
            await form_set(tg_id, step, data)
            await message.answer(
                smartlink_step_prompt(step) + "\n\n(Отмена: /cancel)",
                reply_markup=smartlink_step_kb(),
            )
            return

        await finalize_smartlink_form(message, tg_id, data)
        return

    if form_name == "smartlink_prefill_edit":
        data = form.get("data") or {}
        pending = data.get("pending")
        if pending == "artist":
            if len(txt) < 2:
                await message.answer("Минимум 2 символа. Попробуй ещё раз.", reply_markup=await user_menu_keyboard(tg_id))
                return
            data["artist"] = txt
        elif pending == "title":
            if len(txt) < 1:
                await message.answer("Нужно название релиза.", reply_markup=await user_menu_keyboard(tg_id))
                return
            data["title"] = txt
        elif pending == "cover":
            if not message.photo:
                await message.answer("Пришли фото.", reply_markup=await user_menu_keyboard(tg_id))
                return
            data["cover_file_id"] = message.photo[-1].file_id
        else:
            await start_prefill_editor(message, tg_id, data)
            return
        data.pop("pending", None)
        await form_set(tg_id, 0, data)
        await start_prefill_editor(message, tg_id, data)
        return

    if form_name == "smartlink_caption_edit":
        data = form.get("data") or {}
        smartlink_id = data.get("smartlink_id")
        if not smartlink_id:
            await form_clear(tg_id)
            await message.answer("Смартлинк не найден.", reply_markup=await user_menu_keyboard(tg_id))
            return
        if not txt:
            await message.answer(smartlink_step_prompt(4) + "\n\n(Отмена: /cancel)", reply_markup=smartlink_step_kb())
            return
        if txt.lower() in {"пропустить", "skip"}:
            caption_text = ""
        else:
            if len(txt) > 600:
                await message.answer(
                    "Максимум 600 символов. Сократи текст и отправь снова.\n\n" + smartlink_step_prompt(4),
                    reply_markup=smartlink_step_kb(),
                )
                return
            caption_text = txt
        await apply_caption_update(message, tg_id, smartlink_id, caption_text)
        return

    if form_name == "smartlink_edit":
        info = form.get("data") or {}
        smartlink_id = info.get("smartlink_id")
        page = int(info.get("page") or 0)
        field = info.get("field")
        smartlink = await get_owned_smartlink(tg_id, smartlink_id) if smartlink_id else None
        if not smartlink or not field:
            await form_clear(tg_id)
            await message.answer("Смартлинк не найден.", reply_markup=await user_menu_keyboard(tg_id))
            return

        step = int(form.get("step", 0))
        updates: dict = {}

        if field == "title":
            if step == 0:
                if len(txt) < 2:
                    await message.answer(
                        "Минимум 2 символа. Пришли артиста ещё раз.\n\n(Отмена: /cancel)",
                        reply_markup=await user_menu_keyboard(tg_id),
                    )
                    return
                info_data = info.get("data") or {}
                info_data["artist"] = txt
                info["data"] = info_data
                await form_set(tg_id, 1, info)
                await message.answer(
                    "Теперь пришли название релиза.\n\n(Отмена: /cancel)",
                    reply_markup=await user_menu_keyboard(tg_id),
                )
                return
            info_data = info.get("data") or {}
            artist = info_data.get("artist") or smartlink.get("artist")
            if len(txt) < 1:
                await message.answer(
                    "Нужно название релиза.\n\n(Отмена: /cancel)",
                    reply_markup=await user_menu_keyboard(tg_id),
                )
                return
            updates["artist"] = artist
            updates["title"] = txt
        elif field == "date":
            if txt.lower() in {"нет", "пропустить", "skip"}:
                updates["release_date"] = ""
            else:
                d = parse_date(txt)
                if not d:
                    await message.answer(
                        "Не понял дату. Формат: ДД.ММ.ГГГГ или напиши «нет».\n\n(Отмена: /cancel)",
                        reply_markup=await user_menu_keyboard(tg_id),
                    )
                    return
                updates["release_date"] = d.isoformat()
        elif field == "caption":
            if txt.lower() in {"пропустить", "skip"}:
                updates["caption_text"] = ""
            else:
                if len(txt) > 600:
                    await message.answer(
                        "Максимум 600 символов. Сократи текст.\n\n(Отмена: /cancel)",
                        reply_markup=await user_menu_keyboard(tg_id),
                    )
                    return
                updates["caption_text"] = txt
        elif field == "cover":
            if not message.photo:
                await message.answer(
                    "Пришли фото для обложки.\n\n(Отмена: /cancel)",
                    reply_markup=await user_menu_keyboard(tg_id),
                )
                return
            updates["cover_file_id"] = message.photo[-1].file_id
        elif field == "link":
            platform = info.get("platform")
            links = smartlink.get("links") or {}
            lower = txt.lower()
            if lower in {"удалить", "delete", "remove", "пропустить", "skip"}:
                links.pop(platform, None)
            else:
                if not re.match(r"https?://", txt):
                    await message.answer(
                        "Нужна ссылка вида https://... или слово «удалить».\n\n(Отмена: /cancel)",
                        reply_markup=await user_menu_keyboard(tg_id),
                    )
                    return
                links[platform] = txt
            updates["links"] = links
        else:
            await form_clear(tg_id)
            await message.answer("Не понял запрос.", reply_markup=await user_menu_keyboard(tg_id))
            return

        if updates:
            await update_smartlink_data(smartlink_id, tg_id, updates)
        await form_clear(tg_id)
        updated = await get_smartlink_by_id(smartlink_id)
        if updated:
            await resend_smartlink_card(message, tg_id, updated, page)
        else:
            await message.answer("Смартлинк обновлён.", reply_markup=await user_menu_keyboard(tg_id))
        return

    if not txt or txt.startswith("/"):
        return

    if form_name == "release_date":
        d = parse_date(txt)
        if not d:
            await message.answer(
                "Не понял дату. Формат: ДД.ММ.ГГГГ. Пример: 31.12.2025\n\nПопробуй ещё раз:",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return
        await set_release_date(tg_id, d.isoformat())
        await form_clear(tg_id)
        reminders = await get_reminders_enabled(tg_id)
        await message.answer(
            f"Ок. Дата релиза: {format_date_ru(d)}",
            reply_markup=build_timeline_kb(reminders, has_date=True),
        )
        await message.answer(timeline_text(d, reminders), reply_markup=await user_menu_keyboard(tg_id))
        return

    if form_name == "pitch_texts":
        step = int(form["step"])
        data = form["data"]
        if step < 0 or step >= len(TEXT_FORM_STEPS):
            await form_clear(tg_id)
            await message.answer("Форма сброшена. Нажми «✍️ Тексты» ещё раз.", reply_markup=await user_menu_keyboard(tg_id))
            return
        key, prompt, *rest = TEXT_FORM_STEPS[step]
        optional = rest[0] if rest else False
        value = txt.strip()
        if not value and optional:
            data[key] = ""
        elif len(value) < 2:
            await message.answer(prompt + "\n\n(Отмена: /cancel)", reply_markup=await user_menu_keyboard(tg_id))
            return
        else:
            data[key] = value

        step += 1
        if step < len(TEXT_FORM_STEPS):
            await form_set(tg_id, step, data)
            await message.answer(TEXT_FORM_STEPS[step][1] + "\n\n(Отмена: /cancel)", reply_markup=await user_menu_keyboard(tg_id))
            return

        texts = generate_pitch_texts(data)
        await form_start(tg_id, "pitch_texts_ready")
        await form_set(tg_id, 0, {"texts": texts})

        for idx, text in enumerate(texts, start=1):
            await message.answer(f"Вариант {idx}:\n{text}", reply_markup=await user_menu_keyboard(tg_id))
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 Скопировать 1", callback_data="texts:copy:0")],
                [InlineKeyboardButton(text="📋 Скопировать 2", callback_data="texts:copy:1")],
                [InlineKeyboardButton(text="📋 Скопировать 3", callback_data="texts:copy:2")],
                [InlineKeyboardButton(text="↩️ В фокус", callback_data="back_to_focus")],
            ]
        )
        await message.answer("Выбери, что скопировать:", reply_markup=kb)
        return

    if form_name == "pitch_texts_ready":
        return

    if form_name != "label_submit":
        return

    step = int(form["step"])
    data = form["data"]

    if step < 0 or step >= len(LABEL_FORM_STEPS):
        await form_clear(tg_id)
        await message.answer("Форма сбросилась. Нажми «📩 Запросить дистрибуцию» ещё раз.", reply_markup=await user_menu_keyboard(tg_id))
        return

    key, _ = LABEL_FORM_STEPS[step]
    ok, normalized, err = validate_label_input(key, txt)
    if not ok:
        await message.answer(
            f"{err}\n\n{LABEL_FORM_STEPS[step][1]}\n\n(Отмена: /cancel)",
            reply_markup=await user_menu_keyboard(tg_id)
        )
        return

    data[key] = normalized

    step += 1
    if step < len(LABEL_FORM_STEPS):
        await form_set(tg_id, step, data)
        await message.answer(LABEL_FORM_STEPS[step][1] + "\n\n(Отмена: /cancel)", reply_markup=await user_menu_keyboard(tg_id))
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
    await message.answer("\n".join(result_lines), reply_markup=await user_menu_keyboard(tg_id))

    if not sent_email:
        await message.answer(f"Почта: {LABEL_EMAIL}\n\nТекст письма (скопируй):\n\n{summary}", reply_markup=kb)

    await message.answer(
        "Заявка принята. Срок ответа: 7 дней. Если нет ответа — значит не подошло/не актуально.",
        reply_markup=await user_menu_keyboard(tg_id),
    )

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
