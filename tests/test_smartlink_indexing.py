import asyncio
import os
import unittest

from aiohttp import web

from helpers import build_smartlink_index_payload, push_smartlink_to_index


async def _start_test_server(handler):
    app = web.Application()
    app.router.add_post("/api/index/upsert", handler)
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

    async def test_push_without_cover(self):
        captured = {}

        async def handler(request):
            captured["payload"] = await request.json()
            return web.json_response({"ok": True})

        port, cleanup = await _start_test_server(handler)
        self.addAsyncCleanup(cleanup)
        os.environ["SMARTLINK_INDEX_BASE"] = f"http://localhost:{port}"

        smartlink = {
            "artist": "Test Artist",
            "title": "Test Song",
            "links": {"spotify": "https://example.com"},
        }

        ok, status, error = await push_smartlink_to_index(smartlink)
        self.assertTrue(ok)
        self.assertEqual(status, 200)
        self.assertIsNone(error)
        self.assertIn("payload", captured)
        self.assertNotIn("cover_source", captured["payload"])
        self.assertEqual(captured["payload"].get("artist_slug"), "test-artist")

    async def test_retry_on_server_error(self):
        attempts = []

        async def handler(request):
            attempts.append(1)
            if len(attempts) < 2:
                return web.Response(status=500, text="temporary")
            return web.json_response({"ok": True})

        port, cleanup = await _start_test_server(handler)
        self.addAsyncCleanup(cleanup)
        os.environ["SMARTLINK_INDEX_BASE"] = f"http://localhost:{port}"

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


if __name__ == "__main__":
    unittest.main()
