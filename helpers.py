import datetime as dt
import html

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message


def escape_html(text: str | None) -> str:
    return html.escape(text or "")


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
    try:
        if "-" in s:
            y, m, d = s.split("-")
            return dt.date(int(y), int(m), int(d))
        if "." in s:
            d, m, y = s.split(".")
            return dt.date(int(y), int(m), int(d))
    except Exception:
        return None
    return None


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
