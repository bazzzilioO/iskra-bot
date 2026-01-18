"""Smartlink functions - extraction from bot.py for better code organization."""

import asyncio
import contextlib
import datetime as dt
import json
import logging
from urllib.parse import urlparse

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User, InputMediaPhoto

from config import (
    ADMIN_TG_ID,
    BANDLINK_REFRESH_PLATFORMS,
    COVER_PROXY_BASE,
    KEY_PLATFORM_SET,
    RATE_LIMIT_COOLDOWN_SECONDS,
    SMARTLINK_API_KEY,
    SMARTLINK_INDEX_BASE,
    SMARTLINK_PUBLISH_QUEUE_INTERVAL_SECONDS,
    SMARTLINK_PUBLISH_RETRY_DELAYS,
    SMARTLINK_UPDATE_DEBOUNCE_SECONDS,
    SMARTLINK_WEB_BASE,
)
from db import (
    count_owned_smartlinks,
    enqueue_smartlink_publish_retry,
    list_due_smartlink_publish_jobs,
    list_owned_smartlinks,
    update_smartlink_publish_job,
    delete_smartlink_publish_job,
    fetch_owned_smartlink_by_id,
    fetch_owned_smartlink_from_d1,
    is_smartlink_reminder_set,
    is_smartlink_subscribed,
    save_smartlink_message_reference,
    get_smartlink_messages,
    form_set,
)
from helpers import (
    build_smartlink_id,
    escape_html,
    format_date_ru,
    parse_date,
    get_smartlink_slugs,
    build_smartlink_index_payload,
    log_missing_index_token,
    parse_smartlink_id,
    smartlink_can_remind,
    smartlink_pre_save_active,
    slugify,
    _is_html_response,
    _sanitize_body_for_logging,
)
from keyboards import (
    EXPORT_LABELS,
    KEY_PLATFORM_SET,
    PLATFORM_LABELS,
    SMARTLINK_BUTTON_ORDER,
    SMARTLINK_PLATFORMS,
    build_smartlink_buttons,
    build_smartlink_keyboard,
    smartlink_step_kb,
    smartlinks_menu_kb,
)
from texts import SMARTLINKS_HELP_TEXT, SMARTLINK_IMPORT_PROMPT

logger = logging.getLogger(__name__)

# Constants
ATTRIBUTION_HTML = 'Создано с помощью <a href="https://t.me/iskramusic_bot">ИСКРА</a>'
SMARTLINKS_PAGE_SIZE = 5
MY_SMARTLINKS_PAGE_SIZE = 10
HUMAN_METADATA_PLATFORMS = {"apple", "spotify", "yandex", "vk"}

# Global state
_smartlink_update_tasks: dict[int | str, asyncio.Task] = {}

# HTTP session and rate limit cooldown functions will be imported from bot.py for now
# TODO: Move these to a shared module if needed


async def get_http_session():
    """Get HTTP session - lazy import from bot.py to avoid circular dependencies."""
    from bot import get_http_session
    return await get_http_session()


async def check_rate_limit_cooldown() -> bool:
    """Check rate limit cooldown - lazy import from bot.py."""
    from bot import check_rate_limit_cooldown
    return await check_rate_limit_cooldown()


async def set_rate_limit_cooldown(seconds: int = RATE_LIMIT_COOLDOWN_SECONDS):
    """Set rate limit cooldown - lazy import from bot.py."""
    from bot import set_rate_limit_cooldown
    await set_rate_limit_cooldown(seconds)


def is_rate_limited_response(status: int, body: str | None) -> bool:
    """Check if response is rate limited."""
    if status == 429:
        return True
    if body and _is_html_response(body):
        body_lower = body.lower()
        return "rate limited" in body_lower or "error 1027" in body_lower
    return False


def platform_label(platform: str) -> str:
    """Get platform label."""
    return PLATFORM_LABELS.get(platform, platform)


