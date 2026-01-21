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
import html
import json
import logging
import os
import re
import datetime as dt
from datetime import timezone
import time
import traceback
from typing import IO
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
    BufferedInputFile, InputMediaPhoto,
    User,
)
from aiogram.utils.backoff import Backoff, BackoffConfig
from dotenv import load_dotenv
from db import (
    cycle_account_status as db_cycle_account_status,
    ensure_user as db_ensure_user,
    add_smartlink_reminder,
    remove_smartlink_reminder,
    is_smartlink_reminder_set,
    form_clear,
    form_get,
    form_set,
    form_start,
    get_accounts_state,
    get_export_unlocked,
    get_experience,
    get_important_tasks,
    get_focus_show_completed,
    get_last_update_notified,
    get_release_date,
    get_reminders_enabled,
    get_tasks_state,
    get_updates_opt_in,
    get_updates_opt_in_users,
    init_db,
    is_smartlink_subscribed,
    reset_all_data,
    reset_progress_only,
    save_qc_check,
    set_export_unlocked,
    set_experience,
    set_last_update_notified,
    set_release_date,
    set_smartlink_subscription,
    set_updates_opt_in,
    toggle_updates_opt_in,
    set_focus_show_completed,
    toggle_important_task,
    toggle_reminders_enabled,
    toggle_task_and_get_state,
    was_qc_checked,
    save_smartlink_message_reference,
    get_smartlink_messages,
    count_owned_smartlinks,
    list_owned_smartlinks,
    fetch_owned_smartlink_by_id,
    list_recent_smartlinks,
    enqueue_smartlink_publish_retry,
    list_due_smartlink_publish_jobs,
    update_smartlink_publish_job,
    delete_smartlink_publish_job,
    fetch_owned_smartlink_from_d1,
)
from helpers import (
    build_smartlink_id,
    build_smartlink_key,
    escape_html,
    format_date_ru,
    parse_date,
    normalize_base_url,
    get_smartlink_slugs,
    parse_smartlink_id,
    parse_smartlink_key,
    build_smartlink_index_payload,
    log_missing_index_token,
    safe_edit,
    safe_edit_caption,
    smartlink_can_remind,
    smartlink_pre_save_active,
    slugify,
    _is_html_response,
    _sanitize_body_for_logging,
)
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
from scheduler import build_deadlines, reminder_scheduler
from smartlink import (
    build_smartlink_caption,
    build_owner_payload,
    build_owner_cover_updates,
    _build_smartlink_fallback_text,
    _send_smartlink_fallback,
    _smartlink_sanity_check,
    smartlink_step_prompt,
    smartlinks_help_text,
    build_smartlink_view_text,
    build_my_smartlinks_text,
    build_my_smartlinks_kb,
    start_smartlink_form,
    start_smartlink_import,
    skip_prefilled_smartlink_steps,
    log_smartlink_step,
    refresh_smartlink_links_from_bandlink,
    _send_smartlink_prompt,
    _update_smartlink_prompt,
    _update_prompt_message,
    finalize_smartlink_form,
    send_my_smartlinks,
    send_smartlink_list,
    show_smartlink_view,
    resend_smartlink_card,
    get_release_reminder_state,
    _store_smartlink_message,
    update_smartlink_message,
    schedule_smartlink_update,
    smartlink_publish_scheduler,
    send_smartlink_photo,
    fetch_my_smartlinks_from_index,
    fetch_smartlink_from_index,
    parse_page_marker,
    parse_smartlink_callback_data,
    extract_index_owner_tg_user_id,
    extract_index_owner_fields,
    normalize_index_smartlink,
    smartlink_index_ready,
    fetch_owned_smartlink_with_fallback,
    fetch_owned_smartlink_by_smartlink_id,
    fetch_owned_smartlink_from_index,
    fetch_smartlink_by_id,
    fetch_latest_smartlink_from_index,
    update_smartlink_in_index,
    confirm_smartlink_indexed,
    sync_smartlink_to_web,
    smartlink_slug_exists,
    build_unique_smartlink_slugs,
    build_smartlink_web_url,
    build_cover_proxy_url,
    build_copy_links_text,
    _iter_smartlink_links,
    build_smartlink_export_text,
    pick_selected_metadata,
    start_prefill_editor,
    apply_spotify_upc_selection,
    apply_caption_update,
    fetch_cover_file,
    maybe_upgrade_smartlink_cover_from_photo,
    filter_human_sources,
    show_import_confirmation,
)


async def ensure_user(tg_id: int, username: str | None = None):
    await db_ensure_user(tg_id, username, TASKS, ACCOUNTS)


async def cycle_account_status(tg_id: int, key: str):
    return await db_cycle_account_status(tg_id, key, next_acc_status)


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

from config import (
    ADMIN_TG_ID,
    APP_VERSION,
    BANDLINK_REFRESH_PLATFORMS,
    BANDLINK_USER_AGENT,
    COVER_PROXY_BASE,
    HEALTH_STATE,
    HTTP_TIMEOUT,
    LABEL_EMAIL,
    NETWORK_ERROR_LOG_THROTTLE,
    POLLING_BACKOFF_CONFIG,
    POLLING_LOCK_FILE,
    POLLING_TIMEOUT,
    PORT,
    RATE_LIMIT_COOLDOWN_SECONDS,
    SMARTLINK_API_KEY,
    SMARTLINK_INDEX_BASE,
    SMARTLINK_INDEX_URL,
    SMARTLINK_PUBLISH_QUEUE_INTERVAL_SECONDS,
    SMARTLINK_PUBLISH_RETRY_DELAYS,
    SMARTLINK_UPDATE_DEBOUNCE_SECONDS,
    SMARTLINK_WEB_BASE,
    SMTP_APP_PASSWORD,
    SMTP_TO,
    SMTP_USER,
    SONGLINK_API_URL,
    SONGLINK_PLATFORM_ALIASES,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_UPC_ENABLED,
    TOKEN,
    UPDATES_POST_URL,
)

