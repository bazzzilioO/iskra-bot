import asyncio
import os
import datetime as dt
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from dotenv import load_dotenv

DB_PATH = "bot.db"

# --- Links ---
LINKS = {
    "bandlink_home": "https://band.link/",
    "bandlink_login": "https://band.link/login",

    "spotify_for_artists": "https://artists.spotify.com/",
    "spotify_pitch_info": "https://support.spotify.com/us/artists/article/pitching-music-to-playlist-editors/",

    "yandex_artists_hub": "https://yandex.ru/support/music/ru/performers-and-copyright-holders",
    "yandex_pitch": "https://yandex.ru/support/music/ru/performers-and-copyright-holders/new-release",

    "apple_pitch_guide": "https://itunespartner.apple.com/music/support/5391-apple-music-pitch-user-guide",

    # KION (бывш. МТС)
    "kion_pitch": "https://music.mts.ru/pitch",

    # Звук
    "zvuk_pitch": "https://help.zvuk.com/article/67859",
    "zvuk_studio": "https://studio.zvuk.com/",

    # VK (питчинг/статистика внутри VK Studio)
    "vk_studio_info": "https://the-flow.ru/features/zachem-artistu-studiya-servis-vk-muzyki",

    # TikTok
    "tiktok_for_artists": "https://artists.tiktok.com/",
    "tiktok_account_types": "https://support.tiktok.com/en/using-tiktok/growing-your-audience/switching-to-a-creator-or-business-account",
    "tiktok_artist_cert_help": "https://artists.tiktok.com/help-center/artist-certification",
    "tiktok_music_tab_help": "https://artists.tiktok.com/help-center/music-tab-management",
}

# --- Accounts checklist ---
# status: 0=⬜ недоступно сейчас, 1=⏳ доступно позже, 2=✅ сделано
ACCOUNTS = [
    ("spotify", "Spotify for Artists"),
    ("yandex", "Яндекс для артистов"),
    ("vk", "VK Studio"),
    ("zvuk", "Звук Studio"),
    ("tiktok", "TikTok (аккаунт + Artist/Music Tab)"),
]

def acc_status_emoji(v: int) -> str:
    return "⬜" if v == 0 else ("⏳" if v == 1 else "✅")

def next_acc_status(v: int) -> int:
    return (v + 1) % 3

# --- Tasks (structured & logical) ---
TASKS = [
    # A. Foundations
    (1, "Цель релиза выбрана (зачем это выпускаю)"),
    (2, "Права/ownership: все участники согласны + семплы/биты легальны"),
    (3, "Единый нейминг: артист/трек/фиты везде одинаково"),
    (4, "Жанр + 1–2 референса определены (для питчинга/алгоритмов)"),
    (5, "Мини EPK: аватар + 1 фото + короткое био (для медиа/профилей)"),

    # B. Asset readiness
    (6, "Мастер готов (WAV 24bit)"),
    (7, "Clean/Explicit версия (если нужно)"),
    (8, "Обложка 3000×3000 финальная"),
    (9, "Авторы и сплиты записаны"),

    # C. Distribution
    (10, "Выбран дистрибьютор"),
    (11, "Релиз загружен в дистрибьютора"),
    (12, "Метаданные проверены (язык/explicit/жанр/написание)"),
    (13, "UGC/Content ID настройки проверены (чтобы не словить страйки)"),

    # D. IDs & smartlink
    (14, "Получен UPC/ISRC и/или ссылки площадок (или подтверждение, что появятся)"),
    (15, "Сделана страница релиза в BandLink (Smartlink)"),
    (16, "Сделан пресейв (если доступно)"),

    # E. Profiles & pitching
    (17, "Кабинеты артиста: Spotify / Яндекс / VK / Звук / TikTok (по возможности)"),
    (18, "Шаблон сообщения для плейлистов/медиа готов (5–7 строк)"),
    (19, "Питчинг: Spotify / Яндекс / VK / Звук / КИОН (если доступно)"),

    # F. Content
    (20, "Контент-единицы минимум 3 (тизер/пост/сторис)"),
    (21, "Контент-спринт: 30 вертикалок ДО релиза (рекомендация)"),
    (22, "Контент-спринт: 30 вертикалок ПОСЛЕ релиза (рекомендация)"),

    # G. Outreach
    (23, "Список плейлистов / медиа собран (10–30 точечных)"),
    (24, "Лирика/синхронизация (опционально: Musixmatch/Genius)"),
]

