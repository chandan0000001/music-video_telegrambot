"""
Telegram Delivery Service
Encapsulates sending audio and video files using aiogram with error handling and status updates.
"""
import logging
from pathlib import Path
from typing import Optional
from aiogram import Bot
from aiogram.types import FSInputFile, Message
from aiogram.exceptions import TelegramAPIError, TelegramEntityTooLarge, TelegramBadRequest

logger = logging.getLogger(__name__)


class TelegramService:
    """Manages Telegram file dispatching and progress status messaging."""

    @staticmethod
    async def update_status(message: Message, text: str) -> None:
        """Safely edit message text to show progress."""
        try:
            await message.edit_text(text, parse_mode="Markdown")
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.warning(f"Failed to update status message: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error updating status: {e}")

    @staticmethod
    async def send_audio_file(
        bot: Bot,
        chat_id: int,
        file_path: Path,
        title: str,
        artist: str,
        duration: int,
        thumbnail_path: Optional[Path] = None
    ) -> bool:
        """Deliver audio file using send_audio with metadata."""
        if not file_path.exists():
            logger.error(f"Cannot send audio: file not found at {file_path}")
            return False

        audio_file = FSInputFile(str(file_path))
        thumb_file = FSInputFile(str(thumbnail_path)) if (thumbnail_path and thumbnail_path.exists()) else None

        try:
            await bot.send_audio(
                chat_id=chat_id,
                audio=audio_file,
                title=title[:64],
                performer=artist[:64],
                duration=duration,
                thumbnail=thumb_file,
                caption=f"🎵 *{title}*\n👤 *Artist:* {artist}",
                parse_mode="Markdown"
            )
            return True
        except TelegramEntityTooLarge:
            logger.error(f"TelegramEntityTooLarge: {file_path} exceeded limit")
            await bot.send_message(
                chat_id=chat_id,
                text="❌ **File too large!** The converted audio file exceeds Telegram's upload limits."
            )
            return False
        except TelegramAPIError as e:
            logger.error(f"Telegram API error sending audio to {chat_id}: {e}")
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ **Upload Failed:** Unable to send the audio file ({e.message})."
            )
            return False
        except Exception as e:
            logger.exception(f"Unexpected error sending audio to {chat_id}: {e}")
            await bot.send_message(
                chat_id=chat_id,
                text="❌ **Upload Failed:** An unexpected error occurred while transferring your audio."
            )
            return False

    @staticmethod
    async def send_video_file(
        bot: Bot,
        chat_id: int,
        file_path: Path,
        title: str,
        duration: int,
        width: int,
        height: int,
        thumbnail_path: Optional[Path] = None
    ) -> bool:
        """Deliver video file using send_video with streaming support."""
        if not file_path.exists():
            logger.error(f"Cannot send video: file not found at {file_path}")
            return False

        video_file = FSInputFile(str(file_path))
        thumb_file = FSInputFile(str(thumbnail_path)) if (thumbnail_path and thumbnail_path.exists()) else None

        try:
            await bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=f"🎬 *{title}*\n📺 *Resolution:* {height}p",
                duration=duration,
                width=width,
                height=height,
                thumbnail=thumb_file,
                supports_streaming=True,
                parse_mode="Markdown"
            )
            return True
        except TelegramEntityTooLarge:
            logger.error(f"TelegramEntityTooLarge: {file_path} exceeded limit")
            await bot.send_message(
                chat_id=chat_id,
                text="❌ **File too large!** The video file exceeds Telegram's upload limits."
            )
            return False
        except TelegramAPIError as e:
            logger.error(f"Telegram API error sending video to {chat_id}: {e}")
            await bot.send_message(
                chat_id=chat_id,
                text=f"❌ **Upload Failed:** Unable to send the video ({e.message})."
            )
            return False
        except Exception as e:
            logger.exception(f"Unexpected error sending video to {chat_id}: {e}")
            await bot.send_message(
                chat_id=chat_id,
                text="❌ **Upload Failed:** An unexpected error occurred while transferring your video."
            )
            return False


telegram_service = TelegramService()