# ==================== Text/UI Functions ====================

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
    no_links_line = "Ссылки пока не найдены (попробуй обновить или добавь вручную)."

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
        if not has_platforms:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(no_links_line)
        return "\n".join(lines)

    lines = [f"{artist} — {title}"]
    if release_date:
        lines.append(f"📅 Релиз: {format_date_ru(release_date)}")
    status_line: str | None = None
    today = dt.date.today()
    if presave_active and release_date and release_date > today:
        status_line = "Релиз запланирован. Ссылки появятся ближе к дате или в день релиза."
    if not has_platforms:
        if release_date and release_date > today:
            status_line = "Релиз запланирован. Ссылки появятся ближе к дате или в день релиза."
        elif release_date and release_date <= today:
            status_line = "Ссылки не найдены. Добавь вручную или обнови."
    if status_line:
        lines.append(status_line)
    if caption_text:
        lines.append(caption_text)
    if show_branding:
        lines.append("")
        lines.append(ATTRIBUTION_HTML)
    if not has_platforms:
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(no_links_line)
    return "\n".join(lines)


def build_owner_payload(user: User) -> dict[str, str | None]:
    return {
        "tg_user_id": str(user.id),
        "username": user.username or None,
        "display_name": user.full_name or None,
    }


def build_owner_cover_updates(existing: dict, user: User, bot: Bot) -> dict[str, str]:
    updates: dict[str, str] = {}
    existing_owner_id = str(existing.get("owner_tg_user_id") or "").strip()
    bot_id = str(bot.id)
    if not existing_owner_id or existing_owner_id == bot_id:
        updates["owner_tg_user_id"] = str(user.id)
    existing_username = str(existing.get("owner_tg_username") or "").strip()
    if (not existing_username or existing_owner_id == bot_id) and user.username:
        updates["owner_tg_username"] = user.username
    existing_display_name = str(existing.get("owner_display_name") or "").strip()
    if (not existing_display_name or existing_owner_id == bot_id) and user.full_name:
        updates["owner_display_name"] = user.full_name
    return updates


def _build_smartlink_fallback_text(smartlink: dict) -> str:
    artist = smartlink.get("artist") or "Без артиста"
    title = smartlink.get("title") or "Без названия"
    links = smartlink.get("links") or {}

    lines = [f"{artist} — {title}"]

    if links:
        lines.append("Ссылки:")
        added_keys: set[str] = set()
        for key, label in SMARTLINK_BUTTON_ORDER:
            url = links.get(key)
            if url:
                lines.append(f"- {label}: {url}")
                added_keys.add(key)
        for key, url in links.items():
            if key in added_keys:
                continue
            label = platform_label(key)
            lines.append(f"- {label}: {url}")
    else:
        lines.append("Ссылки: —")

    return "\n".join(lines)


async def _send_smartlink_fallback(bot: Bot, chat_id: int, smartlink: dict):
    fallback_text = _build_smartlink_fallback_text(smartlink)
    return await bot.send_message(chat_id, fallback_text)


def _smartlink_sanity_check():
    dummy = {"id": 0, "links": {}}
    try:
        build_smartlink_buttons(dummy, subscribed=False, can_remind=False)
    except Exception:
        logger.exception("[smartlink] sanity check failed")


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


def smartlinks_help_text() -> str:
    return SMARTLINKS_HELP_TEXT


def build_smartlink_view_text(smartlink: dict) -> str:
    artist = smartlink.get("artist") or "Без артиста"
    title = smartlink.get("title") or "Без названия"
    rd = parse_date(smartlink.get("release_date") or "")
    lines = [f"{artist} — {title}"]
    if rd:
        lines.append(f"📅 {format_date_ru(rd)}")
    return "\n".join(lines)


def build_my_smartlinks_text(
    items: list[dict], page: int, total_pages: int, start_index: int
) -> str:
    if not items:
        return "У тебя пока нет смартлинков. Создай первый через «➕ Создать смарт-линк»."

    lines = [f"📎 Мои смартлинки (страница {page + 1}/{total_pages})", ""]
    for idx, item in enumerate(items, start=start_index + 1):
        artist = item.get("artist") or "Без артиста"
        title = item.get("title") or "Без названия"
        lines.append(f"{idx}. {artist} — {title}")
    return "\n".join(lines)


