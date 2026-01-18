from __future__ import annotations

from typing import Final

from helpers import build_canonical_smartlink_url_from_smartlink


_LOCALES: Final[dict[str, dict[str, str]]] = {
    "ru": {
        "missing_artist": "Без артиста",
        "missing_title": "Без названия",
        "listen_header": "▶️ Слушать:",
        "vk_intro": "Новый релиз уже доступен 👇",
        "release_links": "Release links:",
        "canonical_missing": "Ссылка пока не готова.",
    },
    "en": {
        "missing_artist": "Unknown artist",
        "missing_title": "Untitled",
        "listen_header": "▶️ Listen:",
        "vk_intro": "New release is out 👇",
        "release_links": "Release links:",
        "canonical_missing": "Link is not ready yet.",
    },
}


def _locale(language: str) -> dict[str, str]:
    return _LOCALES.get(language, _LOCALES["ru"])


def build_smartlink_title_line(smartlink: dict, *, language: str = "ru") -> str:
    locale = _locale(language)
    artist = smartlink.get("artist") or locale["missing_artist"]
    title = smartlink.get("title") or locale["missing_title"]
    return f"{artist} — {title}"


def build_copy_links_text(smartlink: dict, *, language: str = "ru") -> str:
    locale = _locale(language)
    title_line = build_smartlink_title_line(smartlink, language=language)
    canonical_url = build_canonical_smartlink_url_from_smartlink(smartlink)
    if not canonical_url:
        return "\n".join([title_line, "", locale["canonical_missing"]])
    return "\n".join([title_line, "", canonical_url])


def build_smartlink_export_text(
    smartlink: dict,
    variant: str,
    *,
    language: str = "ru",
) -> str:
    locale = _locale(language)
    title_line = build_smartlink_title_line(smartlink, language=language)
    canonical_url = build_canonical_smartlink_url_from_smartlink(smartlink)
    if not canonical_url:
        return ""

    if variant == "links":
        return canonical_url

    if variant == "tg":
        return "\n".join([title_line, locale["listen_header"], canonical_url])

    if variant == "vk":
        return "\n".join([title_line, locale["vk_intro"], canonical_url])

    if variant == "universal":
        return "\n".join([title_line, locale["release_links"], canonical_url])

    return ""
