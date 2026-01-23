import datetime as dt
from datetime import timezone
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Iterable

import aiosqlite

DB_PATH = os.getenv("DB_PATH", "bot.db")
SMARTLINK_D1_PATH = (
    os.getenv("SMARTLINK_D1_PATH")
    or os.getenv("SMARTLINK_D1_DB")
    or os.getenv("SMARTLINK_DB_PATH")
    # Fallback to main DB so smartlinks still persist even if a separate D1 path isn't configured.
    or DB_PATH
)
DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_REMINDER_OFFSETS = "-7,-1,0,7"
DEFAULT_REMINDER_TIME = "12:00"
REMINDER_CLEAN_DAYS = 60
logger = logging.getLogger(__name__)

SMARTLINK_PUBLISH_QUEUE_TABLE = "smartlink_publish_queue"


@asynccontextmanager
async def _smartlink_d1_connection():
    if not SMARTLINK_D1_PATH:
        logger.debug("[smartlink-d1] SMARTLINK_D1_PATH not configured; skipping D1 connection")
        yield None
        return
    db = await aiosqlite.connect(SMARTLINK_D1_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


def _parse_json_value(raw):
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


def _coerce_bool(value: object | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _normalize_d1_smartlink(row: aiosqlite.Row) -> dict:
    data = dict(row)
    owner_tg_user_id = str(data.get("owner_tg_user_id") or "").strip()

    artist_slug = str(data.get("artist_slug") or "").strip()
    slug = str(data.get("slug") or "").strip()
    links_raw = data.get("links_json") or data.get("links")
    links = _parse_json_value(links_raw)
    if not isinstance(links, dict):
        links = {}

    metadata_raw = data.get("metadata_json") or data.get("metadata")
    metadata = _parse_json_value(metadata_raw)
    if not isinstance(metadata, dict):
        metadata = {}

    cover_source_raw = data.get("cover_source_json") or data.get("cover_source")
    cover_source = _parse_json_value(cover_source_raw)
    if not isinstance(cover_source, dict):
        cover_source = {}

    cover_file_id = data.get("cover_file_id") or cover_source.get("file_id") or ""

    smartlink = {
        "id": f"{artist_slug}:{slug}" if artist_slug and slug else str(data.get("id") or ""),
        "d1_id": data.get("id"),
        "owner_tg_user_id": owner_tg_user_id,
        "artist": data.get("artist") or data.get("artist_name") or "",
        "title": data.get("title") or "",
        "release_date": data.get("release_date") or "",
        "cover_file_id": cover_file_id or "",
        "cover_source": cover_source or {},
        "links": links,
        "caption_text": data.get("caption_text") or "",
        "branding_disabled": _coerce_bool(data.get("branding_disabled")),
        "branding_paid": _coerce_bool(data.get("branding_paid")),
        "pre_save_enabled": _coerce_bool(data.get("pre_save_enabled"), default=True),
        "reminders_enabled": _coerce_bool(data.get("reminders_enabled"), default=True),
        "cover_url": data.get("cover_url"),
        "artist_slug": artist_slug,
        "slug": slug,
        "cover_version": int(data.get("cover_version") or 1),
        "cover_updated_at": data.get("cover_updated_at"),
        "metadata": metadata,
    }
    return smartlink


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
        await db.execute(
            f"""
        CREATE TABLE IF NOT EXISTS {SMARTLINK_PUBLISH_QUEUE_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist_slug TEXT NOT NULL,
            slug TEXT NOT NULL,
            smartlink_json TEXT NOT NULL,
            owner_json TEXT,
            attempt INTEGER DEFAULT 0,
            next_attempt_at TEXT NOT NULL,
            last_error TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(artist_slug, slug)
        )
        """
        )
        await db.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_tg_user_id TEXT,
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


async def list_owned_smartlinks(
    owner_tg_user_id: int | str,
    limit: int,
    offset: int,
) -> list[dict] | None:
    async with _smartlink_d1_connection() as db:
        if not db:
            return None
        try:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS smartlinks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_tg_user_id TEXT NOT NULL,
                    owner_tg_username TEXT,
                    owner_display_name TEXT,
                    artist_slug TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    artist TEXT,
                    artist_name TEXT,
                    title TEXT,
                    release_date TEXT,
                    cover_file_id TEXT,
                    cover_url TEXT,
                    cover_version INTEGER,
                    caption_text TEXT,
                    branding_disabled INTEGER,
                    branding_paid INTEGER,
                    pre_save_enabled INTEGER,
                    reminders_enabled INTEGER,
                    links_json TEXT,
                    cover_source_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    cover_updated_at TEXT,
                    UNIQUE(owner_tg_user_id, artist_slug, slug)
                )
                """
            )
            query = (
                "SELECT * FROM smartlinks WHERE owner_tg_user_id=? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?"
            )
            logger.info(
                "[smartlink-d1] list query=%s tg_id=%s limit=%s offset=%s",
                query,
                owner_tg_user_id,
                limit,
                offset,
            )
            cur = await db.execute(query, (str(owner_tg_user_id), limit, offset))
            rows = await cur.fetchall()
            items = [_normalize_d1_smartlink(row) for row in rows]
            logger.info(
                "[smartlink-d1] list result tg_id=%s count=%s",
                owner_tg_user_id,
                len(items),
            )
            return items
        except Exception:
            logger.exception("[smartlink-d1] failed to list smartlinks owner=%s", owner_tg_user_id)
            return None


async def count_owned_smartlinks(owner_tg_user_id: int | str) -> int | None:
    async with _smartlink_d1_connection() as db:
        if not db:
            return None
        try:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS smartlinks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_tg_user_id TEXT NOT NULL,
                    owner_tg_username TEXT,
                    owner_display_name TEXT,
                    artist_slug TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    artist TEXT,
                    artist_name TEXT,
                    title TEXT,
                    release_date TEXT,
                    cover_file_id TEXT,
                    cover_url TEXT,
                    cover_version INTEGER,
                    caption_text TEXT,
                    branding_disabled INTEGER,
                    branding_paid INTEGER,
                    pre_save_enabled INTEGER,
                    reminders_enabled INTEGER,
                    links_json TEXT,
                    cover_source_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    cover_updated_at TEXT,
                    UNIQUE(owner_tg_user_id, artist_slug, slug)
                )
                """
            )
            query = "SELECT COUNT(1) FROM smartlinks WHERE owner_tg_user_id=?"
            logger.info(
                "[smartlink-d1] count query=%s tg_id=%s",
                query,
                owner_tg_user_id,
            )
            cur = await db.execute(
                query,
                (str(owner_tg_user_id),),
            )
            row = await cur.fetchone()
            count = int(row[0]) if row else 0
            logger.info(
                "[smartlink-d1] count result tg_id=%s count=%s",
                owner_tg_user_id,
                count,
            )
            return count
        except Exception:
            logger.exception("[smartlink-d1] failed to count smartlinks owner=%s", owner_tg_user_id)
            return None


