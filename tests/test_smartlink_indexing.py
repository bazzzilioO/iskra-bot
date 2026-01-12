import asyncio
import os
import unittest

from aiohttp import web

from helpers import build_smartlink_index_payload, push_smartlink_to_index


async def _start_test_server(
    handler,
    *,
    method: str = "POST",
    path: str = "/api/index/upsert",
    extra_routes: list[tuple[str, str, callable]] | None = None,
):
    app = web.Application()
    app.router.add_route(method, path, handler)
    if extra_routes:
        for route_method, route_path, route_handler in extra_routes:
            app.router.add_route(route_method, route_path, route_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    async def _cleanup():
        await runner.cleanup()

    return port, _cleanup


class SmartlinkIndexingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        os.environ.pop("SMARTLINK_INDEX_BASE", None)
        os.environ.pop("SMARTLINK_API_KEY", None)

    def test_build_payload_without_cover(self):
        payload = build_smartlink_index_payload(
            {
                "artist": "Test Artist",
                "title": "Test Song",
                "links": {"spotify": "https://example.com"},
            }
        )
        self.assertIsNotNone(payload)
        self.assertNotIn("cover_source", payload)
        self.assertNotIn("cover_url", payload)
        self.assertEqual(payload["artist_slug"], "test-artist")
        self.assertEqual(payload["slug"], "test-song")

    def test_build_payload_uses_sreda_cover_for_telegram(self):
        payload = build_smartlink_index_payload(
            {
                "artist": "Cover Artist",
                "title": "Cover Song",
                "links": {"spotify": "https://example.com"},
                "cover_source": {"type": "telegram", "file_id": "file_abc123"},
                "metadata": {"cover_url": "https://railway.example/cover.png"},
                "owner_tg_user_id": "555",
            }
        )

        self.assertEqual(
            payload.get("cover_source"), {"type": "telegram", "file_id": "file_abc123"}
        )
        self.assertEqual(
            payload.get("cover_url"), "https://go.sreda.pw/api/cover/cover-artist/cover-song"
        )
        self.assertEqual(payload.get("owner_tg_user_id"), "555")

    def test_build_payload_prefers_smartlink_owner_tg_user_id(self):
        payload = build_smartlink_index_payload(
            {
                "artist": "Owner Artist",
                "title": "Owner Song",
                "links": {"spotify": "https://example.com"},
                "owner_tg_user_id": "123",
            },
            owner={"tg_user_id": "999", "username": "test"},
        )

        self.assertEqual(payload.get("owner_tg_user_id"), "123")

    def test_cover_source_forced_to_telegram_when_file_id_exists(self):
        payload = build_smartlink_index_payload(
            {
                "artist": "Force Artist",
                "title": "Force Song",
                "links": {"spotify": "https://example.com"},
                "cover_source": {"type": "external", "file_id": "some_file_id"},
                "cover_file_id": "some_file_id",
            }
        )

        self.assertEqual(
            payload.get("cover_source"), {"type": "telegram", "file_id": "some_file_id"}
        )
        self.assertEqual(
            payload.get("cover_url"), "https://go.sreda.pw/api/cover/force-artist/force-song"
        )

    async def test_push_without_cover(self):
        captured = {}

        async def handler(request):
            captured["api_key"] = request.headers.get("X-API-Key")
            captured["auth"] = request.headers.get("Authorization")
            captured["payload"] = await request.json()
            return web.json_response({"ok": True})

        async def confirm_handler(request):
            return web.json_response({"ok": True})

        port, cleanup = await _start_test_server(
            handler,
            extra_routes=[
                ("GET", "/api/smartlinks/{artist_slug}/{slug}", confirm_handler),
            ],
        )
        self.addAsyncCleanup(cleanup)
        os.environ["SMARTLINK_INDEX_BASE"] = f"http://localhost:{port}"
        os.environ["SMARTLINK_API_KEY"] = "test-key"

        smartlink = {
            "artist": "Test Artist",
            "title": "Test Song",
            "links": {"spotify": "https://example.com"},
        }

        ok, status, error = await push_smartlink_to_index(smartlink)
        self.assertTrue(ok)
        self.assertEqual(status, 200)
        self.assertIsNone(error)
        self.assertEqual(captured.get("api_key"), "test-key")
        self.assertIsNone(captured.get("auth"))
        self.assertIn("payload", captured)
        self.assertNotIn("cover_source", captured["payload"])
        self.assertEqual(captured["payload"].get("artist_slug"), "test-artist")

    async def test_fetch_my_smartlinks_uses_owner_tg_user_id(self):
        captured = {}

        async def handler(request):
            captured["owner"] = request.query.get("owner_tg_user_id")
            captured["page"] = request.query.get("page")
            captured["limit"] = request.query.get("limit")
            return web.json_response(
                {
                    "items": [
                        {
                            "artist": "Owner Artist",
                            "title": "Owner Song",
                            "artist_slug": "owner-artist",
                            "slug": "owner-song",
                        }
                    ],
                    "total_count": 1,
                    "total_pages": 1,
                }
            )

        port, cleanup = await _start_test_server(
            handler,
            method="GET",
            path="/api/my",
        )
        self.addAsyncCleanup(cleanup)
        os.environ["SMARTLINK_INDEX_BASE"] = f"http://localhost:{port}"
        os.environ["SMARTLINK_API_KEY"] = "test-key"

        import importlib
        import bot as bot_module

        importlib.reload(bot_module)

        ok, items, total_count, total_pages = await bot_module.fetch_my_smartlinks_from_index(
            123, page=0, limit=5
        )
        self.assertTrue(ok)
        self.assertEqual(total_count, 1)
        self.assertEqual(total_pages, 1)
        self.assertEqual(captured.get("owner"), "123")
        self.assertEqual(captured.get("page"), "0")
        self.assertEqual(captured.get("limit"), "5")
        self.assertIsInstance(items, list)
        self.assertEqual(items[0].get("artist_slug"), "owner-artist")

    async def test_retry_on_server_error(self):
        attempts = []

        async def handler(request):
            attempts.append(1)
            if len(attempts) < 2:
                return web.Response(status=502, text="temporary")
            return web.json_response({"ok": True})

        async def confirm_handler(request):
            return web.json_response({"ok": True})

        port, cleanup = await _start_test_server(
            handler,
            extra_routes=[
                ("GET", "/api/smartlinks/{artist_slug}/{slug}", confirm_handler),
            ],
        )
        self.addAsyncCleanup(cleanup)
        os.environ["SMARTLINK_INDEX_BASE"] = f"http://localhost:{port}"
        os.environ["SMARTLINK_API_KEY"] = "test-key"

        smartlink = {
            "artist": "Retry Artist",
            "title": "Retry Song",
            "links": {"apple_music": "https://example.com"},
        }

        ok, status, error = await push_smartlink_to_index(smartlink)
        self.assertTrue(ok)
        self.assertEqual(status, 200)
        self.assertIsNone(error)
        self.assertGreaterEqual(len(attempts), 2)

    async def test_update_smartlink_sends_api_key(self):
        captured = {}

        async def handler(request):
            captured["api_key"] = request.headers.get("X-API-Key")
            captured["auth"] = request.headers.get("Authorization")
            captured["payload"] = await request.json()
            return web.json_response({"ok": True})

        port, cleanup = await _start_test_server(
            handler,
            method="PUT",
            path="/api/smartlinks/{artist_slug}/{slug}",
        )
        self.addAsyncCleanup(cleanup)
        os.environ["SMARTLINK_INDEX_BASE"] = f"http://localhost:{port}"
        os.environ["SMARTLINK_API_KEY"] = "test-key"

        import importlib
        import bot as bot_module

        importlib.reload(bot_module)

        smartlink = {
            "artist": "Test Artist",
            "title": "Test Song",
            "links": {"spotify": "https://example.com"},
            "owner_tg_user_id": "777",
        }

        ok, status, error = await bot_module.update_smartlink_in_index(
            "test-artist",
            "test-song",
            smartlink,
        )
        self.assertTrue(ok)
        self.assertEqual(status, 200)
        self.assertIsNone(error)
        self.assertEqual(captured.get("api_key"), "test-key")
        self.assertIsNone(captured.get("auth"))
        self.assertEqual(captured.get("payload", {}).get("owner_tg_user_id"), "777")


if __name__ == "__main__":
    unittest.main()
