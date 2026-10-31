"""
Audio Download and FFmpeg Conversion Handler
"""
import asyncio
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery

from database import db
from services.media_service import media_service
from services.telegram_service import telegram_service
from keyboards.quality_keyboard import (
    get_audio_format_keyboard,
    get_audio_quality_keyboard,
    get_retry_keyboard,
    get_cancel_download_keyboard
)
from utils.rate_limiter import concurrency_manager
from utils.progress import DownloadProgressTracker, AsyncProgressReporter
from utils.task_registry import task_registry

logger = logging.getLogger(__name__)
router = Router(name="audio_router")


@router.callback_query(F.data.startswith("opt_audio:"))
async def cb_choose_audio_format(callback: CallbackQuery) -> None:
    """Prompt user to select audio container format (MP3, M4A, WAV)."""
    video_id = callback.data.split(":")[1]
    text = (
        "🎧 **Audio Format Selection**\n\n"
        "Please choose your preferred audio format:\n"
        "• **MP3:** Universally compatible, high quality audio.\n"
        "• **M4A:** AAC codec, efficient compression with great fidelity.\n"
        "• **WAV:** Lossless uncompressed audio."
    )
    if callback.message:
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=get_audio_format_keyboard(video_id),
                parse_mode="Markdown"
            )
        except Exception:
            await callback.message.answer(
                text=text,
                reply_markup=get_audio_format_keyboard(video_id),
                parse_mode="Markdown"
            )
    await callback.answer()


@router.callback_query(F.data.startswith("afmt:"))
async def cb_choose_audio_bitrate(callback: CallbackQuery) -> None:
    """Prompt user to select audio bitrate quality (128, 192, 320 kbps)."""
    parts = callback.data.split(":")
    video_id = parts[1]
    selected_format = parts[2].lower()

    if callback.message:
        await callback.message.edit_text("🔍 *Estimating audio sizes...*", parse_mode="Markdown")

    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    success, info, _ = await media_service.get_media_info(canonical_url)

    approx_sizes = info.get("approx_audio_sizes", {}) if (success and info) else {}

    text = (
        f"🎧 **Audio Quality Selection ({selected_format.upper()})**\n\n"
        f"Select the desired bitrate for FFmpeg conversion:\n"
        f"• **128 kbps:** Lightweight, fast download, standard sound.\n"
        f"• **192 kbps:** Balanced, crisp fidelity.\n"
        f"• **320 kbps:** Maximum bitrate for pristine audio enjoyment.\n\n"
        f"*Note: Bitrate conversions reflect genuine source parameters.*"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_audio_quality_keyboard(video_id, selected_format, approx_sizes),
            parse_mode="Markdown"
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adl:"))
async def cb_start_audio_download(callback: CallbackQuery) -> None:
    """Process and deliver audio with specified format and quality."""
    parts = callback.data.split(":")
    video_id = parts[1]
    selected_format = parts[2].lower()
    bitrate = parts[3]

    user = callback.from_user
    user_id = user.id if user else 0

    if user and await db.is_user_blocked(user_id):
        await callback.answer("Restricted user.", show_alert=True)
        return

    await callback.answer("Starting audio extraction...")
    status_msg = callback.message
    if not status_msg:
        return

    canonical_url = f"https://www.youtube.com/watch?v={video_id}"

    # Record initial pending download in database
    download_id = await db.record_download(
        telegram_id=user_id,
        media_title=f"YouTube_{video_id}",
        source=canonical_url,
        media_type="audio",
        selected_quality=f"{bitrate}kbps",
        selected_format=selected_format,
        status="pending"
    )

    cancel_kb = get_cancel_download_keyboard(user_id)
    current_task = asyncio.current_task()
    if current_task:
        task_registry.register(user_id, current_task)

    acquired_slot = False
    job_dir = None
    reporter = None

    try:
        # Acquire concurrency slot
        await telegram_service.update_status_with_keyboard(
            status_msg,
            f"⏳ *Job queued:* Waiting for an available worker slot...",
            reply_markup=cancel_kb
        )
        await concurrency_manager.acquire()
        acquired_slot = True

        media_title = f"YouTube Audio ({video_id})"
        if status_msg.text and "Title:" in status_msg.text:
            try:
                media_title = status_msg.text.split("Title:")[1].split("\n")[0].strip()
            except Exception:
                pass

        retry_cb = f"adl:{video_id}:{selected_format}:{bitrate}"
        back_cb = f"opt_audio:{video_id}"

        tracker = DownloadProgressTracker(
            media_title=media_title,
            quality_label=f"{selected_format.upper()} {bitrate}kbps",
            media_type="audio"
        )
        if current_task:
            task_registry.register(user_id, current_task, tracker=tracker)

        reporter = AsyncProgressReporter(status_msg, tracker, reply_markup=cancel_kb)
        reporter.start()

        success, result, err, job_dir = await media_service.process_audio(
            video_url=canonical_url,
            output_format=selected_format,
            bitrate_kbps=bitrate,
            tracker=tracker,
            status_callback=lambda text: telegram_service.update_status(status_msg, text)
        )
        await reporter.stop()

        if not success or not result:
            await db.update_download_status(download_id, "failed")
            await telegram_service.update_status_with_keyboard(
                status_msg,
                f"❌ *Audio Processing Failed:*\n{err or 'Unknown error occurred.'}\n\n"
                f"Click below to retry the download or choose a different format:",
                reply_markup=get_retry_keyboard(retry_cb, back_cb)
            )
            return

        sent = await telegram_service.send_audio_file(
            bot=callback.bot,
            chat_id=user_id,
            file_path=result["file_path"],
            title=result["title"],
            artist=result["artist"],
            duration=result["duration"],
            thumbnail_path=result["thumbnail_path"],
            status_message=status_msg
        )

        if sent:
            await db.update_download_status(download_id, "completed")
            try:
                await status_msg.delete()
            except Exception:
                pass
        else:
            await db.update_download_status(download_id, "failed")
            await telegram_service.update_status_with_keyboard(
                status_msg,
                "❌ *Upload Failed:* Telegram was unable to accept the audio file.\n\n"
                "You can retry or choose a different audio format:",
                reply_markup=get_retry_keyboard(retry_cb, back_cb)
            )

    except asyncio.CancelledError:
        logger.info(f"Audio job aborted by user {user_id} ({canonical_url})")
        if reporter:
            await reporter.stop()
        await db.update_download_status(download_id, "cancelled")
        try:
            await telegram_service.update_status(
                status_msg,
                f"🛑 *Download Cancelled*\n"
                f"🎵 `{media_title}`\n\n"
                f"The download was stopped and temporary storage freed."
            )
        except Exception:
            pass
    except Exception as e:
        if reporter:
            await reporter.stop()
        logger.exception(f"Unhandled audio processing error for {canonical_url}: {e}")
        await db.update_download_status(download_id, "failed")
        await telegram_service.update_status_with_keyboard(
            status_msg,
            f"❌ *Error during processing:*\n{str(e)[:150]}\n\n"
            f"Click below to retry the download:",
            reply_markup=get_retry_keyboard(retry_cb, back_cb)
        )
    finally:
        if reporter:
            await reporter.stop()
        task_registry.unregister(user_id)
        if acquired_slot:
            concurrency_manager.release()
        if job_dir:
            media_service.cleanup_job_dir(job_dir)