def build_my_smartlinks_kb(
    items: list[dict], page: int, total_pages: int, start_index: int
) -> InlineKeyboardMarkup:
    inline: list[list[InlineKeyboardButton]] = []

    for idx, item in enumerate(items, start=start_index + 1):
        artist_slug = str(item.get("artist_slug") or "").strip()
        slug = str(item.get("slug") or "").strip()
        smartlink_id = item.get("id")

        if not artist_slug or not slug:
            artist_slug, slug = get_smartlink_slugs(item)

        if not smartlink_id and artist_slug and slug:
            smartlink_id = build_smartlink_id(artist_slug, slug)

        canonical_url = (
            build_smartlink_web_url(artist_slug, slug) if artist_slug and slug else None
        )

        row: list[InlineKeyboardButton] = []

        if canonical_url:
            row.append(InlineKeyboardButton(text=f"{idx}. 🌐 Открыть", url=canonical_url))

        if smartlink_id:
            row.append(
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"smartlinks:edit:{smartlink_id}:p{page}",
                )
            )

        if row:
            inline.append(row)

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"smartlinks:my:{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"smartlinks:my:{page + 1}"))
    if nav_row:
        inline.append(nav_row)

    inline.append([InlineKeyboardButton(text="◀️ Назад", callback_data="smartlinks:menu")])

    return InlineKeyboardMarkup(inline_keyboard=inline)


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


def parse_page_marker(marker: str | None, default: int = 0) -> int:
    if not marker:
        return default
    marker = marker.strip()
    if marker.startswith("p") and marker[1:].lstrip("-").isdigit():
        return int(marker[1:])
    if marker.lstrip("-").isdigit():
        return int(marker)
    return default


def parse_smartlink_callback_data(data: str, tail_size: int) -> tuple[str, list[str]]:
    parts = (data or "").split(":")
    if len(parts) < 3 + tail_size:
        return "", []
    tail = parts[-tail_size:] if tail_size else []
    smartlink_parts = parts[2:-tail_size] if tail_size else parts[2:]
    smartlink_id = ":".join(part for part in smartlink_parts if part)
    return smartlink_id, tail


def build_smartlink_web_url(artist_slug: str, slug: str) -> str:
    if not artist_slug or not slug:
        return ""
    return f"{SMARTLINK_WEB_BASE}/{artist_slug}/{slug}"


def build_cover_proxy_url(artist_slug: str, slug: str) -> str:
    if not artist_slug or not slug:
        return ""
    base = COVER_PROXY_BASE
    if not base:
        return ""
    return f"{base}/api/cover/{artist_slug}/{slug}"


# ==================== Index Functions ====================

def extract_index_owner_tg_user_id(item: dict) -> str:
    value = item.get("owner_tg_user_id")
    if value is not None:
        raw = str(value).strip()
        if raw:
            return raw
    owner = item.get("owner")
    if isinstance(owner, dict):
        raw = str(owner.get("tg_user_id") or "").strip()
        if raw:
            return raw
    return ""


def extract_index_owner_fields(item: dict) -> tuple[str, str]:
    username = ""
    display_name = ""
    raw_username = item.get("owner_tg_username")
    raw_display = item.get("owner_display_name")
    if isinstance(raw_username, str) and raw_username.strip():
        username = raw_username.strip()
    if isinstance(raw_display, str) and raw_display.strip():
        display_name = raw_display.strip()
    owner = item.get("owner")
    if isinstance(owner, dict):
        if not username:
            owner_username = owner.get("username")
            if isinstance(owner_username, str) and owner_username.strip():
                username = owner_username.strip()
        if not display_name:
            owner_display = owner.get("display_name")
            if isinstance(owner_display, str) and owner_display.strip():
                display_name = owner_display.strip()
    return username, display_name


