"""
Video Download and FFmpeg Remuxing Handler
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery

from database import db
from services.media_service import media_service
from services.telegram_service import telegram_service
from keyboards.quality_keyboard import get_video_quality_keyboard
from utils.rate_limiter import concurrency_manager

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

    # Acquire concurrency slot
    await telegram_service.update_status(
        status_msg,
        f"⏳ *Job queued:* Waiting for an available worker slot..."
    )
    await concurrency_manager.acquire()

    job_dir = None
    try:
        await telegram_service.update_status(
            status_msg,
            f"⬇️ *Downloading {resolution} video & audio streams...*"
        )

        success, result, err, job_dir = await media_service.process_video(
            video_url=canonical_url,
            resolution=resolution
        )

        if not success or not result:
            await db.update_download_status(download_id, "failed")
            await telegram_service.update_status(
                status_msg,
                f"❌ *Video Processing Failed:*\n{err or 'Unknown error occurred.'}"
            )
            return

        # Upload to Telegram
        await telegram_service.update_status(
            status_msg,
            f"⬆️ *Uploading {resolution} video to Telegram...*"
        )

        sent = await telegram_service.send_video_file(
            bot=callback.bot,
            chat_id=user_id,
            file_path=result["file_path"],
            title=result["title"],
            duration=result["duration"],
            width=result["width"],
            height=result["height"],
            thumbnail_path=result["thumbnail_path"]
        )

        if sent:
            await db.update_download_status(download_id, "completed")
            try:
                await status_msg.delete()
            except Exception:
                pass
        else:
            await db.update_download_status(download_id, "failed")
            await telegram_service.update_status(
                status_msg,
                "❌ *Upload Failed:* Telegram was unable to accept the video file."
            )

    except Exception as e:
        logger.exception(f"Unhandled video processing error for {canonical_url}: {e}")
        await db.update_download_status(download_id, "failed")
        await telegram_service.update_status(
            status_msg,
            f"❌ *Error during video processing:* {str(e)[:150]}"
        )
    finally:
        concurrency_manager.release()
        if job_dir:
            media_service.cleanup_job_dir(job_dir)
