from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


LINKS = {
    "bandlink_home": "https://band.link/",
    "bandlink_login": "https://band.link/login",
    "spotify_for_artists": "https://artists.spotify.com/",
    "spotify_pitch_info": "https://support.spotify.com/us/artists/article/pitching-music-to-playlist-editors/",
    "yandex_artists_hub": "https://yandex.ru/support/music/ru/performers-and-copyright-holders",
    "yandex_pitch": "https://yandex.ru/support/music/ru/performers-and-copyright-holders/new-release",
    "kion_pitch": "https://music.mts.ru/pitch",  # КИОН (бывш. МТС Music)
    "zvuk_pitch": "https://help.zvuk.com/article/67859",
    "zvuk_studio": "https://studio.zvuk.com/",
    "vk_studio_info": "https://the-flow.ru/features/zachem-artistu-studiya-servis-vk-muzyki",
    "tiktok_for_artists": "https://artists.tiktok.com/",
}


ACCOUNTS = [
    ("spotify", "Spotify for Artists"),
    ("yandex", "Яндекс для артистов"),
    ("vk", "VK Studio"),
    ("zvuk", "Звук Studio"),
    ("tiktok", "TikTok (аккаунт + Artist/Music Tab)"),
]


def next_acc_status(v: int) -> int:
    return (v + 1) % 3


def task_mark(done: int) -> str:
    return "✅" if done else "▫️"


TASKS = [
    (1, "Цель релиза выбрана (зачем это выпускаю)"),
    (2, "Права/ownership: все участники согласны + семплы/биты легальны"),
    (3, "Единый нейминг: артист/трек/фиты везде одинаково"),
    (4, "Жанр + 1–2 референса определены (для питчинга/алгоритмов)"),
    (5, "Мини EPK: аватар + 1 фото + короткое био (для медиа/профилей)"),

    (6, "Мастер готов (WAV 24bit)"),
    (7, "Clean/Explicit версия (если нужно)"),
    (8, "Обложка 3000×3000 финальная"),
    (9, "Авторы и сплиты записаны"),

    (10, "Выбран дистрибьютор"),
    (11, "Релиз загружен в дистрибьютора"),
    (12, "Метаданные проверены (язык/explicit/жанр/написание)"),

    (13, "Получен UPC/ISRC и/ли ссылки площадок (или подтверждение, что появятся)"),
    (14, "Лирика/синхронизация (опционально: Musixmatch/Genius)"),
    (15, "Сделана страница релиза в BandLink (Smartlink)"),
    (16, "Сделан пресейв (если доступно)"),

    (17, "Кабинеты артиста: Spotify / Яндекс / VK / Звук / TikTok (по возможности)"),
    (18, "Шаблон сообщения для плейлистов/медиа готов (5–7 строк)"),
    (19, "Питчинг: Spotify / Яндекс / VK / Звук / КИОН (если доступно)"),

    (20, "Контент-единицы минимум 3 (тизер/пост/сторис)"),
    (21, "Контент-спринт: 30 вертикалок ДО релиза (рекомендация)"),
    (22, "UGC/Content ID настройки проверены (чтобы не словить страйки)"),
    (23, "Контент-спринт: 30 вертикалок ПОСЛЕ релиза (рекомендация)"),

    (24, "Список плейлистов / медиа собран (10–30 точечных)"),
]


SECTIONS = [
    ("prep", "1) Подготовка", [1, 2, 3, 4, 5]),
    ("assets", "2) Материалы релиза", [6, 7, 8, 9]),
    ("dist", "3) Дистрибуция", [10, 11, 12]),
    ("links", "4) UPC / BandLink / Лирика", [13, 14, 15, 16]),
    ("accounts", "5) Кабинеты / Питчинг", [17, 18, 19]),
    ("content", "6) Контент", [20, 21, 22, 23, 24]),
]


SMARTLINK_PLATFORMS = [
    ("yandex", "Яндекс Музыка"),
    ("vk", "VK Музыка"),
    ("apple", "Apple Music"),
    ("spotify", "Spotify"),
    ("itunes", "iTunes"),
    ("zvuk", "Звук"),
    ("youtubemusic", "YouTube Music"),
    ("youtube", "YouTube"),
    ("deezer", "Deezer"),
]


EXTRA_SMARTLINK_PLATFORMS = [
    ("kion", "MTS Music / КИОН"),
]


