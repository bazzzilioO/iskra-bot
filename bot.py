# Table of contents
# CONFIG/ENV
# CONSTANTS
# DB
# HELPERS (core)
# HELPERS
# KEYBOARDS
# FEATURES (focus/smartlink/label/broadcast)
# SCHEDULER
# HANDLERS
# MAIN

import asyncio
import contextlib
import fcntl
import json
import os
import re
import datetime as dt
import time
from typing import IO
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl, urlparse, urlunparse, urlencode

import aiohttp
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    LabeledPrice, PreCheckoutQuery,
    BufferedInputFile,
)
from aiogram.utils.backoff import Backoff, BackoffConfig
from dotenv import load_dotenv
from db import (
    cleanup_reminder_log,
    count_smartlinks,
    cycle_account_status as db_cycle_account_status,
    delete_smartlink,
    ensure_user as db_ensure_user,
    form_clear,
    form_get,
    form_set,
    form_start,
    get_accounts_state,
    get_export_unlocked,
    get_experience,
    get_important_tasks,
    get_last_update_notified,
    get_latest_smartlink,
    get_release_date,
    get_reminder_users,
    get_reminders_enabled,
    get_smartlink_by_id,
    get_smartlink_subscribers,
    get_smartlinks_with_release,
    list_smartlinks,
    get_tasks_state,
    get_updates_opt_in,
    get_updates_opt_in_users,
    get_user_reminder_prefs,
    init_db,
    is_smartlink_subscribed,
    mark_reminder_sent,
    mark_smartlink_day_sent,
    mark_smartlink_notified,
    reset_all_data,
    reset_progress_only,
    save_qc_check,
    save_smartlink,
    set_export_unlocked,
    set_experience,
    set_last_update_notified,
    set_release_date,
    set_smartlink_subscription,
    set_updates_opt_in,
    toggle_updates_opt_in,
    toggle_important_task,
    toggle_reminders_enabled,
    toggle_task_and_get_state,
    update_smartlink_caption,
    update_smartlink_data,
    was_qc_checked,
    was_reminder_sent,
    was_smartlink_day_sent,
)
from helpers import escape_html, format_date_ru, parse_date, safe_edit, safe_edit_caption
from keyboards import (
    ACCOUNTS,
    BRANDING_DISABLE_PRICE,
    EXPORT_LABELS,
    EXPORT_UNLOCK_PRICE,
    EXTRA_SMARTLINK_PLATFORMS,
    KEY_PLATFORM_SET,
    LINKS,
    PLATFORM_LABELS,
    SECTIONS,
    SMARTLINK_BUTTON_ORDER,
    SMARTLINK_PLATFORMS,
    TASKS,
    build_accounts_checklist,
    build_donate_menu_kb,
    build_focus,
    build_focus_keyboard,
    build_important_screen,
    build_links_kb,
    build_reset_menu_kb,
    build_section_page,
    build_sections_menu,
    build_smartlink_buttons,
    build_smartlink_keyboard,
    build_timeline_kb,
    count_progress,
    find_section_for_task,
    get_next_task,
    get_task_title,
    next_acc_status,
    smartlink_branding_confirm_kb,
    smartlink_edit_menu_kb,
    smartlink_export_kb,
    smartlink_export_paywall_kb,
    smartlink_links_menu_kb,
    smartlink_step_kb,
    smartlink_view_kb,
    smartlinks_menu_kb,
    task_mark,
)
from texts import (
    EXPERIENCE_PROMPT_TEXT,
    EXPECTATIONS_TEXT,
    HELP,
    LYRICS_SYNC_TEXT,
    QC_PROMPTS,
    RESOLVER_FALLBACK_TEXT,
    SMARTLINKS_HELP_TEXT,
    SMARTLINK_IMPORT_PROMPT,
    UGC_TIP_TEXT,
)