_SPOTIFY_ACCESS_TOKEN: str | None = None
_SPOTIFY_TOKEN_EXPIRES_AT: dt.datetime | None = None

dp = Dispatcher()
logger = logging.getLogger(__name__)

# Глобальная HTTP сессия для переиспользования
_http_session: aiohttp.ClientSession | None = None
_http_session_lock = asyncio.Lock()

# Rate limit cooldown механизм
_rate_limit_cooldown_until: float | None = None
_rate_limit_cooldown_lock = asyncio.Lock()
# RATE_LIMIT_COOLDOWN_SECONDS импортируется из config


async def get_http_session() -> aiohttp.ClientSession:
    """Получить или создать глобальную HTTP сессию."""
    global _http_session
    if _http_session is None or _http_session.closed:
        async with _http_session_lock:
            # Двойная проверка после получения лока
            if _http_session is None or _http_session.closed:
                timeout = aiohttp.ClientTimeout(total=15)
                _http_session = aiohttp.ClientSession(timeout=timeout)
    return _http_session


async def close_http_session():
    """Закрыть глобальную HTTP сессию (для cleanup)."""
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None


async def check_rate_limit_cooldown() -> bool:
    """Проверить, не в cooldown ли мы после rate limit. True если можно делать запросы."""
    global _rate_limit_cooldown_until
    if _rate_limit_cooldown_until is None:
        return True
    if time.monotonic() >= _rate_limit_cooldown_until:
        async with _rate_limit_cooldown_lock:
            # Двойная проверка после получения лока
            if _rate_limit_cooldown_until is not None and time.monotonic() >= _rate_limit_cooldown_until:
                _rate_limit_cooldown_until = None
        return True
    return False


async def set_rate_limit_cooldown(seconds: int = RATE_LIMIT_COOLDOWN_SECONDS):
    """Установить cooldown после rate limit ошибки."""
    global _rate_limit_cooldown_until
    async with _rate_limit_cooldown_lock:
        _rate_limit_cooldown_until = time.monotonic() + seconds
        logger.warning("[rate-limit] cooldown activated for %d seconds", seconds)


def is_rate_limited_response(status: int, body: str | None) -> bool:
    """Проверить, является ли ответ rate limit ошибкой."""
    if status == 429:
        return True
    if body and _is_html_response(body):
        body_lower = body.lower()
        return "rate limited" in body_lower or "error 1027" in body_lower
    return False

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


# start_smartlink_form and start_smartlink_import are imported from smartlink


# HTTP API handlers вынесены в api.py
from api import start_health_server


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

