"""
Video Download and FFmpeg Remuxing Handler
"""
import asyncio
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery

from database import db
from services.media_service import media_service
from services.telegram_service import telegram_service
from keyboards.quality_keyboard import (
    get_video_quality_keyboard,
    get_retry_keyboard,
    get_cancel_download_keyboard
)
from utils.rate_limiter import concurrency_manager
from utils.progress import DownloadProgressTracker, AsyncProgressReporter
from utils.task_registry import task_registry

logger = logging.getLogger(__name__)
router = Router(name="video_router")


@router.callback_query(F.data.startswith("opt_video:"))
async def cb_choose_video_quality(callback: CallbackQuery) -> None:
    """Inspect and present available video resolutions from low to high."""
    video_id = callback.data.split(":")[1]
    if callback.message:
        await callback.message.edit_text("🔍 *Probing available video streams...*", parse_mode="Markdown")

    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    success, info, err = await media_service.get_media_info(canonical_url)

    if not success or not info:
        if callback.message:
            await callback.message.edit_text(
                f"❌ *Could not load video streams:*\n{err or 'Unknown error.'}",
                parse_mode="Markdown"
            )
        await callback.answer()
        return

    resolutions = info.get("available_resolutions", [])
    if not resolutions:
        if callback.message:
            await callback.message.edit_text(
                "❌ *No standard video resolutions found for this item.*",
                parse_mode="Markdown"
            )
        await callback.answer()
        return

    text = (
        f"🎬 **Video Resolution Selection**\n\n"
        f"**Title:** {info['title']}\n"
        f"**Duration:** `{info['duration_str']}`\n\n"
        f"Select a genuinely available resolution below (low to high):"
    )

    if callback.message:
        await callback.message.edit_text(
            text=text,
            reply_markup=get_video_quality_keyboard(video_id, resolutions),
            parse_mode="Markdown"
        )
    await callback.answer()


@router.callback_query(F.data.startswith("vdl:"))
async def cb_start_video_download(callback: CallbackQuery) -> None:
    """Download, remux, and deliver video."""
    parts = callback.data.split(":")
    video_id = parts[1]
    resolution = parts[2]

    user = callback.from_user
    user_id = user.id if user else 0

    if user and await db.is_user_blocked(user_id):
        await callback.answer("Restricted user.", show_alert=True)
        return

    await callback.answer(f"Starting {resolution} video download...")
    status_msg = callback.message
    if not status_msg:
        return

    canonical_url = f"https://www.youtube.com/watch?v={video_id}"

    # Record initial pending download in database
    download_id = await db.record_download(
        telegram_id=user_id,
        media_title=f"YouTube_{video_id}",
        source=canonical_url,
        media_type="video",
        selected_quality=resolution,
        selected_format="mp4",
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

        # Extract title for loading screen if available
        media_title = f"YouTube Video ({video_id})"
        if status_msg.text and "Title:" in status_msg.text:
            try:
                media_title = status_msg.text.split("Title:")[1].split("\n")[0].strip()
            except Exception:
                pass

        retry_cb = f"vdl:{video_id}:{resolution}"
        back_cb = f"opt_video:{video_id}"

        tracker = DownloadProgressTracker(
            media_title=media_title,
            quality_label=resolution,
            media_type="video"
        )
        if current_task:
            task_registry.register(user_id, current_task, tracker=tracker)

        reporter = AsyncProgressReporter(status_msg, tracker, reply_markup=cancel_kb)
        reporter.start()

        success, result, err, job_dir = await media_service.process_video(
            video_url=canonical_url,
            resolution=resolution,
            tracker=tracker,
            status_callback=lambda text: telegram_service.update_status(status_msg, text)
        )
        await reporter.stop()

        if not success or not result:
            await db.update_download_status(download_id, "failed")
            await telegram_service.update_status_with_keyboard(
                status_msg,
                f"❌ *Video Processing Failed:*\n{err or 'Unknown error occurred.'}\n\n"
                f"Click below to retry the download or choose a different quality:",
                reply_markup=get_retry_keyboard(retry_cb, back_cb)
            )
            return

        sent = await telegram_service.send_video_file(
            bot=callback.bot,
            chat_id=user_id,
            file_path=result["file_path"],
            title=result["title"],
            duration=result["duration"],
            width=result["width"],
            height=result["height"],
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
                "❌ *Upload Failed:* Telegram was unable to accept the video file.\n\n"
                "You can retry or choose a different resolution (e.g. 720p or 480p):",
                reply_markup=get_retry_keyboard(retry_cb, back_cb)
            )

    except asyncio.CancelledError:
        logger.info(f"Video job aborted by user {user_id} ({canonical_url})")
        if reporter:
            await reporter.stop()
        await db.update_download_status(download_id, "cancelled")
        try:
            await telegram_service.update_status(
                status_msg,
                f"🛑 *Download Cancelled*\n"
                f"🎬 `{media_title}`\n\n"
                f"The download was stopped and temporary storage freed."
            )
        except Exception:
            pass
    except Exception as e:
        if reporter:
            await reporter.stop()
        logger.exception(f"Unhandled video processing error for {canonical_url}: {e}")
        await db.update_download_status(download_id, "failed")
        await telegram_service.update_status_with_keyboard(
            status_msg,
            f"❌ *Error during video processing:*\n{str(e)[:150]}\n\n"
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
