# Handlers module - all command and callback handlers extracted from bot.py

import asyncio
import datetime as dt
import logging
import re
import smtplib
from email.mime.text import MIMEText

from aiogram import Bot, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.exceptions import TelegramForbiddenError

# Import dp from bot.py - handlers need it for decorators
# This will be imported after bot.py creates dp, avoiding circular import issues
from bot import dp, DONATE_MAX_STARS, DONATE_MIN_STARS

logger = logging.getLogger(__name__)

# Import from other modules (lazy imports inside functions where needed)
from db import (
    ensure_user as db_ensure_user,
    form_clear,
    form_get,
    form_set,
    form_start,
    get_accounts_state,
    get_export_unlocked,
    get_experience,
    get_focus_show_completed,
    get_important_tasks,
    get_last_update_notified,
    get_release_date,
    get_reminders_enabled,
    get_tasks_state,
    get_updates_opt_in,
    get_updates_opt_in_users,
    set_export_unlocked,
    set_experience,
    set_focus_show_completed,
    set_last_update_notified,
    set_release_date,
    set_smartlink_subscription,
    set_task_done,
    toggle_important_task,
    toggle_reminders_enabled,
    toggle_task_and_get_state,
    toggle_updates_opt_in,
    reset_all_data,
    reset_progress_only,
    save_qc_check,
    cycle_account_status as db_cycle_account_status,
    add_smartlink_reminder,
    remove_smartlink_reminder,
    list_recent_smartlinks,
)
from helpers import (
    format_date_ru,
    parse_date,
    safe_edit,
    safe_edit_caption,
    get_smartlink_slugs,
    build_smartlink_id,
    build_smartlink_key,
    parse_smartlink_key,
)
from keyboards import (
    ACCOUNTS,
    BRANDING_DISABLE_PRICE,
    EXPORT_UNLOCK_PRICE,
    HELP,
    KEY_PLATFORM_SET,
    QC_PROMPTS,
    SMARTLINK_BUTTON_ORDER,
    SMARTLINK_PLATFORMS,
    build_accounts_checklist,
    build_donate_menu_kb,
    build_focus,
    build_focus_keyboard,
    build_important_screen,
    build_links_kb,
    build_reset_menu_kb,
    build_section_page,
    build_sections_menu,
    build_smartlink_keyboard,
    build_timeline_kb,
    get_task_title,
    next_acc_status,
    platform_label,
    smartlink_branding_confirm_kb,
    smartlink_edit_menu_kb,
    smartlink_export_kb,
    smartlink_export_paywall_kb,
    smartlink_links_menu_kb,
    smartlink_step_kb,
    smartlink_view_kb,
    smartlinks_menu_kb,
)
from texts import (
    EXPERIENCE_PROMPT_TEXT,
    EXPECTATIONS_TEXT,
    LYRICS_SYNC_TEXT,
    RESOLVER_FALLBACK_TEXT,
    SMARTLINKS_HELP_TEXT,
    UGC_TIP_TEXT,
    UPDATES_CHANNEL_URL,
)
from scheduler import build_deadlines
from smartlink import (
    build_smartlink_caption,
    build_owner_payload,
    build_owner_cover_updates,
    build_smartlink_view_text,
    build_copy_links_text,
    build_smartlink_export_text,
    build_cover_proxy_url,
    build_unique_smartlink_slugs,
    fetch_owned_smartlink_with_fallback,
    fetch_owned_smartlink_by_smartlink_id,
    fetch_latest_smartlink_from_index,
    fetch_smartlink_from_index,
    fetch_my_smartlinks_from_index,
    normalize_index_smartlink,
    update_smartlink_in_index,
    parse_smartlink_callback_data,
    parse_page_marker,
    smartlink_can_remind,
    schedule_smartlink_update,
    send_smartlink_photo,
    send_my_smartlinks,
    send_smartlink_list,
    show_smartlink_view,
    resend_smartlink_card,
    start_smartlink_form,
    start_smartlink_import,
    skip_prefilled_smartlink_steps,
    log_smartlink_step,
    refresh_smartlink_links_from_bandlink,
    _send_smartlink_prompt,
    _update_smartlink_prompt,
    finalize_smartlink_form,
    start_prefill_editor,
    apply_spotify_upc_selection,
    apply_caption_update,
    fetch_cover_file,
    show_import_confirmation,
    pick_selected_metadata,
    spotify_search_upc,
    detect_platform,
    resolve_links,
    merge_metadata,
    smartlink_step_prompt,
    smartlinks_help_text,
    get_release_reminder_state,
)
from config import (
    ADMIN_TG_ID,
    EXPORT_UNLOCK_PRICE,
    LABEL_EMAIL,
    SMTP_APP_PASSWORD,
    SMTP_TO,
    SMTP_USER,
    SPOTIFY_UPC_ENABLED,
    UPDATES_POST_URL,
)

# Constants and helper functions
SUPPORT_DONATE_PRICE = 50

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

# Helper functions
async def ensure_user(tg_id: int, username: str | None = None):
    from keyboards import TASKS, ACCOUNTS
    await db_ensure_user(tg_id, username, TASKS, ACCOUNTS)

async def cycle_account_status(tg_id: int, key: str):
    return await db_cycle_account_status(tg_id, key, next_acc_status)

async def user_menu_keyboard(tg_id: int):
    from keyboards import ReplyKeyboardMarkup, KeyboardButton
    updates_enabled = await get_updates_opt_in(tg_id)
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

def experience_prompt() -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Первый релиз", callback_data="exp:first")],
        [InlineKeyboardButton(text="🎧 Уже выпускал(а)", callback_data="exp:old")],
    ])
    return EXPERIENCE_PROMPT_TEXT, kb