async def fetch_owned_smartlink_from_d1(
    owner_tg_user_id: int | str,
    artist_slug: str,
    slug: str,
) -> dict | None:
    artist_slug = str(artist_slug or "").strip()
    slug = str(slug or "").strip()
    if not artist_slug or not slug:
        return None
    async with _smartlink_d1_connection() as db:
        if not db:
            return None
        try:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS smartlinks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_tg_user_id TEXT NOT NULL,
                    owner_tg_username TEXT,
                    owner_display_name TEXT,
                    artist_slug TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    artist TEXT,
                    artist_name TEXT,
                    title TEXT,
                    release_date TEXT,
                    cover_file_id TEXT,
                    cover_url TEXT,
                    cover_version INTEGER,
                    caption_text TEXT,
                    branding_disabled INTEGER,
                    branding_paid INTEGER,
                    pre_save_enabled INTEGER,
                    reminders_enabled INTEGER,
                    links_json TEXT,
                    cover_source_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    cover_updated_at TEXT,
                    UNIQUE(owner_tg_user_id, artist_slug, slug)
                )
                """
            )
            query = """
                SELECT * FROM smartlinks
                WHERE owner_tg_user_id=? AND artist_slug=? AND slug=?
                LIMIT 1
                """
            logger.info(
                "[smartlink-d1] fetch_by_slug query=%s tg_id=%s artist_slug=%s slug=%s",
                "SELECT * FROM smartlinks WHERE owner_tg_user_id=? AND artist_slug=? AND slug=? LIMIT 1",
                owner_tg_user_id,
                artist_slug,
                slug,
            )
            cur = await db.execute(
                query,
                (str(owner_tg_user_id), artist_slug, slug),
            )
            row = await cur.fetchone()
            smartlink = _normalize_d1_smartlink(row) if row else None
            logger.info(
                "[smartlink-d1] fetch_by_slug result tg_id=%s found=%s",
                owner_tg_user_id,
                bool(smartlink),
            )
            return smartlink
        except Exception:
            logger.exception(
                "[smartlink-d1] failed to fetch smartlink owner=%s artist_slug=%s slug=%s",
                owner_tg_user_id,
                artist_slug,
                slug,
            )
            return None


async def fetch_owned_smartlink_by_id(
    owner_tg_user_id: int | str,
    smartlink_id: int | str,
) -> dict | None:
    async with _smartlink_d1_connection() as db:
        if not db:
            return None


async def delete_owned_smartlink_from_d1(
    owner_tg_user_id: int | str,
    artist_slug: str,
    slug: str,
) -> bool:
    """Delete a locally persisted smartlink (D1/SQLite)."""
    artist_slug = str(artist_slug or "").strip()
    slug = str(slug or "").strip()
    if not artist_slug or not slug:
        return False
    async with _smartlink_d1_connection() as db:
        if not db:
            return False
        try:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS smartlinks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_tg_user_id TEXT NOT NULL,
                    owner_tg_username TEXT,
                    owner_display_name TEXT,
                    artist_slug TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    artist TEXT,
                    artist_name TEXT,
                    title TEXT,
                    release_date TEXT,
                    cover_file_id TEXT,
                    cover_url TEXT,
                    cover_version INTEGER,
                    caption_text TEXT,
                    branding_disabled INTEGER,
                    branding_paid INTEGER,
                    pre_save_enabled INTEGER,
                    reminders_enabled INTEGER,
                    links_json TEXT,
                    cover_source_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    cover_updated_at TEXT,
                    UNIQUE(owner_tg_user_id, artist_slug, slug)
                )
                """
            )
            cur = await db.execute(
                "DELETE FROM smartlinks WHERE owner_tg_user_id=? AND artist_slug=? AND slug=?",
                (str(owner_tg_user_id), artist_slug, slug),
            )
            await db.commit()
            return bool(cur.rowcount and cur.rowcount > 0)
        except Exception:
            logger.exception(
                "[smartlink-d1] failed to delete smartlink owner=%s artist_slug=%s slug=%s",
                owner_tg_user_id,
                artist_slug,
                slug,
            )
            return False