def normalize_index_smartlink(
    item: dict,
    owner_tg_user_id: int | str | None = None,
    artist_slug: str | None = None,
    slug: str | None = None,
) -> dict:
    artist_slug = (artist_slug or item.get("artist_slug") or "").strip()
    slug = (slug or item.get("slug") or "").strip()
    if not artist_slug or not slug:
        artist_slug, slug = get_smartlink_slugs(item)
    owner_raw = str(owner_tg_user_id).strip() if owner_tg_user_id is not None else ""
    if not owner_raw:
        owner_raw = extract_index_owner_tg_user_id(item)
    owner_tg_username, owner_display_name = extract_index_owner_fields(item)
    cover_source = item.get("cover_source") if isinstance(item.get("cover_source"), dict) else {}
    cover_file_id = item.get("cover_file_id") or cover_source.get("file_id") or ""
    links = item.get("links") if isinstance(item.get("links"), dict) else {}
    return {
        "id": build_smartlink_id(artist_slug, slug),
        "owner_tg_user_id": owner_raw,
        "owner_tg_username": owner_tg_username,
        "owner_display_name": owner_display_name,
        "artist": item.get("artist") or item.get("artist_name") or "",
        "title": item.get("title") or "",
        "release_date": item.get("release_date") or "",
        "cover_file_id": cover_file_id or "",
        "cover_source": cover_source or {},
        "links": links or {},
        "caption_text": item.get("caption_text") or "",
        "branding_disabled": bool(item.get("branding_disabled", False)),
        "branding_paid": bool(item.get("branding_paid", False)),
        "pre_save_enabled": bool(item.get("pre_save_enabled", True)),
        "reminders_enabled": bool(item.get("reminders_enabled", True)),
        "cover_url": item.get("cover_url"),
        "artist_slug": artist_slug,
        "slug": slug,
        "cover_version": int(item.get("cover_version") or 1),
    }


def smartlink_index_ready() -> bool:
    return bool(SMARTLINK_INDEX_BASE and SMARTLINK_API_KEY)


async def fetch_my_smartlinks_from_index(
    tg_id: int,
    page: int = 0,
    limit: int = 10,
) -> tuple[bool, list[dict] | None, int, int]:
    if not SMARTLINK_INDEX_BASE:
        logger.error("[smartlink-my] SMARTLINK_INDEX_BASE missing; skipping fetch")
        return False, None, 0, 1
    url = f"{SMARTLINK_INDEX_BASE}/api/my"
    headers = {}
    if SMARTLINK_API_KEY:
        headers["X-API-Key"] = SMARTLINK_API_KEY

    params = {
        "owner_tg_user_id": str(tg_id),
        "page": str(max(0, page)),
        "limit": str(limit),
    }

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, params=params) as resp:
                body = await resp.text()
                if not (200 <= resp.status < 300):
                    logger.warning("[smartlink-my] status=%s body=%s", resp.status, body)
                    return False, None, 0, 1
                data = await resp.json()
                items = data.get("items") or []
                total_pages = int(data.get("total_pages") or 1)
                total_count = int(data.get("total_count") or len(items))
                return True, items, total_count, max(1, total_pages)
    except Exception as e:
        logger.exception("[smartlink-my] error: %s", e)
        return False, None, 0, 1


