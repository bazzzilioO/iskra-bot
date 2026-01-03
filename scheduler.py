import asyncio
import datetime as dt
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from db import (
    cleanup_reminder_log,
    DEFAULT_TIMEZONE,
    get_due_smartlink_reminders,
    get_reminder_users,
    get_smartlink_by_id,
    get_smartlink_subscribers,
    get_smartlinks_with_release,
    get_user_reminder_prefs,
    mark_reminder_sent,
    mark_smartlink_reminder_sent,
    mark_smartlink_day_sent,
    mark_smartlink_notified,
    was_reminder_sent,
    was_smartlink_reminder_sent,
    was_smartlink_day_sent,
)
from helpers import parse_date

REMINDER_INTERVAL_SECONDS = 300
REMINDER_LAST_CLEAN: dt.date | None = None

DEADLINES = [
    {"key": "pitching", "title": "Pitching (Spotify / Яндекс / VK / Звук / МТС-КИОН)", "offset": -14},
    {"key": "presave", "title": "Pre-save", "offset": -7},
    {"key": "bandlink", "title": "BandLink / Smartlink", "offset": -7},
    {"key": "content_sprint", "title": "Контент-спринт ДО — старт", "offset": -14},
    {"key": "post_1", "title": "Пост-релиз план (+1)", "offset": 1},
    {"key": "post_3", "title": "Пост-релиз план (+3)", "offset": 3},
    {"key": "post_7", "title": "Пост-релиз план (+7)", "offset": 7},
]


def build_deadlines(release_date: dt.date) -> list[tuple[str, str, dt.date]]:
    items: list[tuple[str, str, dt.date]] = []
    for d in DEADLINES:
        items.append((d["key"], d["title"], release_date + dt.timedelta(days=d["offset"])))
    return sorted(items, key=lambda x: x[2])


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


async def process_smartlink_notifications(bot: Bot, send_smartlink_photo: Callable[..., Awaitable]):
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


async def process_smartlink_release_day_reminders(bot: Bot, send_smartlink_photo: Callable[..., Awaitable]):
    today = dt.datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()
    due = await get_due_smartlink_reminders(today.isoformat())

    for smartlink_id, tg_id in due:
        try:
            if await was_smartlink_reminder_sent(tg_id, smartlink_id):
                continue

            try:
                sid = int(smartlink_id)
            except Exception:
                continue

            smartlink = await get_smartlink_by_id(sid)
            if not smartlink:
                continue

            await send_smartlink_photo(
                bot,
                tg_id,
                smartlink,
                release_today=True,
                subscribed=True,
                allow_remind=False,
            )
            await mark_smartlink_reminder_sent(tg_id, smartlink_id)
        except TelegramForbiddenError:
            continue
        except Exception:
            continue


async def reminder_scheduler(bot: Bot, send_smartlink_photo: Callable[..., Awaitable]):
    while True:
        try:
            await asyncio.gather(
                process_reminders(bot),
                process_smartlink_notifications(bot, send_smartlink_photo),
                process_smartlink_release_day_reminders(bot, send_smartlink_photo),
            )
        except Exception as err:
            print(f"[reminder_scheduler] failed: {err}")
        await asyncio.sleep(REMINDER_INTERVAL_SECONDS)