HELP = {
    1: "Одна цель на релиз:\n- старт проекта\n- тест материала\n- собрать статистику\n- портфолио\n- разогрев перед большим релизом\n\nБез цели релиз превращается в «ну мы выпустили и всё».",
    2: "Мини-чек:\n- все соавторы согласны\n- нет чужих битов/семплов без лицензии\n- если кавер — оформлено как кавер\n\nЭто страховка от будущего ада.",
    3: "Проверь написание:\n- регистр букв\n- точки/дефисы\n- фиты\n- транслит\n\nЦель: везде одно и то же имя.",
    4: "Определи:\n- 1 основной жанр\n- 1–2 референса\nЭто нужно для питчинга и алгоритмов.",
    5: "Мини EPK:\n- аватар\n- 1 фото\n- био 3–5 строк\n\nЭто спасает при регистрации кабинетов и при общении с медиа.",
    6: "Финальный мастер: WAV (24-bit, 44.1/48k), без клиппинга.",
    7: "Если мат/жесть — explicit. Иногда нужна clean-версия. Если мата нет — пропускай.",
    8: "Обложка: 3000×3000 (JPG/PNG), без чужих логотипов/брендов/чужих лиц без прав.",
    9: "Запиши авторов и доли (сплиты), чтобы потом не было конфликтов.",
    10: "Дистрибьютор доставляет релиз на площадки. Для MVP выбери одного.",
    11: "Загрузка: WAV, обложка, дата, авторы. Лучше 2–3 недели заранее.",
    12: "Метаданные: артист/трек, язык, explicit, жанр, авторы. Главная ошибка — разные написания.",
    13: "Проверь у дистрибьютора настройки UGC/Content ID (YouTube/TikTok/и т.д.).\nЗадача: не заблокировать самому себе звук/видео.",
    14: "Перед BandLink часто нужно: UPC/ISRC и/или ссылки площадок.\n1) Найди/запроси UPC+ISRC\n2) Попроси ссылки (если дистрибьютор отдаёт)\n3) Или дождись появления релиза.",
    15: f"BandLink: {LINKS['bandlink_home']}\nВход: {LINKS['bandlink_login']}\nОдин линк вместо 10.",
    16: "Пресейв не всегда доступен. Если доступен — веди трафик через BandLink.",
    17: "Кабинеты не всегда доступны до первого релиза.\nСостояния: ⬜ недоступно сейчас → ⏳ позже → ✅ сделано\nЖми «Проверить по списку».",
    18: "Шаблон (5–7 строк): кто ты → жанр+реф → чем цепляет → дата → ссылка(smartlink).",
    19: "Питчинг (ориентир): минимум за 14 дней.\n"
        f"Spotify: {LINKS['spotify_for_artists']}\n"
        f"Info: {LINKS['spotify_pitch_info']}\n"
        f"Яндекс: {LINKS['yandex_pitch']}\n"
        f"Звук Studio: {LINKS['zvuk_studio']} | Инструкция: {LINKS['zvuk_pitch']}\n"
        f"КИОН (бывш. МТС): {LINKS['kion_pitch']}\n"
        "VK: через VK Studio (внутри VK Музыки).",
    20: "Минимум 3: тизер (10–15 сек), пост, сторис. Чтобы в день релиза не паниковать.",
    21: "30 ДО релиза — тестируешь разные моменты трека. Не идеальность, а объём.",
    22: "30 ПОСЛЕ релиза — догоняешь волну: реакции, истории, новые моменты.",
    23: "Список 10–30 контактов по жанру. Лучше меньше, но точнее.",
    24: "Опционально: лирика/синхронизация (Musixmatch/Genius). Может помочь с поиском/карточкой трека.",
}

def expectations_text() -> str:
    return (
        "🧠 Ожидания / реальность\n\n"
        "1) Первый релиз почти никогда не «взлетает». Это нормально.\n"
        "2) Цель — система: процесс, контент, кабинеты, привычка релизиться.\n"
        "3) Алгоритмы любят регулярность, а не один героический залп.\n"
        "4) Мерь себя качеством процесса, а не цифрами первого релиза.\n"
    )

def menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎯 План"), KeyboardButton(text="📋 Все задачи")],
            [KeyboardButton(text="🧾 Кабинеты"), KeyboardButton(text="📅 Таймлайн")],
            [KeyboardButton(text="🔗 Ссылки"), KeyboardButton(text="📤 Экспорт")],
            [KeyboardButton(text="🧠 Ожидания"), KeyboardButton(text="🧹 Сброс")],
        ],
        resize_keyboard=True
    )

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
dp = Dispatcher()


# -------------------- DB --------------------

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            experience TEXT DEFAULT 'unknown',
            release_date TEXT DEFAULT NULL
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

        # migrations safe
        try:
            await db.execute("ALTER TABLE users ADD COLUMN experience TEXT DEFAULT 'unknown'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN release_date TEXT DEFAULT NULL")
        except Exception:
            pass
        try:
            await db.execute("SELECT status FROM user_accounts LIMIT 1")
        except Exception:
            try:
                await db.execute("ALTER TABLE user_accounts ADD COLUMN status INTEGER DEFAULT 0")
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
        for key, _ in ACCOUNTS:
            await db.execute(
                "INSERT OR IGNORE INTO user_accounts (tg_id, key) VALUES (?, ?)",
                (tg_id, key),
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
        return row[0] if row and row[0] else "unknown"


async def set_release_date(tg_id: int, date_str: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET release_date=? WHERE tg_id=?", (date_str, tg_id))
        await db.commit()


async def get_release_date(tg_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT release_date FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else None


async def get_tasks_state(tg_id: int) -> dict[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT task_id, done FROM user_tasks WHERE tg_id=?", (tg_id,))
        rows = await cur.fetchall()
        return {tid: done for tid, done in rows}


async def toggle_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_tasks SET done = 1 - done WHERE tg_id=? AND task_id=?",
            (tg_id, task_id),
        )
        await db.commit()


async def set_task_done(tg_id: int, task_id: int, done: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_tasks SET done=? WHERE tg_id=? AND task_id=?",
            (done, tg_id, task_id),
        )
        await db.commit()


async def get_accounts_state(tg_id: int) -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT key, status FROM user_accounts WHERE tg_id=?", (tg_id,))
        rows = await cur.fetchall()
        return {k: (s if s is not None else 0) for k, s in rows}


async def cycle_account_status(tg_id: int, key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT status FROM user_accounts WHERE tg_id=? AND key=?",
            (tg_id, key),
        )
        row = await cur.fetchone()
        current = row[0] if row and row[0] is not None else 0
        new = next_acc_status(current)
        await db.execute(
            "UPDATE user_accounts SET status=? WHERE tg_id=? AND key=?",
            (new, tg_id, key),
        )
        await db.commit()


async def reset_progress_only(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done=0 WHERE tg_id=?", (tg_id,))
        await db.execute("UPDATE user_accounts SET status=0 WHERE tg_id=?", (tg_id,))
        await db.commit()


async def reset_everything(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done=0 WHERE tg_id=?", (tg_id,))
        await db.execute("UPDATE user_accounts SET status=0 WHERE tg_id=?", (tg_id,))
        await db.execute("UPDATE users SET experience='unknown', release_date=NULL WHERE tg_id=?", (tg_id,))
        await db.commit()


# -------------------- View helpers --------------------

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


def build_links_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="BandLink", url=LINKS["bandlink_home"]),
         InlineKeyboardButton(text="Вход BandLink", url=LINKS["bandlink_login"])],
        [InlineKeyboardButton(text="Spotify for Artists", url=LINKS["spotify_for_artists"]),
         InlineKeyboardButton(text="Spotify Pitching Info", url=LINKS["spotify_pitch_info"])],
        [InlineKeyboardButton(text="Яндекс (артистам)", url=LINKS["yandex_artists_hub"]),
         InlineKeyboardButton(text="Яндекс питчинг", url=LINKS["yandex_pitch"])],
        [InlineKeyboardButton(text="Звук Studio", url=LINKS["zvuk_studio"]),
         InlineKeyboardButton(text="Звук питчинг", url=LINKS["zvuk_pitch"])],
        [InlineKeyboardButton(text="КИОН (бывш. МТС) питчинг", url=LINKS["kion_pitch"])],
        [InlineKeyboardButton(text="TikTok for Artists", url=LINKS["tiktok_for_artists"]),
         InlineKeyboardButton(text="TikTok: тип аккаунта", url=LINKS["tiktok_account_types"])],
        [InlineKeyboardButton(text="TikTok: артист/сертификация", url=LINKS["tiktok_artist_cert_help"]),
         InlineKeyboardButton(text="TikTok: Music Tab", url=LINKS["tiktok_music_tab_help"])],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]
    ])


