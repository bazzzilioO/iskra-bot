import asyncio
import os
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
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
# id, title
TASKS = [
    # A. Foundations
    (1, "Цель релиза выбрана (зачем это выпускаю)"),
    (2, "Права/ownership: все участники согласны + семплы/биты легальны"),
    (3, "Единый нейминг: артист/трек/фиты везде одинаково"),
    (4, "Жанр + 1–2 референса определены (для питчинга/алгоритмов)"),
    (5, "Визуальный якорь: аватар + 1 фото + обложка (минимальный пресс-кит)"),

    # B. Asset readiness
    (6, "Мастер готов (WAV 24bit)"),
    (7, "Clean/Explicit версия (если нужно)"),
    (8, "Обложка 3000×3000 финальная"),
    (9, "Авторы и сплиты записаны"),

    # C. Distribution
    (10, "Выбран дистрибьютор"),
    (11, "Релиз загружен в дистрибьютора"),
    (12, "Метаданные проверены (язык/explicit/жанр/написание)"),

    # D. IDs & smartlink
    (13, "Получен UPC/ISRC и/или ссылки площадок (или подтверждение, что появятся)"),
    (14, "Сделана страница релиза в BandLink (Smartlink)"),
    (15, "Сделан пресейв (если доступно)"),

    # E. Profiles & pitching
    (16, "Кабинеты артиста: Spotify / Яндекс / VK / Звук / TikTok (по возможности)"),
    (17, "Шаблон сообщения для плейлистов/медиа готов (5–7 строк)"),
    (18, "Питчинг: Spotify / Яндекс / VK / Звук / КИОН (если доступно)"),

    # F. Content
    (19, "Контент-единицы минимум 3 (тизер/пост/сторис)"),
    (20, "Контент-спринт: 30 вертикалок ДО релиза (рекомендация)"),
    (21, "Контент-спринт: 30 вертикалок ПОСЛЕ релиза (рекомендация)"),

    # G. Outreach
    (22, "Список плейлистов / медиа собран (10–30 точечных)"),
]

