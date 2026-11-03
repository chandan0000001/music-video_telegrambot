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


def _write_cookie_file_secure(path: Path, content: bytes) -> None:
    """Write cookie file with secure mode 0600 (owner read/write only)."""
    path.write_bytes(content)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


COOKIE_FILE: Path = BASE_DIR / "cookies.txt"
_cookies_b64 = os.getenv("YOUTUBE_COOKIES_BASE64")
_cookies_raw = os.getenv("YOUTUBE_COOKIES")

if not COOKIE_FILE.exists():
    _detected = get_cookie_file()
    if _detected and _detected != COOKIE_FILE:
        try:
            import shutil
            shutil.copy2(_detected, COOKIE_FILE)
            os.chmod(COOKIE_FILE, 0o600)
        except Exception:
            pass
    elif _cookies_b64:
        try:
            _write_cookie_file_secure(COOKIE_FILE, base64.b64decode(_cookies_b64.strip()))
        except Exception:
            pass
    elif _cookies_raw:
        try:
            _write_cookie_file_secure(COOKIE_FILE, _cookies_raw.strip().encode("utf-8"))
        except Exception:
            pass
else:
    try:
        os.chmod(COOKIE_FILE, 0o600)
    except Exception:
        pass

# Instagram Cookies & Session ID (bypasses Instagram login walls and rate limits)
INSTAGRAM_SESSION_ID: Optional[str] = os.getenv("INSTAGRAM_SESSION_ID")
INSTAGRAM_COOKIES: Optional[str] = os.getenv("INSTAGRAM_COOKIES")


def get_instagram_cookie_file() -> Optional[Path]:
    """Search for dedicated instagram_cookies.txt or standard cookies.txt."""
    candidates = [
        BASE_DIR / "instagram_cookies.txt",
        BASE_DIR / "cookies.txt",
        Path.home() / "music_bot" / "instagram_cookies.txt",
        Path.home() / "music_bot" / "cookies.txt",
        Path.home() / "cookies.txt",
        Path("/home/ubuntu/music_bot/cookies.txt"),
    ]
    for c in candidates:
        try:
            if c.exists() and c.is_file() and c.stat().st_size > 20:
                return c
        except Exception:
            pass
    return None


INSTAGRAM_COOKIE_FILE: Optional[Path] = get_instagram_cookie_file()
if not INSTAGRAM_COOKIE_FILE and INSTAGRAM_SESSION_ID:
    _ig_file = BASE_DIR / "instagram_cookies.txt"
    try:
        _content = (
            "# Netscape HTTP Cookie File\n"
            f".instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\t{INSTAGRAM_SESSION_ID.strip()}\n"
        )
        _write_cookie_file_secure(_ig_file, _content.encode("utf-8"))
        INSTAGRAM_COOKIE_FILE = _ig_file
    except Exception:
        pass
elif INSTAGRAM_COOKIE_FILE and INSTAGRAM_COOKIE_FILE.exists():
    try:
        os.chmod(INSTAGRAM_COOKIE_FILE, 0o600)
    except Exception:
        pass

# Telegram Bot Credentials
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_API_SERVER: Optional[str] = os.getenv("TELEGRAM_API_SERVER")

# Telegram MTProto Credentials (enables 2000 MB native uploads without 50MB HTTP limit)
_raw_api_id = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_ID: Optional[int] = int(_raw_api_id.strip()) if (_raw_api_id and _raw_api_id.strip().isdigit()) else None
TELEGRAM_API_HASH: Optional[str] = os.getenv("TELEGRAM_API_HASH").strip() if os.getenv("TELEGRAM_API_HASH") else None

# Administrator Telegram IDs (Numeric User IDs, comma-separated in env)
# CRIT-3: Never hardcode default administrator user IDs in source code
_admin_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: set[int] = {
    int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()
}

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
def _safe_int(env_val: Optional[str], default: int) -> int:
    """Safely parse integer from environment variable, falling back gracefully on format error."""
    try:
        return int(env_val.strip()) if env_val else default
    except (ValueError, AttributeError):
        return default


RATE_LIMIT_REQUESTS: int = _safe_int(os.getenv("RATE_LIMIT_REQUESTS"), 5)
RATE_LIMIT_WINDOW: int = _safe_int(os.getenv("RATE_LIMIT_WINDOW"), 10)

# Audio Encoding Settings (Configurable via .env, comma-separated)
SUPPORTED_AUDIO_FORMATS: list[str] = [
    f.strip().upper() for f in os.getenv("SUPPORTED_AUDIO_FORMATS", "MP3,M4A,WAV").split(",") if f.strip()
]
SUPPORTED_AUDIO_QUALITIES: list[str] = [
    q.strip() for q in os.getenv("SUPPORTED_AUDIO_QUALITIES", "128,192,320").split(",") if q.strip()
]

