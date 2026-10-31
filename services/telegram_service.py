"""
Telegram Delivery Service
Encapsulates sending audio and video files using aiogram with error handling and status updates.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional
from aiogram import Bot
from aiogram.types import FSInputFile, Message, InlineKeyboardMarkup
from aiogram.exceptions import TelegramAPIError, TelegramEntityTooLarge, TelegramBadRequest

logger = logging.getLogger(__name__)


class TelegramService:
    """Manages Telegram file dispatching, live progress screens, and status updates."""

    @staticmethod
    async def update_status(message: Message, text: str) -> None:
        """Safely edit message text to show progress."""
        try:
            await message.edit_text(text, parse_mode="Markdown")
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.debug(f"Status update skipped: {e}")
        except Exception as e:
            logger.debug(f"Unexpected error updating status: {e}")

    @staticmethod
    async def update_status_with_keyboard(
        message: Message,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup] = None
    ) -> None:
        """Safely edit message text and attach an inline keyboard (e.g. for retrying)."""
        try:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                logger.debug(f"Status update with keyboard skipped: {e}")
        except Exception as e:
            logger.debug(f"Unexpected error updating status with keyboard: {e}")

    @staticmethod
    async def _keep_chat_action(bot: Bot, chat_id: int, action: str, stop_event: asyncio.Event) -> None:
        """Periodically trigger Telegram chat action while upload is in flight."""
        while not stop_event.is_set():
            try:
                await bot.send_chat_action(chat_id=chat_id, action=action)
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    async def send_audio_file(
        bot: Bot,
        chat_id: int,
        file_path: Path,
        title: str,
        artist: str,
        duration: int,
        thumbnail_path: Optional[Path] = None,
        status_message: Optional[Message] = None
    ) -> bool:
        """Deliver audio file using send_audio with metadata and live chat action."""
        if not file_path.exists():
            logger.error(f"Cannot send audio: file not found at {file_path}")
            return False

        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if status_message:
            await TelegramService.update_status(
                status_message,
                f"⬆️ *Uploading Audio to Telegram...*\n"
                f"🎵 *Title:* `{title[:40]}`\n"
                f"📦 *File size:* `{file_size_mb:.1f} MB`\n"
                f"📡 *Status:* Uploading directly to your chat..."
            )

        audio_file = FSInputFile(str(file_path))
        thumb_file = FSInputFile(str(thumbnail_path)) if (thumbnail_path and thumbnail_path.exists()) else None

        stop_action = asyncio.Event()
        action_task = asyncio.create_task(
            TelegramService._keep_chat_action(bot, chat_id, "upload_document", stop_action)
        )

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
        finally:
            stop_action.set()
            await action_task

    @staticmethod
    async def send_video_file(
        bot: Bot,
        chat_id: int,
        file_path: Path,
        title: str,
        duration: int,
        width: int,
        height: int,
        thumbnail_path: Optional[Path] = None,
        status_message: Optional[Message] = None
    ) -> bool:
        """Deliver video file using send_video with streaming support and live chat action."""
        if not file_path.exists():
            logger.error(f"Cannot send video: file not found at {file_path}")
            return False

        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if status_message:
            await TelegramService.update_status(
                status_message,
                f"⬆️ *Uploading Video to Telegram...*\n"
                f"🎬 *Title:* `{title[:40]}`\n"
                f"📦 *File size:* `{file_size_mb:.1f} MB`\n"
                f"📡 *Status:* Uploading directly to your chat..."
            )

        video_file = FSInputFile(str(file_path))
        thumb_file = FSInputFile(str(thumbnail_path)) if (thumbnail_path and thumbnail_path.exists()) else None

        stop_action = asyncio.Event()
        action_task = asyncio.create_task(
            TelegramService._keep_chat_action(bot, chat_id, "upload_video", stop_action)
        )

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
        finally:
            stop_action.set()
            await action_task


telegram_service = TelegramService()
