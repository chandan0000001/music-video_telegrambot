"""
Dedicated Instagram Media & Profile Downloader Handler
Supports:
1. HD Reels, Videos, Photos, and Multi-Item Carousels (up to 15 items)
2. Stories & Highlights
3. Profile Pictures (Full HD DP) and Account Stats/Bio for Public & Private Accounts
Integrated with:
- 8/8 Global & 3 Per-User Queue Engine with VIP Priority
- 6s Anti-Spam Link Cooldown Guard
- Real-time 0-100% Download and Upload Progress Meters
- Clean Universal Back Navigation
"""
import html
import asyncio
import logging
from pathlib import Path
from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InputMediaPhoto,
    InputMediaVideo,
    FSInputFile
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from database import db
from services.instagram_service import instagram_service
from services.telegram_service import telegram_service
from keyboards.main_menu import get_back_to_main_keyboard, get_instagram_menu_keyboard
from utils.validators import (
    is_valid_instagram_url,
    extract_instagram_info,
    parse_instagram_input
)
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
from utils.animation_helper import send_download_animation, vanish_download_animation
from handlers.states import UserStates

logger = logging.getLogger(__name__)
router = Router(name="instagram_router")

INSTAGRAM_HUB_TEXT = (
    "📸 **Instagram Downloader**\n"
    "*HD Reels, Posts, Carousels, Stories, Highlights, Audio & Profile DP*\n\n"
    "Supported options:\n"
    "• **Reels & Posts:** Send any Reel, Video, or Photo link\n"
    "• **Carousels:** Full albums with all original photos & videos (no item limits)\n"
    "• **Music & Audio:** Send any Instagram Audio or Reel music link\n"
    "• **Stories & Highlights:** Send any active story or highlight link\n"
    "• **Profile DP & Bio:** Send profile link or `@username` (both public & private accounts supported)\n\n"
    "👉 *Paste your Instagram link or @username below:*"
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


async def _execute_instagram_profile(message: Message, username: str) -> None:
    """Fetch Instagram profile card and original HD DP (Profile Picture)."""
    user = message.from_user
    user_id = user.id if user else 0

    # 1. Anti-Spam Link Cooldown Check
    is_spam, spam_msg = await link_spam_guard.check_link_spam(user_id)
    if is_spam:
        await message.answer(spam_msg, parse_mode="Markdown")
        return

    # 2. Per-User Concurrent Download Limit Check
    can_start, limit_msg = await queue_manager.can_user_start_job(user_id)
    if not can_start:
        await message.answer(limit_msg, parse_mode="Markdown")
        return

    clean_user = username.strip().lstrip("@")
    status_msg = await message.answer(f"🔍 <i>Fetching Instagram profile for @{html.escape(clean_user)}...</i>", parse_mode="HTML")

    success, profile, err_msg = await instagram_service.get_instagram_profile(clean_user)
    if not success or not profile:
        await status_msg.edit_text(
            f"❌ {err_msg or f'Could not find profile for @{html.escape(clean_user)}.'}",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML"
        )
        return

    u_name = html.escape(profile.get("username") or clean_user)
    full_name = html.escape(profile.get("full_name") or clean_user)
    bio = html.escape(profile.get("biography") or "None")
    followers = profile.get("followers_count", 0)
    following = profile.get("following_count", 0)
    posts = profile.get("posts_count", 0)
    is_private = profile.get("is_private", False)
    is_verified = profile.get("is_verified", False)
    badge = " ✅" if is_verified else ""
    privacy_badge = "🔒 <b>Private Account</b>" if is_private else "🌐 <b>Public Account</b>"

    caption_lines = [
        f"📸 <b>Instagram Profile:</b> @{u_name}{badge}",
        f"👤 <b>Name:</b> {full_name}",
        f"📝 <b>Bio:</b> <i>{bio[:300]}</i>\n",
        f"👥 <b>Followers:</b> <code>{followers:,}</code> | <b>Following:</b> <code>{following:,}</code>",
        f"📦 <b>Total Posts:</b> <code>{posts:,}</code>",
        f"🔐 <b>Account Status:</b> {privacy_badge}\n"
    ]

    if is_private:
        caption_lines.append(
            "⚠️ <b>Private Account Notice:</b>\n"
            "<i>This account is private. Posts, reels, and stories cannot be downloaded without mutual follow access, but the original HD profile picture has been fetched above.</i>"
        )
    else:
        caption_lines.append(
            "💡 <i>Tip: Send any Post, Reel, Story, or Highlight link to download media!</i>"
        )

    caption = "\n".join(caption_lines)
    dp_url = profile.get("profile_pic_url_hd")

    if dp_url:
        job_dir = instagram_service._get_job_dir()
        dp_file = job_dir / f"profile_{clean_user}.jpg"
        downloaded_ok = await instagram_service.download_file(dp_url, dp_file)
        if downloaded_ok and dp_file.exists() and dp_file.stat().st_size > 0:
            try:
                await message.answer_photo(
                    photo=FSInputFile(str(dp_file)),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=get_back_to_main_keyboard()
                )
                await vanish_status_message(status_msg, delay=0.2)
                await db.record_download(
                    telegram_id=user_id,
                    media_title=f"Instagram Profile DP: @{u_name}",
                    source=f"https://instagram.com/{clean_user}",
                    media_type="instagram_profile_dp",
                    selected_quality="Original HD",
                    selected_format="jpg",
                    status="completed"
                )
                return
            except Exception as e:
                logger.error(f"Failed to send profile photo for @{clean_user}: {e}")
            finally:
                instagram_service.cleanup_job_dir(job_dir)

    # Fallback if photo couldn't be sent
    await status_msg.edit_text(
        text=caption,
        parse_mode="HTML",
        reply_markup=get_back_to_main_keyboard()
    )


async def _execute_instagram_audio(
    message: Message,
    identifier: str,
    status_msg: Message,
    url: str,
    user_id: int
) -> None:
    """Download and deliver Instagram audio track with tags and cover artwork."""
    await status_msg.edit_text("🔍 <i>Fetching Instagram audio stream...</i>", parse_mode="HTML")
    success, audio_info, err_msg = await instagram_service.get_instagram_audio(identifier)
    if not success or not audio_info:
        await status_msg.edit_text(
            f"❌ {err_msg or 'Could not retrieve audio from this link.'}",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML"
        )
        return

    track_title = audio_info.get("title", f"Instagram Audio {identifier}")
    track_artist = audio_info.get("artist", "Instagram")
    duration = audio_info.get("duration") or 0

    acquired = await queue_manager.acquire_download_slot(
        user_id=user_id,
        media_title=f"🎵 {track_title}",
        status_msg=status_msg
    )
    if not acquired:
        return

    anim_msg = await send_download_animation(message.bot, user_id)
    job_dir = instagram_service._get_job_dir()

    dl_tracker = DownloadProgressTracker(
        media_title=track_title,
        quality_label="Audio MP3",
        media_type="audio"
    )
    dl_reporter = AsyncProgressReporter(message=status_msg, tracker=dl_tracker)
    dl_reporter.start()

    try:
        ok, file_path, thumb_path, dl_err = await instagram_service.download_instagram_audio(
            audio_info=audio_info,
            job_dir=job_dir,
            tracker=dl_tracker
        )
    except Exception as e:
        logger.error(f"Error during Instagram audio download: {e}")
        ok, file_path, thumb_path, dl_err = False, None, None, str(e)
    finally:
        await dl_reporter.stop()
        queue_manager.release_download_slot(user_id)

    if not ok or not file_path or not file_path.exists():
        await vanish_download_animation(anim_msg)
        await status_msg.edit_text(
            f"❌ *Failed to download audio:* {dl_err or 'Unknown error'}",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="Markdown"
        )
        instagram_service.cleanup_job_dir(job_dir)
        return

    await queue_manager.acquire_upload_slot(user_id)
    try:
        sent = await telegram_service.send_audio_file(
            bot=message.bot,
            chat_id=message.chat.id,
            file_path=file_path,
            title=track_title,
            artist=track_artist,
            duration=duration,
            thumbnail_path=thumb_path,
            status_message=status_msg
        )
    finally:
        queue_manager.release_upload_slot(user_id)
        await vanish_download_animation(anim_msg)
        await vanish_status_message(status_msg, delay=0.2)

    await db.record_download(
        telegram_id=user_id,
        media_title=f"{track_title} - {track_artist}",
        source=url,
        media_type="instagram_audio",
        selected_quality="Original Audio",
        selected_format="mp3",
        status="completed" if sent else "failed"
    )
    instagram_service.cleanup_job_dir(job_dir)


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

    status_msg = await message.answer("🔍 *Inspecting Instagram link...*", parse_mode="Markdown")

    info_tuple = extract_instagram_info(url)
    cat = info_tuple[0] if info_tuple else "post"
    identifier = info_tuple[1] if info_tuple else ""

    if cat == "live":
        await status_msg.edit_text(
            "🔴 <b>Access Restricted: Active Live Broadcast</b>\n\n"
            "⚠️ This is an active <b>Instagram Live</b> broadcast and cannot be downloaded while in progress.\n\n"
            "<i>Once the live stream finishes and the creator saves it to their profile as a Reel or Video, send that link to download the complete replay!</i>",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML"
        )
        return

    if cat == "audio":
        await _execute_instagram_audio(message, identifier, status_msg, url, user_id)
        return

    success, media_items, err_msg = await instagram_service.get_instagram_media(url)
    if not success or not media_items:
        if err_msg and ("access restricted" in err_msg.lower() or "private category" in err_msg.lower() or "members-only" in err_msg.lower()):
            await status_msg.edit_text(
                err_msg,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML"
            )
            return

        await status_msg.edit_text(
            f"❌ {err_msg or 'Could not retrieve media from this link.'}",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="HTML"
        )
        return

    if cat == "story":
        cat_name = "Story"
    elif cat == "highlight":
        cat_name = "Highlight"
    elif cat == "reel":
        cat_name = "Reel"
    else:
        cat_name = "Post"

    media_label = "Photo" if media_items[0]["type"] == "photo" else cat_name
    title = f"Instagram {media_label}" if len(media_items) == 1 else f"Instagram Carousel ({len(media_items)} items)"

    # 3. Acquire Global Download Slot from 8/8 Queue
    acquired = await queue_manager.acquire_download_slot(
        user_id=user_id,
        media_title=title,
        status_msg=status_msg
    )
    if not acquired:
        return

    # Send companion entertainment animation/sticker
    anim_msg = await send_download_animation(message.bot, user_id)

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
        await vanish_download_animation(anim_msg)
        await status_msg.edit_text(
            "❌ *Failed to download media files. Please try again.*",
            reply_markup=get_back_to_main_keyboard(),
            parse_mode="Markdown"
        )
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
                    caption=f"📸 *Instagram {media_label} (Original HD)*",
                    reply_markup=get_back_to_main_keyboard(),
                    parse_mode="Markdown"
                )
            else:  # video / reel
                await message.answer_video(
                    video=tracked_file,
                    caption=f"🎬 *Instagram {media_label} (Original HD)*",
                    reply_markup=get_back_to_main_keyboard(),
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

        # Vanish the progress status message and animation cleanly
        await up_reporter.vanish(delay=0.3)
        await vanish_download_animation(anim_msg, delay=0.3)

    except Exception as e:
        logger.error(f"Error delivering Instagram media to user {user_id}: {e}", exc_info=True)
        await vanish_download_animation(anim_msg)
        try:
            await status_msg.edit_text(
                f"❌ *Failed to deliver media:* {str(e)[:100]}",
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="Markdown"
            )
        except Exception:
            pass
    finally:
        await vanish_download_animation(anim_msg, delay=0.1)
        await up_reporter.vanish(delay=0.1)
        queue_manager.release_upload_slot(user_id)
        instagram_service.cleanup_job_dir(job_dir)


@router.message(UserStates.waiting_for_instagram)
async def handle_instagram_waiting_input(message: Message, state: FSMContext) -> None:
    """Handle text input when in waiting_for_instagram state."""
    text = (message.text or "").strip()
    if not text:
        return

    parsed = parse_instagram_input(text)
    if parsed:
        cat, identifier = parsed
        if cat == "profile":
            await state.clear()
            await _execute_instagram_profile(message, identifier)
            return
        else:
            await state.clear()
            await _execute_instagram_download(message, text)
            return

    await message.answer(
        "⚠️ <b>Invalid Instagram Input!</b>\n\n"
        "Please provide one of the following:\n"
        "• <b>Reel or Post:</b> <code>https://instagram.com/reel/C8...</code>\n"
        "• <b>Story or Highlight:</b> <code>https://instagram.com/stories/...</code>\n"
        "• <b>Profile HD DP &amp; Bio:</b> <code>@username</code> or <code>https://instagram.com/username</code>\n\n"
        "👉 <i>Paste a valid link or @username below:</i>",
        reply_markup=get_back_to_main_keyboard(),
        parse_mode="HTML"
    )


@router.message(F.text.func(lambda text: bool(text and is_valid_instagram_url(text))))
async def handle_direct_instagram_url(message: Message, state: FSMContext) -> None:
    """Catch any direct Instagram URL pasted anywhere without prior state."""
    text = (message.text or "").strip()
    if not text:
        return

    await state.clear()
    info = extract_instagram_info(text)
    if info and info[0] == "profile":
        await _execute_instagram_profile(message, info[1])
    else:
        await _execute_instagram_download(message, text)
