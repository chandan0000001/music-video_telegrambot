"""
Application Configuration
Centralized configuration for the Telegram Music & Video Downloader Bot.
Loads credentials and overrides from .env if present.
"""
import os
import base64
from pathlib import Path

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

# Auto-generate cookies.txt from environment variable (for Cloud / PaaS deployments)
COOKIE_FILE: Path = BASE_DIR / "cookies.txt"
_cookies_b64 = os.getenv("YOUTUBE_COOKIES_BASE64")
_cookies_raw = os.getenv("YOUTUBE_COOKIES")

if not COOKIE_FILE.exists():
    if _cookies_b64:
        try:
            COOKIE_FILE.write_bytes(base64.b64decode(_cookies_b64.strip()))
        except Exception:
            pass
    elif _cookies_raw:
        COOKIE_FILE.write_text(_cookies_raw.strip(), encoding="utf-8")

# Telegram Bot Credentials
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

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
# Set to maximum possible (2 GB / 2000 MB).
# Videos exceeding Telegram Cloud Bot API limit (50 MB) are automatically compressed with FFmpeg.
MAX_UPLOAD_SIZE_MB: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "2000"))
MAX_UPLOAD_SIZE_BYTES: int = MAX_UPLOAD_SIZE_MB * 1024 * 1024
TELEGRAM_CLOUD_LIMIT_BYTES: int = int(49.5 * 1024 * 1024)  # 49.5 MB cloud threshold

# Maximum allowed duration for media processing (in seconds, e.g., 3600 = 60 minutes)
MAX_DURATION_SECONDS: int = int(os.getenv("MAX_DURATION_SECONDS", "3600"))

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
SUPPORTED_VIDEO_RESOLUTIONS: list[str] = ["144p", "240p", "360p", "480p", "720p", "1080p"]

# Subprocess Timeout (in seconds)
FFMPEG_TIMEOUT: int = 300
YTDLP_TIMEOUT: int = 300