def build_focus_caption(
    tasks_state: dict[int, int],
    experience: str | None = None,
    important: set[int] | None = None,
    focus_task_id: int | None = None,
) -> str:
    text, _ = build_focus(tasks_state, experience, important, focus_task_id)
    return text


def build_smartlink_caption(
    smartlink: dict, release_today: bool = False, show_listen_label: bool | None = None
) -> str:
    artist = escape_html(smartlink.get("artist") or "")
    title = escape_html(smartlink.get("title") or "")
    caption_text = escape_html(smartlink.get("caption_text") or "")
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


def build_smartlink_keyboard(
    smartlink: dict,
    subscribed: bool = False,
    can_remind: bool = False,
    page: int | None = None,
) -> InlineKeyboardMarkup | None:
    return build_smartlink_buttons(
        smartlink,
        subscribed=subscribed,
        can_remind=can_remind,
        page=page,
    )

LABEL_EMAIL = "sreda.records@gmail.com"
REMINDER_INTERVAL_SECONDS = 300
REMINDER_LAST_CLEAN: dt.date | None = None

HUMAN_METADATA_PLATFORMS = {"apple", "spotify", "yandex", "vk"}


async def ensure_user(tg_id: int, username: str | None = None):
    await db_ensure_user(tg_id, username, TASKS, ACCOUNTS)


async def cycle_account_status(tg_id: int, key: str):
    return await db_cycle_account_status(tg_id, key, next_acc_status)


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

# -------------------- CONFIG --------------------

UPDATES_CHANNEL_URL = "https://t.me/sreda_music"
UPDATES_POST_URL = os.getenv("UPDATES_POST_URL", "")

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

# -------------------- TASKS --------------------

DEADLINES = [
    {"key": "pitching", "title": "Pitching (Spotify / Яндекс / VK / Звук / МТС-КИОН)", "offset": -14},
    {"key": "presave", "title": "Pre-save", "offset": -7},
    {"key": "bandlink", "title": "BandLink / Smartlink", "offset": -7},
    {"key": "content_sprint", "title": "Контент-спринт ДО — старт", "offset": -14},
    {"key": "post_1", "title": "Пост-релиз план (+1)", "offset": 1},
    {"key": "post_3", "title": "Пост-релиз план (+3)", "offset": 3},
    {"key": "post_7", "title": "Пост-релиз план (+7)", "offset": 7},
]


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
    return EXPECTATIONS_TEXT


def lyrics_sync_text() -> str:
    return LYRICS_SYNC_TEXT


def ugc_tip_text() -> str:
    return UGC_TIP_TEXT

def experience_prompt() -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Первый релиз", callback_data="exp:first")],
        [InlineKeyboardButton(text="🎧 Уже выпускал(а)", callback_data="exp:old")],
    ])
    text = EXPERIENCE_PROMPT_TEXT
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
APP_VERSION = os.getenv("APP_VERSION", "dev")
PORT = int(os.getenv("PORT", "8000"))
POLLING_LOCK_FILE = os.getenv("POLLING_LOCK_FILE", "/tmp/iskra_bot_polling.lock")
POLLING_TIMEOUT = int(os.getenv("POLLING_TIMEOUT", "60"))
NETWORK_ERROR_LOG_THROTTLE = float(os.getenv("NETWORK_ERROR_LOG_THROTTLE", "30"))
# HTTP timeout must be numeric: aiogram adds it to polling_timeout internally.
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT_TOTAL", "90"))
POLLING_BACKOFF_CONFIG = BackoffConfig(
    min_delay=float(os.getenv("BACKOFF_MIN_DELAY", "1")),
    max_delay=float(os.getenv("BACKOFF_MAX_DELAY", "60")),
    factor=float(os.getenv("BACKOFF_FACTOR", "2")),
    jitter=float(os.getenv("BACKOFF_JITTER", "0.1")),
)
HEALTH_STATE: dict[str, str | int | None] = {
    "status": "starting",
    "mode": "polling",
    "version": APP_VERSION,
    "bot_id": None,
    "username": None,
    "pid": os.getpid(),
}

