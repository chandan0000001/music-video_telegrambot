"""
Application Configuration
Centralized configuration for the Telegram Music & Video Downloader Bot.
"""
from pathlib import Path

# Telegram Bot Credentials
# Replace with your actual bot token obtained from @BotFather
BOT_TOKEN: str = "YOUR_BOT_TOKEN_HERE"

# Administrator Telegram IDs
ADMIN_IDS: set[int] = {
    2063913663
}

# Base Paths
BASE_DIR: Path = Path(__file__).resolve().parent
DATABASE_PATH: Path = BASE_DIR / "bot_database.sqlite3"
TEMP_DIR: Path = BASE_DIR / "temp"
LOGS_DIR: Path = BASE_DIR / "logs"

# Ensure runtime directories exist
TEMP_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Media Processing & Limits
# Set to maximum possible (2 GB / 2000 MB).
# Videos exceeding Telegram Cloud Bot API limit (50 MB) are automatically compressed with FFmpeg.
MAX_UPLOAD_SIZE_MB: int = 2000
MAX_UPLOAD_SIZE_BYTES: int = MAX_UPLOAD_SIZE_MB * 1024 * 1024
TELEGRAM_CLOUD_LIMIT_BYTES: int = int(49.5 * 1024 * 1024)  # 49.5 MB cloud threshold

# Maximum allowed duration for media processing (in seconds, e.g., 3600 = 60 minutes)
MAX_DURATION_SECONDS: int = 3600

# Concurrency & Worker Limits
MAX_CONCURRENT_DOWNLOADS: int = 3

# Rate Limiting (Per-user)
RATE_LIMIT_REQUESTS: int = 5   # Max requests allowed
RATE_LIMIT_WINDOW: int = 10    # Window in seconds

# Audio Encoding Settings
SUPPORTED_AUDIO_FORMATS: list[str] = ["MP3", "M4A", "WAV"]
SUPPORTED_AUDIO_QUALITIES: list[str] = ["128", "192", "320"]  # in kbps

# Video Encoding Settings
SUPPORTED_VIDEO_FORMATS: list[str] = ["MP4"]
SUPPORTED_VIDEO_RESOLUTIONS: list[str] = ["144p", "240p", "360p", "480p", "720p", "1080p"]

# Subprocess Timeout (in seconds)
FFMPEG_TIMEOUT: int = 300
YTDLP_TIMEOUT: int = 300
