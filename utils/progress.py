"""
Progress Tracker and Visual Loading Screen Generator
Generates beautiful Telegram progress bars with live speed, ETA, and size metrics.
"""
import time
import html
import asyncio
import logging
from pathlib import Path
from typing import Optional, AsyncGenerator
import aiofiles
from aiogram import Bot
from aiogram.types import FSInputFile, Message, InlineKeyboardMarkup
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter, TelegramBadRequest

logger = logging.getLogger(__name__)


async def vanish_status_message(message: Optional[Message], delay: float = 0.2) -> bool:
    """
    Safely vanish (delete) a status or progress message from Telegram.
    Handles rate-limiting (TelegramRetryAfter) and suppresses errors if
    the message was already deleted or cannot be found.

    Args:
        message: The aiogram Message object to delete.
        delay: Optional seconds to wait before deletion (e.g. 0.2s) to allow
               Telegram clients to render media arrival smoothly.

    Returns:
        bool: True if deleted or already gone, False on unexpected error.
    """
    if not message:
        return True

    if delay > 0:
        await asyncio.sleep(delay)

    try:
        await message.delete()
        return True
    except TelegramRetryAfter as e:
        try:
            await asyncio.sleep(e.retry_after + 0.1)
            await message.delete()
            return True
        except Exception:
            return False
    except TelegramBadRequest as e:
        err_msg = str(e).lower()
        if "message to delete not found" in err_msg or "message can't be deleted" in err_msg:
            return True
        logger.debug(f"vanish_status_message bad request: {e}")
        return False
    except TelegramAPIError as e:
        logger.debug(f"vanish_status_message TelegramAPIError: {e}")
        return False
    except Exception as e:
        logger.debug(f"vanish_status_message unexpected error: {e}")
        return False


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
        self.min_update_interval: float = 1.2  # responsive live Telegram message edits
        self._is_cancelled: bool = False

    def cancel(self) -> None:
        """Flag the tracker as cancelled to stop yt-dlp on next progress tick."""
        self._is_cancelled = True

    def ytdlp_hook(self, d: dict) -> None:
        """Synchronous callback executed inside yt-dlp worker thread."""
        if self._is_cancelled:
            from yt_dlp.utils import DownloadCancelled
            raise DownloadCancelled("Download aborted by user.")

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
        """Generate GitHub/Telegram-style HTML status card with 0-100% meter."""
        if self.media_type == "video":
            icon = "🎬"
        elif self.media_type == "photo":
            icon = "📸"
        elif self.media_type == "gif":
            icon = "🌀"
        else:
            icon = "🎵"

        label = html.escape(f"{self.media_type.capitalize()} ({self.quality_label})")
        bar = make_progress_bar(self.percentage)
        speed_str = f"{format_bytes(self.speed)}/s" if self.speed > 0 else "Calculating..."
        downloaded_str = format_bytes(self.downloaded_bytes)
        total_str = format_bytes(self.total_bytes) if self.total_bytes > 0 else "Unknown"
        eta_str = format_seconds(self.eta) if self.eta is not None else "--:--"

        title_display = self.media_title[:40] + "..." if len(self.media_title) > 40 else self.media_title
        title_clean = html.escape(title_display)

        text = (
            f"⬇️ <b>Downloading {label}</b>\n"
            f"{icon} <b>Title:</b> <code>{title_clean}</code>\n\n"
            f"📊 <b>Progress:</b> <code>[{bar}]</code> <b>{self.percentage:.1f}%</b>\n"
            f"⚡ <b>Speed:</b> <code>{speed_str}</code>\n"
            f"💾 <b>Size:</b> <code>{downloaded_str} / {total_str}</code>\n"
            f"⏳ <b>ETA:</b> <code>{eta_str}</code>"
        )
        return text