def build_focus(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = render_list_text(tasks_state, "🎯 Фокус-режим")
    next_task = get_next_task(tasks_state)
    last_done = get_last_done_task(tasks_state)

    rows: list[list[InlineKeyboardButton]] = []

    if next_task:
        task_id, title = next_task
        rows.append([InlineKeyboardButton(text=f"✅ Сделано: {title}", callback_data=f"focus_done:{task_id}")])
        rows.append([InlineKeyboardButton(text="❓ Пояснение", callback_data=f"help:{task_id}")])

    if last_done:
        last_id, last_title = last_done
        rows.append([InlineKeyboardButton(text=f"↩️ Отменить последнее: {last_title}", callback_data=f"undo:{last_id}")])

    rows.append([InlineKeyboardButton(text="🧾 Кабинеты", callback_data="accounts:open"),
                 InlineKeyboardButton(text="📅 Таймлайн", callback_data="timeline")])
    rows.append([InlineKeyboardButton(text="🔗 Ссылки", callback_data="links"),
                 InlineKeyboardButton(text="📤 Экспорт", callback_data="export")])
    rows.append([InlineKeyboardButton(text="🧠 Ожидания", callback_data="expectations"),
                 InlineKeyboardButton(text="📋 Все задачи", callback_data="show_all")])
    rows.append([InlineKeyboardButton(text="🧹 Сброс", callback_data="reset_menu")])

    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def build_all_list(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = render_list_text(tasks_state, "📋 Все задачи (можно отметить любую)")
    inline = []
    for task_id, title in TASKS:
        done = tasks_state.get(task_id, 0)
        btn_text = f"{'✅ Снять' if done else '⬜ Отметить'}: {title}"
        inline.append([InlineKeyboardButton(text=btn_text, callback_data=f"all_toggle:{task_id}")])

    inline.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")])
    return text, InlineKeyboardMarkup(inline_keyboard=inline)


def build_help(task_id: int, title: str) -> tuple[str, InlineKeyboardMarkup]:
    body = HELP.get(task_id, "Пояснение пока не добавлено.")
    rows = []
    if task_id == 17:
        rows.append([InlineKeyboardButton(text="🧾 Проверить кабинеты по списку", callback_data="accounts:open")])
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")])
    return f"❓ {title}\n\n{body}", InlineKeyboardMarkup(inline_keyboard=rows)


def build_accounts_checklist(accounts_state: dict[str, int]) -> tuple[str, InlineKeyboardMarkup]:
    done = sum(1 for k, _ in ACCOUNTS if accounts_state.get(k, 0) == 2)
    later = sum(1 for k, _ in ACCOUNTS if accounts_state.get(k, 0) == 1)
    total = len(ACCOUNTS)

    text = (
        "🧾 Кабинеты артиста — чеклист\n"
        f"✅ сделано: {done}/{total} | ⏳ позже: {later}/{total}\n\n"
        "Состояния: ⬜ недоступно сейчас → ⏳ позже → ✅ сделано\n\n"
    )
    for key, name in ACCOUNTS:
        st = accounts_state.get(key, 0)
        text += f"{acc_status_emoji(st)} {name}\n"

    inline = []
    for key, name in ACCOUNTS:
        st = accounts_state.get(key, 0)
        inline.append([InlineKeyboardButton(text=f"{acc_status_emoji(st)} {name}", callback_data=f"accounts:cycle:{key}")])

    inline.append([InlineKeyboardButton(text="✅ Отметить задачу «Кабинеты артиста» как сделано", callback_data="accounts:finish_task")])
    inline.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")])
    return text, InlineKeyboardMarkup(inline_keyboard=inline)


def parse_date(date_str: str) -> dt.date | None:
    try:
        y, m, d = date_str.split("-")
        return dt.date(int(y), int(m), int(d))
    except Exception:
        return None


def timeline_text(release_date: dt.date | None) -> str:
    if not release_date:
        return (
            "📅 Таймлайн\n\n"
            "Дата релиза не задана.\n"
            "Установи: /set_date YYYY-MM-DD\n\n"
            "После этого я покажу дедлайны: питчинг (−14), контент-спринт и т.д."
        )

    pitch = release_date - dt.timedelta(days=14)
    content_start = release_date - dt.timedelta(days=14)
    content_end = release_date
    after_end = release_date + dt.timedelta(days=7)

    return (
        "📅 Таймлайн\n\n"
        f"Дата релиза: {release_date.isoformat()}\n\n"
        f"Питчинг (ориентир): до {pitch.isoformat()} (релиз − 14 дней)\n"
        f"Контент «30 до»: {content_start.isoformat()} → {content_end.isoformat()}\n"
        f"После релиза (мини-план 7 дней): {release_date.isoformat()} → {after_end.isoformat()}\n\n"
        "Подсказка:\n"
        "- Smartlink (BandLink) делай, когда есть UPC/ссылки.\n"
        "- Кабинеты артиста могут открыться только после появления релиза — это нормально."
    )


def reset_menu_text() -> str:
    return (
        "🧹 Сброс\n\n"
        "Выбери, что сбросить:"
    )


def build_reset_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧹 Сбросить прогресс (задачи+кабинеты)", callback_data="reset_progress_confirm")],
        [InlineKeyboardButton(text="💣 Сбросить ВСЁ (ещё и профиль + дату релиза)", callback_data="reset_all_confirm")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")],
    ])


def build_confirm_kb(yes_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data=yes_cb)],
        [InlineKeyboardButton(text="↩️ Отмена", callback_data="back_to_focus")],
    ])


