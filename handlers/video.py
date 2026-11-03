"""
Video Download and FFmpeg Remuxing Handler
"""
import html
import asyncio
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS, PRICE_SINGLE_720P, PRICE_SINGLE_1080P, SUBSCRIPTION_PLANS
from database import db
from services.media_service import media_service
from services.telegram_service import telegram_service
from keyboards.quality_keyboard import (
    get_video_quality_keyboard,
    get_retry_keyboard,
    get_cancel_download_keyboard,
    get_paywall_keyboard
)
from keyboards.main_menu import get_back_to_main_keyboard
from utils.queue_manager import queue_manager
from utils.progress import DownloadProgressTracker, AsyncProgressReporter, vanish_status_message
from utils.animation_helper import send_download_animation, vanish_download_animation
from utils.task_registry import task_registry

logger = logging.getLogger(__name__)
router = Router(name="video_router")


@router.callback_query(F.data.startswith("opt_video:"))
async def cb_choose_video_quality(callback: CallbackQuery) -> None:
    """Inspect and present available video resolutions from low to high."""
    video_id = callback.data.split(":")[1]
    from utils.selection_session import selection_session_manager
    if not await selection_session_manager.enforce_session_validity(callback, video_id):
        return

    await callback.answer()
    if callback.message:
        try:
            await callback.message.edit_text("🔍 <i>Probing available video streams...</i>", parse_mode="HTML")
        except Exception:
            pass

    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    success, info, err = await media_service.get_media_info(canonical_url)

    if not success or not info:
        if callback.message:
            safe_err = html.escape(err or "Unknown error.")
            try:
                await callback.message.edit_text(
                    f"❌ <b>Could not load video streams:</b>\n{safe_err}",
                    parse_mode="HTML"
                )
            except Exception:
                try:
                    await callback.message.edit_text(
                        f"❌ Could not load video streams:\n{err or 'Unknown error.'}",
                        parse_mode=None
                    )
                except Exception:
                    pass
        return

    resolutions = info.get("available_resolutions", [])
    if not resolutions:
        if callback.message:
            try:
                await callback.message.edit_text(
                    "❌ <b>No standard video resolutions found for this item.</b>",
                    parse_mode="HTML"
                )
            except Exception:
                try:
                    await callback.message.edit_text(
                        "❌ No standard video resolutions found for this item.",
                        parse_mode=None
                    )
                except Exception:
                    pass
        return

    user = callback.from_user
    user_id = user.id if user else 0
    is_admin = user_id in ADMIN_IDS
    sub = await db.get_user_subscription(user_id)
    has_active_sub = bool(sub and sub.get("is_active") and sub.get("today_remaining", 0) > 0)

    admin_banner = "👑 <b>Admin Access:</b> All resolutions unlocked (Free &amp; Unlimited)\n" if is_admin else (
        f"⚡ <b>VIP Pass Active ({html.escape(sub['plan_tier'].capitalize())}):</b> {sub['today_remaining']}/{sub['daily_limit']} HD videos remaining today\n" if has_active_sub else ""
    )

    safe_title = html.escape(info.get("title", "Unknown Title"))
    safe_duration = html.escape(info.get("duration_str", "00:00"))

    text = (
        f"🎬 <b>Video Resolution Selection</b>\n\n"
        f"<b>Title:</b> {safe_title}\n"
        f"<b>Duration:</b> <code>{safe_duration}</code>\n\n"
        f"{admin_banner}"
        f"Select a genuinely available resolution below (low to high):"
    )

    if callback.message:
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=get_video_quality_keyboard(
                    video_id=video_id,
                    available_resolutions=resolutions,
                    is_admin=is_admin,
                    has_active_sub=has_active_sub
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Failed to edit with HTML in cb_choose_video_quality: {e}. Fallback to plain text.")
            plain_text = (
                f"🎬 Video Resolution Selection\n\n"
                f"Title: {info.get('title', 'Unknown Title')}\n"
                f"Duration: {info.get('duration_str', '00:00')}\n\n"
                f"Select a genuinely available resolution below (low to high):"
            )
            await callback.message.edit_text(
                text=plain_text,
                reply_markup=get_video_quality_keyboard(
                    video_id=video_id,
                    available_resolutions=resolutions,
                    is_admin=is_admin,
                    has_active_sub=has_active_sub
                ),
                parse_mode=None
            )

        # Track quality selection session with 10-min TTL
        await selection_session_manager.register_session(
            bot=callback.bot,
            user_id=user_id,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            video_id=video_id
        )
    await callback.answer()


@router.callback_query(F.data.startswith("vdl:"))
async def cb_start_video_download(callback: CallbackQuery) -> None:
    """Check permissions, subscription quota, or trigger paywall."""
    parts = callback.data.split(":")
    video_id = parts[1]
    resolution = parts[2]

    user = callback.from_user
    user_id = user.id if user else 0

    if user and await db.is_user_blocked(user_id):
        await callback.answer("Restricted user.", show_alert=True)
        return

    from utils.selection_session import selection_session_manager
    if not await selection_session_manager.enforce_session_validity(callback, video_id):
        return
    selection_session_manager.clear_session(user_id)

    status_msg = callback.message
    if not status_msg:
        return

    # Check paywall for 720p and higher resolutions
    if resolution in ("720p", "1080p", "1440p", "2160p"):
        is_admin = user_id in ADMIN_IDS
        sub = await db.get_user_subscription(user_id)
        has_active_sub = bool(sub and sub.get("is_active") and sub.get("today_remaining", 0) > 0)

        if not is_admin and not has_active_sub:
            if sub and sub.get("is_active") and sub.get("today_remaining", 0) <= 0:
                await callback.answer("Daily video quota reached!", show_alert=True)
                plan_name = sub.get("plan_tier", "").capitalize()
                limit_num = sub.get("daily_limit", 0)
                await status_msg.edit_text(
                    f"🎬 **Aaj ka quota khatam babu bhaiya! Pura internet aaj hi download karega kya? 😂**\n\n"
                    f"You have used all **{limit_num} HD videos** for today on your {plan_name} plan.\n\n"
                    f"💡 *Midnight (12:00 AM) ko quota reset hoga!*\n"
                    f"Standard qualities (144p - 480p) aur music downloads abhi bhi completely free hain!",
                    parse_mode="Markdown"
                )
                asyncio.create_task(vanish_status_message(status_msg, delay=90.0))
                return

            price = PRICE_SINGLE_720P if resolution == "720p" else PRICE_SINGLE_1080P
            go_price = SUBSCRIPTION_PLANS.get("go", {}).get("price", 35)
            pro_price = SUBSCRIPTION_PLANS.get("pro", {}).get("price", 100)
            ultra_price = SUBSCRIPTION_PLANS.get("ultra", {}).get("price", 299)
            max_price = SUBSCRIPTION_PLANS.get("max", {}).get("price", 799)
            paywall_text = (
                f"🔒 ━━━━━━━━━━━━━━━━━━━ 🔒\n"
                f"   💎 **PREMIUM VIDEO ACCESS ({resolution})** 💎\n"
                f"🔒 ━━━━━━━━━━━━━━━━━━━ 🔒\n\n"
                f"Standard qualities (`144p` - `480p`) & music are **100% Free (Normal Tier)**.\n"
                f"To unlock **{resolution} Full HD**, choose an access pass below:\n\n"
                f"⚡ **Single Video:** `₹{price}` (Unlock this video only)\n"
                f"⚡ **Go Pass:** `₹{go_price} / 7 Days` (20 HD videos/day)\n"
                f"🚀 **Pro Pass:** `₹{pro_price} / 30 Days` (45 HD videos/day)\n"
                f"⚡ **Ultra Pass:** `₹{ultra_price} / 90 Days` (80 HD videos/day)\n"
                f"👑 **Max VIP:** `₹{max_price} / 365 Days` (150 HD videos/day + 2GB Bandwidth)\n\n"
                f"👇 *Tap a button below to get your instant UPI QR code:*"
            )
            await status_msg.edit_text(
                text=paywall_text,
                reply_markup=get_paywall_keyboard(video_id, resolution),
                parse_mode="Markdown"
            )
            await selection_session_manager.register_session(
                bot=callback.bot,
                user_id=user_id,
                chat_id=status_msg.chat.id,
                message_id=status_msg.message_id,
                video_id=video_id
            )
            await callback.answer()
            return
        elif has_active_sub and not is_admin:
            await db.use_subscription_video_quota(user_id)

    await callback.answer(f"Starting {resolution} video download...")
    await execute_video_download_pipeline(
        status_msg=status_msg,
        video_id=video_id,
        resolution=resolution,
        user_id=user_id,
        bot=callback.bot
    )


async def execute_video_download_pipeline(
    status_msg: Message,
    video_id: str,
    resolution: str,
    user_id: int,
    bot=None
) -> None:
    """Download, remux, and deliver video file with progress tracking."""
    if bot is None:
        bot = status_msg.bot

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
    anim_msg = None

    try:
        media_title = f"YouTube Video ({video_id})"
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

        # Check server storage health before accepting large jobs
        from utils.disk_cleaner import check_disk_space_available
        has_disk, _ = check_disk_space_available(min_free_mb=1000)
        if not has_disk:
            await status_msg.edit_text(
                "⚠️ **Server Storage Busy**\n\n"
                "The server's media buffer is currently handling multiple large downloads. "
                "Please retry in 1–2 minutes once active downloads complete!",
                reply_markup=get_retry_keyboard(f"vdl:{video_id}:{resolution}", f"opt_video:{video_id}"),
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
        anim_msg = await send_download_animation(bot, user_id)

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
                    f"❌ *Video Processing Failed:*\n{err or 'Unknown error occurred.'}\n\n"
                    f"Click below to retry the download or choose a different quality:",
                    reply_markup=get_retry_keyboard(retry_cb, back_cb)
                )
            return

        sent = await telegram_service.send_video_file(
            bot=bot,
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
            await vanish_status_message(status_msg, delay=0.3)
            await vanish_download_animation(anim_msg, delay=0.3)
        else:
            await vanish_download_animation(anim_msg)
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
        await vanish_download_animation(anim_msg)
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
        await vanish_download_animation(anim_msg)
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
        await vanish_download_animation(anim_msg)
        task_registry.unregister(user_id)
        if acquired_slot:
            queue_manager.release_download_slot(user_id)
        if job_dir:
            media_service.cleanup_job_dir(job_dir)