async def fetch_smartlink_from_index(
    artist_slug: str, slug: str
) -> tuple[bool, dict | None, int | None]:
    artist_slug = str(artist_slug or "").strip()
    slug = str(slug or "").strip()
    if not SMARTLINK_INDEX_BASE or not artist_slug or not slug:
        return False, None, None
    if not SMARTLINK_API_KEY:
        logger.error(
            "[smartlink-fetch] SMARTLINK_API_KEY missing; skipping index fetch artist_slug=%s slug=%s",
            artist_slug,
            slug,
        )
        return False, None, None

    # Проверяем rate limit cooldown перед запросом
    if not await check_rate_limit_cooldown():
        logger.debug(
            "[smartlink-fetch] skipping request due to rate limit cooldown artist_slug=%s slug=%s",
            artist_slug,
            slug,
        )
        return False, None, 429

    url = f"{SMARTLINK_INDEX_BASE}/api/smartlinks/{artist_slug}/{slug}"
    headers = {"X-API-Key": SMARTLINK_API_KEY}
    try:
        session = await get_http_session()
        async with session.get(url, headers=headers) as resp:
            body = await resp.text()
            sanitized_body = _sanitize_body_for_logging(body)

            # Обработка Rate Limit
            if is_rate_limited_response(resp.status, body):
                logger.warning(
                    "[smartlink-fetch] rate limited artist_slug=%s slug=%s",
                    artist_slug,
                    slug,
                )
                await set_rate_limit_cooldown()
                return False, None, 429

            if not (200 <= resp.status < 300):
                log_missing_index_token(resp.status, body, "fetch_smartlink_from_index")
                logger.warning(
                    "[smartlink-fetch] response status=%s body=%s", resp.status, sanitized_body
                )
                return False, None, resp.status
            try:
                payload = await resp.json()
            except Exception:
                logger.warning(
                    "[smartlink-fetch] failed to parse json status=%s body=%s",
                    resp.status,
                    sanitized_body,
                )
                return False, None, resp.status

            item: dict | None = None
            if isinstance(payload, dict):
                for key in ("smartlink", "item", "data", "result"):
                    value = payload.get(key)
                    if isinstance(value, dict):
                        item = value
                        break
                if item is None:
                    item = payload
            elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
                item = payload[0]

            return True, item, resp.status
    except Exception as err:
        logger.warning("[smartlink-fetch] request error: %s", err)
        return False, None, None


async def fetch_owned_smartlink_with_fallback(
    tg_id: int, artist_slug: str, slug: str
) -> dict | None:
    artist_slug = str(artist_slug or "").strip()
    slug = str(slug or "").strip()
    if not artist_slug or not slug:
        return None

    if smartlink_index_ready():
        ok, item, status = await fetch_smartlink_from_index(artist_slug, slug)
        if ok:
            if isinstance(item, dict):
                owner_id = extract_index_owner_tg_user_id(item)
                if owner_id and owner_id != str(tg_id):
                    return None
                return normalize_index_smartlink(
                    item,
                    owner_tg_user_id=str(tg_id),
                    artist_slug=artist_slug,
                    slug=slug,
                )
            return None
        logger.warning(
            "[smartlink-fetch] index fetch failed artist_slug=%s slug=%s tg_id=%s status=%s",
            artist_slug,
            slug,
            tg_id,
            status,
        )
    else:
        logger.error(
            "[smartlink-index] SMARTLINK_INDEX_BASE or SMARTLINK_API_KEY missing; using D1 fallback tg_id=%s",
            tg_id,
        )

    return await fetch_owned_smartlink_from_d1(tg_id, artist_slug, slug)


async def fetch_owned_smartlink_by_smartlink_id(
    tg_id: int, smartlink_id: int | str
) -> dict | None:
    artist_slug, slug = parse_smartlink_id(smartlink_id)
    if artist_slug and slug:
        return await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
    if smartlink_id:
        return await fetch_owned_smartlink_by_id(tg_id, smartlink_id)
    return None


async def fetch_owned_smartlink_from_index(
    tg_id: int, artist_slug: str, slug: str
) -> dict | None:
    ok, item, _status = await fetch_smartlink_from_index(artist_slug, slug)
    if not ok or not isinstance(item, dict):
        return None
    owner_id = extract_index_owner_tg_user_id(item)
    if owner_id and owner_id != str(tg_id):
        return None
    return normalize_index_smartlink(item, owner_tg_user_id=str(tg_id), artist_slug=artist_slug, slug=slug)


async def fetch_smartlink_by_id(smartlink_id: int | str) -> dict | None:
    artist_slug, slug = parse_smartlink_id(smartlink_id)
    if not artist_slug or not slug:
        return None
    ok, item, _status = await fetch_smartlink_from_index(artist_slug, slug)
    if not ok or not isinstance(item, dict):
        return None
    return normalize_index_smartlink(item, artist_slug=artist_slug, slug=slug)