async def safe_edit(message: Message, text: str, kb: InlineKeyboardMarkup | None):
    try:
        await message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


# -------------------- Commands --------------------

@dp.message(CommandStart())
async def start(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)

    exp = await get_experience(tg_id)
    if exp == "unknown":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🆕 Первый релиз", callback_data="exp:first")],
            [InlineKeyboardButton(text="🎧 Уже выпускал(а)", callback_data="exp:old")],
        ])
        await message.answer(
            "Я ИСКРА — помощник по релизу.\n\n"
            "Чтобы подстроиться: это твой первый релиз или ты уже выпускал музыку?",
            reply_markup=kb
        )
        return

    await message.answer(
        "ИСКРА активна. Жми кнопки меню снизу 👇",
        reply_markup=menu_keyboard()
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "Команды:\n"
        "/plan — фокус-режим\n"
        "/set_date YYYY-MM-DD — задать дату релиза\n"
        "/timeline — показать дедлайны\n"
        "/reset_profile — сбросить «первый/уже выпускал»\n\n"
        "Или просто пользуйся кнопками меню снизу 👇",
        reply_markup=menu_keyboard()
    )


@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await message.answer(text, reply_markup=kb)


@dp.message(Command("timeline"))
async def timeline_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    rd = await get_release_date(tg_id)
    d = parse_date(rd) if rd else None
    await message.answer(timeline_text(d), reply_markup=menu_keyboard())


@dp.message(Command("set_date"))
async def set_date_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)

    parts = message.text.strip().split()
    if len(parts) != 2:
        await message.answer("Формат: /set_date YYYY-MM-DD", reply_markup=menu_keyboard())
        return

    d = parse_date(parts[1])
    if not d:
        await message.answer("Не понял дату. Формат: YYYY-MM-DD (например 2026-01-15)", reply_markup=menu_keyboard())
        return

    await set_release_date(tg_id, d.isoformat())
    await message.answer(f"Ок. Дата релиза установлена: {d.isoformat()}\nНажми «📅 Таймлайн».", reply_markup=menu_keyboard())