async def build_focus_for_user(
    tg_id: int,
    exp: str,
    focus_task_id: int | None = None,
    *,
    show_completed: bool | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    tasks_state = await get_tasks_state(tg_id)
    important = await get_important_tasks(tg_id)
    show_completed = show_completed if show_completed is not None else await get_focus_show_completed(tg_id)
    return build_focus(tasks_state, exp, important, focus_task_id, show_completed)

SMARTLINKS_PAGE_SIZE = 5
MY_SMARTLINKS_PAGE_SIZE = 10
SUPPORT_DONATE_PRICE = 50
DONATE_MIN_STARS = 10
DONATE_MAX_STARS = 5000

# All smartlink utility functions are imported from smartlink module

async def get_spotify_access_token() -> str | None:
    global _SPOTIFY_ACCESS_TOKEN, _SPOTIFY_TOKEN_EXPIRES_AT

    if not SPOTIFY_UPC_ENABLED:
        return None

    now = dt.datetime.now(timezone.utc)
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

    data = {}
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
        return "yandex"
    if host == "open.spotify.com":
        return "spotify"
    if host == "music.apple.com":
        return "apple"


# All smartlink utility functions are imported from smartlink module - duplicates removed

def _allowed_music_platform(host: str, path: str, query: dict[str, str]) -> str | None:
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
            logger.debug("[bandlink] __NEXT_DATA__ found")
        except Exception as e:
            logger.warning("[bandlink] failed to parse __NEXT_DATA__: %s", e)
            next_data = None
    else:
        logger.debug("[bandlink] __NEXT_DATA__ not found")
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
            logger.debug("[bandlink] legacy href parser extracted %d platforms", len(legacy_links))
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

    logger.debug("[bandlink] extracted %d platforms; meta=%s", len(links), "yes" if meta else "no")
    return links, meta


async def resolve_links(url: str) -> tuple[dict[str, str], dict | None]:
    timeout = aiohttp.ClientTimeout(total=10)
    normalized_input_url, _ = normalize_music_url_with_platform(url)

    async def resolve_via_songlink() -> tuple[dict[str, str], dict | None]:
        def collect(platforms: dict, acc: dict[str, str]):
            for platform_key, info in (platforms or {}).items():
                normalized_platform = SONGLINK_PLATFORM_ALIASES.get(platform_key.lower())
                normalized_url, _ = normalize_music_url_with_platform((info or {}).get("url") or "")
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
            logger.debug("[songlink] empty entities from resolver")
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
            logger.debug("[songlink] no metadata from entities")
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

        logger.debug("[songlink] meta source=%s conflict=%s candidates=%s", preferred, conflict, list(meta_candidates.keys()))
        logger.debug("[songlink] extracted %d platforms", len(links))

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


async def refresh_smartlink_links_from_bandlink(smartlink: dict) -> dict[str, str]:
    bandlink_url = (smartlink.get("links") or {}).get("bandlink")
    if not bandlink_url:
        raise ValueError("bandlink_missing")

    html_content = await fetch_bandlink_html(bandlink_url)
    if not html_content:
        raise RuntimeError("bandlink_fetch_failed")

    links, _ = parse_bandlink(html_content)
    filtered_links = {k: v for k, v in links.items() if k in BANDLINK_REFRESH_PLATFORMS}
    if not filtered_links:
        return smartlink.get("links") or {}

    updated_links = dict(smartlink.get("links") or {})
    updated_links.update(filtered_links)
    return updated_links


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


def log_smartlink_step(tg_id: int, step: int, field: str, skipped: bool):
    logger.info(
        "[smartlink] step saved tg_id=%s step=%s field=%s skipped=%s", tg_id, step, field, skipped
    )


async def smartlink_slug_exists(
    artist_slug: str,
    slug: str,
    *,
    owner_tg_user_id: int | None = None,
    skip_api_check: bool = False,
) -> bool:
    artist_slug = (artist_slug or "").strip()
    slug = (slug or "").strip()
    if not artist_slug or not slug:
        return False
    # Сначала проверяем локальную D1 БД - быстрее и надежнее
    if owner_tg_user_id is not None:
        try:
            smartlink = await asyncio.wait_for(
                fetch_owned_smartlink_from_d1(owner_tg_user_id, artist_slug, slug),
                timeout=2.0
            )
            if smartlink:
                return True
        except asyncio.TimeoutError:
            logger.warning("[smartlink-slug] D1 check timeout artist_slug=%s slug=%s", artist_slug, slug)
        except Exception:
            logger.exception("[smartlink-slug] D1 check error artist_slug=%s slug=%s", artist_slug, slug)
    
    # Пропускаем проверку через API если skip_api_check=True (например, при rate limit)
    if skip_api_check:
        return False
    
    # Затем проверяем индекс, но с коротким таймаутом
    if smartlink_index_ready():
        try:
            ok, _item, status = await asyncio.wait_for(
                fetch_smartlink_from_index(artist_slug, slug),
                timeout=3.0
            )
            if ok:
                return True
            # 401 (unauthorized) означает проблему с аутентификацией, не то что slug занят
            # 429 (rate limit) - считаем slug свободным, чтобы не блокировать создание
            if status == 401 or status == 429:
                logger.debug("[smartlink-slug] index auth/rate_limit failed, treating as available artist_slug=%s slug=%s status=%s", artist_slug, slug, status)
                return False
            if status is not None:
                return status != 404
        except asyncio.TimeoutError:
            logger.warning("[smartlink-slug] index check timeout artist_slug=%s slug=%s", artist_slug, slug)
            # При таймауте считаем, что slug свободен, чтобы не блокировать создание
            return False
        except Exception:
            logger.exception("[smartlink-slug] index check error artist_slug=%s slug=%s", artist_slug, slug)
    return False


async def build_unique_smartlink_slugs(
    artist: str,
    title: str,
    *,
    owner_tg_user_id: int | None = None,
    skip_api_check: bool = False,
) -> tuple[str, str]:
    artist_slug = slugify(artist) or "artist"
    base_slug = slugify(title) or "untitled"
    candidate = base_slug
    suffix = 2
    max_iterations = 10  # Защита от бесконечного цикла
    iteration = 0
    while await smartlink_slug_exists(
        artist_slug,
        candidate,
        owner_tg_user_id=owner_tg_user_id,
        skip_api_check=skip_api_check,
    ) and iteration < max_iterations:
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
        iteration += 1
    # Если пропустили проверку API или достигли лимита - добавляем timestamp для уникальности
    if skip_api_check or iteration >= max_iterations:
        import hashlib
        import time
        hash_suffix = hashlib.md5(f"{candidate}-{time.time()}".encode()).hexdigest()[:8]
        candidate = f"{base_slug}-{hash_suffix}"
    return artist_slug, candidate


def build_cover_proxy_url(artist_slug: str, slug: str) -> str:
    if not artist_slug or not slug:
        return ""
    base = COVER_PROXY_BASE
    if not base:
        return ""
    return f"{base}/api/cover/{artist_slug}/{slug}"


def build_smartlink_web_url(artist_slug: str, slug: str) -> str:
    if not artist_slug or not slug:
        return ""
    return f"{SMARTLINK_WEB_BASE}/{artist_slug}/{slug}"


async def sync_smartlink_to_web(payload: dict) -> tuple[bool, int | None, str | None]:
    if not payload or not payload.get("artist_slug") or not payload.get("slug"):
        logger.warning("[smartlink-index] invalid payload slugs, skipping send")
        return False, None, "missing_slugs"

    links = payload.get("links")
    if not isinstance(links, dict):
        logger.warning("[smartlink-index] invalid links payload type=%s", type(links))
        return False, None, "links_invalid"
    if not SMARTLINK_API_KEY:
        logger.error("[smartlink-index] SMARTLINK_API_KEY missing; skipping sync")
        return False, None, "missing_api_key"
    if not SMARTLINK_INDEX_BASE:
        logger.error("[smartlink-index] SMARTLINK_INDEX_BASE missing; skipping sync")
        return False, None, "config_missing"

    # Проверяем rate limit cooldown перед запросом
    if not await check_rate_limit_cooldown():
        logger.debug("[smartlink-index] skipping request due to rate limit cooldown")
        return False, 429, "rate_limit_cooldown"

    url = f"{SMARTLINK_INDEX_BASE}/api/index/upsert"
    headers = {"Content-Type": "application/json", "X-Skip-Sync": "1"}
    headers["X-API-Key"] = SMARTLINK_API_KEY
    logger.info("[smartlink-index] outgoing payload=%s", json.dumps(payload, ensure_ascii=False))
    try:
        session = await get_http_session()
        async with session.post(url, headers=headers, json=payload) as resp:
            status = resp.status
            try:
                body = await resp.text()
            except Exception:
                body = None
        sanitized_body = _sanitize_body_for_logging(body)
        logger.info("[smartlink-index] worker response status=%s body=%s", status, sanitized_body)
        
        # Обработка Rate Limit
        if is_rate_limited_response(status, body):
            logger.warning("[smartlink-index] rate limited by Cloudflare during sync")
            await set_rate_limit_cooldown()
            return False, 429, "rate_limit"
        
        log_missing_index_token(status, body, "sync_smartlink_to_web")
        if 200 <= status < 300:
            return True, status, None
        return False, status, sanitized_body
    except Exception as e:
        return False, None, str(e)



def _is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def _cleanup_user_input_message(message: Message, data: dict):
    last_id = data.pop("_last_input_message_id", None)
    if last_id:
        with contextlib.suppress(Exception):
            await message.bot.delete_message(message.chat.id, last_id)
    data["_last_input_message_id"] = message.message_id
    with contextlib.suppress(Exception):
        await message.bot.delete_message(message.chat.id, message.message_id)


async def _send_smartlink_prompt(message: Message, tg_id: int, step: int, data: dict):
    await _update_smartlink_prompt(
        message,
        tg_id,
        step,
        data,
    )


async def _update_smartlink_prompt(
    message: Message,
    tg_id: int,
    step: int,
    data: dict,
    prefix: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
):
    prompt_text = smartlink_step_prompt(step)
    if prefix:
        prompt_text = f"{prefix}\n\n{prompt_text}"
    prompt_text += "\n\n(Отмена: /cancel)"
    kb = reply_markup if reply_markup is not None else smartlink_step_kb()

    await _update_prompt_message(message, tg_id, data, prompt_text, kb, step=step)


async def _update_prompt_message(
    message: Message,
    tg_id: int,
    data: dict,
    text: str,
    kb: InlineKeyboardMarkup | None,
    *,
    step: int | None = None,
):
    prev_prompt_id = data.get("_prompt_message_id")
    if prev_prompt_id:
        try:
            await message.bot.edit_message_text(
                text, chat_id=message.chat.id, message_id=prev_prompt_id, reply_markup=kb
            )
            if step is not None:
                await form_set(tg_id, step, data)
            return
        except TelegramBadRequest:
            with contextlib.suppress(Exception):
                await message.bot.edit_message_reply_markup(message.chat.id, prev_prompt_id, reply_markup=None)
        except Exception:
            logger.exception("[smartlink] failed to edit prompt tg_id=%s", tg_id)

    prompt = await message.answer(text, reply_markup=kb)
    data["_prompt_message_id"] = prompt.message_id
    if step is not None:
        await form_set(tg_id, step, data)
    return prompt



async def finalize_smartlink_form(message: Message, tg_id: int, data: dict):
    logger.info("[smartlink] finalize start tg_id=%s", tg_id)
    failure_reason: str | None = None
    # Отправляем уведомление о начале обработки
    processing_msg: Message | None = None
    try:
        processing_msg = await message.answer("⏳ Обрабатываю смартлинк...")
    except Exception:
        pass
    
    try:
        
        artist = data.get("artist") or ""
        title = data.get("title") or ""
        release_iso = data.get("release_date") or ""
        cover_file_id = data.get("cover_file_id") or ""
        cover_source = data.get("cover_source") if isinstance(data.get("cover_source"), dict) else {}
        caption_text = data.get("caption_text", "") or ""
        links = data.get("links") or {}
        raw_cover_url = data.get("cover_url") if isinstance(data.get("cover_url"), str) else ""
        cover_url = raw_cover_url.strip() if raw_cover_url and _is_valid_url(raw_cover_url.strip()) else ""
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        artist_slug, slug = await build_unique_smartlink_slugs(
            artist,
            title,
            owner_tg_user_id=tg_id,
        )
        links_clean = {
            k: v
            for k, v in links.items()
            if v and isinstance(v, str) and _is_valid_url(v.strip())
        }
        has_anchor_link = any(links_clean.get(p) for p in KEY_PLATFORM_SET)
        if links_clean.get("bandlink"):
            has_anchor_link = True
        logger.info(
            "[smartlink] links filtered tg_id=%s total=%s valid=%s",
            tg_id,
            len(links),
            len(links_clean),
        )

        cover_source_type = cover_source.get("type") if isinstance(cover_source, dict) else None
        if cover_source_type == "telegram":
            cover_file_id = str(cover_source.get("file_id") or "").strip()
            if not cover_file_id:
                await message.answer(
                    "Обложка не выбрана или недоступна",
                    reply_markup=await user_menu_keyboard(tg_id),
                )
                return
        elif cover_source_type:
            await message.answer(
                "Обложка не выбрана или недоступна",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return
        else:
            cover_source = {}
            cover_source_type = None

        if cover_source_type == "telegram" or cover_file_id:
            cover_url = build_cover_proxy_url(artist_slug, slug) if artist_slug and slug else ""
        else:
            cover_url = cover_url or ""

        missing_fields = [name for name, value in {"artist": artist, "title": title}.items() if not value]
        if missing_fields:
            logger.warning("[smartlink] missing fields tg_id=%s fields=%s", tg_id, missing_fields)

        if not has_anchor_link:
            failure_reason = "Нет ни одной ссылки на платформу. Добавь Spotify, Apple Music, Яндекс, VK или BandLink."
            await _update_prompt_message(message, tg_id, data, failure_reason, None)
            await message.answer(
                failure_reason,
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return

        if missing_fields:
            missing_text = " и ".join("артиста" if f == "artist" else "названия" for f in missing_fields)
            await message.answer(
                f"Нет {missing_text}. Добавь, чтобы карточка выглядела лучше.",
                reply_markup=await user_menu_keyboard(tg_id),
            )

        smartlink_id = build_smartlink_id(artist_slug, slug)
        smartlink = {
            "id": smartlink_id,
            "owner_tg_user_id": str(tg_id),
            "owner_tg_username": message.from_user.username or "",
            "owner_display_name": message.from_user.full_name or "",
            "artist": artist,
            "title": title,
            "release_date": release_iso,
            "cover_file_id": cover_file_id,
            "cover_source": cover_source,
            "links": links_clean,
            "caption_text": caption_text,
            "branding_disabled": bool(data.get("branding_disabled")),
            "artist_slug": artist_slug,
            "slug": slug,
            "created_at": dt.datetime.now(timezone.utc).isoformat(),
            "cover_url": cover_url,
            "metadata": metadata,
            "cover_version": 1,
        }
        sync_payload = build_smartlink_index_payload(smartlink)
        if sync_payload:
            sync_ok, sync_status, sync_error = await sync_smartlink_to_web(sync_payload)
        else:
            sync_ok, sync_status, sync_error = False, None, "payload_invalid"
        if not sync_ok:
            logger.warning(
                "[smartlink] sync to web failed smartlink_id=%s status=%s error=%s",
                smartlink_id,
                sync_status,
                sync_error,
            )

        publish_ok = False
        publish_status: int | None = None
        publish_error: str | None = None
        
        # Если sync не удался из-за rate limit - добавляем в очередь для повтора
        is_rate_limit = sync_status == 429 or sync_error == "rate_limit"
        if not sync_ok and is_rate_limit:
            logger.info(
                "[smartlink] rate limited during create, enqueueing for retry smartlink_id=%s",
                smartlink_id,
            )
            owner = build_owner_payload(message.from_user)
            await enqueue_smartlink_publish_retry(
                artist_slug,
                slug,
                smartlink,
                owner,
                delay_seconds=60,  # Первая попытка через 60 сек
                last_error=f"rate_limit during sync: {sync_error}",
            )
            # Пробуем сразу publish даже при rate limit на sync
            publish_ok, publish_status, publish_error = await update_smartlink_in_index(
                artist_slug,
                slug,
                smartlink,
                owner=owner,
                reason="create",
            )
        elif sync_ok:
            publish_ok, publish_status, publish_error = await update_smartlink_in_index(
                artist_slug,
                slug,
                smartlink,
                owner=build_owner_payload(message.from_user),
                reason="create",
            )
        else:
            publish_status = sync_status
            publish_error = sync_error
        confirm_ok = False
        confirm_status: int | None = None
        confirm_error: str | None = None
        if sync_ok:
            confirm_ok, confirm_status, confirm_error = await confirm_smartlink_indexed(
                artist_slug, slug
            )
            if not confirm_ok:
                logger.error(
                    "[smartlink] index confirm failed smartlink_id=%s status=%s error=%s",
                    smartlink_id,
                    confirm_status,
                    confirm_error,
                )
        allow_remind = smartlink_can_remind(smartlink)
        subscribed = await get_release_reminder_state(tg_id, smartlink_id, allow_remind)
        try:
            await send_smartlink_photo(
                message.bot,
                tg_id,
                smartlink,
                subscribed=subscribed,
                allow_remind=allow_remind,
                show_web_url=confirm_ok,
            )
        except Exception:
            logger.exception(
                "[smartlink] finalize send failed tg_id=%s smartlink_id=%s",
                tg_id,
                smartlink_id,
            )
            await _send_smartlink_fallback(message.bot, tg_id, smartlink)
        platforms_text = ", ".join(platform_label(k) for k, v in links_clean.items() if v)
        rd_text = format_date_ru(parse_date(release_iso)) if release_iso else "—"
        web_url = build_smartlink_web_url(artist_slug, slug) if confirm_ok else ""
        web_status = "🌐 Web: публикация не удалась"
        if sync_ok and publish_ok and confirm_ok:
            web_status = f"🌐 Web: {web_url}"
        elif sync_ok and publish_ok and not confirm_ok:
            web_status = "🌐 Web: опубликовано, индекс обновляется (до 30 сек)"
        summary_lines = [
            "Смартлинк готов ✅" if sync_ok else "Смартлинк не сохранён ❌",
            f"Артист: {artist or '—'}",
            f"Релиз: {title or '—'}",
            f"Дата: {rd_text if rd_text else '—'}",
            f"Площадки: {platforms_text or '—'}",
            web_status,
            (
                "🔄 Sync: ok"
                if sync_ok
                else f"🔄 Sync: fail (status={sync_status}, error={sync_error})"
            ),
        ]
        if sync_ok and not publish_ok:
            summary_lines.append(
                "⚠️ Сохранено, но публикация в web не удалась. Повторяем автоматически."
            )
            if cover_url:
                summary_lines.append(f"🖼️ Обложка: {cover_url}")
        # Удаляем сообщение "Обрабатываю..."
        if processing_msg:
            try:
                await processing_msg.delete()
            except Exception:
                pass
        
        await _update_prompt_message(
            message,
            tg_id,
            data,
            "\n".join(summary_lines),
            smartlinks_menu_kb(),
            step=None,
        )
        logger.info(
            "[smartlink] finalize done tg_id=%s smartlink_id=%s links=%s",
            tg_id,
            smartlink_id,
            len(links_clean),
        )
    except TelegramBadRequest:
        traceback.print_exc()
        logger.exception("[smartlink] finalize failed (bad request) tg_id=%s", tg_id)
        if processing_msg:
            try:
                await processing_msg.delete()
            except Exception:
                pass
        await message.answer("Не удалось отправить карточку. Попробуй изменить данные и повторить.")
    except Exception:
        traceback.print_exc()
        logger.exception("[smartlink] finalize failed tg_id=%s", tg_id)
        if processing_msg:
            try:
                await processing_msg.delete()
            except Exception:
                pass
        error_text = failure_reason or "Не удалось создать смартлинк. Проверь данные или попробуй ещё раз."
        await _update_prompt_message(message, tg_id, data, error_text, None, step=None)
        await message.answer(error_text)
    finally:
        await form_clear(tg_id)


async def fetch_cover_file(cover_url: str) -> BufferedInputFile | None:
    if not cover_url:
        return None
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(cover_url) as resp:
                if resp.status >= 400:
                    logger.warning("[cover] failed to fetch %s: status %s", cover_url, resp.status)
                    return None
                data = await resp.read()
                if not data:
                    return None
                filename = cover_url.split("/")[-1] or "cover.jpg"
                return BufferedInputFile(data, filename=filename)
    except Exception as e:
        logger.warning("[cover] error fetching %s: %s", cover_url, e)
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
            [InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit:{row['id']}")],
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
                logger.info("[cover] downloaded cover from %s", cover_source)
        except Exception as e:
            logger.warning("[cover] failed to show preview: %s", e)
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
            "cover_url": selected_meta.get("cover_url") or "",
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

    latest = await fetch_latest_smartlink_from_index(tg_id)
    if latest and latest.get("artist") and latest.get("title") and latest.get("cover_file_id"):
        links = latest.get("links") or {}
        links["spotify"] = spotify_url
        artist_slug = (latest.get("artist_slug") or "").strip()
        slug = (latest.get("slug") or "").strip()
        if not artist_slug or not slug:
            artist_slug, slug = await build_unique_smartlink_slugs(
                latest.get("artist", ""),
                latest.get("title", ""),
                owner_tg_user_id=tg_id,
            )
        cover_url = (latest.get("cover_url") or "").strip()
        cover_source = latest.get("cover_source") if isinstance(latest.get("cover_source"), dict) else {}
        cover_file_id = latest.get("cover_file_id") or cover_source.get("file_id") or ""
        if cover_file_id and artist_slug and slug:
            cover_url = build_cover_proxy_url(artist_slug, slug)
        smartlink = {
            **latest,
            "links": links,
            "cover_url": cover_url,
            "artist_slug": artist_slug,
            "slug": slug,
            "id": build_smartlink_id(artist_slug, slug),
        }
        index_ok, status, error = await update_smartlink_in_index(
            artist_slug,
            slug,
            smartlink,
            owner=build_owner_payload(message.from_user),
        )
        if not index_ok:
            logger.warning(
                "[smartlink] upc index update failed artist_slug=%s slug=%s status=%s error=%s",
                artist_slug,
                slug,
                status,
                error,
            )
        smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug) or smartlink
        allow_remind = smartlink_can_remind(smartlink)
        subscribed = await get_release_reminder_state(tg_id, smartlink.get("id"), allow_remind)
        await send_smartlink_photo(message.bot, tg_id, smartlink, subscribed=subscribed, allow_remind=allow_remind)
        await message.answer("Добавил Spotify по UPC. Смартлинк обновлён.", reply_markup=await user_menu_keyboard(tg_id))
        return

    await message.answer(
        "Нашёл Spotify. Давай заполним смартлинк: ссылка на Spotify уже подставлена.",
        reply_markup=await user_menu_keyboard(tg_id),
    )
    await start_smartlink_form(message, tg_id, initial_links={"spotify": spotify_url})


async def apply_caption_update(
    message: Message,
    tg_id: int,
    artist_slug: str,
    slug: str,
    caption_text: str,
):
    smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
    if not smartlink:
        await message.answer("Смартлинк не найден.", reply_markup=await user_menu_keyboard(tg_id))
        await form_clear(tg_id)
        return
    updated_payload = {**smartlink, "caption_text": caption_text}
    index_ok, status, error = await update_smartlink_in_index(
        artist_slug,
        slug,
        updated_payload,
        owner=build_owner_payload(message.from_user),
    )
    if not index_ok:
        logger.warning(
            "[smartlink-caption] index update failed artist_slug=%s slug=%s status=%s error=%s",
            artist_slug,
            slug,
            status,
            error,
        )
    smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug) or updated_payload
    smartlink_id = smartlink.get("id")
    updated = await update_smartlink_message(message.bot, smartlink_id) if smartlink_id else False
    if not updated:
        allow_remind = smartlink_can_remind(smartlink)
        subscribed = await get_release_reminder_state(tg_id, smartlink_id, allow_remind)
        await send_smartlink_photo(
            message.bot,
            tg_id,
            smartlink,
            subscribed=subscribed,
            allow_remind=allow_remind,
            store_message=True,
        )
    await message.answer("Текст обновлён.", reply_markup=await user_menu_keyboard(tg_id))
    await form_clear(tg_id)


ATTRIBUTION_HTML = 'Создано с помощью <a href="https://t.me/iskramusic_bot">ИСКРА</a>'


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


async def get_release_reminder_state(tg_id: int, smartlink_id: int | str, allow_remind: bool) -> bool:
    if not allow_remind:
        return False
    if await is_smartlink_reminder_set(tg_id, smartlink_id):
        return True
    return await is_smartlink_subscribed(smartlink_id, tg_id)


# Константы импортируются из config
_smartlink_update_tasks: dict[int | str, asyncio.Task] = {}


async def _store_smartlink_message(message: Message, smartlink: dict, chat_id: int):
    smartlink_id = smartlink.get("id")
    owner_id = smartlink.get("owner_tg_user_id")
    if not smartlink_id or not owner_id:
        return

    await save_smartlink_message_reference(smartlink_id, int(owner_id), int(chat_id), message.message_id)


async def update_smartlink_message(bot: Bot, smartlink_id: int | str) -> bool:
    smartlink = await fetch_smartlink_by_id(smartlink_id)
    if not smartlink:
        logger.warning("[smartlink-update] smartlink not found smartlink_id=%s", smartlink_id)
        return False

    refs = await get_smartlink_messages(smartlink_id)
    if not refs:
        logger.info("[smartlink-update] no stored messages smartlink_id=%s", smartlink_id)
        return False

    artist_slug, slug = get_smartlink_slugs(smartlink)
    web_url = build_smartlink_web_url(artist_slug, slug) if SMARTLINK_WEB_BASE else None
    allow_remind = smartlink_can_remind(smartlink)
    updated_any = False

    for ref in refs:
        chat_id = ref.get("chat_id")
        message_id = ref.get("message_id")
        user_id = ref.get("user_id") or smartlink.get("owner_tg_user_id")
        is_admin = bool(ADMIN_TG_ID and str(chat_id) == str(ADMIN_TG_ID))
        subscribed = False
        try:
            if user_id:
                subscribed = await get_release_reminder_state(int(user_id), smartlink_id, allow_remind)
        except Exception:
            subscribed = False

        kb = build_smartlink_keyboard(
            smartlink,
            subscribed=subscribed,
            can_remind=allow_remind,
            page=None,
            web_url=web_url,
            can_update_web=is_admin,
        )
        caption = build_smartlink_caption(smartlink)
        cover_file_id = smartlink.get("cover_file_id")

        try:
            if cover_file_id:
                media = InputMediaPhoto(media=cover_file_id, caption=caption, parse_mode="HTML")
                await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=media,
                    reply_markup=kb,
                )
            else:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=caption,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            updated_any = True
            logger.info(
                "[smartlink-update] message edited smartlink_id=%s chat_id=%s message_id=%s",
                smartlink_id,
                chat_id,
                message_id,
            )
            continue
        except TelegramBadRequest as err:
            logger.warning(
                "[smartlink-update] edit failed smartlink_id=%s chat_id=%s message_id=%s error=%s",
                smartlink_id,
                chat_id,
                message_id,
                err,
            )
            if not cover_file_id:
                with contextlib.suppress(Exception):
                    await bot.edit_message_caption(
                        chat_id=chat_id,
                        message_id=message_id,
                        caption=caption,
                        reply_markup=kb,
                        parse_mode="HTML",
                    )
                    updated_any = True
                    logger.info(
                        "[smartlink-update] caption edited smartlink_id=%s chat_id=%s message_id=%s",
                        smartlink_id,
                        chat_id,
                        message_id,
                    )
                    continue
        except TelegramForbiddenError as err:
            logger.warning(
                "[smartlink-update] forbidden smartlink_id=%s chat_id=%s message_id=%s error=%s",
                smartlink_id,
                chat_id,
                message_id,
                err,
            )
        except Exception as err:
            logger.exception(
                "[smartlink-update] unexpected error smartlink_id=%s chat_id=%s message_id=%s", smartlink_id, chat_id, message_id
            )

        try:
            new_message = await send_smartlink_photo(
                bot,
                chat_id,
                smartlink,
                subscribed=subscribed,
                allow_remind=allow_remind,
                store_message=True,
            )
            if isinstance(new_message, Message):
                updated_any = True
                logger.info(
                    "[smartlink-update] fallback send stored smartlink_id=%s chat_id=%s message_id=%s",
                    smartlink_id,
                    chat_id,
                    new_message.message_id,
                )
        except Exception as err:
            logger.warning(
                "[smartlink-update] fallback send failed smartlink_id=%s chat_id=%s error=%s",
                smartlink_id,
                chat_id,
                err,
            )

    return updated_any


def schedule_smartlink_update(
    bot: Bot, smartlink_id: int | str, delay: float = SMARTLINK_UPDATE_DEBOUNCE_SECONDS
):
    existing = _smartlink_update_tasks.get(smartlink_id)
    if existing and not existing.done():
        existing.cancel()

    async def _runner():
        try:
            await asyncio.sleep(delay)
            await update_smartlink_message(bot, smartlink_id)
        except asyncio.CancelledError:
            return
        finally:
            if _smartlink_update_tasks.get(smartlink_id) is asyncio.current_task():
                _smartlink_update_tasks.pop(smartlink_id, None)

    _smartlink_update_tasks[smartlink_id] = asyncio.create_task(_runner())


async def smartlink_publish_scheduler():
    while True:
        now = dt.datetime.now(timezone.utc)
        jobs = await list_due_smartlink_publish_jobs(now)
        for job in jobs:
            artist_slug = job.get("artist_slug") or ""
            slug = job.get("slug") or ""
            smartlink = job.get("smartlink") or {}
            owner = job.get("owner")
            ok, status, error = await update_smartlink_in_index(
                artist_slug,
                slug,
                smartlink,
                owner=owner,
                schedule_retry=False,
                reason="retry",
            )
            if ok:
                await delete_smartlink_publish_job(job["id"])
            else:
                attempt = int(job.get("attempt") or 0) + 1
                delay = SMARTLINK_PUBLISH_RETRY_DELAYS[
                    min(attempt, len(SMARTLINK_PUBLISH_RETRY_DELAYS) - 1)
                ]
                await update_smartlink_publish_job(
                    job["id"],
                    attempt,
                    now + dt.timedelta(seconds=delay),
                    f"status={status} error={error}",
                )
        await asyncio.sleep(SMARTLINK_PUBLISH_QUEUE_INTERVAL_SECONDS)


async def send_smartlink_photo(
    bot: Bot,
    chat_id: int,
    smartlink: dict,
    release_today: bool = False,
    subscribed: bool = False,
    allow_remind: bool = False,
    page: int | None = None,
    store_message: bool | None = None,
    show_web_url: bool = True,
):
    try:
        artist_slug, slug = get_smartlink_slugs(smartlink)
        web_url = (
            build_smartlink_web_url(artist_slug, slug)
            if show_web_url and SMARTLINK_WEB_BASE
            else None
        )
        is_admin = bool(ADMIN_TG_ID and str(chat_id) == str(ADMIN_TG_ID))
        caption = build_smartlink_caption(smartlink, release_today=release_today)
        kb = build_smartlink_keyboard(
            smartlink,
            subscribed=subscribed,
            can_remind=allow_remind,
            page=page,
            web_url=web_url,
            can_update_web=is_admin,
        )
    except Exception:
        logger.exception("[smartlink] render failed smartlink_id=%s", smartlink.get("id"))
        return await _send_smartlink_fallback(bot, chat_id, smartlink)
    cover_file_id = smartlink.get("cover_file_id")
    try:
        if cover_file_id:
            msg = await bot.send_photo(
                chat_id,
                photo=cover_file_id,
                caption=caption,
                reply_markup=kb,
                parse_mode="HTML",
            )
        else:
            msg = await bot.send_message(
                chat_id,
                text=caption,
                reply_markup=kb,
                parse_mode="HTML",
            )

        should_store = store_message
        if should_store is None:
            should_store = str(smartlink.get("owner_tg_user_id")) == str(chat_id)
        if should_store:
            await _store_smartlink_message(msg, smartlink, chat_id)

        return msg
    except Exception:
        logger.exception("[smartlink] send failed smartlink_id=%s", smartlink.get("id"))
        return await _send_smartlink_fallback(bot, chat_id, smartlink)

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
# All handlers are now in handlers.py
# Import handlers after all functions are defined to avoid import errors
try:
    import handlers  # noqa: F401 - registers all handlers with dp
    # Log handler registration status
    handler_count = sum(len(handlers) for handlers in dp.subscribers.values())
    logger.info("Handlers module imported successfully, registered %d handler groups", handler_count)
except Exception as e:
    logger.error("Failed to import handlers module: %s", e, exc_info=True)
    raise

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
                logger.warning("Network error during polling: %s. Retrying...", exc)
                last_network_log_at = now

            delay = next(backoff)
            logger.info("Retrying polling in %.1fs (attempt #%d)", delay, backoff.counter)
            await asyncio.sleep(delay)


async def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задан.")
    lock_file = acquire_single_instance_lock(POLLING_LOCK_FILE)
    if lock_file is None:
        logger.warning("Another polling instance is already running (lock: %s). Exiting.", POLLING_LOCK_FILE)
        return

    logger.info("Single-instance lock acquired at %s (pid=%d)", POLLING_LOCK_FILE, os.getpid())

    # Ensure database schema is initialized before starting external services
    await init_db()
    _smartlink_sanity_check()
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
    logger.info(
        "Starting bot in POLLING mode, bot_id=%d, username=@%s, pid=%d",
        me.id,
        me.username,
        os.getpid(),
    )
    await start_health_server(bot)
    logger.info("Dropping webhook and pending updates before polling...")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        asyncio.create_task(reminder_scheduler(bot, send_smartlink_photo))
    except Exception as err:
        logger.error("[main] reminder scheduler not started: %s", err)
    try:
        asyncio.create_task(smartlink_publish_scheduler())
    except Exception as err:
        logger.error("[main] smartlink publish scheduler not started: %s", err)
    try:
        await run_polling(bot)
    finally:
        release_single_instance_lock(lock_file)
        await close_http_session()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())

# TODO (PR-2): вынести повторяющиеся функции для формирования клавиатур и текстов
# - unify safe_edit_caption с safe_edit через общий обработчик
# - собрать общую функцию для экспорта смартлинков (copy/export)
