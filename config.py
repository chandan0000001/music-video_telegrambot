"""
Application Configuration
Centralized configuration for the Telegram Music & Video Downloader Bot.
Loads credentials and overrides from .env if present.
"""
import os
import base64
from pathlib import Path
from typing import Optional

# Base Paths
BASE_DIR: Path = Path(__file__).resolve().parent

# Auto-load local environment file (.env) if present (ignored in git)
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip("'\""))

# Auto-generate or locate cookies.txt across common server paths
def get_cookie_file() -> Optional[Path]:
    """Search for cookies.txt across common deployment paths."""
    candidates = [
        BASE_DIR / "cookies.txt",
        Path.home() / "music_bot" / "cookies.txt",
        Path.home() / "cookies.txt",
        BASE_DIR.parent / "cookies.txt",
        Path("/home/ubuntu/music_bot/cookies.txt"),
        Path("/home/ubuntu/cookies.txt"),
    ]
    for c in candidates:
        try:
            if c.exists() and c.is_file() and c.stat().st_size > 50:
                return c
        except Exception:
            pass
    return None


COOKIE_FILE: Path = BASE_DIR / "cookies.txt"
_cookies_b64 = os.getenv("YOUTUBE_COOKIES_BASE64")
_cookies_raw = os.getenv("YOUTUBE_COOKIES")

if not COOKIE_FILE.exists():
    _detected = get_cookie_file()
    if _detected and _detected != COOKIE_FILE:
        try:
            import shutil
            shutil.copy2(_detected, COOKIE_FILE)
        except Exception:
            pass
    elif _cookies_b64:
        try:
            COOKIE_FILE.write_bytes(base64.b64decode(_cookies_b64.strip()))
        except Exception:
            pass
    elif _cookies_raw:
        COOKIE_FILE.write_text(_cookies_raw.strip(), encoding="utf-8")

# Telegram Bot Credentials
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_API_SERVER: Optional[str] = os.getenv("TELEGRAM_API_SERVER")

# Telegram MTProto Credentials (enables 2000 MB native uploads without 50MB HTTP limit)
_raw_api_id = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_ID: Optional[int] = int(_raw_api_id.strip()) if (_raw_api_id and _raw_api_id.strip().isdigit()) else None
TELEGRAM_API_HASH: Optional[str] = os.getenv("TELEGRAM_API_HASH").strip() if os.getenv("TELEGRAM_API_HASH") else None

# Administrator Telegram IDs (Numeric User IDs, comma-separated in env)
_admin_raw = os.getenv("ADMIN_IDS", "2063913663")
ADMIN_IDS: set[int] = {
    int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()
} or {2063913663}

# Base Paths
DATABASE_PATH: Path = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "bot_database.sqlite3")))
TEMP_DIR: Path = Path(os.getenv("TEMP_DIR", str(BASE_DIR / "temp")))
LOGS_DIR: Path = Path(os.getenv("LOGS_DIR", str(BASE_DIR / "logs")))

# Ensure runtime directories exist
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Media Processing & Limits
# Configured via MAX_UPLOAD_SIZE_MB from environment (.env). Default: 2000 MB (2 GB).
MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "2000"))
MAX_UPLOAD_SIZE_BYTES: int = MAX_UPLOAD_SIZE_MB * 1024 * 1024

# Optional: Proxy for yt-dlp on Datacenter/AWS IPs (e.g., socks5://127.0.0.1:40000 via Cloudflare WARP)
YTDLP_PROXY: Optional[str] = os.getenv("YTDLP_PROXY")

# Maximum allowed duration for media processing (in seconds, e.g., 14400 = 4 hours, 0 = unlimited)
MAX_DURATION_SECONDS: int = int(os.getenv("MAX_DURATION_SECONDS", "14400"))

# Concurrency & Worker Limits
MAX_CONCURRENT_DOWNLOADS: int = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))

# Rate Limiting (Per-user)
RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "5"))
RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "10"))

