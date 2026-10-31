"""
Progress Tracker and Visual Loading Screen Generator
Generates beautiful Telegram progress bars with live speed, ETA, and size metrics.
"""
import time
import asyncio
import logging
from typing import Optional
from aiogram.types import Message
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

logger = logging.getLogger(__name__)


def make_progress_bar(percentage: float, length: int = 10) -> str:
    """Create a Unicode progress bar: [▰▰▰▰▱▱▱▱▱▱]."""
    percentage = max(0.0, min(100.0, percentage))
    filled = int(round(length * (percentage / 100.0)))
    filled = max(0, min(length, filled))
    empty = length - filled
    return "▰" * filled + "▱" * empty


def format_bytes(num_bytes: Optional[float | int]) -> str:
    """Format bytes into readable KiB, MiB, or GiB."""
    if not num_bytes or num_bytes <= 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def format_seconds(seconds: Optional[float | int]) -> str:
    """Format seconds into readable MM:SS."""
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(seconds)
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    m = seconds // 60
    s = seconds % 60
    return f"{m:02d}:{s:02d}"


class DownloadProgressTracker:
    """
    Thread-safe progress data holder updated synchronously by yt-dlp,
    rendered asynchronously to Telegram with rate-limiting.
    """

    def __init__(self, media_title: str, quality_label: str, media_type: str = "video"):
        self.media_title = media_title or "Media"
        self.quality_label = quality_label
        self.media_type = media_type

        # State updated by yt-dlp hooks
        self.status: str = "starting"
        self.downloaded_bytes: int = 0
        self.total_bytes: int = 0
        self.speed: float = 0.0
        self.eta: Optional[int] = None
        self.percentage: float = 0.0

        # Rendering & throttling control
        self.last_update_time: float = 0.0
        self.last_rendered_text: str = ""
        self.min_update_interval: float = 2.5  # seconds between Telegram message edits

    def ytdlp_hook(self, d: dict) -> None:
        """Synchronous callback executed inside yt-dlp worker thread."""
        status = d.get("status")
        if status == "downloading":
            self.status = "downloading"
            self.downloaded_bytes = d.get("downloaded_bytes", 0)
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total and total > 0:
                self.total_bytes = total
                self.percentage = min(100.0, (self.downloaded_bytes / total) * 100.0)
            self.speed = d.get("speed") or 0.0
            self.eta = d.get("eta")
        elif status == "finished":
            self.status = "processing"
            self.percentage = 100.0

    def render_download_card(self) -> str:
        """Generate GitHub/Telegram-style Markdown status card."""
        icon = "🎬" if self.media_type == "video" else "🎵"
        label = f"{self.media_type.capitalize()} ({self.quality_label})"

        bar = make_progress_bar(self.percentage)
        speed_str = f"{format_bytes(self.speed)}/s" if self.speed > 0 else "Calculating..."
        downloaded_str = format_bytes(self.downloaded_bytes)
        total_str = format_bytes(self.total_bytes) if self.total_bytes > 0 else "Unknown"
        eta_str = format_seconds(self.eta) if self.eta is not None else "--:--"

        title_display = self.media_title[:42] + "..." if len(self.media_title) > 42 else self.media_title

        text = (
            f"⬇️ *Downloading {label}*\n"
            f"{icon} *Title:* `{title_display}`\n\n"
            f"📊 *Progress:* `{bar}` *{self.percentage:.1f}%*\n"
            f"⚡ *Speed:* `{speed_str}`\n"
            f"💾 *Size:* `{downloaded_str} / {total_str}`\n"
            f"⏳ *ETA:* `{eta_str}`"
        )
        return text


class AsyncProgressReporter:
    """Manages the background loop updating Telegram status message smoothly."""

    def __init__(self, message: Message, tracker: DownloadProgressTracker):
        self.message = message
        self.tracker = tracker
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = time.time()
                if (now - self.tracker.last_update_time) >= self.tracker.min_update_interval:
                    if self.tracker.status == "downloading" and self.tracker.downloaded_bytes > 0:
                        text = self.tracker.render_download_card()
                        if text != self.tracker.last_rendered_text:
                            await self.message.edit_text(text, parse_mode="Markdown")
                            self.tracker.last_rendered_text = text
                            self.tracker.last_update_time = now
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramAPIError:
                pass
            except Exception as e:
                logger.debug(f"Progress update suppressed: {e}")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=0.8)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            try:
                await self._task
            except Exception:
                pass