async def fetch_latest_smartlink_from_index(tg_id: int) -> dict | None:
    index_ok, items, _total_count, _total_pages = await fetch_my_smartlinks_from_index(
        tg_id, page=0, limit=1
    )
    if not index_ok or not items:
        return None
    item = items[0] if isinstance(items[0], dict) else None
    if not item:
        return None
    return normalize_index_smartlink(item, owner_tg_user_id=str(tg_id))


async def update_smartlink_in_index(
    artist_slug: str,
    slug: str,
    smartlink: dict,
    owner: dict | None = None,
    *,
    schedule_retry: bool = True,
    reason: str | None = None,
) -> tuple[bool, int | None, str | None]:
    if not SMARTLINK_INDEX_BASE:
        return False, None, "config_missing"
    if not SMARTLINK_API_KEY:
        logger.error(
            "[smartlink-index] SMARTLINK_API_KEY missing; skipping update artist_slug=%s slug=%s",
            artist_slug,
            slug,
        )
        return False, None, "missing_api_key"
    # Проверяем rate limit cooldown перед запросом
    if not await check_rate_limit_cooldown():
        logger.debug(
            "[smartlink-publish] skipping request due to rate limit cooldown artist_slug=%s slug=%s",
            artist_slug,
            slug,
        )
        return False, 429, "rate_limit_cooldown"

    index_url = f"{SMARTLINK_INDEX_BASE}/api/smartlinks/{artist_slug}/{slug}"
    headers = {"Content-Type": "application/json"}
    headers["X-API-Key"] = SMARTLINK_API_KEY
    payload = build_smartlink_index_payload(smartlink, owner=owner)
    if not payload:
        return False, None, "payload_invalid"
    try:
        session = await get_http_session()
        async with session.put(index_url, headers=headers, json=payload) as resp:
            body = await resp.text()
            sanitized_body = _sanitize_body_for_logging(body)

            # Обработка Rate Limit
            if is_rate_limited_response(resp.status, body):
                logger.warning(
                    "[smartlink-publish] rate limited artist_slug=%s slug=%s reason=%s",
                    artist_slug,
                    slug,
                    reason,
                )
                await set_rate_limit_cooldown()
                if schedule_retry:
                    await enqueue_smartlink_publish_retry(
                        artist_slug,
                        slug,
                        smartlink,
                        owner,
                        delay_seconds=min(SMARTLINK_PUBLISH_RETRY_DELAYS[-1], 300),  # До 5 минут для rate limit
                        last_error=f"status={resp.status} error=rate_limit",
                    )
                return False, 429, "rate_limit"

            if 200 <= resp.status < 300:
                logger.info(
                    "[smartlink-publish] ok artist_slug=%s slug=%s status=%s reason=%s",
                    artist_slug,
                    slug,
                    resp.status,
                    reason,
                )
                return True, resp.status, None
            log_missing_index_token(resp.status, body, "update_smartlink_in_index")
            if schedule_retry:
                await enqueue_smartlink_publish_retry(
                    artist_slug,
                    slug,
                    smartlink,
                    owner,
                    delay_seconds=SMARTLINK_PUBLISH_RETRY_DELAYS[0],
                    last_error=f"status={resp.status} error={sanitized_body}",
                )
            logger.warning(
                "[smartlink-publish] fail artist_slug=%s slug=%s status=%s error=%s reason=%s",
                artist_slug,
                slug,
                resp.status,
                sanitized_body,
                reason,
            )
            return False, resp.status, sanitized_body
    except Exception as err:
        if schedule_retry:
            await enqueue_smartlink_publish_retry(
                artist_slug,
                slug,
                smartlink,
                owner,
                delay_seconds=SMARTLINK_PUBLISH_RETRY_DELAYS[0],
                last_error=str(err),
            )
        logger.warning(
            "[smartlink-publish] fail artist_slug=%s slug=%s error=%s reason=%s",
            artist_slug,
            slug,
            err,
            reason,
        )
        return False, None, str(err)