@dp.message(Command("reset_profile"))
async def reset_profile(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    await set_experience(tg_id, "unknown")
    await message.answer("Профиль сброшен. Нажми /start и выбери режим заново.", reply_markup=menu_keyboard())


# -------------------- Reply keyboard handlers --------------------

@dp.message(F.text == "🎯 План")
async def rb_plan(message: Message):
    await plan_cmd(message)

@dp.message(F.text == "📋 Все задачи")
async def rb_all(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_all_list(tasks_state)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == "🧾 Кабинеты")
async def rb_accounts(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    acc = await get_accounts_state(tg_id)
    text, kb = build_accounts_checklist(acc)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == "🔗 Ссылки")
async def rb_links(message: Message):
    await message.answer("🔗 Быстрые ссылки:", reply_markup=build_links_kb())

@dp.message(F.text == "📤 Экспорт")
async def rb_export(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    acc_state = await get_accounts_state(tg_id)
    # простой экспорт — текстом
    lines = []
    done, total = count_progress(tasks_state)
    lines.append("ИСКРА — экспорт плана релиза")
    lines.append(f"Прогресс задач: {done}/{total}\n")
    for task_id, title in TASKS:
        status = "✅" if tasks_state.get(task_id, 0) else "⬜"
        lines.append(f"{status} {title}")
    lines.append("\nКабинеты (⬜/⏳/✅):")
    for key, name in ACCOUNTS:
        lines.append(f"{acc_status_emoji(acc_state.get(key, 0))} {name}")
    await message.answer("\n".join(lines), reply_markup=menu_keyboard())

@dp.message(F.text == "🧠 Ожидания")
async def rb_expect(message: Message):
    await message.answer(expectations_text(), reply_markup=menu_keyboard())

@dp.message(F.text == "📅 Таймлайн")
async def rb_timeline(message: Message):
    await timeline_cmd(message)

@dp.message(F.text == "🧹 Сброс")
async def rb_reset(message: Message):
    await message.answer(reset_menu_text(), reply_markup=build_reset_menu_kb())


# -------------------- Inline callbacks --------------------

@dp.callback_query(F.data.startswith("exp:"))
async def set_exp(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    exp = callback.data.split(":")[1]
    await set_experience(tg_id, "first" if exp == "first" else "old")

    await callback.message.answer("Ок. Жми «🎯 План» снизу 👇", reply_markup=menu_keyboard())
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


@dp.callback_query(F.data == "accounts:open")
async def accounts_open(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    state = await get_accounts_state(tg_id)
    text, kb = build_accounts_checklist(state)
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("accounts:cycle:"))
async def accounts_cycle(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    key = callback.data.split(":")[2]
    keys = [k for k, _ in ACCOUNTS]
    if key not in keys:
        await callback.answer("Неизвестный пункт", show_alert=True)
        return

    await cycle_account_status(tg_id, key)

    state = await get_accounts_state(tg_id)
    text, kb = build_accounts_checklist(state)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Ок")


@dp.callback_query(F.data == "accounts:finish_task")
async def accounts_finish_task(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await set_task_done(tg_id, 17, 1)  # task 17 = cabinets
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Задача отмечена")


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


@dp.callback_query(F.data == "links")
async def show_links(callback):
    await safe_edit(callback.message, "🔗 Быстрые ссылки:", build_links_kb())
    await callback.answer()


@dp.callback_query(F.data == "expectations")
async def show_expectations(callback):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]])
    await safe_edit(callback.message, expectations_text(), kb)
    await callback.answer()


@dp.callback_query(F.data == "timeline")
async def show_timeline(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    rd = await get_release_date(tg_id)
    d = parse_date(rd) if rd else None
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]
    ])
    await safe_edit(callback.message, timeline_text(d), kb)
    await callback.answer()


@dp.callback_query(F.data == "reset_menu")
async def reset_menu(callback):
    await safe_edit(callback.message, reset_menu_text(), build_reset_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "reset_progress_confirm")
async def reset_progress_confirm(callback):
    await safe_edit(
        callback.message,
        "🧹 Сбросить прогресс (задачи+кабинеты)?",
        build_confirm_kb("reset_progress_yes")
    )
    await callback.answer()


@dp.callback_query(F.data == "reset_all_confirm")
async def reset_all_confirm(callback):
    await safe_edit(
        callback.message,
        "💣 Сбросить ВСЁ (прогресс + профиль + дату релиза)?",
        build_confirm_kb("reset_all_yes")
    )
    await callback.answer()


@dp.callback_query(F.data == "reset_progress_yes")
async def reset_progress_yes(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await reset_progress_only(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Сбросил")


@dp.callback_query(F.data == "reset_all_yes")
async def reset_all_yes(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await reset_everything(tg_id)
    await callback.message.answer("Всё сброшено. Жми /start.", reply_markup=menu_keyboard())
    await callback.answer("Готово")


@dp.callback_query(F.data == "back_to_focus")
async def back_to_focus(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer()


# -------------------- Runner --------------------

async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан. Добавь его в переменные окружения Railway/Render.")

    await init_db()
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