HELP = {
    1: "Выбери одну цель:\n"
       "- тест материала\n- старт проекта\n- собрать статистику\n- портфолио\n- разогрев перед большим релизом\n\n"
       "Без цели релиз превращается в «ну мы выпустили и всё».",

    2: "Мини-чек:\n"
       "- все соавторы согласны на релиз\n"
       "- нет чужих битов/семплов без лицензии\n"
       "- если кавер — оформлено как кавер (через дистриб)\n\n"
       "Это не «юрист в чат», это просто страховка от будущего ада.",

    3: "Самая частая проблема: разные написания артиста в релизах.\n"
       "Проверь:\n- регистр букв\n- точки/дефисы\n- фиты\n- транслит\n\n"
       "Цель: везде одно и то же имя.",

    4: "Определи:\n- 1 основной жанр\n- 1–2 референса\n\n"
       "Это нужно для питчинга и для того, чтобы алгоритмы не путались.",

    5: "Мини-пресс-кит (без пафоса):\n"
       "- аватар (квадрат)\n"
       "- 1 фото/кадр (для профилей)\n"
       "- обложка релиза\n"
       "Потом ты скажешь спасибо, когда будешь заводить кабинеты.",

    6: "Финальный мастер: WAV (24-bit, 44.1/48k), без клиппинга.\n"
       "Ошибка №1 — mp3 вместо WAV.",

    7: "Если мат/жесть — explicit.\n"
       "Иногда полезна clean-версия, если хочешь больше плейлистов/радио.\n"
       "Если мата нет — пропускай.",

    8: "Обложка: 3000×3000 (JPG/PNG), без чужих логотипов/брендов/чужих лиц без прав.",

    9: "Запиши авторов и доли (сплиты). Это нужно, чтобы потом не было конфликтов.",

    10: "Дистрибьютор доставляет релиз на площадки. Для MVP выбери одного и не прыгай.",

    11: "Загрузка: WAV, обложка, дата релиза, авторы.\n"
        "Лучше 2–3 недели заранее.",

    12: "Метаданные: артист/трек, язык, explicit, жанр, авторы.\n"
        "Главная ошибка — разные написания артиста.",

    13: "Перед BandLink часто нужно дождаться UPC/ISRC и/или ссылок площадок.\n"
        "1) Найди/запроси UPC+ISRC у дистрибьютора\n"
        "2) Попроси ссылки на будущий релиз (если выдаёт)\n"
        "3) Или дождись появления релиза на площадках.",

    14: f"BandLink (smartlink):\n{LINKS['bandlink_home']}\nВход: {LINKS['bandlink_login']}\n\n"
        "Один линк вместо 10 ссылок. Делай, когда есть UPC/ссылки.",

    15: "Пресейв не всегда доступен.\n"
        "Если доступен — веди трафик через BandLink. Если нет — просто smartlink + прогрев.",

    16: "Кабинеты артиста — НЕ всегда доступны до первого релиза.\n"
        "Поэтому у каждого кабинета есть 3 состояния:\n"
        "⬜ недоступно сейчас → ⏳ доступно позже → ✅ сделано\n\n"
        "Spotify: обычно после того, как релиз появился в Spotify.\n"
        "Яндекс: часто после первого релиза или через саппорт.\n"
        "VK/Звук: кабинет может быть раньше, но функции раскрываются после релиза.\n"
        "TikTok: смысл появляется, когда есть контент/релиз.\n\n"
        "Жми «Проверить по списку».",

    17: "Шаблон (5–7 строк):\n"
        "1) кто ты\n2) жанр + 1 референс\n3) чем трек цепляет\n4) дата релиза\n5) ссылка/smartlink\n\n"
        "Не спамь всем подряд. Точечно.",

    18: "Питчинг (ориентир): минимум за 14 дней.\n\n"
        f"Spotify: {LINKS['spotify_for_artists']}\n"
        f"Info: {LINKS['spotify_pitch_info']}\n\n"
        f"Яндекс: {LINKS['yandex_pitch']}\n"
        "Важно: доступ может появиться после релиза/верификации.\n\n"
        f"Звук Studio: {LINKS['zvuk_studio']}\n"
        f"Инструкция: {LINKS['zvuk_pitch']}\n\n"
        f"КИОН (бывш. МТС): {LINKS['kion_pitch']}\n\n"
        "VK: питчинг из VK Studio (внутри экосистемы VK Музыки).\n"
        f"Инфа: {LINKS['vk_studio_info']}",

    19: "Минимум 3: тизер (10–15 сек), пост, сторис.\n"
        "Цель: в день релиза не паниковать.",

    20: "30 ДО релиза — «разложить песню на хуки».\n"
        "- 10–15 моментов трека\n- на каждый 2–3 варианта\n- TikTok/Reels/Shorts\n\n"
        "Не идеальность, а объём и тест.",

    21: "30 ПОСЛЕ релиза — «догонять волну».\n"
        "- реакции/комменты\n- мини-истории\n- новые моменты трека\n\n"
        "Цель — удержать релиз в обороте.",

    22: "Список 10–30 контактов по жанру: плейлисты, паблики, блоги.\n"
        "Лучше меньше, но точнее.",
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
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_accounts (
            tg_id INTEGER,
            key TEXT,
            status INTEGER DEFAULT 0,
            PRIMARY KEY (tg_id, key)
        )
        """)

        # Migrations (safe)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN experience TEXT DEFAULT 'unknown'")
        except Exception:
            pass

        # If older schema had done instead of status for user_accounts
        # We detect columns by attempting select; if fails, ignore.
        try:
            await db.execute("SELECT status FROM user_accounts LIMIT 1")
        except Exception:
            # try migrate from done -> status
            try:
                await db.execute("ALTER TABLE user_accounts ADD COLUMN status INTEGER DEFAULT 0")
                await db.execute("UPDATE user_accounts SET status = COALESCE(status, 0)")
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


async def get_tasks_state(tg_id: int) -> dict[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT task_id, done FROM user_tasks WHERE tg_id=?", (tg_id,))
        rows = await cur.fetchall()
        return {tid: done for tid, done in rows}


async def set_task_done(tg_id: int, task_id: int, done: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_tasks SET done=? WHERE tg_id=? AND task_id=?",
            (done, tg_id, task_id),
        )
        await db.commit()


async def toggle_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_tasks SET done = 1 - done WHERE tg_id=? AND task_id=?",
            (tg_id, task_id),
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


async def reset_progress(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done=0 WHERE tg_id=?", (tg_id,))
        await db.execute("UPDATE user_accounts SET status=0 WHERE tg_id=?", (tg_id,))
        await db.commit()


# -------------------- Logic helpers --------------------

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


def expectations_text() -> str:
    return (
        "🧠 Ожидания / реальность\n\n"
        "1) Первый релиз почти никогда не «взлетает». Это нормально.\n"
        "2) Цель первого релиза — построить систему (контент, кабинеты, питчинг, привычки).\n"
        "3) Алгоритмы любят регулярность, а не один героический залп.\n"
        "4) Не меряй себя цифрами первого релиза. Мерь себя качеством процесса.\n\n"
        "Если ты сделал процесс — ты уже выиграл."
    )


def links_text() -> str:
    return (
        "🔗 Ссылки\n\n"
        f"BandLink: {LINKS['bandlink_home']}\n"
        f"Spotify for Artists: {LINKS['spotify_for_artists']}\n"
        f"Spotify pitching info: {LINKS['spotify_pitch_info']}\n\n"
        f"Яндекс (артистам): {LINKS['yandex_artists_hub']}\n"
        f"Яндекс питчинг: {LINKS['yandex_pitch']}\n\n"
        f"Звук Studio: {LINKS['zvuk_studio']}\n"
        f"Звук питчинг: {LINKS['zvuk_pitch']}\n\n"
        f"КИОН (бывш. МТС) питчинг: {LINKS['kion_pitch']}\n"
        f"VK Studio (инфа): {LINKS['vk_studio_info']}\n\n"
        f"TikTok for Artists: {LINKS['tiktok_for_artists']}\n"
        f"TikTok account types: {LINKS['tiktok_account_types']}\n"
        f"TikTok artist certification: {LINKS['tiktok_artist_cert_help']}\n"
        f"TikTok music tab: {LINKS['tiktok_music_tab_help']}\n"
    )


def export_text(tasks_state: dict[int, int], accounts_state: dict[str, int]) -> str:
    done, total = count_progress(tasks_state)
    lines = []
    lines.append("ИСКРА — экспорт плана релиза")
    lines.append(f"Прогресс задач: {done}/{total}")
    lines.append("")

    for task_id, title in TASKS:
        status = "✅" if tasks_state.get(task_id, 0) else "⬜"
        lines.append(f"{status} {title}")

    lines.append("")
    lines.append("Кабинеты артиста (состояния):")
    lines.append("⬜ недоступно сейчас / ⏳ доступно позже / ✅ сделано")
    for key, name in ACCOUNTS:
        st = accounts_state.get(key, 0)
        lines.append(f"{acc_status_emoji(st)} {name}")

    lines.append("")
    lines.append("Ссылки:")
    lines.append(f"- BandLink: {LINKS['bandlink_home']}")
    lines.append(f"- КИОН (бывш. МТС) питчинг: {LINKS['kion_pitch']}")
    lines.append(f"- Звук питчинг: {LINKS['zvuk_pitch']}")
    lines.append(f"- Spotify for Artists: {LINKS['spotify_for_artists']}")
    lines.append(f"- Яндекс (артистам): {LINKS['yandex_artists_hub']}")
    lines.append(f"- TikTok for Artists: {LINKS['tiktok_for_artists']}")
    lines.append("")
    lines.append("Напоминания:")
    lines.append("- Для BandLink часто нужны UPC/ISRC и/или ссылки площадок (спроси у дистрибьютора).")
    lines.append("- Питчинг лучше подавать минимум за 14 дней.")
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
        "Чтобы подстроиться: это твой первый релиз или ты уже выпускал музыку?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Первый релиз", callback_data="exp:first")],
        [InlineKeyboardButton(text="🎧 Уже выпускал(а)", callback_data="exp:old")],
    ])
    return text, kb


def build_focus(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    done_count, total = count_progress(tasks_state)

    if done_count == total:
        text = (
            "🎉 Поздравляю. По задачам релиз закрыт.\n"
            "Теперь важное — не исчезнуть после дня релиза."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧠 Ожидания / реальность", callback_data="expectations")],
            [InlineKeyboardButton(text="🔗 Ссылки", callback_data="links")],
            [InlineKeyboardButton(text="📤 Экспорт плана", callback_data="export")],
            [InlineKeyboardButton(text="📋 Показать все задачи", callback_data="show_all")],
            [InlineKeyboardButton(text="🔁 Сбросить прогресс", callback_data="reset")],
        ])
        return text, kb

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

    rows.append([InlineKeyboardButton(text="🧠 Ожидания / реальность", callback_data="expectations")])
    rows.append([InlineKeyboardButton(text="🔗 Ссылки", callback_data="links")])
    rows.append([InlineKeyboardButton(text="📤 Экспорт плана", callback_data="export")])
    rows.append([InlineKeyboardButton(text="📋 Показать все задачи", callback_data="show_all")])

    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def build_all_list(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = render_list_text(tasks_state, "📋 Все задачи (можно отметить любую)")

    inline = []
    for task_id, title in TASKS:
        done = tasks_state.get(task_id, 0)
        btn_text = f"{'✅ Снять' if done else '⬜ Отметить'}: {title}"
        inline.append([InlineKeyboardButton(text=btn_text, callback_data=f"all_toggle:{task_id}")])

    inline.append([InlineKeyboardButton(text="🧠 Ожидания / реальность", callback_data="expectations")])
    inline.append([InlineKeyboardButton(text="🔗 Ссылки", callback_data="links")])
    inline.append([InlineKeyboardButton(text="📤 Экспорт плана", callback_data="export")])
    inline.append([InlineKeyboardButton(text="🎯 Вернуться в фокус-режим", callback_data="back_to_focus")])

    return text, InlineKeyboardMarkup(inline_keyboard=inline)


def build_help(task_id: int, title: str) -> tuple[str, InlineKeyboardMarkup]:
    body = HELP.get(task_id, "Пояснение пока не добавлено.")
    text = f"❓ {title}\n\n{body}"

    rows = []
    if task_id == 16:
        rows.append([InlineKeyboardButton(text="🧾 Проверить кабинеты по списку", callback_data="accounts:open")])

    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def build_accounts_checklist(accounts_state: dict[str, int]) -> tuple[str, InlineKeyboardMarkup]:
    # count
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
        inline.append([InlineKeyboardButton(
            text=f"{acc_status_emoji(st)} {name}",
            callback_data=f"accounts:cycle:{key}"
        )])

    inline.append([InlineKeyboardButton(text="✅ Отметить задачу «Кабинеты артиста» как сделано", callback_data="accounts:finish_task")])
    inline.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")])

    return text, InlineKeyboardMarkup(inline_keyboard=inline)


def build_simple_screen(title: str, body: str) -> tuple[str, InlineKeyboardMarkup]:
    text = f"{title}\n\n{body}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]
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
        "ИСКРА активна.\n\n"
        "Команды:\n"
        "/plan — план релиза (фокус-режим)\n"
        "/help — помощь\n"
        "/reset_profile — поменять «первый релиз / уже выпускал»"
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "Открой /plan.\n"
        "Фокус-режим ведёт по одной задаче.\n"
        "В «Показать все задачи» можно отмечать/снимать любую.\n"
        "В «Кабинеты артиста» есть чеклист со статусами ⬜/⏳/✅."
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
        await callback.message.answer("Ок. Первый релиз: я буду считать, что часть кабинетов может быть недоступна заранее.")
    else:
        await set_experience(tg_id, "old")
        await callback.message.answer("Ок. Уже выпускал: всё равно проверим кабинеты — часто они не заведены.")

    await callback.message.answer("Теперь жми /plan")
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

    # Мы не требуем 5/5 ✅. Кабинеты могут быть недоступны.
    # Задача считается "сделано", если пользователь прошёл чеклист и принял реальность.
    await set_task_done(tg_id, 16, 1)

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
    acc_state = await get_accounts_state(tg_id)
    text = export_text(tasks_state, acc_state)

    await callback.message.answer(text)
    await callback.answer("Экспортировал")


@dp.callback_query(F.data == "links")
async def show_links(callback):
    text, kb = build_simple_screen("🔗 Ссылки", links_text())
    await safe_edit(callback.message, text, kb)
    await callback.answer()


@dp.callback_query(F.data == "expectations")
async def show_expectations(callback):
    text, kb = build_simple_screen("🧠 Ожидания / реальность", expectations_text())
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