async def build_focus_for_user(tg_id: int, exp: str, focus_task_id: int | None = None, show_completed: bool | None = None):
    if show_completed is None:
        show_completed = await get_focus_show_completed(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    important = await get_important_tasks(tg_id)
    text, kb = build_focus(tasks_state, exp, important, focus_task_id=focus_task_id, show_completed=show_completed)
    return text, kb

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

async def send_donate_invoice(message: Message, stars: int):
    prices = [LabeledPrice(label=f"Поддержка ИСКРЫ ({stars} ⭐)", amount=stars)]
    await message.answer_invoice(
        title="Поддержать ИСКРУ",
        description="Спасибо! Это помогает развивать бота и добавлять функции.",
        payload=f"donate_iskra_{stars}",
        provider_token="",
        currency="XTR",
        prices=prices,
    )

def build_export_text(tasks_state: dict[int, int]) -> str:
    from keyboards import TASKS, count_progress, task_mark
    done, total = count_progress(tasks_state)
    lines = [f"ИСКРА — экспорт плана релиза\nПрогресс задач: {done}/{total}\n"]
    for task_id, title in TASKS:
        lines.append(f"{task_mark(tasks_state.get(task_id, 0))} {title}")
    return "\n".join(lines)

async def send_export_invoice(message: Message):
    await message.answer(
        "📤 Экспорт плана — 25 ⭐\n\n"
        "Оплата через Telegram Stars. После оплаты пришлю чек-лист релиза.",
        reply_markup=await user_menu_keyboard(message.from_user.id) if message.from_user else None
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

async def _cleanup_user_input_message(message: Message, data: dict):
    """Delete user input message if configured to do so."""
    # This function can be implemented if needed
    pass

# -------------------- Handlers --------------------

@dp.message(CommandStart())
async def start(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    await maybe_send_update_notice(message, tg_id)

    exp = await get_experience(tg_id)
    menu_kb = await user_menu_keyboard(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await message.answer("ИСКРА активна. Жми кнопки меню снизу 👇", reply_markup=menu_kb)
        await message.answer(text, reply_markup=kb)
        return

    await message.answer("ИСКРА активна. Жми кнопки меню снизу 👇", reply_markup=menu_kb)

    focus_text, kb = await build_focus_for_user(tg_id, exp)
    await message.answer(focus_text, reply_markup=kb)

@dp.message(Command("plan"))
async def plan_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    await maybe_send_update_notice(message, tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await message.answer(text, reply_markup=await user_menu_keyboard(tg_id))
        return
    await message.answer("Меню снизу, держу фокус здесь:", reply_markup=await user_menu_keyboard(tg_id))
    text, kb = await build_focus_for_user(tg_id, exp)
    await message.answer(text, reply_markup=kb)

@dp.message(Command("set_date"))
async def set_date_cmd(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) != 2:
        await form_start(tg_id, "release_date")
        await message.answer(
            "Введи дату релиза в формате ДД.ММ.ГГГГ.\nПример: 31.12.2025\n\nОтмена: /cancel",
            reply_markup=await user_menu_keyboard(tg_id),
        )
        return
    d = parse_date(parts[1])
    if not d:
        await message.answer("Не понял дату. Пример: /set_date 31.12.2025", reply_markup=await user_menu_keyboard(tg_id))
        return
    await set_release_date(tg_id, d.isoformat())
    await form_clear(tg_id)
    reminders = await get_reminders_enabled(tg_id)
    await message.answer(f"Ок. Дата релиза: {format_date_ru(d)}", reply_markup=build_timeline_kb(reminders, has_date=True))
    await message.answer(timeline_text(d, reminders), reply_markup=await user_menu_keyboard(tg_id))

@dp.message(Command("cancel"))
async def cancel(message: Message):
    tg_id = message.from_user.id
    await form_clear(tg_id)
    await message.answer("Ок, отменил.", reply_markup=await user_menu_keyboard(tg_id))

@dp.message(Command("broadcast_update"))
async def broadcast_update(message: Message, bot: Bot):
    if not ADMIN_TG_ID or str(message.from_user.id) != ADMIN_TG_ID:
        await message.answer("Нет доступа.")
        return
    await ensure_user(message.from_user.id, message.from_user.username)
    parts = message.text.split(maxsplit=1)
    url = (parts[1] if len(parts) == 2 else UPDATES_POST_URL).strip()
    if not url:
        await message.answer("Укажи ссылку: /broadcast_update <url> или задай UPDATES_POST_URL.")
        return
    users = await get_updates_opt_in_users()
    sent = skipped = errors = 0
    for tg_id, last_notified in users:
        if last_notified == url:
            skipped += 1
            continue
        try:
            await bot.send_message(tg_id, f"⚡️ Есть обновление ИСКРЫ. Подробнее: {url}")
            await set_last_update_notified(tg_id, url)
            sent += 1
        except TelegramForbiddenError:
            skipped += 1
        except Exception:
            errors += 1
        await asyncio.sleep(0.1)
    await message.answer(
        f"Рассылка завершена. Отправлено: {sent}. Пропущено/ошибок: {skipped + errors}.",
        reply_markup=await user_menu_keyboard(message.from_user.id)
    )

@dp.message(Command("smartlinks_republish"))
async def smartlinks_republish_cmd(message: Message):
    if not ADMIN_TG_ID or str(message.from_user.id) != ADMIN_TG_ID:
        await message.answer("Нет доступа.")
        return
    parts = message.text.split(maxsplit=1)
    limit = 10
    if len(parts) == 2:
        try:
            limit = int(parts[1])
        except ValueError:
            limit = 10
    limit = max(1, min(limit, 50))
    items = await list_recent_smartlinks(limit)
    if items is None:
        await message.answer("Не удалось получить смартлинки из D1.")
        return
    if not items:
        await message.answer("Смартлинков нет.")
        return
    ok_count = 0
    fail_count = 0
    owner_payload = build_owner_payload(message.from_user)
    for item in items:
        artist_slug, slug = get_smartlink_slugs(item)
        if not artist_slug or not slug:
            continue
        ok, _status, _error = await update_smartlink_in_index(
            artist_slug,
            slug,
            item,
            owner=owner_payload,
            reason="admin_republish",
        )
        if ok:
            ok_count += 1
        else:
            fail_count += 1
    await message.answer(
        f"Репаблиш завершён. Успешно: {ok_count}. Ошибок/в очереди: {fail_count}."
    )

# Reply keyboard actions
@dp.message(F.text == "🎯 План")
async def rb_plan(message: Message):
    await plan_cmd(message)

@dp.message(F.text == "📦 Задачи по разделам")
async def rb_sections(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_sections_menu(tasks_state)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == "👤 Кабинеты")
async def rb_accounts(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    acc = await get_accounts_state(tg_id)
    text, kb = build_accounts_checklist(acc)
    await message.answer(text, reply_markup=kb)

@dp.message(F.text == "📅 Таймлайн")
async def rb_timeline(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    rd = await get_release_date(tg_id)
    d = parse_date(rd) if rd else None
    reminders = await get_reminders_enabled(tg_id)
    await message.answer(timeline_text(d, reminders), reply_markup=build_timeline_kb(reminders, has_date=bool(d)))

@dp.message(F.text == "⏰ Дата релиза")
async def rb_set_date_hint(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    await message.answer("Команда:\n/set_date ДД.ММ.ГГГГ\nПример:\n/set_date 31.12.2025", reply_markup=await user_menu_keyboard(tg_id))

@dp.message(F.text == "🔗 Ссылки")
async def rb_links(message: Message):
    await message.answer("🔗 Быстрые ссылки:", reply_markup=build_links_kb())

@dp.message(F.text == "🔗 Смарт-линки")
async def rb_smartlinks(message: Message):
    await message.answer("🔗 Смарт-линки — выбери действие:", reply_markup=smartlinks_menu_kb())

@dp.message(F.text == "🧠 Ожидания")
async def rb_expectations(message: Message):
    await message.answer(EXPECTATIONS_TEXT, reply_markup=await user_menu_keyboard(message.from_user.id))

@dp.message(F.text == "📰 Что нового")
async def rb_whats_new(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    if UPDATES_POST_URL:
        text = f"📰 Что нового: {UPDATES_POST_URL}"
    else:
        text = f"{UPDATES_CHANNEL_URL}\nПоследнее обновление — в закреплённом посте канала."
    await message.answer(text, reply_markup=await user_menu_keyboard(tg_id))

@dp.message(F.text.startswith("🔔 Обновления"))
async def rb_toggle_updates(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    enabled = await toggle_updates_opt_in(tg_id)
    reply = "Ок, обновления включены ✅" if enabled else "Ок, обновления выключены ❌"
    await message.answer(reply, reply_markup=await user_menu_keyboard(tg_id))

@dp.message(F.text == "🔄 Сброс")
async def rb_reset(message: Message):
    await message.answer("⚠️ Сбросить чеклист?", reply_markup=build_reset_menu_kb())

@dp.message(F.text == "🧾 Экспорт")
async def rb_export(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)
    await send_export_invoice(message)

@dp.message(F.text == "📩 Запросить дистрибуцию")
async def rb_label(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "label_submit")
    await message.answer(
        "📩 Заявка на дистрибуцию.\n\n"
        f"{LABEL_FORM_STEPS[0][1]}\n\n"
        "Отмена: /cancel",
        reply_markup=await user_menu_keyboard(tg_id)
    )

# -------------------- Stars: DONATE --------------------

@dp.message(F.text == "💫 Поддержать ИСКРУ")
async def rb_donate(message: Message):
    await message.answer(
        "💫 Поддержать ИСКРУ звёздами\n\n"
        "Если бот помог — можешь поддержать проект.\n"
        "Выбери сумму (минимум 10 ⭐):",
        reply_markup=build_donate_menu_kb()
    )

@dp.callback_query(F.data == "donate:menu")
async def donate_menu_cb(callback):
    await safe_edit(
        callback.message,
        "💫 Поддержать ИСКРУ звёздами\n\nВыбери сумму (минимум 10 ⭐):",
        build_donate_menu_kb()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("donate:"))
async def donate_send_invoice_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    amount_s = callback.data.split(":")[1]
    allowed = {BRANDING_DISABLE_PRICE, EXPORT_UNLOCK_PRICE, SUPPORT_DONATE_PRICE}
    if not amount_s.isdigit() or int(amount_s) not in allowed:
        await callback.answer("Не понял сумму", show_alert=True)
        return

    stars = int(amount_s)
    await send_donate_invoice(callback.message, stars)
    await callback.answer("Ок")

@dp.callback_query(F.data == "donate:custom")
async def donate_custom_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "donate_custom")
    await callback.message.answer(
        f"Введи сумму доната в Stars (целое число от {DONATE_MIN_STARS} до {DONATE_MAX_STARS}).",
        reply_markup=await user_menu_keyboard(tg_id),
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery, bot: Bot):
    # обязательный шаг: без этого Telegram будет "крутить" оплату и ругаться, что бот не ответил
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    sp = message.successful_payment
    # sp.currency для Stars будет "XTR"
    if (sp.invoice_payload or "").startswith("donate_iskra_"):
        await message.answer("💫 Принято! Спасибо за поддержку ИСКРЫ 🤝", reply_markup=await user_menu_keyboard(message.from_user.id))
    elif sp.invoice_payload == "export_plan_25":
        tg_id = message.from_user.id
        await ensure_user(tg_id)
        tasks_state = await get_tasks_state(tg_id)
        await message.answer(build_export_text(tasks_state), reply_markup=await user_menu_keyboard(tg_id))
    elif sp.invoice_payload == "smartlink_export_unlock":
        tg_id = message.from_user.id
        await ensure_user(tg_id)
        await set_export_unlocked(tg_id, True)
        await message.answer(
            "Готово! Экспорт смарт-линков активирован для всех твоих ссылок.",
            reply_markup=await user_menu_keyboard(tg_id),
        )
    elif (sp.invoice_payload or "").startswith("smartlink_branding_"):
        tg_id = message.from_user.id
        await ensure_user(tg_id)
        payload = sp.invoice_payload or ""
        smartlink_key = payload.replace("smartlink_branding_", "", 1)
        artist_slug, slug = parse_smartlink_key(smartlink_key)
        if artist_slug and slug:
            smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
            if smartlink:
                updated_payload = {**smartlink, "branding_disabled": True, "branding_paid": True}
                await update_smartlink_in_index(
                    artist_slug,
                    slug,
                    updated_payload,
                    owner=build_owner_payload(message.from_user),
                )
        await message.answer(
            "Готово! Брендинг ИСКРЫ отключён для этого смарт-линка. Если нужно — его можно снова включить бесплатно.",
            reply_markup=await user_menu_keyboard(tg_id),
        )

# -------------------- Inline callbacks --------------------

@dp.callback_query(F.data == "export:inline")
async def export_inline_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await send_export_invoice(callback.message)
    await callback.answer("Счёт на экспорт плана")

@dp.callback_query(F.data.startswith("exp:"))
async def set_exp_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = callback.data.split(":")[1]
    await set_experience(tg_id, "first" if exp == "first" else "old")
    await callback.message.answer("Ок. Меню снизу, держу фокус здесь:", reply_markup=await user_menu_keyboard(tg_id))
    text, kb = await build_focus_for_user(tg_id, "first" if exp == "first" else "old")

    await safe_edit(callback.message, text, kb)
    await callback.answer("Готово")

@dp.callback_query(F.data.startswith("focus_done:"))
async def focus_done_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    task_id = int(callback.data.split(":")[1])
    tasks_state = await get_tasks_state(tg_id)
    was_done = tasks_state.get(task_id, 0) == 1
    await set_task_done(tg_id, task_id, 0 if was_done else 1)
    tasks_state = await get_tasks_state(tg_id)
    important = await get_important_tasks(tg_id)
    show_completed = await get_focus_show_completed(tg_id)
    text, kb = build_focus(tasks_state, exp, important, show_completed=show_completed)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Ок")

@dp.callback_query(F.data == "focus_toggle_completed")
async def focus_toggle_completed_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    current = await get_focus_show_completed(tg_id)
    new_value = not current
    await set_focus_show_completed(tg_id, new_value)
    text, kb = await build_focus_for_user(tg_id, exp, show_completed=new_value)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Обновил фокус")

@dp.callback_query(F.data.startswith("help:"))
async def help_cb(callback):
    task_id = int(callback.data.split(":")[1])
    title = get_task_title(task_id)
    body = HELP.get(task_id, "Пояснение пока не добавлено.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]])
    await safe_edit(callback.message, f"❓ {title}\n\n{body}", kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("qc:"))
async def qc_answer_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    _, task_s, value = callback.data.split(":")
    task_id = int(task_s)
    qc = QC_PROMPTS.get(task_id)
    if not qc:
        await callback.answer("Не актуально")
        return
    await save_qc_check(tg_id, task_id, qc["key"], value)
    if value == "no":
        await callback.message.answer(f"Подсказка: {qc['tip']}", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer("Записал")

@dp.callback_query(F.data == "sections:open")
async def sections_open_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_sections_menu(tasks_state)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("section:"))
async def section_page_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    _, sid, page_s = callback.data.split(":")
    page = int(page_s)
    tasks_state = await get_tasks_state(tg_id)
    text, kb = build_section_page(tasks_state, sid, page)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("sec_toggle:"))
async def section_toggle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    _, sid, page_s, tid_s = callback.data.split(":")
    page = int(page_s)
    task_id = int(tid_s)

    tasks_state = await toggle_task_and_get_state(tg_id, task_id)
    text, kb = build_section_page(tasks_state, sid, page)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Ок")

@dp.callback_query(F.data == "accounts:open")
async def accounts_open_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    state = await get_accounts_state(tg_id)
    text, kb = build_accounts_checklist(state)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("accounts:cycle:"))
async def accounts_cycle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    key = callback.data.split(":")[2]
    if key not in [k for k, _ in ACCOUNTS]:
        await callback.answer("Неизвестный пункт", show_alert=True)
        return
    await cycle_account_status(tg_id, key)
    state = await get_accounts_state(tg_id)
    text, kb = build_accounts_checklist(state)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Ок")

@dp.callback_query(F.data == "timeline")
async def timeline_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    rd = await get_release_date(tg_id)
    d = parse_date(rd) if rd else None
    reminders = await get_reminders_enabled(tg_id)
    kb = build_timeline_kb(reminders, has_date=bool(d))
    await safe_edit(callback.message, timeline_text(d, reminders), kb)
    await callback.answer()

@dp.callback_query(F.data == "reminders:toggle")
async def reminders_toggle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    new_state = await toggle_reminders_enabled(tg_id)
    rd = await get_release_date(tg_id)
    d = parse_date(rd) if rd else None
    kb = build_timeline_kb(new_state, has_date=bool(d))
    await safe_edit(callback.message, timeline_text(d, new_state), kb)
    await callback.answer("Напоминания обновлены")

@dp.callback_query(F.data == "timeline:set_date")
async def timeline_set_date_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "release_date")
    await callback.message.answer(
        "Введи дату релиза в формате ДД.ММ.ГГГГ.\nПример: 31.12.2025\n\nОтмена: /cancel",
        reply_markup=await user_menu_keyboard(tg_id),
    )
    await callback.answer()

@dp.callback_query(F.data == "links")
async def links_cb(callback):
    await safe_edit(callback.message, "🔗 Быстрые ссылки:", build_links_kb())
    await callback.answer()

@dp.callback_query(F.data == "smartlinks:menu")
async def smartlinks_menu_cb(callback):
    await callback.message.answer("🔗 Смарт-линки — выбери действие:", reply_markup=smartlinks_menu_kb())
    await callback.answer()

@dp.callback_query(F.data == "smartlinks:create")
async def smartlinks_create_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await start_smartlink_import(callback.message, tg_id)
    await callback.answer()

@dp.callback_query(F.data == "smartlinks:help")
async def smartlinks_help_cb(callback):
    await callback.message.answer(smartlinks_help_text(), reply_markup=smartlinks_menu_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:my:"))
async def smartlinks_my_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    try:
        page = int(callback.data.split(":")[-1])
    except ValueError:
        page = 0
    await send_my_smartlinks(callback.message, tg_id, page=page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:edit:"))
async def smartlinks_edit_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    smartlink_id, tail = parse_smartlink_callback_data(callback.data, 1)
    page = parse_page_marker(tail[0] if tail else None, default=0)

    if not smartlink_id:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink = await fetch_owned_smartlink_by_smartlink_id(tg_id, smartlink_id)
    if not smartlink:
        logger.warning(
            "[smartlink-edit] not found id=%s tg_id=%s",
            smartlink_id,
            tg_id,
        )
        await callback.answer(
            "Смартлинк не найден или временно недоступен.",
            show_alert=True,
        )
        return
    artist_slug = str(smartlink.get("artist_slug") or "").strip()
    slug = str(smartlink.get("slug") or "").strip()
    resolved_id = smartlink.get("id") or build_smartlink_id(artist_slug, slug) or smartlink_id
    text = build_smartlink_view_text(smartlink)
    await callback.message.answer(
        text + "\n\nВыбери, что обновить:",
        reply_markup=smartlink_edit_menu_kb(
            artist_slug,
            slug,
            page,
            resolved_id,
            smartlink.get("branding_disabled"),
            smartlink.get("branding_paid"),
        ),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:list:"))
async def smartlinks_list_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    try:
        page = int(callback.data.split(":")[-1])
    except ValueError:
        page = 0
    await send_smartlink_list(callback.message, tg_id, page=page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:view:"))
async def smartlinks_view_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    page = int(parts[3])
    await show_smartlink_view(callback.message, tg_id, artist_slug, slug, page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:open:"))
async def smartlinks_open_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    page = int(parts[3])
    smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
    if not smartlink:
        await callback.answer("Смартлинк не найден или временно недоступен", show_alert=True)
        return
    await resend_smartlink_card(callback.message, tg_id, smartlink, page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:choose:"))
async def smartlinks_choose_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Не понял", show_alert=True)
        return
    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
    if not smartlink:
        await callback.answer("Смартлинк не найден или временно недоступен", show_alert=True)
        return
    await resend_smartlink_card(callback.message, tg_id, smartlink, page=0)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:refresh:"))
async def smartlinks_refresh_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    page = int(parts[3])
    smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
    if not smartlink:
        await callback.answer("Смартлинк не найден или временно недоступен", show_alert=True)
        return

    try:
        updated_links = await refresh_smartlink_links_from_bandlink(smartlink)
    except ValueError:
        await callback.answer("Добавь ссылку BandLink, чтобы обновить площадки автоматически.", show_alert=True)
        return
    except Exception:
        logger.exception(
            "[smartlink] bandlink refresh failed artist_slug=%s slug=%s",
            artist_slug,
            slug,
        )
        await callback.answer("Не получилось обновить ссылки. Попробуй позже или добавь вручную.", show_alert=True)
        return

    if updated_links != (smartlink.get("links") or {}):
        updated_payload = {**smartlink, "links": updated_links}
        index_ok, status, error = await update_smartlink_in_index(
            artist_slug,
            slug,
            updated_payload,
            owner=build_owner_payload(callback.from_user),
        )
        if not index_ok:
            logger.warning(
                "[smartlink-edit-links] index update failed artist_slug=%s slug=%s status=%s error=%s",
                artist_slug,
                slug,
                status,
                error,
            )
        smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug) or updated_payload

    allow_remind = smartlink_can_remind(smartlink)
    subscribed = await get_release_reminder_state(tg_id, smartlink.get("id"), allow_remind)
    kb = build_smartlink_keyboard(smartlink, subscribed=subscribed, can_remind=allow_remind, page=page)
    caption = build_smartlink_caption(smartlink)
    await safe_edit_caption(callback.message, caption, kb)
    if smartlink.get("id"):
        schedule_smartlink_update(callback.message.bot, smartlink.get("id"))
    await callback.answer("Карточка обновлена")


@dp.callback_query(F.data.startswith("smartlinks:reindex:"))
async def smartlinks_reindex_cb(callback):
    tg_id = callback.from_user.id
    if ADMIN_TG_ID and str(tg_id) != str(ADMIN_TG_ID):
        await callback.answer("Недоступно", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return

    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return

    ok, item, _status = await fetch_smartlink_from_index(artist_slug, slug)
    if not ok or not isinstance(item, dict):
        await callback.answer("Смартлинк не найден или временно недоступен", show_alert=True)
        return
    smartlink = normalize_index_smartlink(item, artist_slug=artist_slug, slug=slug)

    index_ok, status, error = await update_smartlink_in_index(
        artist_slug,
        slug,
        smartlink,
        owner=build_owner_payload(callback.from_user),
    )
    if index_ok:
        schedule_smartlink_update(callback.message.bot, smartlink.get("id"))
        await callback.answer("✅ Web обновлён", show_alert=True)
    else:
        logger.warning(
            "[smartlink] reindex failed artist_slug=%s slug=%s status=%s error=%s",
            artist_slug,
            slug,
            status,
            error,
        )
        await callback.answer("❌ Ошибка обновления. Попробуй позже.", show_alert=True)


@dp.callback_query(F.data.startswith("smartlinks:delete:"))
async def smartlinks_delete_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    page = int(parts[3])
    await callback.answer("Удаление доступно в веб-кабинете.", show_alert=True)
    await send_my_smartlinks(callback.message, tg_id, page=page)


@dp.callback_query(F.data.startswith("smartlinks:edit_menu:"))
async def smartlinks_edit_menu_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    parts = callback.data.split(":")
    # smartlinks:edit_menu:{smartlink_id}:{page}
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return

    smartlink_id = parts[2]
    page = int(parts[3])

    smartlink = await fetch_owned_smartlink_by_smartlink_id(tg_id, smartlink_id)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    artist_slug = str(smartlink.get("artist_slug") or "").strip()
    slug = str(smartlink.get("slug") or "").strip()
    resolved_id = smartlink.get("id") or build_smartlink_id(artist_slug, slug) or smartlink_id

    text = build_smartlink_view_text(smartlink)

    await callback.message.answer(
        text + "\n\nВыбери, что обновить:",
        reply_markup=smartlink_edit_menu_kb(
            artist_slug,
            slug,
            page,
            resolved_id,
            smartlink.get("branding_disabled"),
            smartlink.get("branding_paid"),
        ),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:edit_field:"))
async def smartlinks_edit_field_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    smartlink_id, tail = parse_smartlink_callback_data(callback.data, 2)
    page = parse_page_marker(tail[0] if len(tail) > 0 else None, default=0)
    field = tail[1] if len(tail) > 1 else ""

    smartlink = None
    if smartlink_id:
        smartlink = await fetch_owned_smartlink_by_smartlink_id(tg_id, smartlink_id)

    if not smartlink or not field:
        logger.warning(
            "[smartlink-edit-field] smartlink not found tg_id=%s id=%s",
            tg_id,
            smartlink_id,
        )
        await callback.answer("Смартлинк не найден или временно недоступен", show_alert=True)
        return

    await form_start(tg_id, "smartlink_edit")
    await form_set(
        tg_id,
        0,
        {"smartlink_id": smartlink_id, "page": page, "field": field, "data": {}},
    )

    if field == "title":
        await callback.message.answer(
            "Обновляем артиста и название.\nПришли артиста (минимум 2 символа).\n\n(Отмена: /cancel)",
            reply_markup=await user_menu_keyboard(tg_id),
        )
    elif field == "date":
        await callback.message.answer(
            "Пришли дату релиза в формате ДД.ММ.ГГГГ или напиши «нет».\n\n(Отмена: /cancel)",
            reply_markup=await user_menu_keyboard(tg_id),
        )
    elif field == "caption":
        await callback.message.answer(
            "Пришли новое описание (до 600 символов) или напиши «пропустить», чтобы очистить.\n\n(Отмена: /cancel)",
            reply_markup=await user_menu_keyboard(tg_id),
        )
    elif field == "cover":
        await callback.message.answer(
            "Пришли новую обложку (фото). Чтобы оставить без изменений — /cancel.",
            reply_markup=await user_menu_keyboard(tg_id),
        )
    else:
        await callback.answer("Не понял", show_alert=True)
        return
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:edit_links:"))
async def smartlinks_edit_links_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    smartlink_id, tail = parse_smartlink_callback_data(callback.data, 1)
    page = parse_page_marker(tail[0] if tail else None, default=0)

    smartlink = None
    if smartlink_id:
        smartlink = await fetch_owned_smartlink_by_smartlink_id(tg_id, smartlink_id)

    if not smartlink:
        logger.warning(
            "[smartlink-edit-links] smartlink not found tg_id=%s id=%s",
            tg_id,
            smartlink_id,
        )
        await callback.answer("Смартлинк не найден или временно недоступен", show_alert=True)
        return
    await callback.message.answer(
        "Выбери платформу для обновления:",
        reply_markup=smartlink_links_menu_kb(smartlink_id, page),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:branding_toggle:"))
async def smartlinks_branding_toggle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    smartlink_id, tail = parse_smartlink_callback_data(callback.data, 1)
    page = parse_page_marker(tail[0] if tail else None, default=0)

    smartlink = None
    if smartlink_id:
        smartlink = await fetch_owned_smartlink_by_smartlink_id(tg_id, smartlink_id)
    if not smartlink:
        logger.warning(
            "[smartlink-edit-branding] smartlink not found tg_id=%s id=%s",
            tg_id,
            smartlink_id,
        )
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    artist_slug = str(smartlink.get("artist_slug") or "").strip()
    slug = str(smartlink.get("slug") or "").strip()
    branding_paid = bool(smartlink.get("branding_paid"))

    if smartlink.get("branding_disabled"):
        updated_payload = {**smartlink, "branding_disabled": False}
        index_ok, status, error = await update_smartlink_in_index(
            artist_slug,
            slug,
            updated_payload,
            owner=build_owner_payload(callback.from_user),
        )
        if not index_ok:
            logger.warning(
                "[smartlink-edit] index_put failed artist_slug=%s slug=%s tg_id=%s status=%s error=%s",
                artist_slug,
                slug,
                tg_id,
                status,
                (error or "")[:300],
            )
            await callback.answer("Не получилось обновить. Попробуй позже.", show_alert=True)
            return
        logger.info(
            "[smartlink-edit] index_put ok artist_slug=%s slug=%s tg_id=%s status=%s",
            artist_slug,
            slug,
            tg_id,
            status,
        )
        updated = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug) or updated_payload
        text = build_smartlink_view_text(updated)
        await callback.message.answer(
            text + "\n\nВыбери, что обновить:",
            reply_markup=smartlink_edit_menu_kb(
                artist_slug,
                slug,
                page,
                updated.get("id") or smartlink_id,
                updated.get("branding_disabled"),
                updated.get("branding_paid"),
            ),
        )
        await callback.answer("Брендинг включён")
        return

    if branding_paid:
        updated_payload = {**smartlink, "branding_disabled": True}
        index_ok, status, error = await update_smartlink_in_index(
            artist_slug,
            slug,
            updated_payload,
            owner=build_owner_payload(callback.from_user),
        )
        if not index_ok:
            logger.warning(
                "[smartlink-edit] index_put failed artist_slug=%s slug=%s tg_id=%s status=%s error=%s",
                artist_slug,
                slug,
                tg_id,
                status,
                (error or "")[:300],
            )
            await callback.answer("Не получилось обновить. Попробуй позже.", show_alert=True)
            return
        logger.info(
            "[smartlink-edit] index_put ok artist_slug=%s slug=%s tg_id=%s status=%s",
            artist_slug,
            slug,
            tg_id,
            status,
        )
        updated = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug) or updated_payload
        text = build_smartlink_view_text(updated)
        await callback.message.answer(
            text + "\n\nВыбери, что обновить:",
            reply_markup=smartlink_edit_menu_kb(
                artist_slug,
                slug,
                page,
                updated.get("id") or smartlink_id,
                updated.get("branding_disabled"),
                updated.get("branding_paid"),
            ),
        )
        await callback.answer("Брендинг отключён")
        return

    await callback.message.answer(
        f"Отключить брендинг ИСКРЫ для этого смарт-линка?\nСтоимость: ⭐ {BRANDING_DISABLE_PRICE}",
        reply_markup=smartlink_branding_confirm_kb(smartlink_id, page),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:branding_cancel:"))
async def smartlinks_branding_cancel_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    smartlink_id, tail = parse_smartlink_callback_data(callback.data, 1)
    page = parse_page_marker(tail[0] if tail else None, default=0)

    smartlink = None
    if smartlink_id:
        smartlink = await fetch_owned_smartlink_by_smartlink_id(tg_id, smartlink_id)
    if not smartlink:
        logger.warning(
            "[smartlink-edit-branding] cancel not found tg_id=%s id=%s",
            tg_id,
            smartlink_id,
        )
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    artist_slug = str(smartlink.get("artist_slug") or "").strip()
    slug = str(smartlink.get("slug") or "").strip()
    text = build_smartlink_view_text(smartlink)
    await callback.message.answer(
        text + "\n\nВыбери, что обновить:",
        reply_markup=smartlink_edit_menu_kb(
            artist_slug,
            slug,
            page,
            smartlink.get("id") or smartlink_id,
            smartlink.get("branding_disabled"),
            smartlink.get("branding_paid"),
        ),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:branding_pay:"))
async def smartlinks_branding_pay_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    smartlink_id, tail = parse_smartlink_callback_data(callback.data, 1)
    page = parse_page_marker(tail[0] if tail else None, default=0)

    smartlink = None
    if smartlink_id:
        smartlink = await fetch_owned_smartlink_by_smartlink_id(tg_id, smartlink_id)
    if not smartlink:
        logger.warning(
            "[smartlink-edit-branding] pay not found tg_id=%s id=%s",
            tg_id,
            smartlink_id,
        )
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    if smartlink.get("branding_disabled"):
        await callback.answer("Брендинг уже отключён", show_alert=True)
        return
    artist_slug = str(smartlink.get("artist_slug") or "").strip()
    slug = str(smartlink.get("slug") or "").strip()
    if smartlink.get("branding_paid"):
        updated_payload = {**smartlink, "branding_disabled": True}
        index_ok, status, error = await update_smartlink_in_index(
            artist_slug,
            slug,
            updated_payload,
            owner=build_owner_payload(callback.from_user),
        )
        if not index_ok:
            logger.warning(
                "[smartlink-edit] index_put failed artist_slug=%s slug=%s tg_id=%s status=%s error=%s",
                artist_slug,
                slug,
                tg_id,
                status,
                (error or "")[:300],
            )
            await callback.answer("Не получилось обновить. Попробуй позже.", show_alert=True)
            return
        logger.info(
            "[smartlink-edit] index_put ok artist_slug=%s slug=%s tg_id=%s status=%s",
            artist_slug,
            slug,
            tg_id,
            status,
        )
        updated = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug) or updated_payload
        text = build_smartlink_view_text(updated)
        await callback.message.answer(
            text + "\n\nВыбери, что обновить:",
            reply_markup=smartlink_edit_menu_kb(
                artist_slug,
                slug,
                page,
                updated.get("id") or smartlink_id,
                updated.get("branding_disabled"),
                updated.get("branding_paid"),
            ),
        )
        await callback.answer("Брендинг уже оплачен")
        return

    prices = [LabeledPrice(label="Отключение брендинга ИСКРЫ", amount=BRANDING_DISABLE_PRICE)]
    smartlink_key = build_smartlink_key(artist_slug, slug)
    await callback.message.answer_invoice(
        title="Отключить брендинг ИСКРЫ",
        description="Брендинг уберётся только у этого смарт-линка.",
        payload=f"smartlink_branding_{smartlink_key}",
        provider_token="",
        currency="XTR",
        prices=prices,
    )
    await callback.answer("Счёт на оплату")


@dp.callback_query(F.data.startswith("smartlinks:edit_link:"))
async def smartlinks_edit_link_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    smartlink_id, tail = parse_smartlink_callback_data(callback.data, 2)
    page = parse_page_marker(tail[0] if len(tail) > 0 else None, default=0)
    platform = tail[1] if len(tail) > 1 else ""
    if platform not in {k for k, _ in SMARTLINK_BUTTON_ORDER}:
        await callback.answer("Платформа не поддерживается", show_alert=True)
        return

    smartlink = None
    if smartlink_id:
        smartlink = await fetch_owned_smartlink_by_smartlink_id(tg_id, smartlink_id)
    if not smartlink:
        logger.warning(
            "[smartlink-edit-link] smartlink not found tg_id=%s id=%s",
            tg_id,
            smartlink_id,
        )
        await callback.answer("Смартлинк не найден", show_alert=True)
        return

    await form_start(tg_id, "smartlink_edit")
    await form_set(
        tg_id,
        0,
        {
            "smartlink_id": smartlink_id,
            "page": page,
            "field": "link",
            "platform": platform,
            "data": {},
        },
    )
    label = platform_label(platform)
    await callback.message.answer(
        f"Пришли ссылку на {label}. Чтобы удалить площадку — напиши «удалить».\n\n(Отмена: /cancel)",
        reply_markup=await user_menu_keyboard(tg_id),
    )
    await callback.answer()


@dp.callback_query(F.data == "smartlink:open")
async def smartlink_open_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    existing = await fetch_latest_smartlink_from_index(tg_id)
    if not existing:
        inline_keyboard = []
        if SPOTIFY_UPC_ENABLED:
            inline_keyboard.append([InlineKeyboardButton(text="⚡ Автозаполнение по UPC", callback_data="smartlink:upc")])
        inline_keyboard.extend([
            [InlineKeyboardButton(text="⚡ Импорт по ссылке", callback_data="smartlink:import")],
            [InlineKeyboardButton(text="✏️ Создать вручную", callback_data="smartlink:new")],
            [InlineKeyboardButton(text="↩️ В фокус", callback_data="back_to_focus")],
        ])
        actions_kb = InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
        await callback.message.answer("Смартлинк не найден. Выбери действие:", reply_markup=actions_kb)
        await callback.answer()
        return

    allow_remind = smartlink_can_remind(existing)
    subscribed = await get_release_reminder_state(tg_id, existing.get("id"), allow_remind)
    await send_smartlink_photo(callback.message.bot, tg_id, existing, subscribed=subscribed, allow_remind=allow_remind)

    inline_keyboard = []
    if SPOTIFY_UPC_ENABLED:
        inline_keyboard.append([InlineKeyboardButton(text="⚡ Автозаполнение по UPC", callback_data="smartlink:upc")])
    inline_keyboard.extend([
        [InlineKeyboardButton(text="⚡ Импорт по ссылке", callback_data="smartlink:import")],
        [InlineKeyboardButton(text="✏️ Обновить", callback_data="smartlink:new")],
        [InlineKeyboardButton(text="✍️ Изменить текст", callback_data="smartlink:caption_edit")],
        [InlineKeyboardButton(text="↩️ В фокус", callback_data="back_to_focus")],
    ])
    manage_kb = InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
    await callback.message.answer("Можно обновить смартлинк:", reply_markup=manage_kb)
    await callback.answer()


@dp.callback_query(F.data == "smartlink:new")
async def smartlink_new_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await start_smartlink_form(callback.message, tg_id)
    await callback.answer()


@dp.callback_query(F.data == "smartlink:upc")
async def smartlink_upc_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)

    if not SPOTIFY_UPC_ENABLED:
        await callback.answer("Не задан SPOTIFY_CLIENT_ID/SECRET", show_alert=True)
        return

    await form_start(tg_id, "smartlink_upc")
    await callback.message.answer(
        "⚡ Автозаполнение по UPC. Пришли UPC (12–14 цифр).\n\n(Отмена: /cancel)",
        reply_markup=await user_menu_keyboard(tg_id),
    )
    await callback.answer()


@dp.callback_query(F.data == "smartlink:import")
async def smartlink_import_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await start_smartlink_import(callback.message, tg_id)
    await callback.answer()


@dp.callback_query(F.data == "smartlink:import_confirm")
async def smartlink_import_confirm_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    form = await form_get(tg_id)
    if not form or form.get("form_name") != "smartlink_import_review":
        await callback.answer("Нет данных для сохранения", show_alert=True)
        return
    data = form.get("data") or {}
    prefill = {
        "artist": data.get("artist") or pick_selected_metadata(data).get("artist"),
        "title": data.get("title") or pick_selected_metadata(data).get("title"),
        "cover_file_id": data.get("cover_file_id") or pick_selected_metadata(data).get("cover_file_id"),
        "release_date": data.get("release_date") or "",
        "caption_text": data.get("caption_text") or "",
        "cover_url": data.get("cover_url") or pick_selected_metadata(data).get("cover_url"),
        "metadata": data.get("metadata") or {},
        "preferred_source": data.get("preferred_source"),
    }
    links = data.get("links") or {}
    await start_smartlink_form(callback.message, tg_id, initial_links=links, prefill=prefill)
    await callback.answer("Проверь данные")


@dp.callback_query(F.data.startswith("smartlink:import_source:"))
async def smartlink_import_source_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    platform = callback.data.split(":")[-1]
    form = await form_get(tg_id)
    if not form or form.get("form_name") != "smartlink_import_review":
        await callback.answer("Нет данных", show_alert=True)
        return
    data = form.get("data") or {}
    metadata = data.get("metadata") or {}
    sources = metadata.get("sources") or {}
    if platform not in sources:
        await callback.answer("Нет такого источника", show_alert=True)
        return
    metadata["preferred_source"] = platform
    data["metadata"] = metadata
    data["preferred_source"] = platform
    await form_set(tg_id, 0, data)
    latest_stub = {
        "artist": data.get("artist", ""),
        "title": data.get("title", ""),
        "release_date": data.get("release_date", ""),
        "caption_text": data.get("caption_text", ""),
        "cover_file_id": data.get("cover_file_id", ""),
    }
    await show_import_confirmation(callback.message, tg_id, data.get("links") or {}, metadata, latest=latest_stub)
    await callback.answer("Источник обновлён")


@dp.callback_query(F.data == "smartlink:import_edit")
async def smartlink_import_edit_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    form = await form_get(tg_id)
    data = (form or {}).get("data") or {}
    if not data:
        await start_smartlink_form(callback.message, tg_id, initial_links={})
        await callback.answer()
        return
    await start_prefill_editor(callback.message, tg_id, data)
    await callback.answer()


@dp.callback_query(F.data == "smartlink:import_cancel")
async def smartlink_import_cancel_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_clear(tg_id)
    await callback.message.answer("Ок, отменил импорт.", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlink:prefill_edit:"))
async def smartlink_prefill_field_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    form = await form_get(tg_id)
    if not form or form.get("form_name") != "smartlink_prefill_edit":
        await callback.answer("Нет данных", show_alert=True)
        return
    field = callback.data.split(":")[-1]
    data = form.get("data") or {}
    if field not in {"artist", "title", "cover"}:
        await callback.answer("Неизвестно", show_alert=True)
        return
    data["pending"] = field
    await form_set(tg_id, 1, data)
    if field == "cover":
        await callback.message.answer("Пришли новую обложку фото.", reply_markup=await user_menu_keyboard(tg_id))
    elif field == "artist":
        await callback.message.answer("Введи артиста:", reply_markup=await user_menu_keyboard(tg_id))
    elif field == "title":
        await callback.message.answer("Введи название релиза:", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer()


@dp.callback_query(F.data == "smartlink:prefill_continue")
async def smartlink_prefill_continue_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    form = await form_get(tg_id)
    if not form or form.get("form_name") not in {"smartlink_prefill_edit", "smartlink_import_review"}:
        await callback.answer("Нет данных", show_alert=True)
        return
    data = form.get("data") or {}
    selected_meta = pick_selected_metadata(data)
    prefill = {
        "artist": data.get("artist") or selected_meta.get("artist"),
        "title": data.get("title") or selected_meta.get("title"),
        "cover_file_id": data.get("cover_file_id") or selected_meta.get("cover_file_id"),
        "release_date": data.get("release_date") or "",
        "caption_text": data.get("caption_text") or "",
        "cover_url": data.get("cover_url") or selected_meta.get("cover_url"),
        "metadata": data.get("metadata") or {},
        "preferred_source": data.get("preferred_source"),
    }
    await start_smartlink_form(callback.message, tg_id, initial_links=data.get("links") or {}, prefill=prefill)
    await callback.answer("Давай сохраним")


@dp.callback_query(F.data == "smartlink:caption_edit")
async def smartlink_caption_edit_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    existing = await fetch_latest_smartlink_from_index(tg_id)
    if not existing:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    artist_slug, slug = get_smartlink_slugs(existing)
    await form_start(tg_id, "smartlink_caption_edit")
    await form_set(
        tg_id,
        0,
        {
            "artist_slug": artist_slug,
            "slug": slug,
            "caption_text": existing.get("caption_text", ""),
        },
    )
    await callback.message.answer(
        smartlink_step_prompt(4) + "\n\n(Отмена: /cancel)",
        reply_markup=smartlink_step_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlink:upc_pick:"))
async def smartlink_upc_pick_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        await callback.answer("Не понял выбор", show_alert=True)
        return

    form = await form_get(tg_id)
    if not form or form.get("form_name") != "smartlink_upc":
        await callback.answer("Запрос устарел, пришли UPC снова", show_alert=True)
        return

    candidates = (form.get("data") or {}).get("candidates") or []
    idx = int(parts[2])
    if idx < 0 or idx >= len(candidates):
        await callback.answer("Запрос устарел, пришли UPC снова", show_alert=True)
        return

    await apply_spotify_upc_selection(callback.message, tg_id, candidates[idx])
    await callback.answer("Готово")


@dp.callback_query(F.data == "smartlink:upc_cancel")
async def smartlink_upc_cancel_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_clear(tg_id)
    await callback.message.answer("Ок, не сохраняю.", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlink:toggle:"))
async def smartlink_toggle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Не понял", show_alert=True)
        return
    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return
    ok, item, _status = await fetch_smartlink_from_index(artist_slug, slug)
    if ok and isinstance(item, dict):
        smartlink = normalize_index_smartlink(item, artist_slug=artist_slug, slug=slug)
    else:
        smartlink = None
    if not smartlink:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return
    if not smartlink_can_remind(smartlink):
        await callback.answer("Релиз уже сегодня или прошёл", show_alert=True)
        return
    smartlink_id = build_smartlink_id(artist_slug, slug)
    current = await get_release_reminder_state(tg_id, smartlink_id, True)
    if current:
        await remove_smartlink_reminder(tg_id, smartlink_id)
    else:
        await add_smartlink_reminder(tg_id, smartlink_id)
    await set_smartlink_subscription(smartlink_id, tg_id, not current)
    allow_remind = smartlink_can_remind(smartlink)
    kb = build_smartlink_keyboard(smartlink, subscribed=not current, can_remind=allow_remind)
    caption = build_smartlink_caption(smartlink)
    await safe_edit_caption(callback.message, caption, kb)
    await callback.answer("Напомню" if not current else "Напоминание выключено")


@dp.callback_query(F.data.startswith("smartrem:"))
async def smartlink_release_reminder_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 3 or parts[2] != "toggle":
        await callback.answer("Не понял", show_alert=True)
        return
    artist_slug, slug = parse_smartlink_key(parts[1])
    if not artist_slug or not slug:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return
    ok, item, _status = await fetch_smartlink_from_index(artist_slug, slug)
    if ok and isinstance(item, dict):
        smartlink = normalize_index_smartlink(item, artist_slug=artist_slug, slug=slug)
    else:
        smartlink = None
    if not smartlink:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return
    allow_remind = smartlink_can_remind(smartlink)
    if not allow_remind:
        await callback.answer("Релиз уже сегодня или прошёл", show_alert=True)
        return
    smartlink_id = build_smartlink_id(artist_slug, slug)
    current = await get_release_reminder_state(tg_id, smartlink_id, allow_remind)
    if current:
        await remove_smartlink_reminder(tg_id, smartlink_id)
        await set_smartlink_subscription(smartlink_id, tg_id, False)
    else:
        await add_smartlink_reminder(tg_id, smartlink_id)
        await set_smartlink_subscription(smartlink_id, tg_id, True)

    kb = build_smartlink_keyboard(smartlink, subscribed=not current, can_remind=allow_remind)
    caption = build_smartlink_caption(smartlink)
    await safe_edit_caption(callback.message, caption, kb)
    await callback.answer("Напомню" if not current else "Напоминание выключено")


@dp.callback_query(F.data.in_({"smartlink:caption_skip", "smartlink:skip"}))
async def smartlink_skip_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    form = await form_get(tg_id)
    if not form:
        await callback.answer("Нет шага", show_alert=True)
        return
    form_name = form.get("form_name")
    data = form.get("data") or {}
    if form_name == "smartlink":
        step = int(form.get("step", 0))
        data["links"] = data.get("links") or {}
        total_steps = 5 + len(SMARTLINK_PLATFORMS)
        if step >= total_steps:
            await callback.answer("Шагов больше нет", show_alert=True)
            return
        field_name = ""
        if step == 0:
            data["artist"] = ""
            field_name = "artist"
        elif step == 1:
            data["title"] = ""
            field_name = "title"
        elif step == 2:
            data["release_date"] = ""
            field_name = "release_date"
        elif step == 3:
            data["cover_file_id"] = ""
            data["cover_source"] = {}
            field_name = "cover_file_id"
        elif step == 4:
            data["caption_text"] = ""
            field_name = "caption_text"
        else:
            idx = step - 5
            if idx < 0 or idx >= len(SMARTLINK_PLATFORMS):
                await form_clear(tg_id)
                await callback.answer("Нет шага", show_alert=True)
                return
            platform_key = SMARTLINK_PLATFORMS[idx][0]
            data["links"][platform_key] = ""
            field_name = platform_key

        log_smartlink_step(tg_id, step, field_name or "unknown", True)
        next_step = skip_prefilled_smartlink_steps(step + 1, data)
        total_steps = 5 + len(SMARTLINK_PLATFORMS)
        if next_step < total_steps:
            await _send_smartlink_prompt(callback.message, tg_id, next_step, data)
        else:
            await form_set(tg_id, next_step, data)
            await finalize_smartlink_form(callback.message, tg_id, data)
        await callback.answer("Пропустил")
        return

    if form_name == "smartlink_caption_edit":
        artist_slug = (data.get("artist_slug") or "").strip()
        slug = (data.get("slug") or "").strip()
        if not artist_slug or not slug:
            await callback.answer("Смартлинк не найден", show_alert=True)
            await form_clear(tg_id)
            return
        await apply_caption_update(callback.message, tg_id, artist_slug, slug, "")
        await callback.answer("Пропустил")
        return

    await callback.answer("Нет шага", show_alert=True)


@dp.callback_query(F.data == "smartlink:cancel")
async def smartlink_cancel_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_clear(tg_id)
    await callback.message.answer("Ок, отменил.", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:copy:"))
async def smartlinks_copy_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Не понял", show_alert=True)
        return

    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
    if not smartlink:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return

    text = build_copy_links_text(smartlink)
    await callback.message.answer(text)
    await callback.answer("Готово")


@dp.callback_query(F.data.startswith("smartlinks:exportfmt:"))
async def smartlinks_export_format_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("Не понял", show_alert=True)
        return

    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_key = parts[2]
    page = int(parts[3]) if parts[3].lstrip("-").isdigit() else -1
    variant = parts[4]
    smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    if not await get_export_unlocked(tg_id):
        await callback.message.answer(
            f"Открыть экспорт смарт-линка (Telegram/VK/PR/ссылки)?\nСтоимость: ⭐ {EXPORT_UNLOCK_PRICE}",
            reply_markup=smartlink_export_paywall_kb(smartlink_key, page),
        )
        await callback.answer()
        return

    export_text = build_smartlink_export_text(smartlink, variant)
    if not export_text.strip():
        await callback.message.answer("Нет данных для экспорта.")
        await callback.answer()
        return

    await callback.message.answer(export_text)
    await callback.answer("Готово")


@dp.callback_query(F.data.startswith("smartlinks:export_back:"))
async def smartlinks_export_back_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return

    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    page = int(parts[3]) if parts[3].lstrip("-").isdigit() else -1
    try:
        await callback.message.delete()
    except Exception:
        pass

    if page >= 0:
        await show_smartlink_view(callback.message, tg_id, artist_slug, slug, page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:export_pay:"))
async def smartlinks_export_pay_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Не понял", show_alert=True)
        return
    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    page = int(parts[3]) if parts[3].lstrip("-").isdigit() else -1
    smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    if await get_export_unlocked(tg_id):
        await callback.answer("Экспорт уже активирован", show_alert=True)
        return

    prices = [LabeledPrice(label="Экспорт смарт-линков", amount=EXPORT_UNLOCK_PRICE)]
    await callback.message.answer_invoice(
        title="Экспорт смарт-линка",
        description="Доступ к экспортам Telegram/VK/PR/ссылки для всех смарт-линков.",
        payload="smartlink_export_unlock",
        provider_token="",
        currency="XTR",
        prices=prices,
    )
    await callback.answer("Счёт на оплату")


@dp.callback_query(F.data.startswith("smartlinks:export_cancel:"))
async def smartlinks_export_cancel_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    page = int(parts[3]) if parts[3].lstrip("-").isdigit() else -1
    try:
        await callback.message.delete()
    except Exception:
        pass
    if page >= 0:
        await show_smartlink_view(callback.message, tg_id, artist_slug, slug, page)
    await callback.answer()


@dp.callback_query(F.data.startswith("smartlinks:export:"))
async def smartlinks_export_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    parts = callback.data.split(":")
    if len(parts) not in {3, 4}:
        await callback.answer("Не понял", show_alert=True)
        return

    artist_slug, slug = parse_smartlink_key(parts[2])
    if not artist_slug or not slug:
        await callback.answer("Не понял", show_alert=True)
        return
    smartlink_key = parts[2]
    page = int(parts[3]) if len(parts) == 4 and parts[3].lstrip("-").isdigit() else -1
    smartlink = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug)
    if not smartlink:
        await callback.answer("Смартлинк не найден", show_alert=True)
        return
    if not await get_export_unlocked(tg_id):
        await callback.message.answer(
            f"Открыть экспорт смарт-линка (Telegram/VK/PR/ссылки)?\nСтоимость: ⭐ {EXPORT_UNLOCK_PRICE}",
            reply_markup=smartlink_export_paywall_kb(smartlink_key, page),
        )
        await callback.answer()
        return

    header = build_smartlink_view_text(smartlink)
    await callback.message.answer(
        header + "\n\nВыбери формат:", reply_markup=smartlink_export_kb(smartlink_key, page)
    )
    await callback.answer()


@dp.callback_query(F.data == "links:lyrics")
async def links_lyrics_cb(callback):
    await safe_edit(callback.message, LYRICS_SYNC_TEXT, build_links_kb())
    await callback.answer()

@dp.callback_query(F.data == "links:ugc")
async def links_ugc_cb(callback):
    await safe_edit(callback.message, UGC_TIP_TEXT, build_links_kb())
    await callback.answer()

@dp.callback_query(F.data == "texts:start")
async def texts_start_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "pitch_texts")
    await form_set(tg_id, 0, {})
    await callback.message.answer("✍️ Тексты для питчинга.\n\n" + TEXT_FORM_STEPS[0][1] + "\n\n(Отмена: /cancel)", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer()

@dp.callback_query(F.data.startswith("texts:copy:"))
async def texts_copy_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    idx = int(callback.data.split(":")[2])
    form = await form_get(tg_id)
    if not form or form.get("form_name") not in {"pitch_texts_ready"}:
        await callback.answer("Нет готовых текстов", show_alert=True)
        return
    texts = form.get("data", {}).get("texts", [])
    if idx < 0 or idx >= len(texts):
        await callback.answer("Нет варианта", show_alert=True)
        return
    await callback.message.answer(texts[idx], reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer("Скопируй текст")

@dp.callback_query(F.data == "reset_menu")
async def reset_menu_cb(callback):
    await safe_edit(callback.message, "🔄 Сброс", build_reset_menu_kb())
    await callback.answer()

@dp.callback_query(F.data == "important:list")
async def important_list_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    tasks_state = await get_tasks_state(tg_id)
    important = await get_important_tasks(tg_id)
    text, kb = build_important_screen(tasks_state, important)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("important:toggle:"))
async def important_toggle_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    task_id = int(callback.data.split(":")[2])
    important = await toggle_important_task(tg_id, task_id)
    tasks_state = await get_tasks_state(tg_id)
    exp = await get_experience(tg_id)
    if callback.message.text and callback.message.text.startswith("🔥 Важное"):
        text, kb = build_important_screen(tasks_state, important)
    else:
        show_completed = await get_focus_show_completed(tg_id)
        text, kb = build_focus(tasks_state, exp, important, show_completed=show_completed)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Обновил")

@dp.callback_query(F.data.startswith("important:focus:"))
async def important_focus_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    task_id = int(callback.data.split(":")[2])
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    text, kb = await build_focus_for_user(tg_id, exp, focus_task_id=task_id)
    await safe_edit(callback.message, text, kb)
    await callback.answer("Готово")

@dp.callback_query(F.data == "reset_progress_yes")
async def reset_progress_yes_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    await reset_progress_only(tg_id)
    text, kb = await build_focus_for_user(tg_id, exp)
    await safe_edit(callback.message, text, kb)
    await callback.message.answer("Прогресс очищен.", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer("Сбросил")

@dp.callback_query(F.data == "reset_all_yes")
async def reset_all_yes_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    await reset_all_data(tg_id)
    text, kb = await build_focus_for_user(tg_id, exp)
    await safe_edit(callback.message, text, kb)
    await callback.message.answer("Сбросил всё: чеклист, дату и напоминания.", reply_markup=await user_menu_keyboard(tg_id))
    await callback.answer("Полный сброс")

@dp.callback_query(F.data == "back_to_focus")
async def back_to_focus_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    exp = await get_experience(tg_id)
    if exp == "unknown":
        text, kb = experience_prompt()
        await callback.message.answer(text, reply_markup=kb)
        await callback.answer()
        return
    text, kb = await build_focus_for_user(tg_id, exp)
    await safe_edit(callback.message, text, kb)
    await callback.answer()

@dp.callback_query(F.data == "label:start")
async def label_start_cb(callback):
    tg_id = callback.from_user.id
    await ensure_user(tg_id)
    await form_start(tg_id, "label_submit")
    await callback.message.answer(
        "📩 Заявка на дистрибуцию.\n\n"
        f"{LABEL_FORM_STEPS[0][1]}\n\n"
        "Отмена: /cancel",
        reply_markup=await user_menu_keyboard(tg_id)
    )
    await callback.answer()


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
        "cover_updated_at": dt.datetime.utcnow().isoformat(),
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


@dp.message()
async def any_message_router(message: Message):
    tg_id = message.from_user.id
    await ensure_user(tg_id, message.from_user.username)

    form = await form_get(tg_id)
    txt = (message.text or "").strip()

    if not form:
        if await maybe_upgrade_smartlink_cover_from_photo(message):
            return
        if not txt or txt.startswith("/"):
            return

        exp = await get_experience(tg_id)
        if exp == "unknown":
            lower = txt.lower()
            inferred: str | None = None
            if "уже" in lower or "не первый" in lower:
                inferred = "old"
            elif "перв" in lower:
                inferred = "first"

            if not inferred:
                text, kb = experience_prompt()
                await message.answer(text, reply_markup=kb)
                return

            await set_experience(tg_id, inferred)
            await message.answer("Ок. Меню снизу, держу фокус здесь:", reply_markup=await user_menu_keyboard(tg_id))
            focus_text, kb = await build_focus_for_user(tg_id, inferred)
            await message.answer(focus_text, reply_markup=kb)
            return
        return

    form_name = form.get("form_name")
    if form_name == "donate_custom":
        if not txt.isdigit():
            await message.answer(
                "Нужна целая сумма в Stars. Попробуй ещё раз.",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return
        stars = int(txt)
        if stars < DONATE_MIN_STARS or stars > DONATE_MAX_STARS:
            await message.answer(
                f"Минимум {DONATE_MIN_STARS} ⭐. Максимум {DONATE_MAX_STARS} ⭐.",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return
        await form_clear(tg_id)
        await send_donate_invoice(message, stars)
        return

    if form_name == "smartlink_upc":
        digits = re.sub(r"\D", "", txt)
        if not re.fullmatch(r"\d{12,14}", digits):
            await message.answer(
                "Нужен UPC: 12–14 цифр. Пришли номер ещё раз.\n\n(Отмена: /cancel)",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return

        results = await spotify_search_upc(digits)
        if not results:
            await message.answer(
                "Не нашёл, попробуй BandLink или вставь ссылки вручную. Можешь прислать другой UPC.",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return

        await form_set(tg_id, 1, {"upc": digits, "candidates": results})
        if len(results) == 1:
            candidate = results[0]
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Подтвердить", callback_data="smartlink:upc_pick:0")],
                    [InlineKeyboardButton(text="Отмена", callback_data="smartlink:upc_cancel")],
                ]
            )
            await message.answer(
                f"Нашёл: {candidate.get('artist') or 'Без артиста'} — {candidate.get('title') or ''}\n"
                f"{candidate.get('spotify_url', '')}\n\nПодтверждаешь?",
                reply_markup=kb,
            )
        else:
            rows = []
            for idx, candidate in enumerate(results):
                label = f"{candidate.get('artist') or ''} — {candidate.get('title') or ''}".strip(" —")
                if len(label) > 60:
                    label = label[:57] + "…"
                if not label:
                    label = f"Вариант {idx + 1}"
                rows.append([InlineKeyboardButton(text=label, callback_data=f"smartlink:upc_pick:{idx}")])
            rows.append([InlineKeyboardButton(text="Отмена", callback_data="smartlink:upc_cancel")])
            await message.answer(
                "Выбери релиз по UPC:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
        return

    if form_name == "smartlink_import":
        if not re.match(r"https?://", txt):
            await message.answer(
                "Нужна ссылка (http/https).\n\nОтмена: /cancel",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return

        data = form.get("data") or {}
        existing_links = data.get("links") or {}
        existing_metadata = data.get("metadata") or {}
        bandlink_help_shown = bool(data.get("bandlink_help_shown"))
        low_links_hint_shown = bool(data.get("low_links_hint_shown"))

        detected_platform = detect_platform(txt) or ""
        if detected_platform and detected_platform != "bandlink":
            await message.answer("Принял ссылку, пытаюсь найти релиз…", reply_markup=await user_menu_keyboard(tg_id))

        links, metadata = await resolve_links(txt)

        merged_links = dict(existing_links)
        added_platforms: list[str] = []
        for platform_key, url in links.items():
            if platform_key not in merged_links or merged_links[platform_key] != url:
                merged_links[platform_key] = url
                added_platforms.append(platform_key)

        merged_metadata = merge_metadata(existing_metadata, metadata)

        key_links_count = sum(1 for p in KEY_PLATFORM_SET if merged_links.get(p))

        if added_platforms:
            added_labels = [platform_label(p) for p in added_platforms]
            total_added = len(merged_links)
            await message.answer(
                f"Добавил площадки: {', '.join(added_labels)}. Всего: {total_added}",
                reply_markup=await user_menu_keyboard(tg_id),
            )

        total = len(merged_links)
        latest = await fetch_latest_smartlink_from_index(tg_id)
        temp_data = {"metadata": merged_metadata, "preferred_source": merged_metadata.get("preferred_source")}
        selected_meta = pick_selected_metadata(temp_data)
        cover_source = selected_meta.get("cover_url") or (merged_metadata or {}).get("cover_url") or ""
        cover_file_id = ""
        if cover_source:
            try:
                input_file = await fetch_cover_file(cover_source)
                if input_file:
                    preview = await message.answer_photo(photo=input_file, caption="Загрузил обложку…")
                    cover_file_id = preview.photo[-1].file_id if preview.photo else ""
                    await preview.delete()
            except Exception as e:
                logger.warning("[cover] failed to auto download: %s", e)

        ready_for_autofill = (
            not (merged_metadata or {}).get("conflict")
            and bool(selected_meta.get("artist"))
            and bool(selected_meta.get("title"))
            and bool(cover_file_id)
            and total >= 2
        )

        if ready_for_autofill:
            data.update(
                {
                    "artist": selected_meta.get("artist", ""),
                    "title": selected_meta.get("title", ""),
                    "cover_file_id": cover_file_id,
                    "links": merged_links,
                    "metadata": merged_metadata,
                    "preferred_source": merged_metadata.get("preferred_source"),
                    "release_date": (latest or {}).get("release_date", ""),
                    "caption_text": (latest or {}).get("caption_text", ""),
                }
            )
            await form_start(tg_id, "smartlink_prefill_edit")
            await form_set(tg_id, 0, data)
            platforms_text = ", ".join(sorted(merged_links.keys())) if merged_links else "—"
            summary_lines = [
                "Нашёл ссылки и данные релиза:",
                f"{data.get('artist') or 'Без артиста'} — {data.get('title') or 'Без названия'}",
                f"Площадки: {platforms_text}",
                "Карточку заполнил автоматически.",
            ]
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Продолжить", callback_data="smartlink:prefill_continue")],
                    [InlineKeyboardButton(text="✏️ Изменить данные", callback_data="smartlink:import_edit")],
                    [InlineKeyboardButton(text="Отмена", callback_data="smartlink:import_cancel")],
                ]
            )
            try:
                await message.answer_photo(photo=cover_file_id, caption="\n".join(summary_lines), reply_markup=kb)
            except Exception:
                await message.answer("\n".join(summary_lines), reply_markup=kb)
            return

        meta_complete = bool((merged_metadata or {}).get("artist") and (merged_metadata or {}).get("title"))

        if key_links_count < 3 and not low_links_hint_shown:
            data["low_links_hint_shown"] = True
            await form_set(tg_id, form.get("step", 0) or 0, data)
            await message.answer(
                "Ссылок мало. Можешь прислать Яндекс или VK — доберу остальные.",
                reply_markup=await user_menu_keyboard(tg_id),
            )

        if total >= 2 and meta_complete:
            await show_import_confirmation(message, tg_id, merged_links, merged_metadata, latest)
            return

        if meta_complete:
            await show_import_confirmation(message, tg_id, merged_links, merged_metadata, latest)
            return

        if total >= 2:
            await show_import_confirmation(message, tg_id, merged_links, merged_metadata, latest)
            return

        data.update({
            "links": merged_links,
            "metadata": merged_metadata,
            "bandlink_help_shown": bandlink_help_shown,
            "low_links_hint_shown": data.get("low_links_hint_shown", False),
        })
        await form_set(tg_id, form.get("step", 0) or 0, data)

        failure = total <= 1 and not meta_complete
        if detected_platform == "bandlink" and not bandlink_help_shown and failure:
            data["bandlink_help_shown"] = True
            await form_set(tg_id, form.get("step", 0) or 0, data)
            await message.answer(
                RESOLVER_FALLBACK_TEXT,
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return

        await message.answer(
            "Не нашёл остальные площадки, пришли ссылку другой платформы.",
            reply_markup=await user_menu_keyboard(tg_id),
        )
        return

    if form_name == "smartlink":
        step = int(form.get("step", 0))
        data = form.get("data") or {}
        links = data.get("links") or {}
        data["links"] = links
        total_steps = 5 + len(SMARTLINK_PLATFORMS)
        skip_text = txt.lower() in {"пропустить", "skip"}
        field_name = ""

        if step == 0:
            if skip_text:
                data["artist"] = ""
            else:
                if len(txt) < 2:
                    await _update_smartlink_prompt(message, tg_id, step, data)
                    return
                data["artist"] = txt
            field_name = "artist"
        elif step == 1:
            if skip_text:
                data["title"] = ""
            else:
                if len(txt) < 1:
                    await _update_smartlink_prompt(message, tg_id, step, data)
                    return
                data["title"] = txt
            field_name = "title"
        elif step == 2:
            if skip_text:
                data["release_date"] = ""
            else:
                d = parse_date(txt)
                if not d:
                    await _update_smartlink_prompt(
                        message,
                        tg_id,
                        step,
                        data,
                        prefix="Не понял дату. Формат: ДД.ММ.ГГГГ",
                    )
                    return
                data["release_date"] = d.isoformat()
            field_name = "release_date"
        elif step == 3:
            if skip_text:
                data["cover_file_id"] = ""
                data["cover_source"] = {}
            else:
                if not message.photo:
                    await _update_smartlink_prompt(
                        message,
                        tg_id,
                        step,
                        data,
                        prefix="Пришли фото для обложки.",
                    )
                    return
                data["cover_file_id"] = message.photo[-1].file_id
                data["cover_source"] = {"type": "telegram", "file_id": message.photo[-1].file_id}
            field_name = "cover_file_id"
        elif step == 4:
            if skip_text:
                data["caption_text"] = ""
            else:
                if not txt:
                    await _update_smartlink_prompt(message, tg_id, step, data)
                    return
                if len(txt) > 600:
                    await _update_smartlink_prompt(
                        message,
                        tg_id,
                        step,
                        data,
                        prefix="Максимум 600 символов. Сократи текст и отправь снова.",
                    )
                    return
                data["caption_text"] = txt
            field_name = "caption_text"
        else:
            idx = step - 5
            if idx < 0 or idx >= len(SMARTLINK_PLATFORMS):
                await form_clear(tg_id)
                return
            if skip_text:
                links[SMARTLINK_PLATFORMS[idx][0]] = ""
            else:
                if not txt:
                    await _update_smartlink_prompt(message, tg_id, step, data)
                    return
                if not re.match(r"https?://", txt):
                    await _update_smartlink_prompt(
                        message,
                        tg_id,
                        step,
                        data,
                        prefix="Нужна ссылка или «Пропустить».",
                    )
                    return
                links[SMARTLINK_PLATFORMS[idx][0]] = txt
            field_name = SMARTLINK_PLATFORMS[idx][0]

        log_smartlink_step(tg_id, step, field_name or "unknown", skip_text)

        await _cleanup_user_input_message(message, data)

        step += 1
        step = skip_prefilled_smartlink_steps(step, data)
        if step < total_steps:
            await _send_smartlink_prompt(message, tg_id, step, data)
            return

        await form_set(tg_id, step, data)
        await finalize_smartlink_form(message, tg_id, data)
        return

    if form_name == "smartlink_prefill_edit":
        data = form.get("data") or {}
        pending = data.get("pending")
        if pending == "artist":
            if len(txt) < 2:
                await message.answer("Минимум 2 символа. Попробуй ещё раз.", reply_markup=await user_menu_keyboard(tg_id))
                return
            data["artist"] = txt
        elif pending == "title":
            if len(txt) < 1:
                await message.answer("Нужно название релиза.", reply_markup=await user_menu_keyboard(tg_id))
                return
            data["title"] = txt
        elif pending == "cover":
            if not message.photo:
                await message.answer("Пришли фото.", reply_markup=await user_menu_keyboard(tg_id))
                return
            data["cover_file_id"] = message.photo[-1].file_id
        else:
            await start_prefill_editor(message, tg_id, data)
            return
        await _cleanup_user_input_message(message, data)
        data.pop("pending", None)
        await form_set(tg_id, 0, data)
        await start_prefill_editor(message, tg_id, data)
        return

    if form_name == "smartlink_caption_edit":
        data = form.get("data") or {}
        artist_slug = (data.get("artist_slug") or "").strip()
        slug = (data.get("slug") or "").strip()
        if not artist_slug or not slug:
            await form_clear(tg_id)
            await message.answer("Смартлинк не найден.", reply_markup=await user_menu_keyboard(tg_id))
            return
        if not txt:
            await message.answer(smartlink_step_prompt(4) + "\n\n(Отмена: /cancel)", reply_markup=smartlink_step_kb())
            return
        if txt.lower() in {"пропустить", "skip"}:
            caption_text = ""
        else:
            if len(txt) > 600:
                await message.answer(
                    "Максимум 600 символов. Сократи текст и отправь снова.\n\n" + smartlink_step_prompt(4),
                    reply_markup=smartlink_step_kb(),
                )
                return
            caption_text = txt
        await apply_caption_update(message, tg_id, artist_slug, slug, caption_text)
        return

    if form_name == "smartlink_edit":
        info = form.get("data") or {}
        smartlink_id = info.get("smartlink_id")
        page = int(info.get("page") or 0)
        field = info.get("field")
        smartlink = None
        if smartlink_id:
            smartlink = await fetch_owned_smartlink_by_smartlink_id(tg_id, smartlink_id)
        if not smartlink or not field:
            await form_clear(tg_id)
            await message.answer("Смартлинк не найден.", reply_markup=await user_menu_keyboard(tg_id))
            return

        step = int(form.get("step", 0))
        updates: dict = {}

        if field == "title":
            if step == 0:
                if len(txt) < 2:
                    await message.answer(
                        "Минимум 2 символа. Пришли артиста ещё раз.\n\n(Отмена: /cancel)",
                        reply_markup=await user_menu_keyboard(tg_id),
                    )
                    return
                info_data = info.get("data") or {}
                info_data["artist"] = txt
                info["data"] = info_data
                await form_set(tg_id, 1, info)
                await message.answer(
                    "Теперь пришли название релиза.\n\n(Отмена: /cancel)",
                    reply_markup=await user_menu_keyboard(tg_id),
                )
                return
            info_data = info.get("data") or {}
            artist = info_data.get("artist") or smartlink.get("artist")
            if len(txt) < 1:
                await message.answer(
                    "Нужно название релиза.\n\n(Отмена: /cancel)",
                    reply_markup=await user_menu_keyboard(tg_id),
                )
                return
            updates["artist"] = artist
            updates["title"] = txt
        elif field == "date":
            if txt.lower() in {"нет", "пропустить", "skip"}:
                updates["release_date"] = ""
            else:
                d = parse_date(txt)
                if not d:
                    await message.answer(
                        "Не понял дату. Формат: ДД.ММ.ГГГГ или напиши «нет».\n\n(Отмена: /cancel)",
                        reply_markup=await user_menu_keyboard(tg_id),
                    )
                    return
                updates["release_date"] = d.isoformat()
        elif field == "caption":
            if txt.lower() in {"пропустить", "skip"}:
                updates["caption_text"] = ""
            else:
                if len(txt) > 600:
                    await message.answer(
                        "Максимум 600 символов. Сократи текст.\n\n(Отмена: /cancel)",
                        reply_markup=await user_menu_keyboard(tg_id),
                    )
                    return
                updates["caption_text"] = txt
        elif field == "cover":
            if not message.photo:
                await message.answer(
                    "Пришли фото для обложки.\n\n(Отмена: /cancel)",
                    reply_markup=await user_menu_keyboard(tg_id),
                )
                return
            updates["cover_file_id"] = message.photo[-1].file_id
            updates["cover_source"] = {"type": "telegram", "file_id": message.photo[-1].file_id}
            updates.update(build_owner_cover_updates(smartlink, message.from_user, message.bot))
        elif field == "link":
            platform = info.get("platform")
            links = smartlink.get("links") or {}
            lower = txt.lower()
            if lower in {"удалить", "delete", "remove", "пропустить", "skip"}:
                links.pop(platform, None)
            else:
                if not re.match(r"https?://", txt):
                    await message.answer(
                        "Нужна ссылка вида https://... или слово «удалить».\n\n(Отмена: /cancel)",
                        reply_markup=await user_menu_keyboard(tg_id),
                    )
                    return
                links[platform] = txt
            updates["links"] = links
        else:
            await form_clear(tg_id)
            await message.answer("Не понял запрос.", reply_markup=await user_menu_keyboard(tg_id))
            return

        cover_updated = bool({"cover_file_id", "cover_source", "cover_url"} & set(updates.keys()))
        if updates:
            updated_payload = {**smartlink, **updates}
            if cover_updated:
                updated_payload["cover_version"] = int(smartlink.get("cover_version") or 1) + 1
                updated_payload["cover_updated_at"] = dt.datetime.utcnow().isoformat()
            artist_slug, slug = get_smartlink_slugs(updated_payload)
            updated_payload["artist_slug"] = artist_slug
            updated_payload["slug"] = slug
            if cover_updated and not updated_payload.get("cover_url") and artist_slug and slug:
                updated_payload["cover_url"] = build_cover_proxy_url(artist_slug, slug)

            index_ok, status, error = await update_smartlink_in_index(
                artist_slug,
                slug,
                updated_payload,
                owner=build_owner_payload(message.from_user),
            )
            if index_ok:
                logger.info(
                    "[smartlink-edit] index_put ok artist_slug=%s slug=%s tg_id=%s status=%s",
                    artist_slug,
                    slug,
                    tg_id,
                    status,
                )
            else:
                logger.warning(
                    "[smartlink-edit] index_put failed artist_slug=%s slug=%s tg_id=%s status=%s error=%s",
                    artist_slug,
                    slug,
                    tg_id,
                    status,
                    (error or "")[:300],
                )
                await form_clear(tg_id)
                await message.answer(
                    "Не удалось обновить смартлинк. Попробуй ещё раз позже.",
                    reply_markup=await user_menu_keyboard(tg_id),
                )
                return

            updated = await fetch_owned_smartlink_with_fallback(tg_id, artist_slug, slug) or updated_payload
            await form_clear(tg_id)
            if updated:
                if updated.get("id"):
                    schedule_smartlink_update(message.bot, updated["id"])
                await message.answer(
                    "Смартлинк обновлён.", reply_markup=smartlink_view_kb(updated, page)
                )
            else:
                await message.answer(
                    "Смартлинк обновлён.", reply_markup=await user_menu_keyboard(tg_id)
                )
        return

    if not txt or txt.startswith("/"):
        return

    if form_name == "release_date":
        d = parse_date(txt)
        if not d:
            await message.answer(
                "Не понял дату. Формат: ДД.ММ.ГГГГ. Пример: 31.12.2025\n\nПопробуй ещё раз:",
                reply_markup=await user_menu_keyboard(tg_id),
            )
            return
        await set_release_date(tg_id, d.isoformat())
        await form_clear(tg_id)
        reminders = await get_reminders_enabled(tg_id)
        await message.answer(
            f"Ок. Дата релиза: {format_date_ru(d)}",
            reply_markup=build_timeline_kb(reminders, has_date=True),
        )
        await message.answer(timeline_text(d, reminders), reply_markup=await user_menu_keyboard(tg_id))
        return

    if form_name == "pitch_texts":
        step = int(form["step"])
        data = form["data"]
        if step < 0 or step >= len(TEXT_FORM_STEPS):
            await form_clear(tg_id)
            await message.answer("Форма сброшена. Нажми «✍️ Тексты» ещё раз.", reply_markup=await user_menu_keyboard(tg_id))
            return
        key, prompt, *rest = TEXT_FORM_STEPS[step]
        optional = rest[0] if rest else False
        value = txt.strip()
        if not value and optional:
            data[key] = ""
        elif len(value) < 2:
            await message.answer(prompt + "\n\n(Отмена: /cancel)", reply_markup=await user_menu_keyboard(tg_id))
            return
        else:
            data[key] = value

        step += 1
        if step < len(TEXT_FORM_STEPS):
            await form_set(tg_id, step, data)
            await message.answer(TEXT_FORM_STEPS[step][1] + "\n\n(Отмена: /cancel)", reply_markup=await user_menu_keyboard(tg_id))
            return

        texts = generate_pitch_texts(data)
        await form_start(tg_id, "pitch_texts_ready")
        await form_set(tg_id, 0, {"texts": texts})

        for idx, text in enumerate(texts, start=1):
            await message.answer(f"Вариант {idx}:\n{text}", reply_markup=await user_menu_keyboard(tg_id))
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📋 Скопировать 1", callback_data="texts:copy:0")],
                [InlineKeyboardButton(text="📋 Скопировать 2", callback_data="texts:copy:1")],
                [InlineKeyboardButton(text="📋 Скопировать 3", callback_data="texts:copy:2")],
                [InlineKeyboardButton(text="↩️ В фокус", callback_data="back_to_focus")],
            ]
        )
        await message.answer("Выбери, что скопировать:", reply_markup=kb)
        return

    if form_name == "pitch_texts_ready":
        return

    if form_name != "label_submit":
        return

    step = int(form["step"])
    data = form["data"]

    if step < 0 or step >= len(LABEL_FORM_STEPS):
        await form_clear(tg_id)
        await message.answer("Форма сбросилась. Нажми «📩 Запросить дистрибуцию» ещё раз.", reply_markup=await user_menu_keyboard(tg_id))
        return

    key, _ = LABEL_FORM_STEPS[step]
    ok, normalized, err = validate_label_input(key, txt)
    if not ok:
        await message.answer(
            f"{err}\n\n{LABEL_FORM_STEPS[step][1]}\n\n(Отмена: /cancel)",
            reply_markup=await user_menu_keyboard(tg_id)
        )
        return

    data[key] = normalized

    await _cleanup_user_input_message(message, data)

    step += 1
    if step < len(LABEL_FORM_STEPS):
        await form_set(tg_id, step, data)
        await message.answer(LABEL_FORM_STEPS[step][1] + "\n\n(Отмена: /cancel)", reply_markup=await user_menu_keyboard(tg_id))
        return

    summary = render_label_summary(data)
    subject = f"[SREDA / LABEL] Demo submission: {data.get('artist_name','')}".strip()

    sent_tg = False
    if ADMIN_TG_ID and ADMIN_TG_ID.isdigit():
        try:
            await message.bot.send_message(
                int(ADMIN_TG_ID),
                summary + f"\nОт: @{message.from_user.username or 'без_username'} (tg_id: {tg_id})"
            )
            sent_tg = True
        except Exception:
            sent_tg = False

    sent_email = await try_send_email(subject, summary)

    mailto = f"mailto:{LABEL_EMAIL}?subject={subject.replace(' ', '%20')}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Открыть почту", url=mailto)],
        [InlineKeyboardButton(text="🎯 Вернуться в фокус", callback_data="back_to_focus")],
    ])

    result_lines = ["✅ Заявка собрана."]
    result_lines.append("✓ Отправил в Telegram лейблу." if sent_tg else "⚠️ Не смог отправить в Telegram (проверь ADMIN_TG_ID).")
    result_lines.append("✓ И на почту отправил автоматически." if sent_email else "⧗ Авто-почта не настроена/не доступна — ниже шаблон письма.")
    await message.answer("\n".join(result_lines), reply_markup=await user_menu_keyboard(tg_id))

    if not sent_email:
        await message.answer(f"Почта: {LABEL_EMAIL}\n\nТекст письма (скопируй):\n\n{summary}", reply_markup=kb)

    await message.answer(
        "Заявка принята. Срок ответа: 7 дней. Если нет ответа — значит не подошло/не актуально.",
        reply_markup=await user_menu_keyboard(tg_id),
    )

    await form_clear(tg_id)