async def delete_smartlink_state(smartlink_id: int | str) -> None:
    """Delete auxiliary bot state for a smartlink (subscriptions/reminders/stored messages)."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "DELETE FROM smartlink_subscriptions WHERE smartlink_id=?",
                (smartlink_id,),
            )
        except Exception:
            pass
        try:
            await db.execute(
                "DELETE FROM smartlink_reminders WHERE smartlink_id=?",
                (smartlink_id,),
            )
        except Exception:
            pass
        try:
            await db.execute(
                "DELETE FROM smartlink_reminder_sends WHERE smartlink_id=?",
                (smartlink_id,),
            )
        except Exception:
            pass
        try:
            await db.execute(
                "DELETE FROM smartlink_reminder_log WHERE smartlink_id=?",
                (smartlink_id,),
            )
        except Exception:
            pass
        try:
            await db.execute(
                "DELETE FROM smartlink_messages WHERE smartlink_id=?",
                (smartlink_id,),
            )
        except Exception:
            pass
        await db.commit()
        try:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS smartlinks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_tg_user_id TEXT NOT NULL,
                    owner_tg_username TEXT,
                    owner_display_name TEXT,
                    artist_slug TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    artist TEXT,
                    artist_name TEXT,
                    title TEXT,
                    release_date TEXT,
                    cover_file_id TEXT,
                    cover_url TEXT,
                    cover_version INTEGER,
                    caption_text TEXT,
                    branding_disabled INTEGER,
                    branding_paid INTEGER,
                    pre_save_enabled INTEGER,
                    reminders_enabled INTEGER,
                    links_json TEXT,
                    cover_source_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    cover_updated_at TEXT,
                    UNIQUE(owner_tg_user_id, artist_slug, slug)
                )
                """
            )
            query = """
                SELECT * FROM smartlinks
                WHERE id=? AND owner_tg_user_id=?
                LIMIT 1
                """
            logger.info(
                "[smartlink-d1] fetch_by_id query=%s tg_id=%s id=%s",
                "SELECT * FROM smartlinks WHERE id=? AND owner_tg_user_id=? LIMIT 1",
                owner_tg_user_id,
                smartlink_id,
            )
            cur = await db.execute(query, (smartlink_id, str(owner_tg_user_id)))
            row = await cur.fetchone()
            smartlink = _normalize_d1_smartlink(row) if row else None
            logger.info(
                "[smartlink-d1] fetch_by_id result tg_id=%s id=%s found=%s",
                owner_tg_user_id,
                smartlink_id,
                bool(smartlink),
            )
            return smartlink
        except Exception:
            logger.exception(
                "[smartlink-d1] failed to fetch smartlink owner=%s id=%s",
                owner_tg_user_id,
                smartlink_id,
            )
            return None


