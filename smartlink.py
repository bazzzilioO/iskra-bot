"""Smartlink functions - extraction from bot.py for better code organization."""

import asyncio
import contextlib
import datetime as dt
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
    enqueue_smartlink_publish_retry,
    list_due_smartlink_publish_jobs,
    update_smartlink_publish_job,
    delete_smartlink_publish_job,
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