class AsyncProgressReporter:
    """Manages the background loop updating Telegram status message smoothly."""

    def __init__(
        self,
        message: Message,
        tracker: DownloadProgressTracker,
        reply_markup: Optional[InlineKeyboardMarkup] = None
    ):
        self.message = message
        self.tracker = tracker
        self.reply_markup = reply_markup
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._is_stopped = False

    async def _run(self) -> None:
        # Initial render immediately so user sees 0% meter without delay
        try:
            text = self.tracker.render_download_card()
            await self.message.edit_text(text, parse_mode="HTML", reply_markup=self.reply_markup)
            self.tracker.last_rendered_text = text
            self.tracker.last_update_time = time.time()
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                try:
                    await self.message.edit_text(text, parse_mode=None, reply_markup=self.reply_markup)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Initial progress render error: {e}")

        while not self._stop_event.is_set():
            try:
                now = time.time()
                if (now - self.tracker.last_update_time) >= self.tracker.min_update_interval:
                    text = self.tracker.render_download_card()
                    if text != self.tracker.last_rendered_text:
                        try:
                            await self.message.edit_text(
                                text,
                                parse_mode="HTML",
                                reply_markup=self.reply_markup
                            )
                        except TelegramBadRequest as e:
                            if "message is not modified" not in str(e).lower():
                                await self.message.edit_text(text, parse_mode=None, reply_markup=self.reply_markup)
                        self.tracker.last_rendered_text = text
                        self.tracker.last_update_time = now
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramAPIError:
                pass
            except Exception as e:
                logger.debug(f"Progress update suppressed: {e}")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=0.4)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self, render_final: bool = False) -> None:
        """Stop the reporter background loop. Optionally render 100% completion card."""
        if self._is_stopped:
            return
        self._is_stopped = True
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

        if render_final:
            try:
                self.tracker.percentage = 100.0
                if self.tracker.total_bytes > 0:
                    self.tracker.downloaded_bytes = self.tracker.total_bytes
                card = self.tracker.render_download_card()
                if card != self.tracker.last_rendered_text:
                    await self.message.edit_text(card, parse_mode="Markdown", reply_markup=self.reply_markup)
            except Exception:
                pass

    async def vanish(self, delay: float = 0.2) -> bool:
        """Stop progress reporting immediately (no final edit) and delete the status message."""
        await self.stop(render_final=False)
        return await vanish_status_message(self.message, delay=delay)


class UploadProgressTracker:
    """
    Thread-safe progress data holder updated when chunks are read for upload,
    rendered asynchronously to Telegram with rate-limiting.
    """

    def __init__(
        self,
        media_title: str,
        total_bytes: int,
        media_type: str = "video",
        part_info: Optional[str] = None
    ):
        self.media_title = media_title or "Media"
        self.total_bytes = max(1, total_bytes)
        self.media_type = media_type
        self.part_info = part_info  # e.g., "Part 1 of 3"

        self.uploaded_bytes: int = 0
        self.percentage: float = 0.0
        self.speed: float = 0.0
        self.eta: Optional[int] = None

        self.start_time: float = time.time()
        self.last_update_time: float = 0.0
        self.last_rendered_text: str = ""
        self.min_update_interval: float = 1.2

        self._last_calc_time: float = self.start_time
        self._last_calc_bytes: int = 0

    def on_bytes_read(self, chunk_len: int) -> None:
        self.uploaded_bytes += chunk_len
        self.percentage = min(100.0, (self.uploaded_bytes / self.total_bytes) * 100.0)

        now = time.time()
        time_diff = now - self._last_calc_time
        if time_diff >= 1.0:
            bytes_diff = self.uploaded_bytes - self._last_calc_bytes
            instant_speed = bytes_diff / time_diff
            if self.speed > 0:
                self.speed = 0.7 * instant_speed + 0.3 * self.speed
            else:
                self.speed = instant_speed
            self._last_calc_time = now
            self._last_calc_bytes = self.uploaded_bytes

            remaining_bytes = max(0, self.total_bytes - self.uploaded_bytes)
            if self.speed > 0:
                self.eta = int(remaining_bytes / self.speed)

    def update_progress(self, current_bytes: int, total_bytes: int) -> None:
        """Update tracker with absolute cumulative bytes (used by MTProto progress hooks)."""
        self.uploaded_bytes = current_bytes
        if total_bytes > 0:
            self.total_bytes = total_bytes
        self.percentage = min(100.0, (self.uploaded_bytes / self.total_bytes) * 100.0)

        now = time.time()
        time_diff = now - self._last_calc_time
        if time_diff >= 1.0:
            bytes_diff = self.uploaded_bytes - self._last_calc_bytes
            instant_speed = bytes_diff / time_diff
            if self.speed > 0:
                self.speed = 0.7 * instant_speed + 0.3 * self.speed
            else:
                self.speed = instant_speed
            self._last_calc_time = now
            self._last_calc_bytes = self.uploaded_bytes

            remaining_bytes = max(0, self.total_bytes - self.uploaded_bytes)
            if self.speed > 0:
                self.eta = int(remaining_bytes / self.speed)

    def render_upload_card(self) -> str:
        if self.media_type == "video":
            icon = "🎬"
        elif self.media_type == "photo":
            icon = "📸"
        elif self.media_type == "gif":
            icon = "🌀"
        else:
            icon = "🎵"

        part_prefix = f" • {html.escape(self.part_info)}" if self.part_info else ""
        bar = make_progress_bar(self.percentage)
        speed_str = f"{format_bytes(self.speed)}/s" if self.speed > 0 else "Uploading..."
        uploaded_str = format_bytes(self.uploaded_bytes)
        total_str = format_bytes(self.total_bytes)
        eta_str = format_seconds(self.eta) if self.eta is not None else "--:--"

        title_display = self.media_title[:38] + "..." if len(self.media_title) > 38 else self.media_title
        title_clean = html.escape(title_display)
        m_type = html.escape(self.media_type.capitalize())

        text = (
            f"⬆️ <b>Uploading {m_type}{part_prefix}</b>\n"
            f"{icon} <b>Title:</b> <code>{title_clean}</code>\n\n"
            f"📊 <b>Progress:</b> <code>[{bar}]</code> <b>{self.percentage:.1f}%</b>\n"
            f"⚡ <b>Upload Speed:</b> <code>{speed_str}</code>\n"
            f"💾 <b>Sent:</b> <code>{uploaded_str} / {total_str}</code>\n"
            f"⏳ <b>ETA:</b> <code>{eta_str}</code>"
        )
        return text


