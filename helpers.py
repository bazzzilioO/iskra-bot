import asyncio
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
SMARTLINK_WEB_BASE = None


def _is_html_response(body: str | None) -> bool:
    """Проверяет, является ли ответ HTML страницей (например, от Cloudflare Rate Limit)."""
    if not body:
        return False
    body_lower = body.strip().lower()
    return body_lower.startswith("<!doctype") or body_lower.startswith("<html") or "<title>" in body_lower[:500]


def _sanitize_body_for_logging(body: str | None, max_length: int = 200) -> str:
    """Очищает тело ответа для логирования - убирает HTML и обрезает длинные строки."""
    if not body:
        return ""
    if _is_html_response(body):
        # Для HTML ищем название ошибки
        if "rate limited" in body.lower():
            return "<HTML: Cloudflare Rate Limit>"
        if "error" in body.lower():
            return "<HTML: Error page>"
        return "<HTML response>"
    if len(body) > max_length:
        return body[:max_length] + "..."
    return body


def log_missing_index_token(status: int | None, body: str | None, context: str) -> bool:
    normalized_body = (body or "").lower()
    if status == 500 or "missing_index_token" in normalized_body:
        logger.fatal(
            "[smartlink-index] missing index token context=%s status=%s body=%s",
            context,
            status,
            _sanitize_body_for_logging(body),
        )
        return True
    return False


def normalize_base_url(base: str | None, default: str | None = DEFAULT_SMARTLINK_BASE) -> str:
    base = (base or "").strip()
    if not base:
        if default is None:
            return ""
        base = default
    if not re.match(r"^https?://", base):
        base = f"https://{base}"
    return base.rstrip("/")


SMARTLINK_WEB_BASE = normalize_base_url(os.getenv("SMARTLINK_WEB_BASE"), DEFAULT_SMARTLINK_BASE)


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
            logger.warning("[safe_edit] edit failed: %s; answer failed: %s", edit_err, answer_err)
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
            logger.warning("[safe_edit_caption] edit failed: %s; answer failed: %s", edit_err, answer_err)
            return None