SMARTLINK_BUTTON_ORDER = [*SMARTLINK_PLATFORMS, *EXTRA_SMARTLINK_PLATFORMS]
KEY_PLATFORM_SET = {"yandex", "vk", "apple", "spotify"}

PLATFORM_LABELS = {
    **{k: v for k, v in SMARTLINK_PLATFORMS},
    **{k: v for k, v in EXTRA_SMARTLINK_PLATFORMS},
    "youtube": "YouTube",
    "youtubemusic": "YouTube Music",
    "bandlink": "BandLink",
}


EXPORT_LABELS: dict[str, tuple[str, str, str, str]] = {
    "yandex": ("Яндекс Музыка", "Яндекс Музыка", "Yandex Music", "Yandex"),
    "vk": ("VK Музыка", "VK Музыка", "VK Music", "VK"),
    "apple": ("Apple Music", "Apple Music", "Apple Music", "Apple"),
    "spotify": ("Spotify", "Spotify", "Spotify", "Spotify"),
    "itunes": ("iTunes", "iTunes", "iTunes", "iTunes"),
    "zvuk": ("Звук", "Звук", "Zvuk", "Zvuk"),
    "youtubemusic": ("YouTube Music", "YouTube Music", "YouTube Music", "YouTube Music"),
    "youtube": ("YouTube", "YouTube", "YouTube", "YouTube"),
    "deezer": ("Deezer", "Deezer", "Deezer", "Deezer"),
    "kion": ("MTS Music / КИОН", "MTS Music / КИОН", "MTS Music", "MTS Music"),
    "bandlink": ("BandLink", "BandLink", "BandLink", "BandLink"),
}


BRANDING_DISABLE_PRICE = 10
EXPORT_UNLOCK_PRICE = 25


def count_progress(tasks_state: dict[int, int]) -> tuple[int, int]:
    total = len(TASKS)
    done = sum(1 for task_id, _ in TASKS if tasks_state.get(task_id, 0) == 1)
    return done, total


def get_next_task(tasks_state: dict[int, int]):
    for task_id, title in TASKS:
        if tasks_state.get(task_id, 0) == 0:
            return task_id, title
    return None


def get_task_title(task_id: int) -> str:
    for tid, t in TASKS:
        if tid == task_id:
            return t
    return "Задача"


def find_section_for_task(task_id: int) -> tuple[str, str] | None:
    for sid, stitle, ids in SECTIONS:
        if task_id in ids:
            return sid, stitle
    return None