class TrackedFSInputFile(FSInputFile):
    """
    Subclass of aiogram's FSInputFile that notifies an UploadProgressTracker
    as chunks are streamed to Telegram servers.
    """

    def __init__(
        self,
        path: str | Path,
        tracker: Optional[UploadProgressTracker] = None,
        filename: Optional[str] = None,
        chunk_size: int = 512 * 1024,
    ):
        super().__init__(path=path, filename=filename, chunk_size=chunk_size)
        self.tracker = tracker

    async def read(self, bot: Bot) -> AsyncGenerator[bytes, None]:
        async with aiofiles.open(self.path, "rb") as f:
            while chunk := await f.read(self.chunk_size):
                if self.tracker:
                    self.tracker.on_bytes_read(len(chunk))
                yield chunk


class AsyncUploadProgressReporter:
    """Periodically renders the live upload screen to Telegram with real-time 0-100% meter."""

    def __init__(self, message: Message, tracker: UploadProgressTracker):
        self.message = message
        self.tracker = tracker
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._is_stopped = False

    async def _run(self) -> None:
        # Initial render immediately so user sees 0% upload meter right away
        try:
            text = self.tracker.render_upload_card()
            await self.message.edit_text(text, parse_mode="HTML")
            self.tracker.last_rendered_text = text
            self.tracker.last_update_time = time.time()
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                try:
                    await self.message.edit_text(text, parse_mode=None)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Initial upload card render error: {e}")

        while not self._stop_event.is_set():
            try:
                now = time.time()
                if (now - self.tracker.last_update_time) >= self.tracker.min_update_interval:
                    text = self.tracker.render_upload_card()
                    if text != self.tracker.last_rendered_text:
                        try:
                            await self.message.edit_text(text, parse_mode="HTML")
                        except TelegramBadRequest as e:
                            if "message is not modified" not in str(e).lower():
                                await self.message.edit_text(text, parse_mode=None)
                        self.tracker.last_rendered_text = text
                        self.tracker.last_update_time = now
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramAPIError:
                pass
            except Exception as e:
                logger.debug(f"Upload progress update suppressed: {e}")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=0.4)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self, render_final: bool = False) -> None:
        """Stop the upload reporter background loop. Optionally render 100% completion card."""
        if self._is_stopped:
            return
        self._is_stopped = True
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

        if render_final:
            try:
                self.tracker.percentage = 100.0
                if self.tracker.total_bytes > 0:
                    self.tracker.uploaded_bytes = self.tracker.total_bytes
                card = self.tracker.render_upload_card()
                if card != self.tracker.last_rendered_text:
                    await self.message.edit_text(card, parse_mode="Markdown")
            except Exception:
                pass

    async def vanish(self, delay: float = 0.2) -> bool:
        """Stop upload reporting immediately (no final edit) and delete the status message."""
        await self.stop(render_final=False)
        return await vanish_status_message(self.message, delay=delay)
