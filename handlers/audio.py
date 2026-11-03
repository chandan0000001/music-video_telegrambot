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
from keyboards.main_menu import get_back_to_main_keyboard
from utils.queue_manager import queue_manager
from utils.progress import DownloadProgressTracker, AsyncProgressReporter, vanish_status_message
from utils.animation_helper import send_download_animation, vanish_download_animation
from utils.task_registry import task_registry

logger = logging.getLogger(__name__)
router = Router(name="audio_router")


@router.callback_query(F.data.startswith("opt_audio:"))
async def cb_choose_audio_format(callback: CallbackQuery) -> None:
    """Prompt user to select audio container format (MP3, M4A, WAV)."""
    video_id = callback.data.split(":")[1]
    user_id = callback.from_user.id if callback.from_user else 0

    from utils.selection_session import selection_session_manager
    is_valid, reason = selection_session_manager.check_validity(user_id, video_id)
    if not is_valid:
        await callback.answer(reason, show_alert=True)
        return
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

        # Track session with 10-min TTL
        user_id = callback.from_user.id if callback.from_user else 0
        from utils.selection_session import selection_session_manager
        await selection_session_manager.register_session(
            bot=callback.bot,
            user_id=user_id,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            video_id=video_id
        )
    await callback.answer()


@router.callback_query(F.data.startswith("afmt:"))
async def cb_choose_audio_bitrate(callback: CallbackQuery) -> None:
    """Prompt user to select audio bitrate quality (128, 192, 320 kbps)."""
    await callback.answer()
    parts = callback.data.split(":")
    video_id = parts[1]
    selected_format = parts[2].lower()

    if callback.message:
        try:
            await callback.message.edit_text("🔍 *Estimating audio sizes...*", parse_mode="Markdown")
        except Exception:
            pass

    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    success, info, _ = await media_service.get_media_info(canonical_url)

    approx_sizes = info.get("approx_audio_sizes", {}) if (success and info) else {}

    text = (
        f"🎧 **Audio Quality Selection ({selected_format.upper()})**\n\n"
        f"Select the desired bitrate for conversion:\n"
        f"• **128 kbps:** Fast download, standard sound\n"
        f"• **192 kbps:** Balanced, crisp fidelity\n"
        f"• **320 kbps:** Maximum studio bitrate\n\n"
        f"*Note: Bitrate conversions reflect genuine source parameters.*"
    )

    if callback.message:
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=get_audio_quality_keyboard(video_id, selected_format, approx_sizes),
                parse_mode="Markdown"
            )
        except Exception:
            pass

        # Track session with 10-min TTL
        user_id = callback.from_user.id if callback.from_user else 0
        from utils.selection_session import selection_session_manager
        await selection_session_manager.register_session(
            bot=callback.bot,
            user_id=user_id,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            video_id=video_id
        )


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

    from utils.selection_session import selection_session_manager
    is_valid, reason = selection_session_manager.check_validity(user_id, video_id)
    if not is_valid:
        await callback.answer(reason, show_alert=True)
        return
    selection_session_manager.clear_session(user_id)

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

    media_title = f"YouTube Audio ({video_id})"
    if status_msg.text and "Title:" in status_msg.text:
        try:
            media_title = status_msg.text.split("Title:")[1].split("\n")[0].strip()
        except Exception:
            pass

    # Check per-user limit
    can_start, limit_msg = await queue_manager.can_user_start_job(user_id)
    if not can_start:
        await status_msg.edit_text(limit_msg, parse_mode="Markdown")
        return

    cancel_kb = get_cancel_download_keyboard(user_id)
    current_task = asyncio.current_task()
    if current_task:
        task_registry.register(user_id, current_task)

    acquired_slot = False
    job_dir = None
    reporter = None
    anim_msg = None

    try:
        # Check server storage health before accepting jobs
        from utils.disk_cleaner import check_disk_space_available
        has_disk, _ = check_disk_space_available(min_free_mb=600)
        if not has_disk:
            await status_msg.edit_text(
                "⚠️ **Server Storage Busy**\n\n"
                "The server's media buffer is currently handling multiple large downloads. "
                "Please retry in 1–2 minutes once active downloads complete!",
                reply_markup=get_retry_keyboard(f"adl:{video_id}:{selected_format}:{bitrate}", f"opt_audio:{video_id}"),
                parse_mode="Markdown"
            )
            return

        # Acquire slot via dynamic 8/8 Queue
        acquired = await queue_manager.acquire_download_slot(
            user_id=user_id,
            media_title=media_title,
            status_msg=status_msg
        )
        if not acquired:
            return
        acquired_slot = True

        # Send companion entertainment animation/sticker
        anim_msg = await send_download_animation(callback.bot, user_id)

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
            await vanish_download_animation(anim_msg)
            await db.update_download_status(download_id, "failed")
            if err and ("access restricted" in err.lower() or "access notice" in err.lower()):
                await status_msg.edit_text(
                    err,
                    reply_markup=get_back_to_main_keyboard(),
                    parse_mode="HTML"
                )
            else:
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
            await vanish_status_message(status_msg, delay=0.3)
            await vanish_download_animation(anim_msg, delay=0.3)
        else:
            await vanish_download_animation(anim_msg)
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
        await vanish_download_animation(anim_msg)
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
        await vanish_download_animation(anim_msg)
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
        await vanish_download_animation(anim_msg)
        task_registry.unregister(user_id)
        if acquired_slot:
            queue_manager.release_download_slot(user_id)
        if job_dir:
            media_service.cleanup_job_dir(job_dir)
