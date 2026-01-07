import datetime as dt
import json
import logging
import os
from typing import Iterable

import aiosqlite

DB_PATH = os.getenv("DB_PATH", "bot.db")
DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_REMINDER_OFFSETS = "-7,-1,0,7"
DEFAULT_REMINDER_TIME = "12:00"
REMINDER_CLEAN_DAYS = 60
logger = logging.getLogger(__name__)

_SMARTLINK_LEGACY_OWNER_COLUMNS: list[str] | None = None
_SMARTLINK_COLUMNS: set[str] | None = None
_SMARTLINK_COLUMN_TYPES: dict[str, str] | None = None


async def _get_smartlink_legacy_owner_columns(db: aiosqlite.Connection) -> list[str]:
    global _SMARTLINK_LEGACY_OWNER_COLUMNS
    if _SMARTLINK_LEGACY_OWNER_COLUMNS is not None:
        return _SMARTLINK_LEGACY_OWNER_COLUMNS
    cur = await db.execute("PRAGMA table_info(smartlinks)")
    rows = await cur.fetchall()
    column_names = {row[1] for row in rows}
    candidates = ("legacy_owner_id", "owner_id", "user_id", "tg_id", "chat_id")
    _SMARTLINK_LEGACY_OWNER_COLUMNS = [col for col in candidates if col in column_names]
    return _SMARTLINK_LEGACY_OWNER_COLUMNS


async def _get_smartlink_columns(db: aiosqlite.Connection) -> set[str]:
    global _SMARTLINK_COLUMNS
    if _SMARTLINK_COLUMNS is not None:
        return _SMARTLINK_COLUMNS
    cur = await db.execute("PRAGMA table_info(smartlinks)")
    rows = await cur.fetchall()
    _SMARTLINK_COLUMNS = {row[1] for row in rows}
    return _SMARTLINK_COLUMNS


async def _get_smartlink_column_types(db: aiosqlite.Connection) -> dict[str, str]:
    global _SMARTLINK_COLUMN_TYPES
    if _SMARTLINK_COLUMN_TYPES is not None:
        return _SMARTLINK_COLUMN_TYPES
    cur = await db.execute("PRAGMA table_info(smartlinks)")
    rows = await cur.fetchall()
    _SMARTLINK_COLUMN_TYPES = {row[1]: (row[2] or "").lower() for row in rows}
    return _SMARTLINK_COLUMN_TYPES


def _smartlink_select_fields(columns: set[str]) -> str:
    def column_or_null(name: str, alias: str | None = None) -> str:
        if name in columns:
            return f"{name} AS {alias or name}"
        return f"NULL AS {alias or name}"

    if "owner_tg_user_id" in columns and "owner_tg_id" in columns:
        owner_expr = "COALESCE(owner_tg_user_id, owner_tg_id) AS owner_tg_id"
    elif "owner_tg_user_id" in columns:
        owner_expr = "owner_tg_user_id AS owner_tg_id"
    elif "owner_tg_id" in columns:
        owner_expr = "owner_tg_id AS owner_tg_id"
    else:
        owner_expr = "NULL AS owner_tg_id"

    if "cover_source" in columns and "cover_source_json" in columns:
        cover_source_expr = "COALESCE(cover_source, cover_source_json) AS cover_source_json"
    elif "cover_source" in columns:
        cover_source_expr = "cover_source AS cover_source_json"
    elif "cover_source_json" in columns:
        cover_source_expr = "cover_source_json AS cover_source_json"
    else:
        cover_source_expr = "NULL AS cover_source_json"

    fields = [
        column_or_null("id"),
        owner_expr,
        column_or_null("artist"),
        column_or_null("title"),
        column_or_null("release_date"),
        column_or_null("pre_save_enabled"),
        column_or_null("reminders_enabled"),
        column_or_null("project_id"),
        column_or_null("cover_file_id"),
        cover_source_expr,
        column_or_null("links_json"),
        column_or_null("caption_text"),
        column_or_null("branding_disabled"),
        column_or_null("created_at"),
        column_or_null("branding_paid"),
        column_or_null("cover_url"),
        column_or_null("artist_slug"),
        column_or_null("slug"),
        column_or_null("cover_version"),
    ]
    return ", ".join(fields)


def _smartlink_slugs_clause(columns: set[str]) -> str:
    if "artist_slug" in columns and "slug" in columns:
        return "lower(coalesce(artist_slug, '')) = lower(?) AND lower(coalesce(slug, '')) = lower(?)"
    return "1=0"


async def _migrate_legacy_smartlink_owners(
    db: aiosqlite.Connection, owner_tg_id: int, legacy_columns: list[str]
) -> None:
    migrated = 0
    for column in legacy_columns:
        cur = await db.execute(
            f"""
            UPDATE smartlinks
            SET owner_tg_id=?
            WHERE {column}=?
              AND (owner_tg_id IS NULL OR owner_tg_id=0 OR owner_tg_id!=?)
            """,
            (owner_tg_id, owner_tg_id, owner_tg_id),
        )
        migrated += cur.rowcount or 0
    if migrated:
        await db.commit()
        logger.warning(
            "[smartlinks-owner] migrated legacy owner_tg_id=%s rows=%s columns=%s",
            owner_tg_id,
            migrated,
            legacy_columns,
        )


