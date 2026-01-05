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

    title_raw = (smartlink or {}).get("title") or ""
    slug = (smartlink or {}).get("slug") or _slugify(title_raw)

    return artist_slug, slug


def build_smartlink_index_payload(smartlink: dict) -> dict | None:
    artist_slug, slug = get_smartlink_slugs(smartlink)
    if not artist_slug or not slug:
        logger.warning(
            "[smartlink-index] missing slugs, skipping sync artist_slug=%s slug=%s",
            artist_slug,
            slug,
        )
        return None

    raw_links = (smartlink or {}).get("links")
    if raw_links is None:
        links: dict[str, str] = {}
    elif isinstance(raw_links, dict):
        invalid_keys = [k for k, v in raw_links.items() if not isinstance(v, str)]
        if invalid_keys:
            logger.warning(
                "[smartlink-index] links malformed (non-string values) keys=%s", invalid_keys
            )
            return None
        links = {k: v.strip() for k, v in raw_links.items() if isinstance(v, str) and v.strip()}
    else:
        logger.warning("[smartlink-index] links malformed type=%s", type(raw_links))
        return None

    cover_url_candidates: list[str] = []
    direct_cover_url = (smartlink or {}).get("cover_url")
    if isinstance(direct_cover_url, str):
        cover_url_candidates.append(direct_cover_url.strip())

    metadata = (smartlink or {}).get("metadata")
    if isinstance(metadata, dict):
        meta_cover_url = metadata.get("cover_url")
        if isinstance(meta_cover_url, str):
            cover_url_candidates.append(meta_cover_url.strip())
        sources = metadata.get("sources")
        if isinstance(sources, dict):
            for source_meta in sources.values():
                if not isinstance(source_meta, dict):
                    continue
                source_cover = source_meta.get("cover_url")
                if isinstance(source_cover, str):
                    cover_url_candidates.append(source_cover.strip())

    cover_url: str | None = None
    for candidate in cover_url_candidates:
        if candidate and re.match(r"^https?://", candidate):
            cover_url = candidate
            break

    cover_source_payload: dict | None = None
    cover_source_type = "none"

    if cover_url:
        cover_source_type = "external"
    else:
        cover_file_id = (smartlink or {}).get("cover_file_id")
        if isinstance(cover_file_id, str):
            cover_file_id = cover_file_id.strip()
            if cover_file_id:
                cover_source_type = "telegram"
                cover_source_payload = {"type": "telegram", "file_id": cover_file_id}

    logger.info("[smartlink-index] cover source=%s", cover_source_type)

    payload = {
        "artist_slug": artist_slug,
        "slug": slug,
        "title": (smartlink or {}).get("title") or "",
        "artist_name": (smartlink or {}).get("artist_name")
        or (smartlink or {}).get("artist")
        or "",
        "release_date": (smartlink or {}).get("release_date") or None,
        "links": links,
    }

    if cover_url:
        payload["cover_url"] = cover_url
    elif cover_source_payload:
        payload["cover_source"] = cover_source_payload

    return payload


async def push_smartlink_to_index(smartlink: dict) -> bool:
    base_url = normalize_base_url(os.getenv("SMARTLINK_INDEX_BASE"), DEFAULT_SMARTLINK_BASE)
    index_url = f"{base_url}/api/index/upsert"
    api_key = os.getenv("SMARTLINK_API_KEY")
    if not index_url:
        logger.info("[smartlink-index] index url is not configured, skipping")
        return False

    payload = build_smartlink_index_payload(smartlink)
    if not payload:
        logger.warning("[smartlink-index] payload invalid, skipping send")
        return False

    headers = {"Content-Type": "application/json", "X-Skip-Sync": "1"}
    if api_key:
        headers["X-API-Key"] = api_key

    timeout = aiohttp.ClientTimeout(total=15)
    logger.info("[smartlink-index] outgoing payload=%s", json.dumps(payload, ensure_ascii=False))
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(index_url, headers=headers, json=payload) as resp:
                try:
                    body = await resp.text()
                except Exception:
                    body = None
                truncated_body = (body[:1000] if body else body)
                logger.info(
                    "[smartlink-index] worker response status=%s body=%s",
                    resp.status,
                    truncated_body,
                )
                if 200 <= resp.status < 300:
                    return True
    except Exception as err:
        logger.warning("[smartlink-index] request error: %s", err)
    return False
