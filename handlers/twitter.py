"""
Dedicated X (Twitter) Media Downloader Handler
Supports original high-res photos, videos, and GIFs from public X/Twitter posts.
"""
import logging
from pathlib import Path
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    FSInputFile,
    InputMediaPhoto,
    InputMediaVideo
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from database import db
from services.twitter_service import twitter_service
from keyboards.main_menu import get_twitter_menu_keyboard, get_back_to_main_keyboard
from utils.validators import is_valid_twitter_url
from utils.rate_limiter import link_spam_guard
from utils.queue_manager import queue_manager
from utils.progress import (
    DownloadProgressTracker,
    UploadProgressTracker,
    AsyncProgressReporter,
    AsyncUploadProgressReporter,
    TrackedFSInputFile,
    vanish_status_message
)
from handlers.states import UserStates

logger = logging.getLogger(__name__)
router = Router(name="twitter_router")

TWITTER_HUB_TEXT = (
    "🐦 **X (Twitter) Downloader**\n"
    "*Original HD Videos, Photos & GIFs*\n\n"
    "Paste any public X or Twitter post link below to download original uncompressed media.\n\n"
    "👉 *Paste your X link below:*"
)


@router.callback_query(F.data == "menu:twitter")
async def cb_menu_twitter(callback: CallbackQuery, state: FSMContext) -> None:
    """Open dedicated X (Twitter) Downloader hub."""
    user = callback.from_user
    if user and await db.is_user_blocked(user.id):
        await callback.answer("🚫 You are restricted from using this service.", show_alert=True)
        return

    await state.set_state(UserStates.waiting_for_twitter)
    if callback.message:
        try:
            await callback.message.edit_text(
                text=TWITTER_HUB_TEXT,
                reply_markup=get_twitter_menu_keyboard(),
                parse_mode="Markdown"
            )
        except Exception:
            await callback.message.answer(
                text=TWITTER_HUB_TEXT,
                reply_markup=get_twitter_menu_keyboard(),
                parse_mode="Markdown"
            )
    await callback.answer()


@router.message(Command("twitter"))
@router.message(Command("x"))
async def cmd_twitter(message: Message, state: FSMContext) -> None:
    """Handle /twitter or /x command."""
    user = message.from_user
    if user and await db.is_user_blocked(user.id):
        await message.answer("🚫 *You have been restricted from using this service.*")
        return

    await state.set_state(UserStates.waiting_for_twitter)
    await message.answer(
        text=TWITTER_HUB_TEXT,
        reply_markup=get_twitter_menu_keyboard(),
        parse_mode="Markdown"
    )