async def _smartlink_owner_filter(
    db: aiosqlite.Connection, owner_tg_id: int
) -> tuple[str, list[object]]:
    columns = await _get_smartlink_columns(db)
    if "owner_tg_user_id" in columns:
        return "owner_tg_user_id=?", [str(owner_tg_id)]

    legacy_columns = await _get_smartlink_legacy_owner_columns(db)
    clauses: list[str] = []
    params: list[int] = []
    if "owner_tg_id" in columns:
        clauses.append("owner_tg_id=?")
        params.append(owner_tg_id)
    if legacy_columns and "owner_tg_id" in columns:
        await _migrate_legacy_smartlink_owners(db, owner_tg_id, legacy_columns)
    for column in legacy_columns:
        clauses.append(f"{column}=?")
        params.append(owner_tg_id)
    if not clauses:
        return "(1=0)", []
    return f"({' OR '.join(clauses)})", params


def _parse_offsets(raw: str | None) -> list[int]:
    values: list[int] = []
    for part in (raw or DEFAULT_REMINDER_OFFSETS).split(","):
        try:
            values.append(int(part.strip()))
        except ValueError:
            continue
    return values or [-7, -1, 0, 7]


def _parse_reminder_time(raw: str | None) -> dt.time | None:
    if not raw:
        return None
    try:
        h, m = raw.split(":")
        return dt.time(int(h), int(m))
    except Exception:
        return None