# Video Encoding Settings (Configurable via .env, comma-separated)
SUPPORTED_VIDEO_FORMATS: list[str] = [
    f.strip().upper() for f in os.getenv("SUPPORTED_VIDEO_FORMATS", "MP4").split(",") if f.strip()
]
SUPPORTED_VIDEO_RESOLUTIONS: list[str] = [
    r.strip() for r in os.getenv("SUPPORTED_VIDEO_RESOLUTIONS", "144p,240p,360p,480p,720p,1080p,1440p").split(",") if r.strip()
]

# Subprocess Timeout (in seconds, e.g. 1800 = 30 minutes for long videos)
FFMPEG_TIMEOUT: int = _safe_int(os.getenv("FFMPEG_TIMEOUT"), 1800)
YTDLP_TIMEOUT: int = _safe_int(os.getenv("YTDLP_TIMEOUT"), 1800)

# Downloader Acceleration (Disabled by default: YouTube throttles aria2c to 300KB/s; native yt-dlp reaches 10+ MB/s)
USE_ARIA2: bool = os.getenv("USE_ARIA2", "false").lower() in ("true", "1", "yes")

# Payment & Subscription Configuration (INR)
PAYMENT_UPI_ID: str = os.getenv("PAYMENT_UPI_ID", "")
PAYMENT_MERCHANT_NAME: str = os.getenv("PAYMENT_MERCHANT_NAME", "Music & Video Bot")
GMAIL_USER: str = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")

PRICE_SINGLE_720P: int = _safe_int(os.getenv("PRICE_SINGLE_720P"), 19)
PRICE_SINGLE_1080P: int = _safe_int(os.getenv("PRICE_SINGLE_1080P"), 27)

# Subscription Plans (Normal, Go, Pro, Ultra, Max)
SUBSCRIPTION_PLANS = {
    "normal": {
        "name": "Normal",
        "badge": "🟢 Normal (Free)",
        "price": 0,
        "days": 0,
        "daily_limit": _safe_int(os.getenv("LIMIT_TIER_NORMAL_DAILY"), 0),
        "description": os.getenv("DESC_TIER_NORMAL", "Standard 144p-480p videos & unlimited music")
    },
    "go": {
        "name": "Go",
        "badge": "⚡ Go Pass",
        "price": _safe_int(os.getenv("PRICE_TIER_GO"), 35),
        "days": _safe_int(os.getenv("DAYS_TIER_GO"), 7),
        "daily_limit": _safe_int(os.getenv("LIMIT_TIER_GO_DAILY"), 20),
        "description": os.getenv("DESC_TIER_GO", "20 HD Videos/day (720p/1080p) + High Priority")
    },
    "pro": {
        "name": "Pro",
        "badge": "🚀 Pro Pass",
        "price": _safe_int(os.getenv("PRICE_TIER_PRO"), 100),
        "days": _safe_int(os.getenv("DAYS_TIER_PRO"), 30),
        "daily_limit": _safe_int(os.getenv("LIMIT_TIER_PRO_DAILY"), 45),
        "description": os.getenv("DESC_TIER_PRO", "45 HD Videos/day (1080p 60fps) + 8-stream Turbo")
    },
    "ultra": {
        "name": "Ultra",
        "badge": "⚡ Ultra Pass",
        "price": _safe_int(os.getenv("PRICE_TIER_ULTRA"), 299),
        "days": _safe_int(os.getenv("DAYS_TIER_ULTRA"), 90),
        "daily_limit": _safe_int(os.getenv("LIMIT_TIER_ULTRA_DAILY"), 80),
        "description": os.getenv("DESC_TIER_ULTRA", "80 HD/2K Videos/day + VIP Queue Priority")
    },
    "max": {
        "name": "MAX VIP",
        "badge": "👑 MAX VIP",
        "price": _safe_int(os.getenv("PRICE_TIER_MAX"), 799),
        "days": _safe_int(os.getenv("DAYS_TIER_MAX"), 365),
        "daily_limit": _safe_int(os.getenv("LIMIT_TIER_MAX_DAILY"), 150),
        "description": os.getenv("DESC_TIER_MAX", "150 HD/4K Videos/day + 2GB Bandwidth + VIP Badge")
    }
}

ORDER_EXPIRY_MINUTES: int = _safe_int(os.getenv("ORDER_EXPIRY_MINUTES"), 10)


