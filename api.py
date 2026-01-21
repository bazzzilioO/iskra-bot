"""HTTP API handlers для health check и smartlink API endpoints."""
import logging
import aiohttp
from aiohttp import web
from aiogram import Bot

from config import (
    HEALTH_STATE,
    PORT,
    SMARTLINK_API_KEY,
    TOKEN,
)
from smartlink import normalize_index_smartlink

logger = logging.getLogger(__name__)


async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response(HEALTH_STATE)


async def smartlink_api_handler(request: web.Request) -> web.Response:
    """API endpoint для получения smartlink по ID."""
    if SMARTLINK_API_KEY:
        api_key = request.headers.get("X-API-Key")
        if api_key != SMARTLINK_API_KEY:
            return web.json_response({"error": "unauthorized"}, status=401)

    # Импортируем здесь чтобы избежать циклических зависимостей
    from bot import fetch_smartlink_by_id
    
    smartlink_id = request.match_info.get("id", "")
    smartlink = await fetch_smartlink_by_id(smartlink_id)
    if not smartlink:
        return web.json_response({"error": "not_found"}, status=404)

    response = {
        "id": smartlink.get("id"),
        "artist": smartlink.get("artist"),
        "title": smartlink.get("title"),
        "release_date": smartlink.get("release_date"),
        "cover_file_id": smartlink.get("cover_file_id"),
        "cover_url": smartlink.get("cover_url"),
        "links": smartlink.get("links"),
        "caption_text": smartlink.get("caption_text"),
    }
    return web.json_response(response)


async def smartlink_latest_api_handler(request: web.Request) -> web.Response:
    """API endpoint для получения последнего smartlink пользователя."""
    if SMARTLINK_API_KEY:
        api_key = request.headers.get("X-API-Key")
        if api_key != SMARTLINK_API_KEY:
            return web.json_response({"error": "unauthorized"}, status=401)
    owner_raw = request.query.get("owner_tg_user_id")
    if not owner_raw or not owner_raw.isdigit():
        return web.json_response({"error": "owner_required"}, status=400)
    
    # Импортируем здесь чтобы избежать циклических зависимостей
    from bot import fetch_latest_smartlink_from_index
    
    smartlink = await fetch_latest_smartlink_from_index(int(owner_raw))
    if not smartlink:
        return web.json_response({"error": "not_found"}, status=404)

    response = {
        "id": smartlink.get("id"),
        "artist": smartlink.get("artist"),
        "title": smartlink.get("title"),
        "release_date": smartlink.get("release_date"),
        "cover_file_id": smartlink.get("cover_file_id"),
        "cover_url": smartlink.get("cover_url"),
        "links": smartlink.get("links"),
        "caption_text": smartlink.get("caption_text"),
    }
    return web.json_response(response)


async def cover_proxy_handler(request: web.Request) -> web.StreamResponse | web.Response:
    """Proxy для обложек smartlink через Telegram API."""
    bot: Bot | None = request.app.get("bot")
    if bot is None:
        return web.json_response({"error": "bot_unavailable"}, status=503)

    artist_slug = request.match_info.get("artist_slug")
    slug = request.match_info.get("slug")
    smartlink_id = request.match_info.get("smartlink_id")

    # Импортируем здесь чтобы избежать циклических зависимостей
    from bot import fetch_smartlink_by_id, fetch_smartlink_from_index, get_http_session

    smartlink: dict | None = None
    if smartlink_id:
        smartlink = await fetch_smartlink_by_id(smartlink_id)

    if not smartlink and artist_slug and slug:
        ok, item, _status = await fetch_smartlink_from_index(artist_slug, slug)
        if ok and isinstance(item, dict):
            smartlink = normalize_index_smartlink(item, artist_slug=artist_slug, slug=slug)
        else:
            smartlink = None

    if not smartlink:
        return web.Response(status=404, text="not found")

    cover_file_id = (smartlink.get("cover_file_id") or "").strip()
    if not cover_file_id:
        source = smartlink.get("cover_source") or {}
        cover_file_id = str(source.get("file_id") or "").strip()
    if not cover_file_id:
        return web.Response(status=404, text="cover not found")

    try:
        file = await bot.get_file(cover_file_id)
    except Exception as e:
        logger.exception("[cover-proxy] failed to get file file_id=%s", cover_file_id)
        return web.Response(status=502, text="file lookup failed")

    file_path = getattr(file, "file_path", None)
    if not file_path:
        return web.Response(status=502, text="file path missing")

    download_url = f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"
    session = await get_http_session()
    try:
        async with session.get(download_url) as resp:
            if resp.status != 200:
                return web.Response(status=resp.status, text="download failed")
            headers = {
                "Cache-Control": "public, max-age=0, must-revalidate",
            }
            content_type = resp.headers.get("Content-Type") or "application/octet-stream"
            stream = web.StreamResponse(status=200, headers={"Content-Type": content_type, **headers})
            await stream.prepare(request)
            async for chunk in resp.content.iter_chunked(64 * 1024):
                await stream.write(chunk)
            await stream.write_eof()
            return stream
    except Exception:
        logger.exception("[cover-proxy] failed to stream cover smartlink_id=%s", smartlink.get("id"))
        return web.Response(status=502, text="stream failed")


async def start_health_server(bot: Bot | None = None) -> web.AppRunner:
    """Запустить HTTP сервер с health check и API endpoints."""
    app = web.Application()
    app.add_routes(
        [
            web.get("/health", health_handler),
            web.get("/api/smartlink/{id}", smartlink_api_handler),
            web.get("/api/smartlink/latest", smartlink_latest_api_handler),
            web.get("/api/cover/{artist_slug}/{slug}", cover_proxy_handler),
            web.get(r"/api/cover/{smartlink_id:\\d+}", cover_proxy_handler),
        ]
    )
    if bot:
        app["bot"] = bot
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Health endpoint available on port %s (GET /health)", PORT)
    return runner
