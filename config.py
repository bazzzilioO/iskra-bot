"""Конфигурация бота - переменные окружения и константы."""
import os
import logging
from dotenv import load_dotenv
from aiogram.utils.backoff import BackoffConfig
from helpers import normalize_base_url

load_dotenv()
logging.basicConfig(level=logging.INFO)

# Bot
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID = os.getenv("ADMIN_TG_ID")
APP_VERSION = os.getenv("APP_VERSION", "dev")
PORT = int(os.getenv("PORT", "8000"))

# Polling
POLLING_LOCK_FILE = os.getenv("POLLING_LOCK_FILE", "/tmp/iskra_bot_polling.lock")
POLLING_TIMEOUT = int(os.getenv("POLLING_TIMEOUT", "60"))
NETWORK_ERROR_LOG_THROTTLE = float(os.getenv("NETWORK_ERROR_LOG_THROTTLE", "30"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT_TOTAL", "90"))
POLLING_BACKOFF_CONFIG = BackoffConfig(
    min_delay=float(os.getenv("BACKOFF_MIN_DELAY", "1")),
    max_delay=float(os.getenv("BACKOFF_MAX_DELAY", "60")),
    factor=float(os.getenv("BACKOFF_FACTOR", "2")),
    jitter=float(os.getenv("BACKOFF_JITTER", "0.1")),
)

# Smartlink
SMARTLINK_API_KEY = (
    os.getenv("SMARTLINK_API_KEY")
    # Backward/alternate env var names (to avoid silent 401s when deploying)
    or os.getenv("GO_API_KEY")
    or os.getenv("GO_API_TOKEN")
    or os.getenv("SMARTLINK_INDEX_TOKEN")
    or os.getenv("SMARTLINK_TOKEN")
)
SMARTLINK_INDEX_BASE = normalize_base_url(
    os.getenv("SMARTLINK_INDEX_BASE") or os.getenv("GO_INDEX_BASE"),
    None,
)
SMARTLINK_INDEX_URL = f"{SMARTLINK_INDEX_BASE}/api/index/upsert" if SMARTLINK_INDEX_BASE else ""
SMARTLINK_WEB_BASE = normalize_base_url(
    os.getenv("SMARTLINK_WEB_BASE") or os.getenv("SMARTLINK_PUBLIC_BASE"),
    "https://go.sreda.pw",
)
SMARTLINK_PUBLISH_RETRY_DELAYS = [60, 300, 900, 3600]
SMARTLINK_UPDATE_DEBOUNCE_SECONDS = 1.5
SMARTLINK_PUBLISH_QUEUE_INTERVAL_SECONDS = 60

# Cover proxy
COVER_PROXY_BASE = normalize_base_url(
    os.getenv("COVER_PROXY_BASE")
    or os.getenv("PUBLIC_BASE_URL")
    or os.getenv("BOT_PUBLIC_BASE")
    or os.getenv("RAILWAY_PUBLIC_DOMAIN"),
    f"http://localhost:{PORT}",
)

# Rate limit
RATE_LIMIT_COOLDOWN_SECONDS = 60

# Updates
UPDATES_CHANNEL_URL = "https://t.me/sreda_music"
UPDATES_POST_URL = os.getenv("UPDATES_POST_URL", "")

# Email
LABEL_EMAIL = "sreda.records@gmail.com"
SMTP_USER = os.getenv("SMTP_USER")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD")
SMTP_TO = os.getenv("SMTP_TO") or LABEL_EMAIL

# Spotify
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_UPC_ENABLED = bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)

# Health state
HEALTH_STATE: dict[str, str | int | None] = {
    "status": "starting",
    "mode": "polling",
    "version": APP_VERSION,
    "bot_id": None,
    "username": None,
    "pid": os.getpid(),
}

# BandLink/SongLink
BANDLINK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
BANDLINK_REFRESH_PLATFORMS = {"spotify", "yandex", "apple", "vk", "zvuk", "youtube", "deezer", "youtubemusic"}
SONGLINK_API_URL = "https://api.song.link/v1-alpha.1/links"
SONGLINK_PLATFORM_ALIASES = {
    "spotify": "spotify",
    "applemusic": "apple",
    "applemusicapp": "apple",
    "apple": "apple",
    "itunes": "itunes",
    "youtubemusic": "youtubemusic",
    "youtube": "youtube",
    "deezer": "deezer",
    "yandex": "yandex",
    "yandexmusic": "yandex",
    "vk": "vk",
    "zvuk": "zvuk",
    "kion": "kion",
    "mts": "kion",
}