def build_focus(
    tasks_state: dict[int, int],
    experience: str | None = None,
    important: set[int] | None = None,
    focus_task_id: int | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    done, total = count_progress(tasks_state)
    next_task = None
    if focus_task_id:
        next_task = (focus_task_id, get_task_title(focus_task_id))
    else:
        next_task = get_next_task(tasks_state)

    lines = []
    lines.append("🎯 Фокус-режим")
    if experience == "first":
        lines.append("Тип релиза: первый")
    elif experience == "old":
        lines.append("Тип релиза: не первый")
    lines.append(f"Прогресс общий: {done}/{total}\n")

    rows: list[list[InlineKeyboardButton]] = []

    if not next_task:
        lines.append("✨ Всё выполнено. Поздравляю с закрытием релиза.")
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    task_id, title = next_task
    sec = find_section_for_task(task_id)
    if sec:
        sid, stitle = sec
        idx = next((i for i, s in enumerate(SECTIONS) if s[0] == sid), 0) + 1
        sec_total = len(SECTIONS)
        section_ids = next((s[2] for s in SECTIONS if s[0] == sid), [])
        section_done = sum(1 for tid in section_ids if tasks_state.get(tid, 0) == 1)
        lines.append(f"Раздел: {idx}/{sec_total} — {stitle}")
        lines.append(f"Прогресс по разделу: {section_done}/{len(section_ids)}")
    lines.append(f"Следующая задача:\n▫️ {title}\n")

    upcoming = []
    for tid, t in TASKS:
        if tid == task_id:
            continue
        if tasks_state.get(tid, 0) == 0:
            upcoming.append(t)
        if len(upcoming) >= 3:
            break
    if upcoming:
        lines.append("Дальше по очереди:")
        for t in upcoming:
            lines.append(f"▫️ {t}")

    is_done = tasks_state.get(task_id, 0) == 1
    mark_text = f"↩️ Отменить: {title}" if is_done else f"✅ Сделано: {title}"
    rows.append([
        InlineKeyboardButton(
            text=mark_text,
            callback_data=f"focus_done:{task_id}"
        )
    ])
    imp_set = important or set()
    imp_text = "🔥 Убрать из важных" if task_id in imp_set else "⭐ Важное"
    rows.append([InlineKeyboardButton(text=imp_text, callback_data=f"important:toggle:{task_id}")])
    rows.append([InlineKeyboardButton(text="❓ Пояснение", callback_data=f"help:{task_id}")])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def build_focus_keyboard(
    tasks_state: dict[int, int],
    experience: str | None = None,
    important: set[int] | None = None,
    focus_task_id: int | None = None,
) -> InlineKeyboardMarkup:
    _, kb = build_focus(tasks_state, experience, important, focus_task_id)
    return kb


def build_sections_menu(tasks_state: dict[int, int]) -> tuple[str, InlineKeyboardMarkup]:
    done, total = count_progress(tasks_state)
    text = f"📦 Задачи по разделам\nПрогресс: {done}/{total}\n\nВыбери раздел:"
    inline = []
    for sid, title, ids in SECTIONS:
        section_done = sum(1 for tid in ids if tasks_state.get(tid, 0) == 1)
        inline.append([InlineKeyboardButton(text=f"{title} ({section_done}/{len(ids)})", callback_data=f"section:{sid}:0")])
    inline.append([InlineKeyboardButton(text="↩️ Назад в фокус", callback_data="back_to_focus")])
    return text, InlineKeyboardMarkup(inline_keyboard=inline)


def build_section_page(tasks_state: dict[int, int], section_id: str, page: int, page_size: int = 6) -> tuple[str, InlineKeyboardMarkup]:
    sec = next((s for s in SECTIONS if s[0] == section_id), None)
    if not sec:
        return "Раздел не найден.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="↩️ Назад", callback_data="sections:open")]])

    _, title, ids = sec
    items = [(tid, get_task_title(tid)) for tid in ids]

    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    start = page * page_size
    chunk = items[start:start + page_size]

    done, total = count_progress(tasks_state)
    header = f"{title}\nПрогресс общий: {done}/{total}\nСтраница: {page+1}/{total_pages}\n"
    text_lines = [header]

    inline = []

    for tid, t in chunk:
        is_done = tasks_state.get(tid, 0) == 1
        text_lines.append(f"{task_mark(1 if is_done else 0)} {t}")

        btn = "✅ Снять" if is_done else "▫️ Отметить"
        inline.append([
            InlineKeyboardButton(text=f"{btn}", callback_data=f"sec_toggle:{section_id}:{page}:{tid}"),
            InlineKeyboardButton(text="❓", callback_data=f"help:{tid}")
        ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"section:{section_id}:{page-1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Вперёд ▶️", callback_data=f"section:{section_id}:{page+1}"))
    if nav_row:
        inline.append(nav_row)

    inline.append([
        InlineKeyboardButton(text="📋 К разделам", callback_data="sections:open"),
        InlineKeyboardButton(text="🎯 В фокус", callback_data="back_to_focus"),
    ])

    return "\n".join(text_lines), InlineKeyboardMarkup(inline_keyboard=inline)


def build_important_screen(tasks_state: dict[int, int], important_ids: set[int]) -> tuple[str, InlineKeyboardMarkup]:
    if not important_ids:
        text = "🔥 Важное\n\nПока ничего не закреплено. Отметь задачу кнопкой ⭐ Важное во фокусе."
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎯 В фокус", callback_data="back_to_focus")]])
        return text, kb

    text_lines = ["🔥 Важное"]
    inline: list[list[InlineKeyboardButton]] = []
    for tid in sorted(important_ids):
        title = get_task_title(tid)
        status = "✅" if tasks_state.get(tid, 0) == 1 else "▫️"
        text_lines.append(f"{status} {title}")
        inline.append(
            [
                InlineKeyboardButton(text="➡️ В фокус", callback_data=f"important:focus:{tid}"),
                InlineKeyboardButton(text="🔥 Снять", callback_data=f"important:toggle:{tid}"),
            ]
        )
    inline.append([InlineKeyboardButton(text="🎯 В фокус", callback_data="back_to_focus")])
    return "\n".join(text_lines), InlineKeyboardMarkup(inline_keyboard=inline)


def build_accounts_checklist(accounts_state: dict[str, int]) -> tuple[str, InlineKeyboardMarkup]:
    text = "👤 Кабинеты артиста\nСостояния: ▫️ → ⏳ → ✅\n\n"
    for key, name in ACCOUNTS:
        v = accounts_state.get(key, 0)
        emoji = "▫️" if v == 0 else ("⏳" if v == 1 else "✅")
        text += f"{emoji} {name}\n"
    inline = []
    for key, name in ACCOUNTS:
        inline.append([InlineKeyboardButton(text=f"{name}", callback_data=f"accounts:cycle:{key}")])
    inline.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")])
    return text, InlineKeyboardMarkup(inline_keyboard=inline)


def build_links_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Важное", callback_data="important:list")],
        [InlineKeyboardButton(text="🔗 Смартлинк", callback_data="smartlink:open")],
        [InlineKeyboardButton(text="✍️ Тексты", callback_data="texts:start")],
        [InlineKeyboardButton(text="BandLink", url=LINKS["bandlink_home"])],
        [InlineKeyboardButton(text="Spotify for Artists", url=LINKS["spotify_for_artists"])],
        [InlineKeyboardButton(text="Яндекс (артистам)", url=LINKS["yandex_artists_hub"])],
        [InlineKeyboardButton(text="Звук Studio", url=LINKS["zvuk_studio"])],
        [InlineKeyboardButton(text="КИОН (бывш. МТС) питчинг", url=LINKS["kion_pitch"])],
        [InlineKeyboardButton(text="TikTok for Artists", url=LINKS["tiktok_for_artists"])],
        [InlineKeyboardButton(text="Лирика/синхронизация", callback_data="links:lyrics")],
        [InlineKeyboardButton(text="UGC / Content ID", callback_data="links:ugc")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")]
    ])