_CYRILLIC_TRANSLIT_MAP = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def slugify(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    transliterated = "".join(_CYRILLIC_TRANSLIT_MAP.get(ch, ch) for ch in raw)
    transliterated = re.sub(r"[^a-z0-9\s_-]", "", transliterated)
    transliterated = re.sub(r"[\s_]+", "-", transliterated)
    transliterated = re.sub(r"-{2,}", "-", transliterated)
    return transliterated.strip("-")


def get_smartlink_slugs(smartlink: dict) -> tuple[str, str]:
    artist_raw = (smartlink or {}).get("artist") or ""
    artist_slug = (smartlink or {}).get("artist_slug") or slugify(artist_raw) or "artist"

    title_raw = (smartlink or {}).get("title") or ""
    slug = (smartlink or {}).get("slug") or slugify(title_raw) or "untitled"

    return artist_slug, slug


def build_smartlink_id(artist_slug: str, slug: str) -> str:
    if not artist_slug or not slug:
        return ""
    return f"{artist_slug}:{slug}"


def parse_smartlink_id(smartlink_id: int | str | None) -> tuple[str, str]:
    if smartlink_id is None:
        return "", ""
    raw = str(smartlink_id)
    if ":" not in raw:
        return "", ""
    artist_slug, slug = raw.split(":", 1)
    return artist_slug.strip(), slug.strip()


def build_smartlink_key(artist_slug: str, slug: str) -> str:
    if not artist_slug or not slug:
        return ""
    return f"{artist_slug}|{slug}"


def parse_smartlink_key(key: str | None) -> tuple[str, str]:
    raw = (key or "").strip()
    if "|" not in raw:
        return "", ""
    artist_slug, slug = raw.split("|", 1)
    return artist_slug.strip(), slug.strip()


def build_smartlink_index_payload(
    smartlink: dict, owner: dict | None = None
) -> dict | None:
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

    cover_source = (smartlink or {}).get("cover_source")
    telegram_file_id: str | None = None

    if isinstance(cover_source, dict):
        raw_file_id = str(cover_source.get("file_id") or "").strip()
        if raw_file_id:
            if raw_file_id.isdigit():
                logger.warning(
                    "[smartlink-index] telegram cover malformed file_id=%s", raw_file_id
                )
            else:
                telegram_file_id = raw_file_id
        source_type = cover_source.get("type")
        if source_type and source_type != "telegram" and telegram_file_id:
            logger.warning(
                "[smartlink-index] overriding cover_source type=%s to telegram", source_type
            )
        elif source_type and source_type != "telegram":
            logger.warning(
                "[smartlink-index] cover source unsupported type=%s", source_type
            )
        elif not source_type and raw_file_id:
            logger.info("[smartlink-index] cover source missing type; assuming telegram")

    if not telegram_file_id:
        cover_file_id = (smartlink or {}).get("cover_file_id")
        if isinstance(cover_file_id, str):
            cover_file_id = cover_file_id.strip()
            if cover_file_id:
                if cover_file_id.isdigit():
                    logger.warning(
                        "[smartlink-index] telegram cover malformed file_id=%s", cover_file_id
                    )
                else:
                    telegram_file_id = cover_file_id

    if telegram_file_id:
        cover_source_type = "telegram"
        cover_source_payload = {"type": "telegram", "file_id": telegram_file_id}
        # Prefer an explicit cover_url already present in the smartlink payload (e.g. bot cover proxy).
        # Fallback to SMARTLINK_WEB_BASE only if nothing was provided.
        if not cover_url:
            cover_url = f"{SMARTLINK_WEB_BASE}/api/cover/{artist_slug}/{slug}"
    elif cover_url:
        cover_source_type = "external"
    else:
        logger.info("[smartlink-index] cover missing; indexing without cover")

    logger.info("[smartlink-index] cover source=%s", cover_source_type)

    cover_version_raw = (smartlink or {}).get("cover_version")
    try:
        cover_version = int(cover_version_raw)
    except Exception:
        cover_version = 1
    if cover_version <= 0:
        cover_version = 1

    payload = {
        "artist_slug": artist_slug,
        "slug": slug,
        "title": (smartlink or {}).get("title") or "",
        "artist": (smartlink or {}).get("artist") or (smartlink or {}).get("artist_name") or "",
        "artist_name": (smartlink or {}).get("artist_name")
        or (smartlink or {}).get("artist")
        or "",
        "release_date": (smartlink or {}).get("release_date") or None,
        "links": links,
        "cover_version": cover_version,
    }
    owner_tg_user_id = (smartlink or {}).get("owner_tg_user_id")
    owner_tg_username = (smartlink or {}).get("owner_tg_username")
    owner_display_name = (smartlink or {}).get("owner_display_name")
    if isinstance(owner, dict):
        if owner_tg_user_id is None:
            owner_tg_user_id = owner.get("tg_user_id") or owner.get("owner_tg_user_id")
        if not owner_tg_username:
            owner_tg_username = owner.get("username") or owner.get("owner_tg_username")
        if not owner_display_name:
            owner_display_name = owner.get("display_name") or owner.get("owner_display_name")
    if owner_tg_user_id is not None:
        payload["owner_tg_user_id"] = str(owner_tg_user_id).strip()
    if owner_tg_username:
        payload["owner_tg_username"] = owner_tg_username
    if owner_display_name:
        payload["owner_display_name"] = owner_display_name

    caption_text = (smartlink or {}).get("caption_text")
    if caption_text is not None:
        payload["caption_text"] = caption_text

    for flag_name in ("branding_disabled", "branding_paid", "pre_save_enabled", "reminders_enabled"):
        if flag_name in (smartlink or {}):
            payload[flag_name] = bool((smartlink or {}).get(flag_name))

    if cover_source_payload:
        payload["cover_source"] = cover_source_payload
    if cover_url:
        payload["cover_url"] = cover_url
    if owner:
        payload["owner"] = owner

    return payload


async def push_smartlink_to_index(
    smartlink: dict, owner: dict | None = None
) -> tuple[bool, int | None, str | None]:
    base_url = normalize_base_url(os.getenv("SMARTLINK_INDEX_BASE"), None)
    index_url = f"{base_url}/api/index/upsert"
    api_key = (
        os.getenv("SMARTLINK_API_KEY")
        or os.getenv("GO_API_KEY")
        or os.getenv("GO_API_TOKEN")
        or os.getenv("SMARTLINK_INDEX_TOKEN")
        or os.getenv("SMARTLINK_TOKEN")
    )
    if not base_url:
        logger.info("[smartlink-index] index url is not configured, skipping")
        return False, None, "config_missing"

    payload = build_smartlink_index_payload(smartlink, owner=owner)
    if not payload:
        logger.warning("[smartlink-index] payload invalid, skipping send")
        return False, None, "payload_invalid"
    if not api_key:
        logger.error("[smartlink-index] SMARTLINK_API_KEY missing; skipping send")
        return False, None, "missing_api_key"

    headers = {"Content-Type": "application/json", "X-Skip-Sync": "1"}
    headers["X-API-Key"] = api_key

    timeout = aiohttp.ClientTimeout(total=15)
    redacted_payload = json.dumps(payload, ensure_ascii=False)
    logger.info("[smartlink-index] outgoing payload=%s", redacted_payload)

    max_attempts = 3
    last_status: int | None = None
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(index_url, headers=headers, json=payload) as resp:
                    try:
                        body = await resp.text()
                    except Exception:
                        body = None
                    sanitized_body = _sanitize_body_for_logging(body)
                    logger.info(
                        "[smartlink-index] worker response status=%s body=%s",
                        resp.status,
                        sanitized_body,
                    )
                    last_status = resp.status
                    
                    # Обработка Rate Limit (429) от Cloudflare
                    if resp.status == 429 or (body and _is_html_response(body) and "rate limited" in body.lower()):
                        last_error = "rate_limit"
                        logger.warning(
                            "[smartlink-index] rate limited by Cloudflare, will retry with longer delay"
                        )
                        if attempt < max_attempts:
                            # Для rate limit используем более длинную задержку
                            backoff_seconds = min(60 * attempt, 300)  # До 5 минут
                            logger.info(
                                "[smartlink-index] retrying after rate limit backoff=%ss attempt=%s",
                                backoff_seconds,
                                attempt + 1,
                            )
                            await asyncio.sleep(backoff_seconds)
                            continue
                        return False, 429, "rate_limit"
                    
                    if log_missing_index_token(resp.status, body, "push_smartlink_to_index"):
                        return False, resp.status, sanitized_body
                    if 200 <= resp.status < 300:
                        verified, verify_status, verify_error = await _verify_index_entry(
                            session, base_url, payload["artist_slug"], payload["slug"], headers
                        )
                        if verified:
                            return True, resp.status, None
                        return False, verify_status, verify_error
                    last_error = sanitized_body
                    if resp.status < 500:
                        return False, resp.status, sanitized_body
        except Exception as err:
            last_status = None
            last_error = str(err)
            logger.warning("[smartlink-index] request error attempt=%s error=%s", attempt, err)

        if attempt < max_attempts:
            backoff_seconds = 2 ** (attempt - 1)
            logger.info(
                "[smartlink-index] retrying attempt=%s backoff=%ss status=%s", 
                attempt + 1,
                backoff_seconds,
                last_status,
            )
            await asyncio.sleep(backoff_seconds)

    return False, last_status, last_error


async def _verify_index_entry(
    session: aiohttp.ClientSession,
    base_url: str,
    artist_slug: str,
    slug: str,
    headers: dict,
) -> tuple[bool, int | None, str | None]:
    verify_url = f"{base_url}/api/smartlinks/{artist_slug}/{slug}"
    try:
        async with session.get(verify_url, headers=headers) as resp:
            status = resp.status
            try:
                body = await resp.text()
            except Exception:
                body = None
            sanitized_body = _sanitize_body_for_logging(body)
            if log_missing_index_token(status, body, "verify_index_entry"):
                return False, status, sanitized_body
            if status == 404:
                logger.error(
                    "[smartlink-index] verify failed: not found artist_slug=%s slug=%s",
                    artist_slug,
                    slug,
                )
                return False, status, "not_found"
            if 200 <= status < 300:
                return True, status, None
            logger.warning(
                "[smartlink-index] verify failed artist_slug=%s slug=%s status=%s body=%s",
                artist_slug,
                slug,
                status,
                sanitized_body,
            )
            return False, status, sanitized_body
    except Exception as err:
        logger.warning(
            "[smartlink-index] verify error artist_slug=%s slug=%s error=%s",
            artist_slug,
            slug,
            err,
        )
        return False, None, str(err)