async def init_db():
    """Initialize the SQLite database schema and tuning pragmas."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("PRAGMA temp_store=MEMORY;")
        await db.execute("PRAGMA cache_size=-20000;")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            experience TEXT DEFAULT 'unknown',
            username TEXT,
            release_date TEXT DEFAULT NULL,
            reminders_enabled INTEGER DEFAULT 1,
            reminder_offsets TEXT DEFAULT '-7,-1,0,7',
            reminder_time TEXT DEFAULT '12:00',
            timezone TEXT DEFAULT 'Europe/Moscow',
            export_unlocked INTEGER DEFAULT 0
        )
        """)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN reminder_offsets TEXT DEFAULT '-7,-1,0,7'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN reminder_time TEXT DEFAULT '12:00'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'Europe/Moscow'")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN reminders_enabled INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN release_date TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN updates_opt_in INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_update_notified TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN export_unlocked INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reminder_log (
            tg_id INTEGER,
            key TEXT,
            "when" TEXT,
            sent_on TEXT,
            PRIMARY KEY (tg_id, key, "when")
        )
        """)
        try:
            await db.execute("ALTER TABLE reminder_log ADD COLUMN sent_on TEXT")
        except Exception:
            pass
        await db.execute("CREATE INDEX IF NOT EXISTS idx_reminder_log_sent_on ON reminder_log(sent_on)")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_tasks (
            tg_id INTEGER,
            task_id INTEGER,
            done INTEGER DEFAULT 0,
            PRIMARY KEY (tg_id, task_id)
        )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_tasks_tg ON user_tasks(tg_id)"
        )
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_accounts (
            tg_id INTEGER,
            key TEXT,
            status INTEGER DEFAULT 0,
            PRIMARY KEY (tg_id, key)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_forms (
            tg_id INTEGER PRIMARY KEY,
            form_name TEXT,
            step INTEGER DEFAULT 0,
            data_json TEXT DEFAULT '{}'
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS smartlinks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_tg_id INTEGER,
            artist TEXT,
            title TEXT,
            release_date TEXT,
            pre_save_enabled INTEGER DEFAULT 1,
            reminders_enabled INTEGER DEFAULT 1,
            project_id INTEGER,
            cover_file_id TEXT,
            cover_source_json TEXT DEFAULT '{}',
            links_json TEXT DEFAULT '{}',
            caption_text TEXT,
            branding_disabled INTEGER DEFAULT 0,
            created_at TEXT,
            branding_paid INTEGER DEFAULT 0,
            cover_url TEXT,
            cover_version INTEGER DEFAULT 1,
            artist_slug TEXT,
            slug TEXT
        )
        """)
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN project_id INTEGER")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN cover_source_json TEXT DEFAULT '{}' ")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN branding_disabled INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN created_at TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN branding_paid INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN cover_url TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN cover_version INTEGER DEFAULT 1")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN artist_slug TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN slug TEXT")
        except Exception:
            pass
        await db.execute("""
        CREATE TABLE IF NOT EXISTS smartlink_subscriptions (
            smartlink_id INTEGER,
            subscriber_tg_id INTEGER,
            notified INTEGER DEFAULT 0,
            PRIMARY KEY (smartlink_id, subscriber_tg_id)
        )
        """)
        try:
            await db.execute("ALTER TABLE smartlink_subscriptions ADD COLUMN notified INTEGER DEFAULT 0")
        except Exception:
            pass
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS smartlink_reminders (
            smartlink_id INTEGER,
            tg_id INTEGER,
            created_at TEXT,
            UNIQUE(smartlink_id, tg_id)
        )
        """
        )
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS smartlink_reminder_sends (
            smartlink_id INTEGER,
            tg_id INTEGER,
            sent_at TEXT,
            UNIQUE(smartlink_id, tg_id)
        )
        """
        )
        await db.execute("""
        CREATE TABLE IF NOT EXISTS smartlink_reminder_log (
            smartlink_id INTEGER,
            subscriber_tg_id INTEGER,
            offset_days INTEGER,
            sent_on TEXT,
            PRIMARY KEY (smartlink_id, subscriber_tg_id, offset_days)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS smartlink_messages (
            smartlink_id INTEGER,
            user_id INTEGER,
            chat_id INTEGER,
            message_id INTEGER,
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (smartlink_id, chat_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_tg_id INTEGER,
            name TEXT NOT NULL,
            slug TEXT,
            created_at TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS important_tasks (
            tg_id INTEGER,
            task_id INTEGER,
            PRIMARY KEY (tg_id, task_id)
        )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_important_tasks_tg ON important_tasks(tg_id)"
        )
        await db.execute("""
        CREATE TABLE IF NOT EXISTS qc_checks (
            tg_id INTEGER,
            task_id INTEGER,
            key TEXT,
            value TEXT,
            PRIMARY KEY (tg_id, task_id, key)
        )
        """)
        await db.commit()


async def ensure_user(
    tg_id: int,
    username: str | None = None,
    tasks: Iterable[tuple[int, str]] | None = None,
    accounts: Iterable[tuple[str, str]] | None = None,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (tg_id,))
        if username is not None:
            await db.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
        for task_id, _ in tasks or []:
            await db.execute("INSERT OR IGNORE INTO user_tasks (tg_id, task_id) VALUES (?, ?)", (tg_id, task_id))
        for key, _ in accounts or []:
            await db.execute("INSERT OR IGNORE INTO user_accounts (tg_id, key) VALUES (?, ?)", (tg_id, key))
        await db.commit()


async def get_experience(tg_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT experience FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else "unknown"


async def set_experience(tg_id: int, exp: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET experience=? WHERE tg_id=?", (exp, tg_id))
        await db.commit()


async def set_release_date(tg_id: int, date_str: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT release_date FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        current = row[0] if row else None
        if current == date_str:
            return
        await db.execute("UPDATE users SET release_date=? WHERE tg_id=?", (date_str, tg_id))
        await db.execute("DELETE FROM reminder_log WHERE tg_id=?", (tg_id,))
        await db.commit()


async def get_release_date(tg_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT release_date FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else None


async def set_reminders_enabled(tg_id: int, enabled: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT reminders_enabled FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        current = row[0] if row else 1
        if current == (1 if enabled else 0):
            return
        await db.execute("UPDATE users SET reminders_enabled=? WHERE tg_id=?", (1 if enabled else 0, tg_id))
        await db.commit()


async def get_reminders_enabled(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT reminders_enabled FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else True


async def toggle_reminders_enabled(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT reminders_enabled FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        current = row[0] if row else 1
        new_value = 0 if current else 1
        await db.execute("UPDATE users SET reminders_enabled=? WHERE tg_id=?", (new_value, tg_id))
        await db.commit()
        return bool(new_value)


async def get_user_reminder_prefs(tg_id: int) -> tuple[str, list[int], dt.time | None]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT timezone, reminder_offsets, reminder_time FROM users WHERE tg_id=?",
            (tg_id,),
        )
        row = await cur.fetchone()
    timezone = row[0] if row and row[0] else DEFAULT_TIMEZONE
    offsets_raw = row[1] if row else DEFAULT_REMINDER_OFFSETS
    reminder_time_raw = row[2] if row else DEFAULT_REMINDER_TIME
    return timezone, _parse_offsets(offsets_raw), _parse_reminder_time(reminder_time_raw) or _parse_reminder_time(DEFAULT_REMINDER_TIME)


async def get_updates_opt_in(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT updates_opt_in FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else True


async def set_updates_opt_in(tg_id: int, enabled: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET updates_opt_in=? WHERE tg_id=?", (1 if enabled else 0, tg_id))
        await db.commit()


async def set_export_unlocked(tg_id: int, unlocked: bool = True):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET export_unlocked=? WHERE tg_id=?",
            (1 if unlocked else 0, tg_id),
        )
        await db.commit()


async def get_export_unlocked(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT export_unlocked FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return bool(row[0]) if row and row[0] is not None else False


async def toggle_updates_opt_in(tg_id: int) -> bool:
    enabled = await get_updates_opt_in(tg_id)
    await set_updates_opt_in(tg_id, not enabled)
    return not enabled


async def get_last_update_notified(tg_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT last_update_notified FROM users WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else None


async def set_last_update_notified(
    tg_id: int,
    value: str | None,
    db: aiosqlite.Connection | None = None,
    *,
    commit: bool = True,
):
    if db:
        await db.execute("UPDATE users SET last_update_notified=? WHERE tg_id=?", (value, tg_id))
        if commit:
            await db.commit()
        return
    async with aiosqlite.connect(DB_PATH) as db_conn:
        await db_conn.execute("UPDATE users SET last_update_notified=? WHERE tg_id=?", (value, tg_id))
        await db_conn.commit()


async def get_tasks_state(tg_id: int) -> dict[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT task_id, done FROM user_tasks WHERE tg_id=? AND task_id > 0", (tg_id,)
        )
        rows = await cur.fetchall()
        return {tid: done for tid, done in rows}


async def toggle_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done = 1 - done WHERE tg_id=? AND task_id=?", (tg_id, task_id))
        await db.commit()


async def toggle_task_and_get_state(tg_id: int, task_id: int) -> dict[int, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done = 1 - done WHERE tg_id=? AND task_id=?", (tg_id, task_id))
        cur = await db.execute("SELECT task_id, done FROM user_tasks WHERE tg_id=?", (tg_id,))
        rows = await cur.fetchall()
        await db.commit()
        return {tid: done for tid, done in rows}


async def set_task_done(tg_id: int, task_id: int, done: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT done FROM user_tasks WHERE tg_id=? AND task_id=?", (tg_id, task_id))
        row = await cur.fetchone()
        current = row[0] if row else 0
        if current == done:
            return False
        await db.execute("UPDATE user_tasks SET done=? WHERE tg_id=? AND task_id=?", (done, tg_id, task_id))
        await db.commit()
        return True


FOCUS_SHOW_COMPLETED_TASK_ID = -1000


async def get_focus_show_completed(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT done FROM user_tasks WHERE tg_id=? AND task_id=?",
            (tg_id, FOCUS_SHOW_COMPLETED_TASK_ID),
        )
        row = await cur.fetchone()
    return bool(row[0]) if row else False


async def set_focus_show_completed(tg_id: int, show: bool):
    value = 1 if show else 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_tasks (tg_id, task_id, done) VALUES (?, ?, ?) "
            "ON CONFLICT(tg_id, task_id) DO UPDATE SET done=excluded.done",
            (tg_id, FOCUS_SHOW_COMPLETED_TASK_ID, value),
        )
        await db.commit()


async def get_accounts_state(tg_id: int) -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT key, status FROM user_accounts WHERE tg_id=?", (tg_id,))
        rows = await cur.fetchall()
        return {k: (s if s is not None else 0) for k, s in rows}


async def cycle_account_status(tg_id: int, key: str, status_fn) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT status FROM user_accounts WHERE tg_id=? AND key=?", (tg_id, key))
        row = await cur.fetchone()
        current = row[0] if row and row[0] is not None else 0
        new = status_fn(current)
        await db.execute("UPDATE user_accounts SET status=? WHERE tg_id=? AND key=?", (new, tg_id, key))
        await db.commit()
        return new


async def add_important_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO important_tasks (tg_id, task_id) VALUES (?, ?)",
            (tg_id, task_id)
        )
        await db.commit()


async def remove_important_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM important_tasks WHERE tg_id=? AND task_id=?",
            (tg_id, task_id)
        )
        await db.commit()


async def get_important_tasks(tg_id: int) -> set[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT task_id FROM important_tasks WHERE tg_id=?",
            (tg_id,),
        )
        rows = await cur.fetchall()
        return {r[0] for r in rows}


async def toggle_important_task(tg_id: int, task_id: int) -> set[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT task_id FROM important_tasks WHERE tg_id=?",
            (tg_id,),
        )
        rows = await cur.fetchall()
        important = {r[0] for r in rows}
        if task_id in important:
            await db.execute(
                "DELETE FROM important_tasks WHERE tg_id=? AND task_id=?",
                (tg_id, task_id)
            )
        else:
            await db.execute(
                "INSERT OR IGNORE INTO important_tasks (tg_id, task_id) VALUES (?, ?)",
                (tg_id, task_id)
            )
        cur = await db.execute(
            "SELECT task_id FROM important_tasks WHERE tg_id=?",
            (tg_id,),
        )
        rows = await cur.fetchall()
        await db.commit()
        return {r[0] for r in rows}


async def save_qc_check(tg_id: int, task_id: int, key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO qc_checks (tg_id, task_id, key, value) VALUES (?, ?, ?, ?)",
            (tg_id, task_id, key, value)
        )
        await db.commit()


async def was_qc_checked(tg_id: int, task_id: int, key: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM qc_checks WHERE tg_id=? AND task_id=? AND key=?",
            (tg_id, task_id, key)
        )
        row = await cur.fetchone()
        return row is not None


async def reset_progress_only(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done=0 WHERE tg_id=?", (tg_id,))
        await db.execute("UPDATE user_accounts SET status=0 WHERE tg_id=?", (tg_id,))
        await db.commit()


async def reset_all_data(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done=0 WHERE tg_id=?", (tg_id,))
        await db.execute("UPDATE user_accounts SET status=0 WHERE tg_id=?", (tg_id,))
        await db.execute("DELETE FROM important_tasks WHERE tg_id=?", (tg_id,))
        await db.execute("DELETE FROM qc_checks WHERE tg_id=?", (tg_id,))
        await db.execute("DELETE FROM reminder_log WHERE tg_id=?", (tg_id,))
        await db.execute("DELETE FROM smartlink_subscriptions WHERE subscriber_tg_id=?", (tg_id,))
        await db.execute("DELETE FROM smartlink_reminders WHERE tg_id=?", (tg_id,))
        await db.execute("DELETE FROM smartlink_reminder_sends WHERE tg_id=?", (tg_id,))
        owner_clause, owner_params = await _smartlink_owner_filter(db, tg_id)
        await db.execute(f"DELETE FROM smartlinks WHERE {owner_clause}", owner_params)
        await db.execute(
            "UPDATE users SET release_date=NULL, reminders_enabled=1 WHERE tg_id=?",
            (tg_id,)
        )
        await db.execute("DELETE FROM user_forms WHERE tg_id=?", (tg_id,))
        await db.commit()


def _smartlink_row_to_dict(row) -> dict:
    if not row:
        return {}
    cover_source_json = row[9] if len(row) > 9 else "{}"
    try:
        cover_source = json.loads(cover_source_json or "{}")
    except Exception:
        cover_source = {}
    if not cover_source and (row[8] if len(row) > 8 else ""):
        cover_source = {"type": "telegram", "file_id": row[8]}
    artist_slug = row[16] if len(row) > 16 else None
    slug = row[17] if len(row) > 17 else None
    cover_version = row[18] if len(row) > 18 and row[18] is not None else 1
    return {
        "id": row[0],
        "owner_tg_id": row[1],
        "artist": row[2] or "",
        "title": row[3] or "",
        "release_date": row[4],
        "pre_save_enabled": bool(row[5]) if len(row) > 5 else True,
        "reminders_enabled": bool(row[6]) if len(row) > 6 else True,
        "project_id": row[7] if len(row) > 7 else None,
        "cover_file_id": row[8] if len(row) > 8 else "",
        "cover_source": cover_source,
        "links": json.loads(row[10] or "{}"),
        "caption_text": row[11] or "",
        "branding_disabled": bool(row[12]) if len(row) > 12 else False,
        "created_at": row[13] if len(row) > 13 else None,
        "branding_paid": bool(row[14]) if len(row) > 14 else False,
        "cover_url": row[15] if len(row) > 15 else None,
        "artist_slug": artist_slug,
        "slug": slug,
        "cover_version": cover_version,
    }


async def save_smartlink(
    owner_tg_id: int,
    artist: str,
    title: str,
    release_date_iso: str,
    cover_file_id: str,
    cover_source: dict | None,
    links: dict,
    caption_text: str,
    branding_disabled: bool = False,
    pre_save_enabled: bool = True,
    reminders_enabled: bool = True,
    project_id: int | None = None,
    cover_url: str | None = None,
    artist_slug: str | None = None,
    slug: str | None = None,
    cover_version: int = 1,
) -> int | str:
    async with aiosqlite.connect(DB_PATH) as db:
        columns = await _get_smartlink_columns(db)

        id_type = column_types.get("id", "")
        id_is_text = "text" in id_type
        smartlink_id = None
        if id_is_text:
            if not artist_slug or not slug:
                raise ValueError("artist_slug and slug are required for TEXT smartlink id")
            smartlink_id = f"{artist_slug}:{slug}"

        insert_columns: list[str] = []
        values: list = []

        if "id" in columns and id_is_text:
            insert_columns.append("id")
            values.append(smartlink_id)
        if "owner_tg_user_id" in columns:
            insert_columns.append("owner_tg_user_id")
            values.append(str(owner_tg_id))
        elif "owner_tg_id" in columns:
            insert_columns.append("owner_tg_id")
            values.append(owner_tg_id)
        if "artist" in columns:
            insert_columns.append("artist")
            values.append(artist)
        if "title" in columns:
            insert_columns.append("title")
            values.append(title)
        if "release_date" in columns:
            insert_columns.append("release_date")
            values.append(release_date_iso)
        if "pre_save_enabled" in columns:
            insert_columns.append("pre_save_enabled")
            values.append(1 if pre_save_enabled else 0)
        if "reminders_enabled" in columns:
            insert_columns.append("reminders_enabled")
            values.append(1 if reminders_enabled else 0)
        if "project_id" in columns:
            insert_columns.append("project_id")
            values.append(project_id)
        if "cover_file_id" in columns:
            insert_columns.append("cover_file_id")
            values.append(cover_file_id)
        if "cover_source" in columns:
            insert_columns.append("cover_source")
            values.append(json.dumps(cover_source or {}, ensure_ascii=False))
        elif "cover_source_json" in columns:
            insert_columns.append("cover_source_json")
            values.append(json.dumps(cover_source or {}, ensure_ascii=False))
        if "links_json" in columns:
            insert_columns.append("links_json")
            values.append(json.dumps(links, ensure_ascii=False))
        if "caption_text" in columns:
            insert_columns.append("caption_text")
            values.append(caption_text)
        if "branding_disabled" in columns:
            insert_columns.append("branding_disabled")
            values.append(1 if branding_disabled else 0)
        if "created_at" in columns:
            insert_columns.append("created_at")
            values.append(dt.datetime.utcnow().isoformat())
        if "cover_url" in columns:
            insert_columns.append("cover_url")
            values.append(cover_url)
        if "artist_slug" in columns:
            insert_columns.append("artist_slug")
            values.append(artist_slug)
        if "slug" in columns:
            insert_columns.append("slug")
            values.append(slug)
        if "cover_version" in columns:
            insert_columns.append("cover_version")
            values.append(cover_version)

        if not insert_columns:
            raise RuntimeError("smartlinks table has no supported columns")

        placeholders = ", ".join(["?"] * len(insert_columns))
        cur = await db.execute(
            f"INSERT INTO smartlinks ({', '.join(insert_columns)}) VALUES ({placeholders})",
            values,
        )
        await db.commit()
        return smartlink_id if id_is_text else cur.lastrowid


async def update_smartlink_caption(smartlink_id: int | str, caption_text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE smartlinks SET caption_text=? WHERE id=?",
            (caption_text, smartlink_id),
        )
        await db.commit()


async def get_latest_smartlink(owner_tg_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        columns = await _get_smartlink_columns(db)
        select_fields = _smartlink_select_fields(columns)
        owner_clause, owner_params = await _smartlink_owner_filter(db, owner_tg_id)
        order_by = "COALESCE(created_at, '') DESC, id DESC" if "created_at" in columns else "id DESC"
        cur = await db.execute(
            f"SELECT {select_fields} FROM smartlinks WHERE {owner_clause} ORDER BY {order_by} LIMIT 1",
            owner_params,
        )
        row = await cur.fetchone()
        return _smartlink_row_to_dict(row) if row else None


async def get_smartlink_by_id(smartlink_id: int | str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        columns = await _get_smartlink_columns(db)
        select_fields = _smartlink_select_fields(columns)
        cur = await db.execute(
            f"SELECT {select_fields} FROM smartlinks WHERE id=?",
            (smartlink_id,),
        )
        row = await cur.fetchone()
        return _smartlink_row_to_dict(row) if row else None


async def get_smartlink_by_slugs(artist_slug: str, slug: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        columns = await _get_smartlink_columns(db)
        select_fields = _smartlink_select_fields(columns)
        slugs_clause = _smartlink_slugs_clause(columns)
        if slugs_clause == "1=0":
            return None
        cur = await db.execute(
            f"""
            SELECT {select_fields}
            FROM smartlinks
            WHERE {slugs_clause}
            ORDER BY id DESC
            LIMIT 1
            """,
            (artist_slug, slug),
        )
        row = await cur.fetchone()
    return _smartlink_row_to_dict(row) if row else None


async def get_smartlink_by_owner_slugs(
    owner_tg_id: int, artist_slug: str, slug: str
) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        columns = await _get_smartlink_columns(db)
        select_fields = _smartlink_select_fields(columns)
        slugs_clause = _smartlink_slugs_clause(columns)
        if slugs_clause == "1=0":
            return None
        owner_clause, owner_params = await _smartlink_owner_filter(db, owner_tg_id)
        cur = await db.execute(
            f"""
            SELECT {select_fields}
            FROM smartlinks
            WHERE {owner_clause}
              AND {slugs_clause}
            ORDER BY id DESC
            LIMIT 1
            """,
            (*owner_params, artist_slug, slug),
        )
        row = await cur.fetchone()
    return _smartlink_row_to_dict(row) if row else None


async def list_smartlinks(owner_tg_id: int, limit: int = 5, offset: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        columns = await _get_smartlink_columns(db)
        select_fields = _smartlink_select_fields(columns)
        owner_clause, owner_params = await _smartlink_owner_filter(db, owner_tg_id)
        order_by = "COALESCE(created_at, '') DESC, id DESC" if "created_at" in columns else "id DESC"
        cur = await db.execute(
            f"""
            SELECT {select_fields}
            FROM smartlinks
            WHERE {owner_clause}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            (*owner_params, limit, offset),
        )
        return [_smartlink_row_to_dict(row) for row in await cur.fetchall()]