async def list_recent_smartlinks(limit: int) -> list[dict] | None:
    async with _smartlink_d1_connection() as db:
        if not db:
            return None
        try:
            # Ensure schema exists (for bot.db fallback and fresh deployments).
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS smartlinks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_tg_user_id TEXT NOT NULL,
                    owner_tg_username TEXT,
                    owner_display_name TEXT,
                    artist_slug TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    artist TEXT,
                    artist_name TEXT,
                    title TEXT,
                    release_date TEXT,
                    cover_file_id TEXT,
                    cover_url TEXT,
                    cover_version INTEGER,
                    caption_text TEXT,
                    branding_disabled INTEGER,
                    branding_paid INTEGER,
                    pre_save_enabled INTEGER,
                    reminders_enabled INTEGER,
                    links_json TEXT,
                    cover_source_json TEXT,
                    metadata_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    cover_updated_at TEXT,
                    UNIQUE(owner_tg_user_id, artist_slug, slug)
                )
                """
            )
            cur = await db.execute("PRAGMA table_info(smartlinks)")
            rows = await cur.fetchall()
            columns = {row[1] for row in rows}
            if "updated_at" in columns and "created_at" in columns:
                order_expr = "COALESCE(updated_at, created_at) DESC"
            elif "updated_at" in columns:
                order_expr = "updated_at DESC"
            elif "created_at" in columns:
                order_expr = "created_at DESC"
            else:
                order_expr = "rowid DESC"
            query = f"SELECT * FROM smartlinks ORDER BY {order_expr} LIMIT ?"
            cur = await db.execute(query, (limit,))
            rows = await cur.fetchall()
            return [_normalize_d1_smartlink(row) for row in rows]
        except Exception:
            logger.exception("[smartlink-d1] failed to list recent smartlinks")
            return None


def _serialize_json(payload: dict | None) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False)


async def upsert_owned_smartlink_to_d1(smartlink: dict) -> bool:
    """Upsert a smartlink into local D1 (sqlite) store.

    This is a resilience layer: even if the remote Index/Web publish fails,
    the user should still be able to see and manage their smartlinks.
    """
    async with _smartlink_d1_connection() as db:
        if not db:
            return False

        owner_tg_user_id = str(smartlink.get("owner_tg_user_id") or "").strip()
        artist_slug = str(smartlink.get("artist_slug") or "").strip()
        slug = str(smartlink.get("slug") or "").strip()
        if not owner_tg_user_id or not artist_slug or not slug:
            logger.warning(
                "[smartlink-d1] upsert skipped (missing owner/slugs) owner=%s artist_slug=%s slug=%s",
                owner_tg_user_id,
                artist_slug,
                slug,
            )
            return False

        try:
            cur = await db.execute("PRAGMA table_info(smartlinks)")
            rows = await cur.fetchall()
            columns = {row[1] for row in rows}

            now_iso = dt.datetime.now(timezone.utc).isoformat()

            links_json = _serialize_json(
                smartlink.get("links") if isinstance(smartlink.get("links"), dict) else {}
            )
            cover_source_json = _serialize_json(
                smartlink.get("cover_source")
                if isinstance(smartlink.get("cover_source"), dict)
                else {}
            )
            metadata_json = _serialize_json(
                smartlink.get("metadata") if isinstance(smartlink.get("metadata"), dict) else {}
            )

            value_map: dict[str, object] = {
                "owner_tg_user_id": owner_tg_user_id,
                "owner_tg_username": smartlink.get("owner_tg_username") or None,
                "owner_display_name": smartlink.get("owner_display_name") or None,
                "artist_slug": artist_slug,
                "slug": slug,
                "artist": smartlink.get("artist") or None,
                "artist_name": smartlink.get("artist_name") or None,
                "title": smartlink.get("title") or None,
                "release_date": smartlink.get("release_date") or None,
                "cover_file_id": smartlink.get("cover_file_id") or None,
                "cover_url": smartlink.get("cover_url") or None,
                "cover_version": int(smartlink.get("cover_version") or 1),
                "caption_text": smartlink.get("caption_text") or None,
                "branding_disabled": 1 if smartlink.get("branding_disabled") else 0,
                "branding_paid": 1 if smartlink.get("branding_paid") else 0,
                "pre_save_enabled": 1 if smartlink.get("pre_save_enabled", True) else 0,
                "reminders_enabled": 1 if smartlink.get("reminders_enabled", True) else 0,
                "links_json": links_json,
                "cover_source_json": cover_source_json,
                "metadata_json": metadata_json,
                "created_at": smartlink.get("created_at") or now_iso,
                "updated_at": now_iso,
                "cover_updated_at": smartlink.get("cover_updated_at") or None,
            }

            update_cols = [c for c in value_map.keys() if c in columns and c != "created_at"]
            insert_cols = [c for c in value_map.keys() if c in columns]

            if not insert_cols:
                logger.warning(
                    "[smartlink-d1] upsert skipped (unsupported schema) columns=%s",
                    sorted(columns),
                )
                return False

            if update_cols:
                update_set = ", ".join(f"{c}=?" for c in update_cols)
                update_values = [value_map[c] for c in update_cols]
                update_query = (
                    f"UPDATE smartlinks SET {update_set} "
                    "WHERE owner_tg_user_id=? AND artist_slug=? AND slug=?"
                )
                cur = await db.execute(
                    update_query,
                    (*update_values, owner_tg_user_id, artist_slug, slug),
                )
                if cur.rowcount and cur.rowcount > 0:
                    await db.commit()
                    logger.info(
                        "[smartlink-d1] upsert updated owner=%s artist_slug=%s slug=%s",
                        owner_tg_user_id,
                        artist_slug,
                        slug,
                    )
                    return True

            insert_cols_sql = ", ".join(insert_cols)
            insert_placeholders = ", ".join("?" for _ in insert_cols)
            insert_values = [value_map[c] for c in insert_cols]
            insert_query = f"INSERT INTO smartlinks ({insert_cols_sql}) VALUES ({insert_placeholders})"
            await db.execute(insert_query, insert_values)
            await db.commit()
            logger.info(
                "[smartlink-d1] upsert inserted owner=%s artist_slug=%s slug=%s",
                owner_tg_user_id,
                artist_slug,
                slug,
            )
            return True
        except Exception:
            logger.exception(
                "[smartlink-d1] upsert failed owner=%s artist_slug=%s slug=%s",
                owner_tg_user_id,
                artist_slug,
                slug,
            )
            return False



async def enqueue_smartlink_publish_retry(
    artist_slug: str,
    slug: str,
    smartlink: dict,
    owner: dict | None,
    *,
    delay_seconds: int,
    last_error: str | None,
) -> None:
    now = dt.datetime.now(timezone.utc)
    next_attempt_at = (now + dt.timedelta(seconds=delay_seconds)).isoformat()
    created_at = now.isoformat()
    smartlink_json = _serialize_json(smartlink)
    owner_json = _serialize_json(owner)
    if not smartlink_json:
        logger.warning("[smartlink-publish] skip enqueue missing payload artist_slug=%s slug=%s", artist_slug, slug)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            INSERT INTO {SMARTLINK_PUBLISH_QUEUE_TABLE}
                (artist_slug, slug, smartlink_json, owner_json, attempt, next_attempt_at, last_error, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(artist_slug, slug) DO UPDATE SET
                smartlink_json=excluded.smartlink_json,
                owner_json=excluded.owner_json,
                attempt=0,
                next_attempt_at=excluded.next_attempt_at,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at
            """,
            (
                artist_slug,
                slug,
                smartlink_json,
                owner_json,
                next_attempt_at,
                last_error,
                created_at,
                created_at,
            ),
        )
        await db.commit()