# Audio Encoding Settings
SUPPORTED_AUDIO_FORMATS: list[str] = ["MP3", "M4A", "WAV"]
SUPPORTED_AUDIO_QUALITIES: list[str] = ["128", "192", "320"]  # in kbps

# Video Encoding Settings
SUPPORTED_VIDEO_FORMATS: list[str] = ["MP4"]
SUPPORTED_VIDEO_RESOLUTIONS: list[str] = ["144p", "240p", "360p", "480p", "720p", "1080p", "1440p"]

# Subprocess Timeout (in seconds, e.g. 1800 = 30 minutes for long videos)
FFMPEG_TIMEOUT: int = int(os.getenv("FFMPEG_TIMEOUT", "1800"))
YTDLP_TIMEOUT: int = int(os.getenv("YTDLP_TIMEOUT", "1800"))

# Downloader Acceleration (Disabled by default: YouTube throttles aria2c to 300KB/s; native yt-dlp reaches 10+ MB/s)
USE_ARIA2: bool = os.getenv("USE_ARIA2", "false").lower() in ("true", "1", "yes")

# Payment & Subscription Configuration (INR)
PAYMENT_UPI_ID: str = os.getenv("PAYMENT_UPI_ID", "srikantap571-1@okaxis")
PAYMENT_MERCHANT_NAME: str = os.getenv("PAYMENT_MERCHANT_NAME", "Telegram_mv_bot")
GMAIL_USER: str = os.getenv("GMAIL_USER", "srikantap571@gmail.com")
GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")

PRICE_SINGLE_720P: int = int(os.getenv("PRICE_SINGLE_720P", "19"))
PRICE_SINGLE_1080P: int = int(os.getenv("PRICE_SINGLE_1080P", "27"))

# Subscription Plans (Normal, Go, Pro, Ultra, Max)
SUBSCRIPTION_PLANS = {
    "normal": {
        "name": "Normal",
        "badge": "🟢 Normal (Free)",
        "price": 0,
        "days": 0,
        "daily_limit": 0,
        "description": "Standard 144p-480p videos & unlimited music"
    },
    "go": {
        "name": "Go",
        "badge": "⚡ Go Pass",
        "price": int(os.getenv("PRICE_TIER_GO", "35")),
        "days": int(os.getenv("DAYS_TIER_GO", "7")),
        "daily_limit": int(os.getenv("LIMIT_TIER_GO_DAILY", "20")),
        "description": "20 HD Videos/day (720p/1080p) + High Priority"
    },
    "pro": {
        "name": "Pro",
        "badge": "🚀 Pro Pass",
        "price": int(os.getenv("PRICE_TIER_PRO", "100")),
        "days": int(os.getenv("DAYS_TIER_PRO", "30")),
        "daily_limit": int(os.getenv("LIMIT_TIER_PRO_DAILY", "45")),
        "description": "45 HD Videos/day (1080p 60fps) + 8-stream Turbo"
    },
    "ultra": {
        "name": "Ultra",
        "badge": "⚡ Ultra Pass",
        "price": int(os.getenv("PRICE_TIER_ULTRA", "299")),
        "days": int(os.getenv("DAYS_TIER_ULTRA", "90")),
        "daily_limit": int(os.getenv("LIMIT_TIER_ULTRA_DAILY", "80")),
        "description": "80 HD/2K Videos/day + VIP Queue Priority"
    },
    "max": {
        "name": "Max",
        "badge": "👑 Max VIP Pass",
        "price": int(os.getenv("PRICE_TIER_MAX", "799")),
        "days": int(os.getenv("DAYS_TIER_MAX", "365")),
        "daily_limit": int(os.getenv("LIMIT_TIER_MAX_DAILY", "150")),
        "description": "150 HD/4K Videos/day + 2GB Bandwidth + VIP Badge"
    }
}

ORDER_EXPIRY_MINUTES: int = int(os.getenv("ORDER_EXPIRY_MINUTES", "10"))


