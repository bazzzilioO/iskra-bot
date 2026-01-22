"""Smartlink functions - extraction from bot.py for better code organization."""

import asyncio
import contextlib
import datetime as dt
from datetime import timezone
import json
import logging
import os
import traceback
from urllib.parse import urlparse

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message, User, InputMediaPhoto

from config import (
    ADMIN_TG_ID,
    BANDLINK_REFRESH_PLATFORMS,
    BANDLINK_USER_AGENT,
    COVER_PROXY_BASE,
    RATE_LIMIT_COOLDOWN_SECONDS,
    SMARTLINK_API_KEY,
    SMARTLINK_INDEX_BASE,
    SMARTLINK_PUBLISH_QUEUE_INTERVAL_SECONDS,
    SMARTLINK_PUBLISH_RETRY_DELAYS,
    SMARTLINK_UPDATE_DEBOUNCE_SECONDS,
    SMARTLINK_WEB_BASE,
    SONGLINK_PLATFORM_ALIASES,
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
    form_clear,
    form_get,
    form_set,
    form_start,
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
    smartlink_view_kb,
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
    # Index/D1 payloads may use different field names
    artist = smartlink.get("artist") or smartlink.get("artist_name") or "Без артиста"
    title = smartlink.get("title") or smartlink.get("track_title") or "Без названия"
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
        artist = item.get("artist") or item.get("artist_name") or "Без артиста"
        title = item.get("title") or item.get("track_title") or "Без названия"
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


def _index_api_key_candidates() -> list[str]:
    """Return a list of possible Index API keys (deployments may use different env var names)."""
    candidates = [
        SMARTLINK_API_KEY,
        os.getenv("ISKRA_API_KEY"),
        os.getenv("GO_API_KEY"),
        os.getenv("GO_API_TOKEN"),
        os.getenv("SMARTLINK_INDEX_TOKEN"),
        os.getenv("SMARTLINK_TOKEN"),
    ]
    uniq: list[str] = []
    for v in candidates:
        if not v:
            continue
        s = str(v).strip()
        if not s or s in uniq:
            continue
        uniq.append(s)
    return uniq


def _build_index_auth_headers(api_key: str) -> dict[str, str]:
    """Build auth headers for Index API; different deployments may accept different schemes."""
    key = str(api_key or "").strip()
    if not key:
        return {}
    # Send both: many backends accept one of them and ignore the other.
    return {"X-API-Key": key, "Authorization": f"Bearer {key}"}


async def fetch_my_smartlinks_from_index(
    tg_id: int,
    page: int = 0,
    limit: int = 10,
) -> tuple[bool, list[dict] | None, int, int]:
    if not SMARTLINK_INDEX_BASE:
        logger.error("[smartlink-my] SMARTLINK_INDEX_BASE missing; skipping fetch")
        return False, None, 0, 1
    url = f"{SMARTLINK_INDEX_BASE}/api/my"
    params = {
        "owner_tg_user_id": str(tg_id),
        "page": str(max(0, page)),
        "limit": str(limit),
    }

    api_keys = _index_api_key_candidates()
    # If there is no key at all, still try once (some deployments allow public access).
    header_attempts: list[dict[str, str]] = [_build_index_auth_headers(k) for k in api_keys] or [{}]

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            last_status: int | None = None
            last_body: str | None = None
            for headers in header_attempts:
                async with session.get(url, headers=headers, params=params) as resp:
                    body = await resp.text()
                    last_status, last_body = resp.status, body

                    if 200 <= resp.status < 300:
                        data = await resp.json()
                        items = data.get("items") or []
                        total_pages = int(data.get("total_pages") or 1)
                        total_count = int(data.get("total_count") or len(items))
                        return True, items, total_count, max(1, total_pages)

                    # If auth failed and we have more keys to try, keep going.
                    if resp.status == 401 and len(header_attempts) > 1:
                        continue

                    logger.warning("[smartlink-my] status=%s body=%s", resp.status, body)
                    return False, None, 0, 1

            logger.warning("[smartlink-my] status=%s body=%s", last_status, last_body)
            return False, None, 0, 1
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


# ==================== Message Sending & Update Functions ====================

async def get_release_reminder_state(tg_id: int, smartlink_id: int | str, allow_remind: bool) -> bool:
    if not allow_remind:
        return False
    if await is_smartlink_reminder_set(tg_id, smartlink_id):
        return True
    return await is_smartlink_subscribed(smartlink_id, tg_id)


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
                "[smartlink-update] unexpected error smartlink_id=%s chat_id=%s message_id=%s",
                smartlink_id,
                chat_id,
                message_id,
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


async def send_my_smartlinks(message: Message, tg_id: int, page: int = 0):
    ok, items, total_count, total_pages = await fetch_my_smartlinks_from_index(
        tg_id,
        page=page,
        limit=MY_SMARTLINKS_PAGE_SIZE,
    )
    if not ok or items is None:
        # Helpful diagnostics: in production D1 may be disabled and Index may require an API key
        if not SMARTLINK_INDEX_BASE or not SMARTLINK_API_KEY:
            await message.answer(
                "⚠️ Смартлинки сейчас не настроены: нет доступа к индексу (SMARTLINK_INDEX_BASE/SMARTLINK_API_KEY) "
                "и нет локальной D1 (SMARTLINK_D1_PATH). Если ты разворачиваешь бота на Railway — добавь эти переменные "
                "в окружение и перезапусти сервис.",
                reply_markup=smartlinks_menu_kb(),
            )
            # Continue to attempt D1 fallback below (may still work)
        fallback_count = await count_owned_smartlinks(tg_id)
        fallback_items = await list_owned_smartlinks(
            tg_id,
            limit=MY_SMARTLINKS_PAGE_SIZE,
            offset=max(0, page) * MY_SMARTLINKS_PAGE_SIZE,
        )
        if fallback_count is not None and fallback_items is not None:
            total_count = fallback_count
            items = fallback_items
            total_pages = max(
                1,
                (total_count + MY_SMARTLINKS_PAGE_SIZE - 1) // MY_SMARTLINKS_PAGE_SIZE,
            )
            await message.answer(
                "⚠️ Не удалось получить список из индекса. Показываю данные из D1.",
                reply_markup=smartlinks_menu_kb(),
            )
        else:
            await message.answer(
                "❌ Не удалось получить список смартлинков из D1. Попробуй позже.",
                reply_markup=smartlinks_menu_kb(),
            )
            return

    page = max(0, min(page, total_pages - 1))
    start_index = page * MY_SMARTLINKS_PAGE_SIZE
    text = build_my_smartlinks_text(items, page, total_pages, start_index)
    kb = build_my_smartlinks_kb(items, page, total_pages, start_index)
    await message.answer(text, reply_markup=kb)
    return


async def send_smartlink_list(message: Message, tg_id: int, page: int = 0):
    await message.answer(
        "Локальные черновики отключены. Список доступен в «📎 Мои смартлинки».",
        reply_markup=smartlinks_menu_kb(),
    )


async def show_smartlink_view(
    message: Message, tg_id: int, artist_slug: str, slug: str, page: int
):
    smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
    if not smartlink:
        await message.answer(
            "Смартлинк не найден или временно недоступен.",
            reply_markup=smartlinks_menu_kb(),
        )
        return
    text = build_smartlink_view_text(smartlink)
    await message.answer(text, reply_markup=smartlink_view_kb(smartlink, page))


async def resend_smartlink_card(message: Message, tg_id: int, smartlink: dict, page: int):
    allow_remind = smartlink_can_remind(smartlink)
    subscribed = await get_release_reminder_state(tg_id, smartlink.get("id"), allow_remind)
    await send_smartlink_photo(message.bot, tg_id, smartlink, subscribed=subscribed, allow_remind=allow_remind, page=page)
    await message.answer("Выбери действие:", reply_markup=smartlink_view_kb(smartlink, page))


# ==================== Form Functions ====================

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


async def refresh_smartlink_links_from_bandlink(smartlink: dict) -> dict[str, str]:
    """Refresh smartlink links from BandLink - lazy import to avoid circular dependencies."""
    from bot import fetch_bandlink_html, parse_bandlink
    
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

    logger.info(
        "[smartlink] wizard started tg_id=%s initial_step=%s prefilled=%s", tg_id, step, bool(prefill)
    )

    total_steps = 5 + len(SMARTLINK_PLATFORMS)
    if step >= total_steps:
        await finalize_smartlink_form(message, tg_id, data)
        return

    await _send_smartlink_prompt(message, tg_id, step, data)


async def start_smartlink_import(message: Message, tg_id: int):
    """Start smartlink import - lazy import to avoid circular dependencies."""
    from bot import user_menu_keyboard
    
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


async def finalize_smartlink_form(message: Message, tg_id: int, data: dict):
    """Finalize smartlink form - lazy import to avoid circular dependencies."""
    from bot import user_menu_keyboard
    
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


# ==================== Import & Prefill Helper Functions ====================

def pick_selected_metadata(data: dict) -> dict:
    """Pick selected metadata from data dict."""
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
    """Start prefill editor - lazy import to avoid circular dependencies."""
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
    """Apply Spotify UPC selection - lazy import to avoid circular dependencies."""
    from bot import user_menu_keyboard
    
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
    """Apply caption update - lazy import to avoid circular dependencies."""
    from bot import user_menu_keyboard
    
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


async def fetch_cover_file(cover_url: str) -> BufferedInputFile | None:
    """Fetch cover file from URL."""
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


async def maybe_upgrade_smartlink_cover_from_photo(message: Message) -> bool:
    """Handle lazy Telegram cover upgrade when a photo is sent outside forms.

    Returns True if the photo was processed (including skipped with logging),
    False if the message was not a photo and should be handled elsewhere.
    """
    if not message.photo:
        return False

    tg_id = message.from_user.id
    latest = await fetch_latest_smartlink_from_index(tg_id)

    if not latest:
        logger.info("[cover-upgrade] skip: no smartlink context tg_id=%s", tg_id)
        return True

    cover_source = latest.get("cover_source") if isinstance(latest.get("cover_source"), dict) else {}
    cover_file_id = (latest.get("cover_file_id") or "").strip()
    if (cover_source.get("type") or cover_file_id):
        logger.info(
            "[cover-upgrade] skip: cover already exists smartlink_id=%s", latest.get("id")
        )
        return True

    file_id = message.photo[-1].file_id if message.photo else ""
    if not file_id or file_id.isdigit():
        logger.info(
            "[cover-upgrade] skip: invalid file_id smartlink_id=%s file_id=%s",
            latest.get("id"),
            file_id,
        )
        return True

    updates = {
        "cover_file_id": file_id,
        "cover_source": {"type": "telegram", "file_id": file_id},
        "cover_updated_at": dt.datetime.now(timezone.utc).isoformat(),
    }
    updates.update(build_owner_cover_updates(latest, message.from_user, message.bot))
    artist_slug = (latest.get("artist_slug") or "").strip()
    slug = (latest.get("slug") or "").strip()
    if not artist_slug or not slug:
        artist_slug, slug = await build_unique_smartlink_slugs(
            latest.get("artist", ""),
            latest.get("title", ""),
            owner_tg_user_id=tg_id,
        )
    cover_url = build_cover_proxy_url(artist_slug, slug) if artist_slug and slug else ""
    if cover_url:
        updates.update({"cover_url": cover_url, "artist_slug": artist_slug, "slug": slug})

    updated_payload = {**latest, **updates}
    updated_payload["cover_version"] = int(latest.get("cover_version") or 1) + 1
    index_ok, status, error = await update_smartlink_in_index(
        artist_slug,
        slug,
        updated_payload,
        owner=build_owner_payload(message.from_user),
    )
    if not index_ok:
        logger.warning(
            "[cover-upgrade] index update failed artist_slug=%s slug=%s status=%s error=%s",
            artist_slug,
            slug,
            status,
            error,
        )
        return True
    latest = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug) or updated_payload
    logger.info(
        "[cover-upgrade] success smartlink_id=%s file_id=%s", latest.get("id"), file_id
    )

    if latest.get("id"):
        schedule_smartlink_update(message.bot, latest.get("id"))

    return True


# ==================== Import Helper Functions ====================

def filter_human_sources(sources: dict[str, dict]) -> dict[str, dict]:
    """Filter sources to only include human metadata platforms."""
    filtered: dict[str, dict] = {}
    for key, meta in (sources or {}).items():
        normalized_key = SONGLINK_PLATFORM_ALIASES.get(key, key)
        if normalized_key not in HUMAN_METADATA_PLATFORMS:
            continue
        filtered.setdefault(normalized_key, meta or {})
    return filtered


async def show_import_confirmation(
    message: Message,
    tg_id: int,
    links: dict[str, str],
    metadata: dict | None,
    latest: dict | None = None,
):
    """Show import confirmation - lazy import to avoid circular dependencies."""
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
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="smartlink:prefill_edit")],
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