async def confirm_smartlink_indexed(
    artist_slug: str, slug: str
) -> tuple[bool, int | None, str | None]:
    if not SMARTLINK_INDEX_BASE:
        return False, None, "config_missing"
    if not SMARTLINK_API_KEY:
        logger.error(
            "[smartlink-index] SMARTLINK_API_KEY missing; skipping confirm artist_slug=%s slug=%s",
            artist_slug,
            slug,
        )
        return False, None, "missing_api_key"
    # Проверяем rate limit cooldown перед запросом
    if not await check_rate_limit_cooldown():
        logger.debug(
            "[smartlink-index] skipping confirm request due to rate limit cooldown artist_slug=%s slug=%s",
            artist_slug,
            slug,
        )
        return False, 429, "rate_limit_cooldown"

    url = f"{SMARTLINK_INDEX_BASE}/api/smartlinks/{artist_slug}/{slug}"
    headers = {"X-API-Key": SMARTLINK_API_KEY}
    try:
        session = await get_http_session()
        async with session.get(url, headers=headers) as resp:
            body = await resp.text()
            sanitized_body = _sanitize_body_for_logging(body)

            # Обработка Rate Limit
            if is_rate_limited_response(resp.status, body):
                logger.warning(
                    "[smartlink-index] rate limited during confirm artist_slug=%s slug=%s",
                    artist_slug,
                    slug,
                )
                await set_rate_limit_cooldown()
                return False, 429, "rate_limit"

            if log_missing_index_token(resp.status, body, "confirm_smartlink_indexed"):
                return False, resp.status, sanitized_body
            if resp.status == 404:
                logger.error(
                    "[smartlink-index] confirm not found artist_slug=%s slug=%s",
                    artist_slug,
                    slug,
                )
                return False, resp.status, "not_found"
            if 200 <= resp.status < 300:
                return True, resp.status, None
            logger.warning(
                "[smartlink-index] confirm failed artist_slug=%s slug=%s status=%s body=%s",
                artist_slug,
                slug,
                resp.status,
                sanitized_body,
            )
            return False, resp.status, sanitized_body
    except Exception as err:
        logger.warning(
            "[smartlink-index] confirm error artist_slug=%s slug=%s error=%s",
            artist_slug,
            slug,
            err,
        )
        return False, None, str(err)


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
                timeout=2.0,
            )
            if smartlink:
                return True
        except asyncio.TimeoutError:
            logger.warning(
                "[smartlink-slug] D1 check timeout artist_slug=%s slug=%s", artist_slug, slug
            )
        except Exception:
            logger.exception(
                "[smartlink-slug] D1 check error artist_slug=%s slug=%s", artist_slug, slug
            )

    # Пропускаем проверку через API если skip_api_check=True (например, при rate limit)
    if skip_api_check:
        return False

    # Затем проверяем индекс, но с коротким таймаутом
    if smartlink_index_ready():
        try:
            ok, _item, status = await asyncio.wait_for(
                fetch_smartlink_from_index(artist_slug, slug), timeout=3.0
            )
            if ok:
                return True
            # 401 (unauthorized) означает проблему с аутентификацией, не то что slug занят
            # 429 (rate limit) - считаем slug свободным, чтобы не блокировать создание
            if status == 401 or status == 429:
                logger.debug(
                    "[smartlink-slug] index auth/rate_limit failed, treating as available artist_slug=%s slug=%s status=%s",
                    artist_slug,
                    slug,
                    status,
                )
                return False
            if status is not None:
                return status != 404
        except asyncio.TimeoutError:
            logger.warning(
                "[smartlink-slug] index check timeout artist_slug=%s slug=%s", artist_slug, slug
            )
            # При таймауте считаем, что slug свободен, чтобы не блокировать создание
            return False
        except Exception:
            logger.exception(
                "[smartlink-slug] index check error artist_slug=%s slug=%s", artist_slug, slug
            )
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
    while (
        await smartlink_slug_exists(
            artist_slug, candidate, owner_tg_user_id=owner_tg_user_id, skip_api_check=skip_api_check
        )
        and iteration < max_iterations
    ):
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
