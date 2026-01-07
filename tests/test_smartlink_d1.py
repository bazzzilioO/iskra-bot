import asyncio
import os
import tempfile
import unittest

import aiosqlite


class SmartlinkD1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        os.environ["SMARTLINK_D1_PATH"] = self.tmp.name

        async with aiosqlite.connect(self.tmp.name) as db:
            await db.execute(
                """
                CREATE TABLE smartlinks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_tg_user_id TEXT,
                    artist_slug TEXT,
                    slug TEXT,
                    title TEXT,
                    links_json TEXT,
                    created_at TEXT
                )
                """
            )
            await db.execute(
                """
                INSERT INTO smartlinks (owner_tg_user_id, artist_slug, slug, title, links_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "123",
                    "test-artist",
                    "test-song",
                    "Test Song",
                    '{"spotify": "https://example.com"}',
                    "2024-01-01T00:00:00",
                ),
            )
            await db.commit()

    async def asyncTearDown(self):
        os.environ.pop("SMARTLINK_D1_PATH", None)
        if self.tmp:
            os.unlink(self.tmp.name)

    async def test_list_owned_smartlinks(self):
        import importlib
        import db as db_module

        importlib.reload(db_module)

        count = await db_module.count_owned_smartlinks(123)
        items = await db_module.list_owned_smartlinks(123, limit=10, offset=0)

        self.assertEqual(count, 1)
        self.assertIsNotNone(items)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].get("artist_slug"), "test-artist")
        self.assertEqual(items[0].get("slug"), "test-song")
        self.assertEqual(items[0].get("title"), "Test Song")


if __name__ == "__main__":
    unittest.main()
