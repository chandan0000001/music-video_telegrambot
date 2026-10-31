"""
MTProto High-Speed Direct Uploader Service
Uploads files up to 2000 MB (2 GB) directly to Telegram Data Centers using native MTProto.
Bypasses the 50 MB HTTP Bot API limit without requiring file splitting or lossy compression.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional
from aiogram.types import Message as AiogramMessage

from config import TELEGRAM_API_ID, TELEGRAM_API_HASH, BOT_TOKEN, TEMP_DIR
from utils.progress import UploadProgressTracker, AsyncUploadProgressReporter

logger = logging.getLogger(__name__)


class MTProtoService:
    """Manages high-speed direct MTProto client for sending files up to 2000 MB."""

    def __init__(self):
        self.client = None
        self._is_started = False
        self._lock = asyncio.Lock()

    @property
    def is_configured(self) -> bool:
        """Returns True if Telegram App credentials are configured in .env."""
        return bool(TELEGRAM_API_ID and TELEGRAM_API_HASH and BOT_TOKEN)

    async def start(self) -> bool:
        """Start the MTProto client if credentials are provided."""
        if not self.is_configured:
            logger.info("MTProto credentials not configured. Using standard Bot API.")
            return False

        async with self._lock:
            if self._is_started and self.client:
                return True
            try:
                from hydrogram import Client
                self.client = Client(
                    name="bot_mtproto_worker",
                    api_id=TELEGRAM_API_ID,
                    api_hash=TELEGRAM_API_HASH,
                    bot_token=BOT_TOKEN,
                    workdir=str(TEMP_DIR),
                    in_memory=True
                )
                await self.client.start()
                self._is_started = True
                me = await self.client.get_me()
                logger.info(f"MTProto 2GB Engine active: @{me.username} (ID: {me.id})")
                return True
            except Exception as e:
                logger.warning(f"Could not start MTProto Client: {e}. Fallback to Bot API.")
                self.client = None
                self._is_started = False
                return False

    async def stop(self) -> None:
        """Gracefully stop the MTProto client."""
        async with self._lock:
            if self.client and self._is_started:
                try:
                    await self.client.stop()
                except Exception as e:
                    logger.debug(f"Error stopping MTProto client: {e}")
                finally:
                    self._is_started = False
                    self.client = None

    async def is_ready(self) -> bool:
        """Ensure client is connected."""
        if not self._is_started or not self.client:
            return await self.start()
        return True

    async def send_video(
        self,
        chat_id: int,
        file_path: Path,
        title: str,
        duration: int,
        width: int,
        height: int,
        thumbnail_path: Optional[Path] = None,
        status_message: Optional[AiogramMessage] = None
    ) -> bool:
        """Upload video up to 2 GB via native MTProto with live animated progress."""
        if not await self.is_ready():
            return False

        file_size_bytes = file_path.stat().st_size
        caption = f"🎬 **{title}**\n📺 **Resolution:** {height}p\n⚡ Delivered via High-Speed MTProto (2 GB Mode)"

        upload_tracker = None
        upload_reporter = None
        if status_message:
            upload_tracker = UploadProgressTracker(
                media_title=title,
                total_bytes=file_size_bytes,
                media_type="video"
            )
            upload_reporter = AsyncUploadProgressReporter(status_message, upload_tracker)
            upload_reporter.start()

        async def mtproto_progress(current: int, total: int):
            if upload_tracker:
                upload_tracker.update_progress(current, total)

        thumb_str = str(thumbnail_path) if (thumbnail_path and thumbnail_path.exists()) else None

        try:
            await self.client.send_video(
                chat_id=chat_id,
                video=str(file_path),
                caption=caption,
                duration=duration,
                width=width,
                height=height,
                thumb=thumb_str,
                supports_streaming=True,
                progress=mtproto_progress
            )
            return True
        except Exception as e:
            logger.error(f"MTProto video upload failed for chat {chat_id}: {e}")
            return False
        finally:
            if upload_reporter:
                await upload_reporter.stop()

    async def send_audio(
        self,
        chat_id: int,
        file_path: Path,
        title: str,
        artist: str,
        duration: int,
        thumbnail_path: Optional[Path] = None,
        status_message: Optional[AiogramMessage] = None
    ) -> bool:
        """Upload audio up to 2 GB via native MTProto with live animated progress."""
        if not await self.is_ready():
            return False

        file_size_bytes = file_path.stat().st_size
        caption = f"🎵 **{title}**\n👤 **Artist:** {artist}\n⚡ Delivered via High-Speed MTProto (2 GB Mode)"

        upload_tracker = None
        upload_reporter = None
        if status_message:
            upload_tracker = UploadProgressTracker(
                media_title=title,
                total_bytes=file_size_bytes,
                media_type="audio"
            )
            upload_reporter = AsyncUploadProgressReporter(status_message, upload_tracker)
            upload_reporter.start()

        async def mtproto_progress(current: int, total: int):
            if upload_tracker:
                upload_tracker.update_progress(current, total)

        thumb_str = str(thumbnail_path) if (thumbnail_path and thumbnail_path.exists()) else None

        try:
            await self.client.send_audio(
                chat_id=chat_id,
                audio=str(file_path),
                caption=caption,
                title=title[:64],
                performer=artist[:64],
                duration=duration,
                thumb=thumb_str,
                progress=mtproto_progress
            )
            return True
        except Exception as e:
            logger.error(f"MTProto audio upload failed for chat {chat_id}: {e}")
            return False
        finally:
            if upload_reporter:
                await upload_reporter.stop()


mtproto_service = MTProtoService()
