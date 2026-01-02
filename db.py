import datetime as dt
import json
from typing import Iterable

import aiosqlite

DB_PATH = "bot.db"
DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_REMINDER_OFFSETS = "-7,-1,0,7"
DEFAULT_REMINDER_TIME = "12:00"
REMINDER_CLEAN_DAYS = 60


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
            links_json TEXT DEFAULT '{}',
            caption_text TEXT,
            branding_disabled INTEGER DEFAULT 0,
            created_at TEXT,
            branding_paid INTEGER DEFAULT 0
        )
        """)
        try:
            await db.execute("ALTER TABLE smartlinks ADD COLUMN project_id INTEGER")
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
        cur = await db.execute("SELECT task_id, done FROM user_tasks WHERE tg_id=?", (tg_id,))
        rows = await cur.fetchall()
        return {tid: done for tid, done in rows}


async def toggle_task(tg_id: int, task_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE user_tasks SET done = 1 - done WHERE tg_id=? AND task_id=?", (tg_id, task_id))
        await db.commit()


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
        await db.execute("DELETE FROM smartlinks WHERE owner_tg_id=?", (tg_id,))
        await db.execute(
            "UPDATE users SET release_date=NULL, reminders_enabled=1 WHERE tg_id=?",
            (tg_id,)
        )
        await db.execute("DELETE FROM user_forms WHERE tg_id=?", (tg_id,))
        await db.commit()


def _smartlink_row_to_dict(row) -> dict:
    if not row:
        return {}
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
        "links": json.loads(row[9] or "{}"),
        "caption_text": row[10] or "",
        "branding_disabled": bool(row[11]) if len(row) > 11 else False,
        "created_at": row[12] if len(row) > 12 else None,
        "branding_paid": bool(row[13]) if len(row) > 13 else False,
    }


async def save_smartlink(
    owner_tg_id: int,
    artist: str,
    title: str,
    release_date_iso: str,
    cover_file_id: str,
    links: dict,
    caption_text: str,
    branding_disabled: bool = False,
    pre_save_enabled: bool = True,
    reminders_enabled: bool = True,
    project_id: int | None = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO smartlinks (owner_tg_id, artist, title, release_date, pre_save_enabled, reminders_enabled, project_id, cover_file_id, links_json, caption_text, branding_disabled, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner_tg_id,
                artist,
                title,
                release_date_iso,
                1 if pre_save_enabled else 0,
                1 if reminders_enabled else 0,
                project_id,
                cover_file_id,
                json.dumps(links, ensure_ascii=False),
                caption_text,
                1 if branding_disabled else 0,
                dt.datetime.utcnow().isoformat(),
            ),
        )
        await db.commit()
        return cur.lastrowid


async def update_smartlink_caption(smartlink_id: int, caption_text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE smartlinks SET caption_text=? WHERE id=?",
            (caption_text, smartlink_id),
        )
        await db.commit()


async def get_latest_smartlink(owner_tg_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, owner_tg_id, artist, title, release_date, pre_save_enabled, reminders_enabled, project_id, cover_file_id, links_json, caption_text, branding_disabled, created_at, branding_paid FROM smartlinks WHERE owner_tg_id=? ORDER BY id DESC LIMIT 1",
            (owner_tg_id,),
        )
        row = await cur.fetchone()
        return _smartlink_row_to_dict(row) if row else None


async def get_smartlink_by_id(smartlink_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, owner_tg_id, artist, title, release_date, pre_save_enabled, reminders_enabled, project_id, cover_file_id, links_json, caption_text, branding_disabled, created_at, branding_paid FROM smartlinks WHERE id=?",
            (smartlink_id,),
        )
        row = await cur.fetchone()
        return _smartlink_row_to_dict(row) if row else None


async def list_smartlinks(owner_tg_id: int, limit: int = 5, offset: int = 0) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, owner_tg_id, artist, title, release_date, pre_save_enabled, reminders_enabled, project_id, cover_file_id, links_json, caption_text, branding_disabled, created_at, branding_paid FROM smartlinks WHERE owner_tg_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (owner_tg_id, limit, offset),
        )
        return [_smartlink_row_to_dict(row) for row in await cur.fetchall()]


async def count_smartlinks(owner_tg_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM smartlinks WHERE owner_tg_id=?", (owner_tg_id,))
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def update_smartlink_data(smartlink_id: int, owner_tg_id: int, updates: dict) -> bool:
    allowed = {
        "artist",
        "title",
        "release_date",
        "cover_file_id",
        "links",
        "caption_text",
        "branding_disabled",
        "branding_paid",
        "pre_save_enabled",
        "reminders_enabled",
        "project_id",
    }
    fields: list[str] = []
    params: list = []

    for key, value in updates.items():
        if key not in allowed:
            continue
        if key == "links":
            fields.append("links_json=?")
            params.append(json.dumps(value or {}, ensure_ascii=False))
        elif key == "branding_disabled":
            fields.append("branding_disabled=?")
            params.append(1 if value else 0)
        elif key == "branding_paid":
            fields.append("branding_paid=?")
            params.append(1 if value else 0)
        else:
            fields.append(f"{key}=?")
            params.append(value)

    if not fields:
        return False

    params.extend([smartlink_id, owner_tg_id])

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE smartlinks SET {', '.join(fields)} WHERE id=? AND owner_tg_id=?",
            params,
        )
        await db.commit()
    return True


async def delete_smartlink(smartlink_id: int, owner_tg_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM smartlinks WHERE id=? AND owner_tg_id=?", (smartlink_id, owner_tg_id))
        await db.execute("DELETE FROM smartlink_subscriptions WHERE smartlink_id=?", (smartlink_id,))
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
        cur = await db.execute(
            "SELECT id, owner_tg_id, artist, title, release_date, pre_save_enabled, reminders_enabled, project_id, cover_file_id, links_json, caption_text, branding_disabled, created_at, branding_paid FROM smartlinks WHERE release_date IS NOT NULL",
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