def smartlinks_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Создать смарт-линк", callback_data="smartlinks:create")],
            [InlineKeyboardButton(text="📂 Мои смарт-линки", callback_data="smartlinks:list:0")],
            [InlineKeyboardButton(text="✏️ Редактировать смарт-линк", callback_data="smartlinks:list:0")],
            [InlineKeyboardButton(text="📋 Скопировать ссылки", callback_data="smartlinks:list:0")],
            [InlineKeyboardButton(text="❓ Помощь по смарт-линкам", callback_data="smartlinks:help")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_focus")],
        ]
    )


def smartlink_view_kb(smartlink_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Открыть", callback_data=f"smartlinks:open:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"smartlinks:edit_menu:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text="📋 Скопировать ссылки", callback_data=f"smartlinks:copy:{smartlink_id}")],
            [InlineKeyboardButton(text=f"📤 Экспорт ⭐{EXPORT_UNLOCK_PRICE}", callback_data=f"smartlinks:export:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"smartlinks:delete:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"smartlinks:list:{page}")],
        ]
    )


def smartlink_edit_menu_kb(
    smartlink_id: int, page: int, branding_disabled: bool = False, branding_paid: bool = False
) -> InlineKeyboardMarkup:
    if branding_disabled:
        branding_text = "🏷 Брендинг ИСКРЫ: Выкл"
    elif branding_paid:
        branding_text = "🏷 Брендинг ИСКРЫ: Вкл"
    else:
        branding_text = "Убрать брендинг ⭐10"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Артист/Название", callback_data=f"smartlinks:edit_field:{smartlink_id}:{page}:title")],
            [InlineKeyboardButton(text="Дата релиза", callback_data=f"smartlinks:edit_field:{smartlink_id}:{page}:date")],
            [InlineKeyboardButton(text="Описание", callback_data=f"smartlinks:edit_field:{smartlink_id}:{page}:caption")],
            [InlineKeyboardButton(text="Обложка", callback_data=f"smartlinks:edit_field:{smartlink_id}:{page}:cover")],
            [InlineKeyboardButton(text="Ссылки", callback_data=f"smartlinks:edit_links:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text=branding_text, callback_data=f"smartlinks:branding_toggle:{smartlink_id}:{page}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"smartlinks:view:{smartlink_id}:{page}")],
        ]
    )


