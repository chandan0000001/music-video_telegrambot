"""
Dedicated Instagram Media Downloader Handler
Supports HD Reels, Videos, Photos, and Multi-Item Carousels from public Instagram posts.
Integrated with:
1. 8/8 Global & 3 Per-User Queue Engine
2. 5-8s Anti-Spam Link Cooldown Guard
3. Real-time 0-100% Download and Upload Progress Meters
"""
import asyncio
import logging
from pathlib import Path
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InputMediaPhoto,
    InputMediaVideo
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from database import db
from services.instagram_service import instagram_service
from keyboards.main_menu import get_back_to_main_keyboard, get_instagram_menu_keyboard
from utils.validators import is_valid_instagram_url
from utils.rate_limiter import link_spam_guard
from utils.queue_manager import queue_manager
from utils.progress import (
    DownloadProgressTracker,
    UploadProgressTracker,
    AsyncProgressReporter,
    AsyncUploadProgressReporter,
    TrackedFSInputFile
)
from handlers.states import UserStates

logger = logging.getLogger(__name__)
router = Router(name="instagram_router")

INSTAGRAM_HUB_TEXT = (
    "📸 **Instagram Downloader**\n"
    "*Original HD Reels, Videos, Photos & Carousels*\n\n"
    "Paste any public Instagram post or Reel link below to download original media.\n\n"
    "👉 *Paste your Instagram link below:*"
)


@router.callback_query(F.data == "menu:instagram")
async def cb_menu_instagram(callback: CallbackQuery, state: FSMContext) -> None:
    """Open dedicated Instagram Downloader hub."""
    await callback.answer()
    user = callback.from_user
    if user and await db.is_user_blocked(user.id):
        await callback.answer("🚫 You are restricted from using this service.", show_alert=True)
        return

    await state.set_state(UserStates.waiting_for_instagram)
    if callback.message:
        try:
            await callback.message.edit_text(
                text=INSTAGRAM_HUB_TEXT,
                reply_markup=get_instagram_menu_keyboard(),
                parse_mode="Markdown"
            )
        except Exception:
            await callback.message.answer(
                text=INSTAGRAM_HUB_TEXT,
                reply_markup=get_instagram_menu_keyboard(),
                parse_mode="Markdown"
            )


@router.message(Command("instagram"))
@router.message(Command("ig"))
async def cmd_instagram(message: Message, state: FSMContext) -> None:
    """Handle /instagram or /ig command."""
    user = message.from_user
    if user and await db.is_user_blocked(user.id):
        await message.answer("🚫 *You have been restricted from using this service.*")
        return

    await state.set_state(UserStates.waiting_for_instagram)
    await message.answer(
        text=INSTAGRAM_HUB_TEXT,
        reply_markup=get_instagram_menu_keyboard(),
        parse_mode="Markdown"
    )


async def _execute_instagram_download(message: Message, url: str) -> None:
    """Core download and dispatch pipeline for Instagram media with queue and live meters."""
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

    status_msg = await message.answer("🔍 *Inspecting Instagram post...*", parse_mode="Markdown")

    success, media_items, err_msg = await instagram_service.get_instagram_media(url)
    if not success or not media_items:
        await status_msg.edit_text(
            f"❌ {err_msg or 'Could not retrieve media from this post.'}",
            parse_mode="Markdown"
        )
        return

    media_label = "Photo" if media_items[0]["type"] == "photo" else "Reel/Video"
    title = f"Instagram {media_label}" if len(media_items) == 1 else f"Instagram Carousel ({len(media_items)} items)"

    # 3. Acquire Global Download Slot from 8/8 Queue
    acquired = await queue_manager.acquire_download_slot(
        user_id=user_id,
        media_title=title,
        status_msg=status_msg
    )
    if not acquired:
        return

    job_dir = instagram_service._get_job_dir()

    # 4. Live Download Progress Meter
    dl_tracker = DownloadProgressTracker(
        media_title=title,
        quality_label="Original HD",
        media_type=media_items[0]["type"] if len(media_items) == 1 else "video"
    )
    dl_reporter = AsyncProgressReporter(message=status_msg, tracker=dl_tracker)
    dl_reporter.start()

    try:
        downloaded = await instagram_service.download_all_media(media_items, job_dir, tracker=dl_tracker)
    except Exception as e:
        logger.error(f"Error during Instagram media download: {e}")
        downloaded = []
    finally:
        await dl_reporter.stop()
        queue_manager.release_download_slot(user_id)

    if not downloaded:
        await status_msg.edit_text("❌ *Failed to download media files. Please try again.*", parse_mode="Markdown")
        instagram_service.cleanup_job_dir(job_dir)
        return

    # 5. Acquire Global Upload Slot from 8/8 Queue
    await queue_manager.acquire_upload_slot(user_id)

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
                    caption="📸 *Instagram Photo (Original HD)*",
                    parse_mode="Markdown"
                )
            else:  # video / reel
                await message.answer_video(
                    video=tracked_file,
                    caption="🎬 *Instagram Reel / Video (Original HD)*",
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
                    caption = (
                        f"📸 *Instagram Carousel ({len(chunk)} item{'s' if len(chunk) > 1 else ''})*"
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
                    else:
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
            media_type="instagram_media",
            selected_quality="Original HD",
            selected_format="original",
            status="completed"
        )

        try:
            await asyncio.sleep(0.6)
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Error delivering Instagram media to user {user_id}: {e}", exc_info=True)
        try:
            await status_msg.edit_text(f"❌ *Failed to deliver media:* {str(e)[:100]}", parse_mode="Markdown")
        except Exception:
            pass
    finally:
        await up_reporter.stop()
        queue_manager.release_upload_slot(user_id)
        instagram_service.cleanup_job_dir(job_dir)


@router.message(UserStates.waiting_for_instagram)
async def handle_instagram_waiting_input(message: Message, state: FSMContext) -> None:
    """Handle text input when in waiting_for_instagram state."""
    text = (message.text or "").strip()
    if not text:
        return

    if is_valid_instagram_url(text):
        await state.clear()
        await _execute_instagram_download(message, text)
    else:
        await message.answer(
            "⚠️ *Invalid Instagram URL!*\n\n"
            "Please paste a valid link to an Instagram Reel or Post.\n\n"
            "💡 *Example:* `https://www.instagram.com/reel/C8...`",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="Markdown"
        )


@router.message(F.text.func(lambda text: bool(text and is_valid_instagram_url(text))))
async def handle_direct_instagram_url(message: Message, state: FSMContext) -> None:
    """Catch any direct Instagram URL pasted anywhere without prior state."""
    text = (message.text or "").strip()
    if not text:
        return

    await state.clear()
    await _execute_instagram_download(message, text)