async def list_due_smartlink_publish_jobs(
    now: dt.datetime,
    limit: int = 10,
) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""
            SELECT id, artist_slug, slug, smartlink_json, owner_json, attempt
            FROM {SMARTLINK_PUBLISH_QUEUE_TABLE}
            WHERE next_attempt_at <= ?
            ORDER BY next_attempt_at
            LIMIT ?
            """,
            (now.isoformat(), limit),
        )
        rows = await cur.fetchall()
        jobs: list[dict] = []
        for row in rows:
            smartlink = _parse_json_value(row["smartlink_json"])
            owner = _parse_json_value(row["owner_json"])
            jobs.append(
                {
                    "id": row["id"],
                    "artist_slug": row["artist_slug"],
                    "slug": row["slug"],
                    "smartlink": smartlink if isinstance(smartlink, dict) else {},
                    "owner": owner if isinstance(owner, dict) else None,
                    "attempt": int(row["attempt"] or 0),
                }
            )
        return jobs


async def update_smartlink_publish_job(
    job_id: int,
    attempt: int,
    next_attempt_at: dt.datetime,
    last_error: str | None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            UPDATE {SMARTLINK_PUBLISH_QUEUE_TABLE}
            SET attempt=?, next_attempt_at=?, last_error=?, updated_at=?
            WHERE id=?
            """,
            (attempt, next_attempt_at.isoformat(), last_error, dt.datetime.now(timezone.utc).isoformat(), job_id),
        )
        await db.commit()


