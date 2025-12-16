import asyncio
import os
import json
import datetime as dt
import aiosqlite
import smtplib
from email.mime.text import MIMEText

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from dotenv import load_dotenv

DB_PATH = "bot.db"

LABEL_EMAIL = "sreda.records@gmail.com"  # твоя почта (лейбл)

# --- Links ---
LINKS = {
    "bandlink_home": "https://band.link/",
    "bandlink_login": "https://band.link/login",

    "spotify_for_artists": "https://artists.spotify.com/",
    "spotify_pitch_info": "https://support.spotify.com/us/artists/article/pitching-music-to-playlist-editors/",

    "yandex_artists_hub": "https://yandex.ru/support/music/ru/performers-and-copyright-holders",
    "yandex_pitch": "https://yandex.ru/support/music/ru/performers-and-copyright-holders/new-release",

    "kion_pitch": "https://music.mts.ru/pitch",

    "zvuk_pitch": "https://help.zvuk.com/article/67859",
    "zvuk_studio": "https://studio.zvuk.com/",

    "vk_studio_info": "https://the-flow.ru/features/zachem-artistu-studiya-servis-vk-muzyki",

    "tiktok_for_artists": "https://artists.tiktok.com/",
    "tiktok_account_types": "https://support.tiktok.com/en/using-tiktok/growing-your-audience/switching-to-a-creator-or-business-account",
    "tiktok_artist_cert_help": "https://artists.tiktok.com/help-center/artist-certification",
    "tiktok_music_tab_help": "https://artists.tiktok.com/help-center/music-tab-management",
}

# --- Accounts checklist ---
ACCOUNTS = [
    ("spotify", "Spotify for Artists"),
    ("yandex", "Яндекс для артистов"),
    ("vk", "VK Studio"),
    ("zvuk", "Звук Studio"),
    ("tiktok", "TikTok (аккаунт + Artist/Music Tab)"),
]

def acc_status_emoji(v: int) -> str:
    return "·" if v == 0 else ("⧗" if v == 1 else "✓")

def next_acc_status(v: int) -> int:
    return (v + 1) % 3

def task_mark(done: int) -> str:
    return "✓" if done else "·"

# --- Tasks (уже нормальная логика) ---
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

HELP = {
    13: "UPC/ISRC часто нужны для smartlink и верификаций. Запроси у дистрибьютора.",
    14: "Опционально: Musixmatch/Genius. Помогает с карточкой трека/поиском.",
    21: "30 ДО — тестируешь моменты трека. Объём > идеальность.",
    23: "30 ПОСЛЕ — реакции, мини-истории, новые моменты песни.",
}

def expectations_text() -> str:
    return (
        "🧠 Ожидания / реальность\n\n"
        "1) Первый релиз почти никогда не «взлетает». Это нормально.\n"
        "2) Цель — система: процесс, контент, кабинеты.\n"
        "3) Алгоритмы любят регулярность.\n"
        "4) Мерь себя качеством процесса, не цифрами первого релиза.\n"
    )

def menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎯 План"), KeyboardButton(text="📋 Все задачи")],
            [KeyboardButton(text="🧾 Кабинеты"), KeyboardButton(text="📅 Таймлайн")],
            [KeyboardButton(text="🗓️ Установить дату"), KeyboardButton(text="🔗 Ссылки")],
            [KeyboardButton(text="📩 На лейбл"), KeyboardButton(text="📤 Экспорт")],
            [KeyboardButton(text="🧠 Ожидания"), KeyboardButton(text="🧹 Сброс")],
        ],
        resize_keyboard=True
    )

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID = os.getenv("ADMIN_TG_ID")  # обязательно (цифры)

