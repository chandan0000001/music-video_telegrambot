"""
Telegram Delivery Service
Encapsulates sending audio and video files using aiogram with error handling and status updates.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional, List
from aiogram import Bot
from aiogram.types import FSInputFile, Message, InlineKeyboardMarkup
from aiogram.exceptions import TelegramAPIError, TelegramEntityTooLarge, TelegramBadRequest

from config import TELEGRAM_API_SERVER
from services.ffmpeg_service import ffmpeg_service

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
        """Deliver audio file using send_audio with metadata and live chat action (with chunking if >48MB)."""
        if not file_path.exists():
            logger.error(f"Cannot send audio: file not found at {file_path}")
            return False

        file_size_mb = file_path.stat().st_size / (1024 * 1024)

        # If audio exceeds Telegram Cloud 50MB single-upload limit, split into 40-45MB chunks
        if file_size_mb > 48 and not TELEGRAM_API_SERVER:
            if status_message:
                await TelegramService.update_status(
                    status_message,
                    f"📦 *Preparing Audio Chunks (40-45 MB each)...*\n"
                    f"🎵 *{title[:40]}*\n"
                    f"✨ *Preserving 100% Original Audio Fidelity...*"
                )
            chunks_dir = file_path.parent / "audio_chunks"
            parts = await ffmpeg_service.split_audio_into_chunks(
                input_file=file_path,
                output_dir=chunks_dir,
                duration=duration,
                target_format=file_path.suffix.replace(".", "") or "mp3",
                target_chunk_bytes=44 * 1024 * 1024
            )
            if len(parts) > 1:
                total_parts = len(parts)
                logger.info(f"Delivering audio in {total_parts} chunks to chat {chat_id}")
                for idx, part_file in enumerate(parts, start=1):
                    part_size_mb = part_file.stat().st_size / (1024 * 1024)
                    if status_message:
                        await TelegramService.update_status(
                            status_message,
                            f"⬆️ *Uploading Audio Part {idx} of {total_parts}...*\n"
                            f"🎵 *{title[:40]}*\n"
                            f"📦 *Chunk size:* `{part_size_mb:.1f} MB`"
                        )
                    part_audio = FSInputFile(str(part_file), chunk_size=1024 * 1024)
                    thumb_file = FSInputFile(str(thumbnail_path), chunk_size=256 * 1024) if (thumbnail_path and thumbnail_path.exists()) else None
                    caption = f"🎵 *{title}* (Part {idx}/{total_parts})\n👤 *Artist:* {artist} | 💾 *Size:* {part_size_mb:.1f} MB"
                    await bot.send_audio(
                        chat_id=chat_id,
                        audio=part_audio,
                        title=f"{title[:50]} (Part {idx}/{total_parts})",
                        performer=artist[:64],
                        duration=max(1, duration // total_parts),
                        thumbnail=thumb_file,
                        caption=caption,
                        parse_mode="Markdown"
                    )
                return True

        if status_message:
            await TelegramService.update_status(
                status_message,
                f"⬆️ *Uploading Audio to Telegram...*\n"
                f"🎵 *Title:* `{title[:40]}`\n"
                f"📦 *File size:* `{file_size_mb:.1f} MB`\n"
                f"📡 *Status:* Uploading directly to your chat..."
            )

        audio_file = FSInputFile(str(file_path), chunk_size=1024 * 1024)
        thumb_file = FSInputFile(str(thumbnail_path), chunk_size=256 * 1024) if (thumbnail_path and thumbnail_path.exists()) else None

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

        # If video exceeds Telegram Cloud 50MB single-upload limit, split into optimal 40-45MB chunks
        # This preserves 100% original visual quality (e.g. 1080p60) without any compression downgrade!
        if file_size_mb > 48 and not TELEGRAM_API_SERVER:
            if status_message:
                await TelegramService.update_status(
                    status_message,
                    f"📦 *Preparing Optimal Chunks (40-45 MB each)...*\n"
                    f"🎬 *{title[:40]}*\n"
                    f"✨ *Preserving 100% Original {height}p Quality without compression Loss...*"
                )
            chunks_dir = file_path.parent / "video_chunks"
            parts = await ffmpeg_service.split_video_into_chunks(
                input_file=file_path,
                output_dir=chunks_dir,
                duration=duration,
                target_chunk_bytes=44 * 1024 * 1024
            )
            if len(parts) > 1:
                total_parts = len(parts)
                logger.info(f"Delivering video in {total_parts} chunks to chat {chat_id}")
                for idx, part_file in enumerate(parts, start=1):
                    part_size_mb = part_file.stat().st_size / (1024 * 1024)
                    if status_message:
                        await TelegramService.update_status(
                            status_message,
                            f"⬆️ *Uploading Part {idx} of {total_parts}...*\n"
                            f"🎬 *{title[:40]}*\n"
                            f"📦 *Chunk size:* `{part_size_mb:.1f} MB`\n"
                            f"📡 Sending directly to your chat..."
                        )

                    # probe exact part duration
                    part_dur = max(1, duration // total_parts)
                    part_probe = await ffmpeg_service.probe_media(part_file)
                    if part_probe and "format" in part_probe and "duration" in part_probe["format"]:
                        try:
                            part_dur = int(float(part_probe["format"]["duration"]))
                        except Exception:
                            pass

                    part_video = FSInputFile(str(part_file), chunk_size=1024 * 1024)
                    thumb_file = FSInputFile(str(thumbnail_path), chunk_size=256 * 1024) if (thumbnail_path and thumbnail_path.exists()) else None
                    caption = (
                        f"🎬 *{title}* (Part {idx}/{total_parts})\n"
                        f"📺 *Resolution:* {height}p | 💾 *Size:* {part_size_mb:.1f} MB"
                    )

                    await bot.send_video(
                        chat_id=chat_id,
                        video=part_video,
                        caption=caption,
                        duration=part_dur,
                        width=width,
                        height=height,
                        thumbnail=thumb_file,
                        supports_streaming=True,
                        parse_mode="Markdown"
                    )
                return True

        if status_message:
            await TelegramService.update_status(
                status_message,
                f"⬆️ *Uploading Video to Telegram...*\n"
                f"🎬 *Title:* `{title[:40]}`\n"
                f"📦 *File size:* `{file_size_mb:.1f} MB`\n"
                f"📡 *Status:* Uploading directly to your chat..."
            )

        video_file = FSInputFile(str(file_path), chunk_size=1024 * 1024)
        thumb_file = FSInputFile(str(thumbnail_path), chunk_size=256 * 1024) if (thumbnail_path and thumbnail_path.exists()) else None

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
            logger.warning(f"TelegramEntityTooLarge: {file_path} ({file_size_mb:.1f} MB) rejected by cloud server.")
            if status_message:
                await TelegramService.update_status(
                    status_message,
                    f"⚠️ *Telegram Cloud Server Limit (50 MB) Reached!*\n"
                    f"File is `{file_size_mb:.1f} MB`. Telegram Cloud API limits bot uploads to 50 MB.\n"
                    f"🗜 Compressing video to fit Telegram cloud limits..."
                )
            try:
                from services.ffmpeg_service import ffmpeg_service
                compressed_file = file_path.parent / f"compressed_{file_path.name}"
                compressed = await ffmpeg_service.compress_video_to_target_size(
                    input_file=file_path,
                    output_file=compressed_file,
                    target_size_bytes=48 * 1024 * 1024,
                    duration=duration
                )
                if compressed and compressed_file.exists():
                    comp_video = FSInputFile(str(compressed_file), chunk_size=1024 * 1024)
                    await bot.send_video(
                        chat_id=chat_id,
                        video=comp_video,
                        caption=f"🎬 *{title}*\n📺 *Resolution:* {height}p (Optimized for Telegram Cloud)",
                        duration=duration,
                        width=width,
                        height=height,
                        thumbnail=thumb_file,
                        supports_streaming=True,
                        parse_mode="Markdown"
                    )
                    return True
            except Exception as comp_err:
                logger.error(f"Fallback compression failed: {comp_err}")

            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❌ **File too large!** The video is {file_size_mb:.1f} MB.\n"
                    "Telegram Cloud restricts bot uploads to 50 MB unless using a Local Bot API server (`TELEGRAM_API_SERVER` in .env)."
                )
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