async def count_smartlinks(owner_tg_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        owner_clause, owner_params = await _smartlink_owner_filter(db, owner_tg_id)
        cur = await db.execute(
            f"SELECT COUNT(*) FROM smartlinks WHERE {owner_clause}", owner_params
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def get_owned_smartlink(owner_tg_id: int, smartlink_id: int | str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        columns = await _get_smartlink_columns(db)
        select_fields = _smartlink_select_fields(columns)
        owner_clause, owner_params = await _smartlink_owner_filter(db, owner_tg_id)
        cur = await db.execute(
            f"SELECT {select_fields} FROM smartlinks WHERE id=? AND {owner_clause}",
            (smartlink_id, *owner_params),
        )
        row = await cur.fetchone()
        return _smartlink_row_to_dict(row) if row else None


async def count_all_smartlinks() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM smartlinks")
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def update_smartlink_data(
    smartlink_id: int | str, owner_tg_id: int, updates: dict
) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        columns = await _get_smartlink_columns(db)
        allowed = {
            "artist",
            "title",
            "release_date",
            "cover_file_id",
            "cover_source",
            "cover_url",
            "cover_version",
            "links",
            "caption_text",
            "branding_disabled",
            "branding_paid",
            "pre_save_enabled",
            "reminders_enabled",
            "project_id",
            "artist_slug",
            "slug",
        }
        fields: list[str] = []
        params: list = []

        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "links":
                if "links_json" in columns:
                    fields.append("links_json=?")
                    params.append(json.dumps(value or {}, ensure_ascii=False))
            elif key == "cover_source":
                cover_column = "cover_source" if "cover_source" in columns else "cover_source_json"
                if cover_column in columns:
                    fields.append(f"{cover_column}=?")
                    params.append(json.dumps(value or {}, ensure_ascii=False))
            elif key == "branding_disabled":
                fields.append("branding_disabled=?")
                params.append(1 if value else 0)
            elif key == "branding_paid":
                fields.append("branding_paid=?")
                params.append(1 if value else 0)
            else:
                if key in columns:
                    fields.append(f"{key}=?")
                    params.append(value)

        if not fields:
            return False
        owner_clause, owner_params = await _smartlink_owner_filter(db, owner_tg_id)
        params.extend([smartlink_id, *owner_params])
        await db.execute(
            f"UPDATE smartlinks SET {', '.join(fields)} WHERE id=? AND {owner_clause}",
            params,
        )
        await db.commit()
    return True


async def update_smartlink_data_by_slugs(
    owner_tg_id: int, artist_slug: str, slug: str, updates: dict
) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        columns = await _get_smartlink_columns(db)
        column_types = await _get_smartlink_column_types(db)
        allowed = {
            "artist",
            "title",
            "release_date",
            "cover_file_id",
            "cover_source",
            "cover_url",
            "cover_version",
            "links",
            "caption_text",
            "branding_disabled",
            "branding_paid",
            "pre_save_enabled",
            "reminders_enabled",
            "project_id",
            "artist_slug",
            "slug",
        }
        fields: list[str] = []
        params: list = []

        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "links":
                if "links_json" in columns:
                    fields.append("links_json=?")
                    params.append(json.dumps(value or {}, ensure_ascii=False))
            elif key == "cover_source":
                cover_column = "cover_source" if "cover_source" in columns else "cover_source_json"
                if cover_column in columns:
                    fields.append(f"{cover_column}=?")
                    params.append(json.dumps(value or {}, ensure_ascii=False))
            elif key == "branding_disabled":
                fields.append("branding_disabled=?")
                params.append(1 if value else 0)
            elif key == "branding_paid":
                fields.append("branding_paid=?")
                params.append(1 if value else 0)
            else:
                if key in columns:
                    fields.append(f"{key}=?")
                    params.append(value)

        if ("artist_slug" in updates or "slug" in updates) and "id" in columns:
            id_type = column_types.get("id", "")
            if "text" in id_type:
                new_artist = updates.get("artist_slug", artist_slug)
                new_slug = updates.get("slug", slug)
                if new_artist and new_slug:
                    fields.append("id=?")
                    params.append(f"{new_artist}:{new_slug}")

        if not fields:
            return False
        slugs_clause = _smartlink_slugs_clause(columns)
        if slugs_clause == "1=0":
            return False
        owner_clause, owner_params = await _smartlink_owner_filter(db, owner_tg_id)
        params.extend([*owner_params, artist_slug, slug])
        await db.execute(
            f"UPDATE smartlinks SET {', '.join(fields)} WHERE {owner_clause} AND {slugs_clause}",
            params,
        )
        await db.commit()
    return True


async def bump_smartlink_cover_version(
    smartlink_id: int | str, owner_tg_id: int | None = None
) -> int | None:
    params: list = [smartlink_id]
    where = "id=?"
    if owner_tg_id is not None:
        async with aiosqlite.connect(DB_PATH) as db:
            owner_clause, owner_params = await _smartlink_owner_filter(db, owner_tg_id)
            where += f" AND {owner_clause}"
            params.extend(owner_params)
            await db.execute(
                f"UPDATE smartlinks SET cover_version=COALESCE(cover_version, 1) + 1 WHERE {where}",
                params,
            )
            await db.commit()

            cur = await db.execute(
                f"SELECT cover_version FROM smartlinks WHERE {where}", params
            )
            row = await cur.fetchone()
            return int(row[0]) if row else None

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE smartlinks SET cover_version=COALESCE(cover_version, 1) + 1 WHERE {where}",
            params,
        )
        await db.commit()

        cur = await db.execute(f"SELECT cover_version FROM smartlinks WHERE {where}", params)
        row = await cur.fetchone()
        return int(row[0]) if row else None


async def delete_smartlink(smartlink_id: int | str, owner_tg_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        owner_clause, owner_params = await _smartlink_owner_filter(db, owner_tg_id)
        await db.execute(
            f"DELETE FROM smartlinks WHERE id=? AND {owner_clause}",
            (smartlink_id, *owner_params),
        )
        await db.execute("DELETE FROM smartlink_subscriptions WHERE smartlink_id=?", (smartlink_id,))
        await db.execute("DELETE FROM smartlink_reminders WHERE smartlink_id=?", (smartlink_id,))
        await db.execute("DELETE FROM smartlink_reminder_sends WHERE smartlink_id=?", (smartlink_id,))
        await db.execute("DELETE FROM smartlink_messages WHERE smartlink_id=?", (smartlink_id,))
        await db.commit()


async def set_smartlink_subscription(smartlink_id: int, subscriber_tg_id: int, subscribed: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        if subscribed:
            await db.execute(
                "INSERT OR REPLACE INTO smartlink_subscriptions (smartlink_id, subscriber_tg_id, notified) VALUES (?, ?, 0)",
                (smartlink_id, subscriber_tg_id),
            )
        else:
            await db.execute(
                "DELETE FROM smartlink_subscriptions WHERE smartlink_id=? AND subscriber_tg_id=?",
                (smartlink_id, subscriber_tg_id),
            )
        await db.commit()


async def is_smartlink_subscribed(smartlink_id: int, subscriber_tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM smartlink_subscriptions WHERE smartlink_id=? AND subscriber_tg_id=?",
            (smartlink_id, subscriber_tg_id),
        )
        row = await cur.fetchone()
        return row is not None


async def get_smartlink_subscribers(smartlink_id: int) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT subscriber_tg_id FROM smartlink_subscriptions WHERE smartlink_id=?",
            (smartlink_id,),
        )
        return [row[0] for row in await cur.fetchall()]


async def mark_smartlink_notified(smartlink_id: int, subscriber_tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE smartlink_subscriptions SET notified=1 WHERE smartlink_id=? AND subscriber_tg_id=?",
            (smartlink_id, subscriber_tg_id),
        )
        await db.commit()


async def get_smartlinks_with_release() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        columns = await _get_smartlink_columns(db)
        select_fields = _smartlink_select_fields(columns)
        cur = await db.execute(
            f"SELECT {select_fields} FROM smartlinks WHERE release_date IS NOT NULL",
        )
        return [_smartlink_row_to_dict(row) for row in await cur.fetchall()]


async def form_start(tg_id: int, form_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_forms (tg_id, form_name, step, data_json) VALUES (?, ?, 0, ?)",
            (tg_id, form_name, "{}")
        )
        await db.commit()


async def form_get(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT form_name, step, data_json FROM user_forms WHERE tg_id=?", (tg_id,))
        row = await cur.fetchone()
    if not row:
        return None
    form_name, step, data_json = row
    try:
        data = json.loads(data_json or "{}")
    except Exception:
        data = {}
    return {"form_name": form_name, "step": step, "data": data}


async def form_set(tg_id: int, step: int, data: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_forms SET step=?, data_json=? WHERE tg_id=?",
            (step, json.dumps(data, ensure_ascii=False), tg_id)
        )
        await db.commit()


async def form_clear(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM user_forms WHERE tg_id=?", (tg_id,))
        await db.commit()


async def was_reminder_sent(tg_id: int, key: str, when: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM reminder_log WHERE tg_id=? AND key=? AND \"when\"=?",
            (tg_id, key, when)
        )
        row = await cur.fetchone()
        return row is not None


async def mark_reminder_sent(tg_id: int, key: str, when: str, sent_on: dt.date):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO reminder_log (tg_id, key, \"when\", sent_on) VALUES (?, ?, ?, ?)",
            (tg_id, key, when, sent_on.isoformat())
        )
        await db.commit()


async def was_smartlink_day_sent(smartlink_id: int, subscriber_tg_id: int, offset_days: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM smartlink_reminder_log WHERE smartlink_id=? AND subscriber_tg_id=? AND offset_days=?",
            (smartlink_id, subscriber_tg_id, offset_days),
        )
        return await cur.fetchone() is not None


async def mark_smartlink_day_sent(smartlink_id: int, subscriber_tg_id: int, offset_days: int, sent_on: dt.date):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO smartlink_reminder_log (smartlink_id, subscriber_tg_id, offset_days, sent_on) VALUES (?, ?, ?, ?)",
            (smartlink_id, subscriber_tg_id, offset_days, sent_on.isoformat()),
        )
        await db.commit()


async def save_smartlink_message_reference(
    smartlink_id: int, user_id: int, chat_id: int, message_id: int
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO smartlink_messages (smartlink_id, user_id, chat_id, message_id, created_at, updated_at)
            VALUES (?, ?, ?, ?,
                COALESCE((SELECT created_at FROM smartlink_messages WHERE smartlink_id=? AND chat_id=?), ?),
                ?
            )
            """,
            (
                smartlink_id,
                user_id,
                chat_id,
                message_id,
                smartlink_id,
                chat_id,
                dt.datetime.utcnow().isoformat(),
                dt.datetime.utcnow().isoformat(),
            ),
        )
        await db.commit()


async def get_smartlink_messages(smartlink_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT smartlink_id, user_id, chat_id, message_id FROM smartlink_messages WHERE smartlink_id=?",
            (smartlink_id,),
        )
        rows = await cur.fetchall()
    return [
        {
            "smartlink_id": row[0],
            "user_id": row[1],
            "chat_id": row[2],
            "message_id": row[3],
        }
        for row in rows
    ]


def _parse_smartlink_date(date_str: str | None) -> dt.date | None:
    if not date_str:
        return None
    try:
        if "-" in date_str:
            y, m, d = date_str.split("-")
            return dt.date(int(y), int(m), int(d))
        if "." in date_str:
            d, m, y = date_str.split(".")
            return dt.date(int(y), int(m), int(d))
    except Exception:
        return None
    return None


async def add_smartlink_reminder(tg_id: int, smartlink_id: int | str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO smartlink_reminders (smartlink_id, tg_id, created_at) VALUES (?, ?, ?)",
            (smartlink_id, tg_id, dt.datetime.utcnow().isoformat()),
        )
        await db.commit()
        return cur.rowcount > 0


async def remove_smartlink_reminder(tg_id: int, smartlink_id: int | str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM smartlink_reminders WHERE smartlink_id=? AND tg_id=?",
            (smartlink_id, tg_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def is_smartlink_reminder_set(tg_id: int, smartlink_id: int | str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM smartlink_reminders WHERE smartlink_id=? AND tg_id=?",
            (smartlink_id, tg_id),
        )
        return await cur.fetchone() is not None


async def get_due_smartlink_reminders(today_date_str: str) -> list[tuple[int | str, int]]:
    target_date = _parse_smartlink_date(today_date_str)
    if not target_date:
        return []

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT r.smartlink_id, r.tg_id, s.release_date FROM smartlink_reminders r JOIN smartlinks s ON r.smartlink_id = s.id"
        )
        rows = await cur.fetchall()

    due: list[tuple[int | str, int]] = []
    for smartlink_id, tg_id, release_date in rows:
        rd = _parse_smartlink_date(release_date)
        if rd and rd == target_date:
            due.append((smartlink_id, tg_id))
    return due


async def mark_smartlink_reminder_sent(tg_id: int, smartlink_id: int | str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO smartlink_reminder_sends (smartlink_id, tg_id, sent_at) VALUES (?, ?, ?)",
            (smartlink_id, tg_id, dt.datetime.utcnow().isoformat()),
        )
        await db.commit()


async def was_smartlink_reminder_sent(tg_id: int, smartlink_id: int | str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM smartlink_reminder_sends WHERE smartlink_id=? AND tg_id=?",
            (smartlink_id, tg_id),
        )
        return await cur.fetchone() is not None


async def cleanup_reminder_log(today: dt.date, clean_days: int = REMINDER_CLEAN_DAYS):
    threshold = today - dt.timedelta(days=clean_days)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM reminder_log WHERE sent_on IS NOT NULL AND sent_on < ?",
            (threshold.isoformat(),),
        )
        await db.commit()


async def get_reminder_users() -> list[tuple[int, str | None, str | None]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT tg_id, username, release_date FROM users WHERE reminders_enabled=1 AND release_date IS NOT NULL"
        )
        return await cur.fetchall()


async def get_updates_opt_in_users() -> list[tuple[int, str | None]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT tg_id, last_update_notified FROM users WHERE updates_opt_in=1"
        )
        return await cur.fetchall()