def smartlink_links_menu_kb(smartlink_id: int, page: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for key, label in SMARTLINK_BUTTON_ORDER:
        rows.append([InlineKeyboardButton(text=label, callback_data=f"smartlinks:edit_link:{smartlink_id}:{page}:{key}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"smartlinks:edit_menu:{smartlink_id}:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def smartlink_export_kb(smartlink_id: int, page: int | None = None) -> InlineKeyboardMarkup:
    page_marker = page if page is not None else -1
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Текст для Telegram", callback_data=f"smartlinks:exportfmt:{smartlink_id}:{page_marker}:tg")],
            [InlineKeyboardButton(text="🧱 Текст для VK", callback_data=f"smartlinks:exportfmt:{smartlink_id}:{page_marker}:vk")],
            [InlineKeyboardButton(text="🌐 Универсальный текст", callback_data=f"smartlinks:exportfmt:{smartlink_id}:{page_marker}:universal")],
            [InlineKeyboardButton(text="🔗 Только ссылки", callback_data=f"smartlinks:exportfmt:{smartlink_id}:{page_marker}:links")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"smartlinks:export_back:{smartlink_id}:{page_marker}")],
        ]
    )


def smartlink_export_paywall_kb(smartlink_id: int, page: int | None = None) -> InlineKeyboardMarkup:
    page_marker = page if page is not None else -1
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⭐ Оплатить {EXPORT_UNLOCK_PRICE} Stars",
                    callback_data=f"smartlinks:export_pay:{smartlink_id}:{page_marker}",
                )
            ],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"smartlinks:export_cancel:{smartlink_id}:{page_marker}")],
        ]
    )


def smartlink_branding_confirm_kb(smartlink_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⭐ Оплатить {BRANDING_DISABLE_PRICE} Stars",
                    callback_data=f"smartlinks:branding_pay:{smartlink_id}:{page}",
                )
            ],
            [InlineKeyboardButton(text="◀️ Отмена", callback_data=f"smartlinks:branding_cancel:{smartlink_id}:{page}")],
        ]
    )


def build_smartlink_buttons(
    smartlink: dict,
    subscribed: bool = False,
    can_remind: bool = False,
    page: int | None = None,
) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    links = smartlink.get("links") or {}
    page_marker = page if page is not None else -1
    presave_active = smartlink_pre_save_active(smartlink)

    if not presave_active:
        platform_rows: list[list[InlineKeyboardButton]] = []
        for key, label in SMARTLINK_BUTTON_ORDER:
            url = links.get(key)
            if url:
                platform_rows.append([InlineKeyboardButton(text=label, url=url)])

        if platform_rows:
            rows.extend(platform_rows)

    if can_remind:
        toggle_text = "🔕 Не напоминать" if subscribed else "🔔 Напомнить о релизе"
        rows.append([InlineKeyboardButton(text=toggle_text, callback_data=f"smartrem:{smartlink.get('id')}:toggle")])

    rows.append([InlineKeyboardButton(text="📋 Скопировать ссылки", callback_data=f"smartlinks:copy:{smartlink.get('id')}")])
    rows.append([InlineKeyboardButton(text="📤 Экспорт", callback_data=f"smartlinks:export:{smartlink.get('id')}:{page_marker}")])

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def build_smartlink_keyboard(
    smartlink: dict,
    subscribed: bool = False,
    can_remind: bool = False,
    page: int | None = None,
) -> InlineKeyboardMarkup | None:
    return build_smartlink_buttons(
        smartlink,
        subscribed=subscribed,
        can_remind=can_remind,
        page=page,
    )


def smartlink_step_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="smartlink:skip")],
            [InlineKeyboardButton(text="Отмена", callback_data="smartlink:cancel")],
        ]
    )


def build_timeline_kb(reminders_enabled: bool, has_date: bool = True) -> InlineKeyboardMarkup:
    toggle_text = "🔔 Напоминания: вкл" if reminders_enabled else "🔕 Напоминания: выкл"
    rows = [[InlineKeyboardButton(text=toggle_text, callback_data="reminders:toggle")]]
    if has_date:
        rows.append([InlineKeyboardButton(text="📅 Установить дату", callback_data="timeline:set_date")])
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_focus")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_reset_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да, сбросить", callback_data="reset_progress_yes")],
        [InlineKeyboardButton(text="Сбросить всё (дата/настройки)", callback_data="reset_all_yes")],
        [InlineKeyboardButton(text="Отмена", callback_data="back_to_focus")],
    ])


def build_donate_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Спасибо ⭐10", callback_data="donate:10")],
        [InlineKeyboardButton(text="Поддержать ⭐25", callback_data="donate:25")],
        [InlineKeyboardButton(text="Сильно поддержать ⭐50", callback_data="donate:50")],
        [InlineKeyboardButton(text="Своя сумма", callback_data="donate:custom")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_focus")],
    ])

