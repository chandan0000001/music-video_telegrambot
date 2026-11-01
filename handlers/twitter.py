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
from handlers.states import UserStates

logger = logging.getLogger(__name__)
router = Router(name="twitter_router")

TWITTER_HUB_TEXT = (
    "🐦 ━━━━━━━━━━━━━━━━━━━ 🐦\n"
    "    ✨ **X (TWITTER) DOWNLOADER** ✨\n"
    "    *Original HD Media Extractor*\n"
    "🐦 ━━━━━━━━━━━━━━━━━━━ 🐦\n\n"
    "Welcome to the dedicated **X / Twitter Downloader**!\n\n"
    "🚀 **Supported Media:**\n"
    "• 📸 **High-Res Photos:** Original uncompressed raw quality\n"
    "• 🎬 **HD Videos:** Full 720p / 1080p MP4\n"
    "• 🌀 **GIF Animations:** Native looping animations\n"
    "• 📦 **Multi-Media Albums:** Up to 15 photos/videos per post\n\n"
    "💡 *Example Links:*\n"
    "• `https://x.com/username/status/1234567890`\n"
    "• `https://twitter.com/username/status/1234567890`\n\n"
    "👉 *Paste your X / Twitter link below:*"
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
    """Core download and dispatch pipeline for X/Twitter media."""
    user = message.from_user
    user_id = user.id if user else 0

    status_msg = await message.answer("🔍 *Inspecting X (Twitter) media...*", parse_mode="Markdown")

    success, media_items, err_msg = await twitter_service.get_tweet_media(url)
    if not success or not media_items:
        await status_msg.edit_text(
            f"❌ {err_msg or 'Could not retrieve media from this post.'}",
            parse_mode="Markdown"
        )
        return

    total_count = len(media_items)
    await status_msg.edit_text(
        f"⬇️ *Downloading {total_count} media file(s) in original quality...*",
        parse_mode="Markdown"
    )

    job_dir = twitter_service._get_job_dir()

    try:
        downloaded = await twitter_service.download_all_media(media_items, job_dir)
        if not downloaded:
            await status_msg.edit_text("❌ *Failed to download media files. Please try again.*", parse_mode="Markdown")
            return

        await status_msg.edit_text("🚀 *Uploading to Telegram...*", parse_mode="Markdown")

        # Delivery logic
        if len(downloaded) == 1:
            item = downloaded[0]
            item_type = item["type"]
            f_path = item["file_path"]

            if item_type == "photo":
                await message.answer_photo(
                    photo=FSInputFile(str(f_path)),
                    caption="📸 *X (Twitter) Photo (Original HD)*",
                    parse_mode="Markdown"
                )
            elif item_type == "gif":
                await message.answer_animation(
                    animation=FSInputFile(str(f_path)),
                    caption="🌀 *X (Twitter) GIF*",
                    parse_mode="Markdown"
                )
            else:  # video
                await message.answer_video(
                    video=FSInputFile(str(f_path)),
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
                    # Add caption only on the first item of each album
                    caption = (
                        f"📸 *X (Twitter) Album ({len(chunk)} item{'s' if len(chunk) > 1 else ''})*"
                        if idx == 0 else None
                    )

                    if item_type == "photo":
                        media_group.append(
                            InputMediaPhoto(
                                media=FSInputFile(str(f_path)),
                                caption=caption,
                                parse_mode="Markdown"
                            )
                        )
                    else:  # video or gif in media group
                        media_group.append(
                            InputMediaVideo(
                                media=FSInputFile(str(f_path)),
                                caption=caption,
                                parse_mode="Markdown"
                            )
                        )

                await message.answer_media_group(media=media_group)

        # Record download in database
        await db.add_download(
            telegram_id=user_id,
            media_type="twitter_media",
            format_name="original",
            quality=f"{len(downloaded)} items",
            status="completed"
        )

        # Delete the progress status message cleanly
        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Error delivering Twitter media to user {user_id}: {e}", exc_info=True)
        try:
            await status_msg.edit_text(f"❌ *Failed to deliver media:* {str(e)[:100]}", parse_mode="Markdown")
        except Exception:
            pass
    finally:
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


@router.message(F.text)
async def handle_direct_twitter_url(message: Message, state: FSMContext) -> None:
    """Catch any direct Twitter/X URL pasted anywhere without prior state."""
    text = (message.text or "").strip()
    if not text:
        return

    if is_valid_twitter_url(text):
        await state.clear()
        await _execute_twitter_download(message, text)
