import datetime as dt
import html
import json
import logging
import os
import re

import aiohttp

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message


def escape_html(text: str | None) -> str:
    return html.escape(text or "")


logger = logging.getLogger(__name__)


DEFAULT_SMARTLINK_BASE = "https://go.sreda.pw"


def normalize_base_url(base: str | None, default: str = DEFAULT_SMARTLINK_BASE) -> str:
    base = (base or "").strip()
    if not base:
        base = default
    if not re.match(r"^https?://", base):
        base = f"https://{base}"
    return base.rstrip("/")


def format_date_ru(value: dt.date | dt.datetime | str | None) -> str:
    if isinstance(value, dt.datetime):
        value = value.date()
    if isinstance(value, str):
        parsed = parse_date(value)
        value = parsed if parsed else None
    if isinstance(value, dt.date):
        return value.strftime("%d.%m.%Y")
    return ""


async def safe_edit(target: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> Message | None:
    try:
        await target.edit_text(text, reply_markup=reply_markup)
        return target
    except TelegramBadRequest:
        return target
    except Exception as edit_err:
        try:
            return await target.answer(text, reply_markup=reply_markup)
        except Exception as answer_err:
            print(f"[safe_edit] edit failed: {edit_err}; answer failed: {answer_err}")
            return None


def parse_date(date_str: str) -> dt.date | None:
    """
    Понимает:
      - YYYY-MM-DD
      - DD.MM.YYYY
    """
    s = (date_str or "").strip()
    if not s:
        return None
    try:
        normalized = re.sub(r"[\s,/-]+", ".", s)
        if normalized:
            parts = normalized.split(".")
            if len(parts) == 3:
                first, second, third = parts
                if len(first) == 4:
                    y, m, d = first, second, third
                else:
                    d, m, y = first, second, third
                return dt.date(int(y), int(m), int(d))
    except Exception:
        logger.warning("[parse_date] failed to parse raw='%s'", date_str)
        return None
    return None


def smartlink_pre_save_active(smartlink: dict) -> bool:
    if not smartlink:
        return False
    rd = parse_date(smartlink.get("release_date") or "")
    return bool(rd and rd > dt.date.today() and smartlink.get("pre_save_enabled", True))


def smartlink_can_remind(smartlink: dict) -> bool:
    rd = parse_date(smartlink.get("release_date") or "") if smartlink else None
    return bool(rd and rd > dt.date.today() and smartlink.get("reminders_enabled", True))


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


def _slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[ _]+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")


def get_smartlink_slugs(smartlink: dict) -> tuple[str, str]:
    artist_raw = (smartlink or {}).get("artist") or ""
    artist_slug = (smartlink or {}).get("artist_slug") or _slugify(artist_raw)
    if not artist_slug:
        artist_slug = f"artist-{smartlink.get('id') if smartlink else 'unknown'}"

    title_raw = (smartlink or {}).get("title") or ""
    slug = (smartlink or {}).get("slug") or _slugify(title_raw)
    if not slug:
        slug = f"release-{smartlink.get('id') if smartlink else 'unknown'}"

    return artist_slug, slug


async def push_smartlink_to_index(smartlink: dict) -> bool:
    base_url = normalize_base_url(os.getenv("SMARTLINK_INDEX_BASE"), DEFAULT_SMARTLINK_BASE)
    index_url = f"{base_url}/api/index/upsert"
    api_key = os.getenv("SMARTLINK_API_KEY")
    if not index_url:
        logger.info("[smartlink-index] index url is not configured, skipping")
        return False

    artist_slug, slug = get_smartlink_slugs(smartlink)
    artist_raw = (smartlink or {}).get("artist") or ""
    title_raw = (smartlink or {}).get("title") or ""

    artist_name = (smartlink or {}).get("artist_name") or artist_raw
    if not artist_name and artist_slug:
        artist_name = artist_slug.replace("-", " ").upper()

    links = (smartlink or {}).get("links") or {}
    payload = {
        "id": str((smartlink or {}).get("id")),
        "artist_slug": artist_slug,
        "slug": slug,
        "title": title_raw,
        "artist_name": artist_name,
        "release_date": (smartlink or {}).get("release_date"),
        "cover_source": (smartlink or {}).get("cover_source"),
        "links_json": json.dumps(links, ensure_ascii=False),
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(index_url, headers=headers, json=payload) as resp:
                if 200 <= resp.status < 300:
                    logger.info("Indexed smartlink: %s/%s", artist_slug, slug)
                    return True
                try:
                    body = await resp.text()
                except Exception:
                    body = None
                logger.warning(
                    "[smartlink-index] failed status=%s body=%s", resp.status, body
                )
    except Exception as err:
        logger.warning("[smartlink-index] request error: %s", err)
    return False