SMTP_USER = os.getenv("SMTP_USER")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD")
SMTP_TO = os.getenv("SMTP_TO") or LABEL_EMAIL
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_UPC_ENABLED = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)

_SPOTIFY_ACCESS_TOKEN: str | None = None
_SPOTIFY_TOKEN_EXPIRES_AT: dt.datetime | None = None

dp = Dispatcher()

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
        SMARTLINK_IMPORT_PROMPT,
        reply_markup=await user_menu_keyboard(tg_id),
    )


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response(HEALTH_STATE)


async def start_health_server() -> web.AppRunner:
    app = web.Application()
    app.add_routes([web.get("/health", health_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Health endpoint available on port {PORT} (GET /health)")
    return runner


def acquire_single_instance_lock(lock_path: str) -> IO[str] | None:
    """Try to acquire an exclusive file lock to avoid running multiple polling instances."""

    dir_name = os.path.dirname(lock_path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None

    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def release_single_instance_lock(lock_file: IO[str]) -> None:
    with contextlib.suppress(Exception):
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()

# -------------------- UX helpers --------------------

async def build_focus_for_user(tg_id: int, exp: str, focus_task_id: int | None = None) -> tuple[str, InlineKeyboardMarkup]:
    tasks_state = await get_tasks_state(tg_id)
    important = await get_important_tasks(tg_id)
    return build_focus(tasks_state, exp, important, focus_task_id)

SMARTLINKS_PAGE_SIZE = 5
SUPPORT_DONATE_PRICE = 50
DONATE_MIN_STARS = 10
DONATE_MAX_STARS = 5000



def smartlinks_help_text() -> str:
    return SMARTLINKS_HELP_TEXT


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
    kb = build_smartlink_keyboard(
        smartlink,
        subscribed=subscribed,
        can_remind=allow_remind,
        page=page,
    )
    return await bot.send_photo(
        chat_id,
        photo=smartlink.get("cover_file_id"),
        caption=caption,
        reply_markup=kb,
        parse_mode="HTML",
    )

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

# -------------------- Reminders --------------------

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
    global REMINDER_LAST_CLEAN
    if REMINDER_LAST_CLEAN != today:
        await cleanup_reminder_log(today)
        REMINDER_LAST_CLEAN = today

    users = await get_reminder_users()

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
                if await was_reminder_sent(tg_id, key, when_label):
                    continue
                try:
                    await bot.send_message(tg_id, prefix)
                    await mark_reminder_sent(tg_id, key, when_label, today)
                except TelegramForbiddenError:
                    continue
                except Exception:
                    continue


async def process_smartlink_notifications(bot: Bot):
    smartlinks = await get_smartlinks_with_release()

    for smartlink in smartlinks:
        if not smartlink.get("reminders_enabled"):
            continue
        rd = parse_date(smartlink.get("release_date") or "")
        if not rd:
            continue
        subscribers = await get_smartlink_subscribers(smartlink.get("id"))
        for subscriber_tg_id in subscribers:
            tz, offsets, reminder_time = await get_user_reminder_prefs(subscriber_tg_id)
            now_local = dt.datetime.now(ZoneInfo(tz))
            if reminder_time and (now_local.hour != reminder_time.hour or now_local.minute != reminder_time.minute):
                continue
            for offset in offsets:
                target_date = rd + dt.timedelta(days=offset)
                if target_date != now_local.date():
                    continue
                if await was_smartlink_day_sent(smartlink.get("id"), subscriber_tg_id, offset):
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
                    await mark_smartlink_day_sent(smartlink.get("id"), subscriber_tg_id, offset, now_local.date())
                    if offset == 0:
                        await mark_smartlink_notified(smartlink.get("id"), subscriber_tg_id)
                except TelegramForbiddenError:
                    continue
                except Exception:
                    continue


async def reminder_scheduler(bot: Bot):
    while True:
        try:
            await asyncio.gather(
                process_reminders(bot),
                process_smartlink_notifications(bot),
            )
        except Exception as err:
            print(f"[reminder_scheduler] failed: {err}")
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
    users = await get_updates_opt_in_users()
    sent = skipped = errors = 0
    for tg_id, last_notified in users:
        if last_notified == url:
            skipped += 1
            continue
        try:
            await bot.send_message(tg_id, f"⚡️ Есть обновление ИСКРЫ. Подробнее: {url}")
            await set_last_update_notified(tg_id, url)
            sent += 1
        except TelegramForbiddenError:
            skipped += 1
        except Exception:
            errors += 1
        await asyncio.sleep(0.1)
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

    tasks_state = await toggle_task_and_get_state(tg_id, task_id)
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
    new_state = await toggle_reminders_enabled(tg_id)
    rd = await get_release_date(tg_id)
    d = parse_date(rd) if rd else None
    kb = build_timeline_kb(new_state, has_date=bool(d))
    await safe_edit(callback.message, timeline_text(d, new_state), kb)
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
    kb = build_smartlink_keyboard(smartlink, subscribed=not current, can_remind=allow_remind)
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
    important = await toggle_important_task(tg_id, task_id)
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

async def run_polling(bot: Bot):
    backoff = Backoff(POLLING_BACKOFF_CONFIG)
    last_network_log_at = 0.0

    while True:
        try:
            await dp.start_polling(
                bot,
                polling_timeout=POLLING_TIMEOUT,
                backoff_config=POLLING_BACKOFF_CONFIG,
                allowed_updates=dp.resolve_used_update_types(),
                close_bot_session=False,
            )
            break
        except TelegramNetworkError as exc:
            now = time.monotonic()
            if now - last_network_log_at >= NETWORK_ERROR_LOG_THROTTLE:
                print(f"Network error during polling: {exc}. Retrying...")
                last_network_log_at = now

            delay = next(backoff)
            print(f"Retrying polling in {delay:.1f}s (attempt #{backoff.counter})")
            await asyncio.sleep(delay)


async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан.")
    lock_file = acquire_single_instance_lock(POLLING_LOCK_FILE)
    if lock_file is None:
        print(f"Another polling instance is already running (lock: {POLLING_LOCK_FILE}). Exiting.")
        return

    print(f"Single-instance lock acquired at {POLLING_LOCK_FILE} (pid={os.getpid()})")

    # Ensure database schema is initialized before starting external services
    await init_db()
    timeout_seconds = float(HTTP_TIMEOUT)
    session = AiohttpSession(timeout=timeout_seconds)
    if not isinstance(session.timeout, (int, float)):
        with contextlib.suppress(Exception):
            session.timeout = float(getattr(session.timeout, "total", timeout_seconds))
    if not isinstance(session.timeout, (int, float)):
        session.timeout = timeout_seconds
    bot = Bot(token=TOKEN, session=session)
    me = await bot.get_me()
    HEALTH_STATE.update({
        "status": "running",
        "bot_id": me.id,
        "username": me.username,
    })
    print(
        "Starting bot in POLLING mode, "
        f"bot_id={me.id}, username=@{me.username}, pid={os.getpid()}"
    )
    await start_health_server()
    print("Dropping webhook and pending updates before polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        asyncio.create_task(reminder_scheduler(bot))
    except Exception as err:
        print(f"[main] reminder scheduler not started: {err}")
    try:
        await run_polling(bot)
    finally:
        release_single_instance_lock(lock_file)
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())

# TODO (PR-2): вынести повторяющиеся функции для формирования клавиатур и текстов
# - unify safe_edit_caption с safe_edit через общий обработчик
# - собрать общую функцию для экспорта смартлинков (copy/export)