async def delete_smartlink_publish_job(job_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"DELETE FROM {SMARTLINK_PUBLISH_QUEUE_TABLE} WHERE id=?",
            (job_id,),
        )
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
        await db.execute(
            "UPDATE users SET release_date=NULL, reminders_enabled=1 WHERE tg_id=?",
            (tg_id,)
        )
        await db.execute("DELETE FROM user_forms WHERE tg_id=?", (tg_id,))
        await db.commit()


async def set_smartlink_subscription(smartlink_id: int | str, subscriber_tg_id: int, subscribed: bool):
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


async def is_smartlink_subscribed(smartlink_id: int | str, subscriber_tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM smartlink_subscriptions WHERE smartlink_id=? AND subscriber_tg_id=?",
            (smartlink_id, subscriber_tg_id),
        )
        row = await cur.fetchone()
        return row is not None


async def get_smartlink_subscribers(smartlink_id: int | str) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT subscriber_tg_id FROM smartlink_subscriptions WHERE smartlink_id=?",
            (smartlink_id,),
        )
        return [row[0] for row in await cur.fetchall()]


async def mark_smartlink_notified(smartlink_id: int | str, subscriber_tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE smartlink_subscriptions SET notified=1 WHERE smartlink_id=? AND subscriber_tg_id=?",
            (smartlink_id, subscriber_tg_id),
        )
        await db.commit()


async def list_smartlink_subscription_ids() -> list[int | str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT DISTINCT smartlink_id FROM smartlink_subscriptions")
        rows = await cur.fetchall()
        return [row[0] for row in rows if row and row[0] is not None]


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


async def was_smartlink_day_sent(smartlink_id: int | str, subscriber_tg_id: int, offset_days: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM smartlink_reminder_log WHERE smartlink_id=? AND subscriber_tg_id=? AND offset_days=?",
            (smartlink_id, subscriber_tg_id, offset_days),
        )
        return await cur.fetchone() is not None


async def mark_smartlink_day_sent(smartlink_id: int | str, subscriber_tg_id: int, offset_days: int, sent_on: dt.date):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO smartlink_reminder_log (smartlink_id, subscriber_tg_id, offset_days, sent_on) VALUES (?, ?, ?, ?)",
            (smartlink_id, subscriber_tg_id, offset_days, sent_on.isoformat()),
        )
        await db.commit()


async def save_smartlink_message_reference(
    smartlink_id: int | str, user_id: int, chat_id: int, message_id: int
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
                dt.datetime.now(timezone.utc).isoformat(),
                dt.datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def get_smartlink_messages(smartlink_id: int | str) -> list[dict]:
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


async def add_smartlink_reminder(tg_id: int, smartlink_id: int | str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO smartlink_reminders (smartlink_id, tg_id, created_at) VALUES (?, ?, ?)",
            (smartlink_id, tg_id, dt.datetime.now(timezone.utc).isoformat()),
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


async def list_smartlink_reminders() -> list[tuple[int | str, int]]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT smartlink_id, tg_id FROM smartlink_reminders")
        rows = await cur.fetchall()
        return [(row[0], row[1]) for row in rows]


async def mark_smartlink_reminder_sent(tg_id: int, smartlink_id: int | str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO smartlink_reminder_sends (smartlink_id, tg_id, sent_at) VALUES (?, ?, ?)",
            (smartlink_id, tg_id, dt.datetime.now(timezone.utc).isoformat()),
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