SMTP_USER = os.getenv("SMTP_USER")  # опционально
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD")  # опционально
SMTP_TO = os.getenv("SMTP_TO") or LABEL_EMAIL

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
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_forms (
            tg_id INTEGER PRIMARY KEY,
            form_name TEXT,
            step INTEGER DEFAULT 0,
            data_json TEXT DEFAULT '{}'
        )
        """)
        await db.commit()

async def ensure_user(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (tg_id,))
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

# ---------- Forms (label submission) ----------

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

# -------------------- UX builders --------------------

def count_progress(tasks_state: dict[int, int]) -> tuple[int, int]:
    total = len(TASKS)
    done = sum(1 for task_id, _ in TASKS if tasks_state.get(task_id, 0) == 1)
    return done, total

def get_next_task(tasks_state: dict[int, int]):
    for task_id, title in TASKS:
        if tasks_state.get(task_id, 0) == 0:
            return task_id, title
    return None

def render_list_text(tasks_state: dict[int, int], header: str) -> str:
    done, total = count_progress(tasks_state)
    text = f"{header}\nПрогресс: {done}/{total}\n\n"
    for task_id, title in TASKS:
        text += f"{task_mark(tasks_state.get(task_id, 0))} {title}\n"
    return text

def build_focus(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = render_list_text(tasks_state, "🎯 Фокус-режим")
    next_task = get_next_task(tasks_state)

    rows: list[list[InlineKeyboardButton]] = []
    if next_task:
        task_id, title = next_task
        rows.append([InlineKeyboardButton(text=f"✓ Сделано: {title}", callback_data=f"focus_done:{task_id}")])
        rows.append([InlineKeyboardButton(text="❓ Пояснение", callback_data=f"help:{task_id}")])

    rows.append([InlineKeyboardButton(text="📩 На лейбл", callback_data="label:start")])
    rows.append([InlineKeyboardButton(text="📋 Все задачи", callback_data="show_all"),
                 InlineKeyboardButton(text="🧾 Кабинеты", callback_data="accounts:open")])
    rows.append([InlineKeyboardButton(text="📅 Таймлайн", callback_data="timeline"),
                 InlineKeyboardButton(text="🔗 Ссылки", callback_data="links")])
    rows.append([InlineKeyboardButton(text="🧹 Сброс", callback_data="reset_menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)

def build_all_list(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = render_list_text(tasks_state, "📋 Все задачи")
    inline = []
    for task_id, title in TASKS:
        done = tasks_state.get(task_id, 0)
        btn_text = f"{'✓ Снять' if done else '· Отметить'}: {title}"
        inline.append([InlineKeyboardButton(text=btn_text, callback_data=f"all_toggle:{task_id}")])
    inline.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")])
    return text, InlineKeyboardMarkup(inline_keyboard=inline)

def build_accounts_checklist(accounts_state: dict[str, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = "🧾 Кабинеты артиста\nСостояния: · → ⧗ → ✓\n\n"
    for key, name in ACCOUNTS:
        text += f"{acc_status_emoji(accounts_state.get(key, 0))} {name}\n"

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

def parse_date(date_str: str) -> dt.date | None:
    try:
        y, m, d = date_str.split("-")
        return dt.date(int(y), int(m), int(d))
    except Exception:
        return None

def timeline_text(release_date: dt.date | None) -> str:
    if not release_date:
        return "📅 Таймлайн\n\nДата релиза не задана.\nУстанови: /set_date YYYY-MM-DD"
    pitch = release_date - dt.timedelta(days=14)
    after_end = release_date + dt.timedelta(days=7)
    return (
        "📅 Таймлайн\n\n"
        f"Дата релиза: {release_date.isoformat()}\n"
        f"Питчинг: до {pitch.isoformat()} (−14)\n"
        f"После релиза: {release_date.isoformat()} → {after_end.isoformat()}\n"
    )

def build_reset_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧹 Сбросить прогресс", callback_data="reset_progress_yes")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")],
    ])

async def safe_edit(message: Message, text: str, kb: InlineKeyboardMarkup | None):
    try:
        await message.edit_text(text, reply_markup=kb)
    except Exception:
        pass

# -------------------- Email send (optional) --------------------

def try_send_email(subject: str, body: str) -> bool:
    """
    Отправляет письмо через Gmail SMTP, если заданы SMTP_USER и SMTP_APP_PASSWORD.
    Иначе возвращает False.
    """
    if not SMTP_USER or not SMTP_APP_PASSWORD:
        return False

    try:
        msg = MIMEText(body, _charset="utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = SMTP_TO

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SMTP_USER, SMTP_APP_PASSWORD)
            server.sendmail(SMTP_USER, [SMTP_TO], msg.as_string())
        return True
    except Exception:
        return False

# -------------------- Commands --------------------

@dp.message(CommandStart())
async def start(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)

    if not ADMIN_TG_ID:
        await message.answer(
            "⚠️ Важно: не задан ADMIN_TG_ID.\n"
            "Добавь ADMIN_TG_ID (цифры) в переменные окружения, чтобы заявки приходили тебе в личку.",
            reply_markup=menu_keyboard()
        )

    exp = await get_experience(tg_id)
    if exp == "unknown":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🆕 Первый релиз", callback_data="exp:first")],
            [InlineKeyboardButton(text="🎧 Уже выпускал(а)", callback_data="exp:old")],
        ])
        await message.answer(
            "Я ИСКРА — помощник по релизу.\n\n"
            "Это твой первый релиз или ты уже выпускал музыку?",
            reply_markup=kb
        )
        return

    await message.answer("ИСКРА активна. Жми кнопки меню снизу 👇", reply_markup=menu_keyboard())

@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await message.answer(text, reply_markup=kb)

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
        await message.answer("Не понял дату. Пример: /set_date 2026-01-15", reply_markup=menu_keyboard())
        return
    await set_release_date(tg_id, d.isoformat())
    await message.answer(f"Ок. Дата релиза: {d.isoformat()}", reply_markup=menu_keyboard())

# -------------------- Reply keyboard handlers --------------------

@dp.message(F.text == "🎯 План")
async def rb_plan(message: Message):
    await plan_cmd(message)

@dp.message(F.text == "📩 На лейбл")
async def rb_label(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "label_submit")
    await message.answer(
        "📩 Заявка на лейбл/дистрибуцию.\n\n"
        "Шаг 1/6: Как тебя зовут (имя/ник)?\n"
        "Можно в любой момент отменить: /cancel",
        reply_markup=menu_keyboard()
    )

@dp.message(Command("cancel"))
async def cancel(message: Message):
    tg_id = message.from_user.id
    await form_clear(tg_id)
    await message.answer("Ок, отменил.", reply_markup=menu_keyboard())

# -------------------- Form flow handler --------------------

LABEL_FORM_STEPS = [
    ("name", "Шаг 1/6: Как тебя зовут (имя/ник)?"),
    ("artist_name", "Шаг 2/6: Название проекта/артиста (как будет на площадках)?"),
    ("contact", "Шаг 3/6: Контакт для связи (Telegram @... или email)?"),
    ("genre", "Шаг 4/6: Жанр + 1–2 референса (через запятую)?"),
    ("links", "Шаг 5/6: Ссылки на материал (приватная ссылка, облако, SoundCloud и т.п.).\nФайлы в бота не кидаем — только ссылки."),
    ("release_date", "Шаг 6/6: Планируемая дата релиза (если есть) или напиши «нет»."),
]

def render_label_summary(data: dict) -> str:
    return (
        "📩 Заявка на лейбл\n\n"
        f"Кто: {data.get('name','')}\n"
        f"Артист/проект: {data.get('artist_name','')}\n"
        f"Контакт: {data.get('contact','')}\n"
        f"Жанр/референсы: {data.get('genre','')}\n"
        f"Ссылки: {data.get('links','')}\n"
        f"Дата релиза: {data.get('release_date','')}\n"
    )

@dp.message()
async def any_message_router(message: Message):
    """
    Ловим сообщения пользователя, если он в процессе формы.
    Остальные сообщения не трогаем (чтобы не ломать UX).
    """
    tg_id = message.from_user.id
    await ensure_user(tg_id)

    form = await form_get(tg_id)
    if not form or form.get("form_name") != "label_submit":
        return  # не форма — ничего не делаем

    text_in = (message.text or "").strip()
    if not text_in:
        await message.answer("Напиши текстом 🙂 (или /cancel)", reply_markup=menu_keyboard())
        return

    step = int(form["step"])
    data = form["data"]

    # guard
    if step < 0 or step >= len(LABEL_FORM_STEPS):
        await form_clear(tg_id)
        await message.answer("Форма сломалась, я сбросил её. Попробуй ещё раз.", reply_markup=menu_keyboard())
        return

    key, _ = LABEL_FORM_STEPS[step]
    data[key] = text_in

    step += 1
    if step < len(LABEL_FORM_STEPS):
        await form_set(tg_id, step, data)
        await message.answer(LABEL_FORM_STEPS[step][1] + "\n\n(Отмена: /cancel)", reply_markup=menu_keyboard())
        return

    # финал
    summary = render_label_summary(data)

    # 1) в личку админу (тебе)
    if ADMIN_TG_ID and ADMIN_TG_ID.isdigit():
        admin_id = int(ADMIN_TG_ID)
        try:
            await message.bot.send_message(
                admin_id,
                summary + f"\nОт: @{message.from_user.username or 'без_username'} (tg_id: {tg_id})"
            )
            sent_tg = True
        except Exception:
            sent_tg = False
    else:
        sent_tg = False

    # 2) на почту (если настроено)
    subject = f"[SREDA / LABEL] Demo submission: {data.get('artist_name','')}".strip()
    sent_email = try_send_email(subject, summary)

    # 3) если email не настроен — даём артисту готовый текст письма
    mailto = f"mailto:{LABEL_EMAIL}?subject={subject.replace(' ', '%20')}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Открыть почту", url=mailto)],
        [InlineKeyboardButton(text="🎯 Вернуться в план", callback_data="back_to_focus")],
    ])

    result_lines = ["✅ Заявка собрана."]
    if sent_tg:
        result_lines.append("✓ Отправил(а) на лейбл в Telegram.")
    else:
        result_lines.append("⚠️ Не смог отправить в Telegram (проверь ADMIN_TG_ID и что бот может писать тебе).")

    if sent_email:
        result_lines.append("✓ И на почту тоже отправил автоматически.")
    else:
        result_lines.append("⧗ Авто-почта не настроена — ниже готовый шаблон письма (можно просто отправить вручную).")

    await message.answer("\n".join(result_lines), reply_markup=menu_keyboard())
    if not sent_email:
        await message.answer(
            f"Почта: {LABEL_EMAIL}\n\nТекст письма (скопируй):\n\n{summary}",
            reply_markup=kb
        )

    await form_clear(tg_id)

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
    await callback.answer("Ок")

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
    if key not in [k for k, _ in ACCOUNTS]:
        await callback.answer("Неизвестно", show_alert=True)
        return
    await cycle_account_status(tg_id, key)
    state = await get_accounts_state(tg_id)
    text, kb = build_accounts_checklist(state)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Ок")

@dp.callback_query(F.data == "timeline")
async def show_timeline(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    rd = await get_release_date(tg_id)
    d = parse_date(rd) if rd else None
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]])
    await safe_edit(callback.message, timeline_text(d), kb)
    await callback.answer()

@dp.callback_query(F.data == "links")
async def show_links(callback):
    await safe_edit(callback.message, "🔗 Быстрые ссылки:", build_links_kb())
    await callback.answer()

@dp.callback_query(F.data == "reset_menu")
async def reset_menu(callback):
    await safe_edit(callback.message, "🧹 Сброс", build_reset_menu_kb())
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

@dp.callback_query(F.data == "back_to_focus")
async def back_to_focus(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_focus(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data == "label:start")
async def label_start(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "label_submit")
    await callback.message.answer("📩 Заявка на лейбл.\n\n" + LABEL_FORM_STEPS[0][1] + "\n\n(Отмена: /cancel)", reply_markup=menu_keyboard())
    await callback.answer()

# -------------------- Runner --------------------

async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан.")

    await init_db()
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