async def _execute_twitter_download(message: Message, url: str) -> None:
    """Core download and dispatch pipeline for X/Twitter media with live download and upload meters."""
    user = message.from_user
    user_id = user.id if user else 0

    # 1. Anti-Spam Link Cooldown Check
    is_spam, spam_msg = await link_spam_guard.check_link_spam(user_id)
    if is_spam:
        await message.answer(spam_msg, parse_mode="Markdown")
        return

    # 2. Per-User Concurrent Download Limit Check (Max 3)
    can_start, limit_msg = await queue_manager.can_user_start_job(user_id)
    if not can_start:
        await message.answer(limit_msg, parse_mode="Markdown")
        return

    status_msg = await message.answer("🔍 *Inspecting X (Twitter) media...*", parse_mode="Markdown")

    success, media_items, err_msg = await twitter_service.get_tweet_media(url)
    if not success or not media_items:
        if err_msg and ("access restricted" in err_msg.lower() or "private category" in err_msg.lower() or "members-only" in err_msg.lower()):
            await status_msg.edit_text(
                err_msg,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML"
            )
            return

        await status_msg.edit_text(
            f"❌ {err_msg or 'Could not retrieve media from this post.'}",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML"
        )
        return

    media_label = "Photo" if media_items[0]["type"] == "photo" else "Video"
    title = f"X / Twitter {media_label}" if len(media_items) == 1 else f"X Album ({len(media_items)} items)"

    # 3. Acquire Global Download Slot from 8/8 Queue
    acquired = await queue_manager.acquire_download_slot(
        user_id=user_id,
        media_title=title,
        status_msg=status_msg
    )
    if not acquired:
        return

    job_dir = twitter_service._get_job_dir()

    # 4. LIVE DOWNLOAD PROGRESS METER
    dl_tracker = DownloadProgressTracker(
        media_title=title,
        quality_label="Original HD",
        media_type=media_items[0]["type"] if len(media_items) == 1 else "video"
    )
    dl_reporter = AsyncProgressReporter(message=status_msg, tracker=dl_tracker)
    dl_reporter.start()

    try:
        downloaded = await twitter_service.download_all_media(media_items, job_dir, tracker=dl_tracker)
    except Exception as e:
        logger.error(f"Error during Twitter media download: {e}")
        downloaded = []
    finally:
        await dl_reporter.stop()
        queue_manager.release_download_slot(user_id)

    if not downloaded:
        await status_msg.edit_text("❌ *Failed to download media files. Please try again.*", parse_mode="Markdown")
        twitter_service.cleanup_job_dir(job_dir)
        return

    # 5. Acquire Global Upload Slot from 8/8 Queue
    await queue_manager.acquire_upload_slot(user_id)

    # 6. LIVE UPLOAD PROGRESS METER
    total_upload_bytes = sum(item["file_path"].stat().st_size for item in downloaded if item.get("file_path"))
    up_tracker = UploadProgressTracker(
        media_title=title,
        total_bytes=max(1, total_upload_bytes),
        media_type=downloaded[0]["type"] if len(downloaded) == 1 else "video"
    )
    up_reporter = AsyncUploadProgressReporter(message=status_msg, tracker=up_tracker)
    up_reporter.start()

    try:
        # Delivery logic
        if len(downloaded) == 1:
            item = downloaded[0]
            item_type = item["type"]
            f_path = item["file_path"]

            tracked_file = TrackedFSInputFile(str(f_path), tracker=up_tracker)

            if item_type == "photo":
                await message.answer_photo(
                    photo=tracked_file,
                    caption="📸 *X (Twitter) Photo (Original HD)*",
                    parse_mode="Markdown"
                )
            elif item_type == "gif":
                await message.answer_animation(
                    animation=tracked_file,
                    caption="🌀 *X (Twitter) GIF*",
                    parse_mode="Markdown"
                )
            else:  # video
                await message.answer_video(
                    video=tracked_file,
                    caption="🎬 *X (Twitter) Video (Original HD)*",
                    parse_mode="Markdown"
                )
        else:
            # Group into albums (Telegram limit: max 10 per media group)
            chunks = [downloaded[i:i + 10] for i in range(0, len(downloaded), 10)]
            for chunk_idx, chunk in enumerate(chunks):
                media_group = []
                for idx, item in enumerate(chunk):
                    f_path = item["file_path"]
                    item_type = item["type"]
                    tracked_file = TrackedFSInputFile(str(f_path), tracker=up_tracker)
                    # Add caption only on the first item of each album
                    caption = (
                        f"📸 *X (Twitter) Album ({len(chunk)} item{'s' if len(chunk) > 1 else ''})*"
                        if idx == 0 else None
                    )

                    if item_type == "photo":
                        media_group.append(
                            InputMediaPhoto(
                                media=tracked_file,
                                caption=caption,
                                parse_mode="Markdown"
                            )
                        )
                    else:  # video or gif in media group
                        media_group.append(
                            InputMediaVideo(
                                media=tracked_file,
                                caption=caption,
                                parse_mode="Markdown"
                            )
                        )

                await message.answer_media_group(media=media_group)

        # Record download in database
        await db.record_download(
            telegram_id=user_id,
            media_title=title,
            source=url,
            media_type="twitter_media",
            selected_quality="Original HD",
            selected_format="original",
            status="completed"
        )

        # Vanish the progress status message cleanly
        await up_reporter.vanish(delay=0.3)

    except Exception as e:
        logger.error(f"Error delivering Twitter media to user {user_id}: {e}", exc_info=True)
        try:
            await status_msg.edit_text(f"❌ *Failed to deliver media:* {str(e)[:100]}", parse_mode="Markdown")
        except Exception:
            pass
    finally:
        await up_reporter.vanish(delay=0.1)
        queue_manager.release_upload_slot(user_id)
        twitter_service.cleanup_job_dir(job_dir)


@router.message(UserStates.waiting_for_twitter)
async def handle_twitter_waiting_input(message: Message, state: FSMContext) -> None:
    """Handle text input when in waiting_for_twitter state."""
    text = (message.text or "").strip()
    if not text:
        return

    if is_valid_twitter_url(text):
        await state.clear()
        await _execute_twitter_download(message, text)
    else:
        await message.answer(
            "⚠️ *Invalid X (Twitter) URL!*\n\n"
            "Please paste a valid link to an X/Twitter post.\n\n"
            "💡 *Example:* `https://x.com/username/status/1234567890`",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="Markdown"
        )


@router.message(F.text.func(lambda text: bool(text and is_valid_twitter_url(text))))
async def handle_direct_twitter_url(message: Message, state: FSMContext) -> None:
    """Catch any direct Twitter/X URL pasted anywhere without prior state."""
    text = (message.text or "").strip()
    if not text:
        return

    await state.clear()
    await _execute_twitter_download(message, text)
