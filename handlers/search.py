"""
Music Search & YouTube Link Processing Handler
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from database import db
from services.music_service import music_service
from services.media_service import media_service
from keyboards.search_keyboard import get_search_results_keyboard, get_media_type_keyboard
from keyboards.quality_keyboard import get_youtube_music_keyboard
from keyboards.main_menu import get_back_to_main_keyboard
from utils.validators import (
    is_valid_youtube_url,
    is_safe_url,
    is_youtube_music_url,
    is_youtube_playlist_url,
    is_valid_twitter_url,
    is_valid_instagram_url,
    is_cancel_intent
)
from utils.rate_limiter import link_spam_guard
from handlers.states import UserStates

logger = logging.getLogger(__name__)
router = Router(name="search_router")


@router.callback_query(F.data == "menu:search")
async def cb_menu_search(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to enter music search query."""
    await callback.answer()
    await state.set_state(UserStates.waiting_for_search_query)
    text = (
        "🔍 **Search Music**\n\n"
        "Send any song title, artist, or album name to search.\n\n"
        "💡 *Example:* `Blinding Lights The Weeknd`\n\n"
        "👉 *Send your song name below:*"
    )
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")
        except Exception:
            await callback.message.answer(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")


@router.callback_query(F.data == "menu:audio")
async def cb_menu_audio(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to submit YouTube link for audio extraction."""
    await callback.answer()
    await state.set_state(UserStates.waiting_for_audio_link)
    text = (
        "🎧 **Download Audio**\n\n"
        "Paste any YouTube or YouTube Music link to extract audio.\n\n"
        "⚡ **Supported:** MP3 (128k-320k), M4A (AAC), Lossless WAV\n\n"
        "👉 *Paste your link below:*"
    )
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")
        except Exception:
            await callback.message.answer(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")


@router.callback_query(F.data == "menu:video")
async def cb_menu_video(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to submit YouTube link for video download."""
    await callback.answer()
    await state.set_state(UserStates.waiting_for_video_link)
    text = (
        "🎬 **Download Video**\n\n"
        "Paste any YouTube video or Shorts link to download.\n\n"
        "⚡ **Resolutions:** 144p, 240p, 360p, 480p, 720p HD, 1080p Full HD\n\n"
        "👉 *Paste your link below:*"
    )
    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")
        except Exception:
            await callback.message.answer(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")


@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext) -> None:
    """Handle /search command with optional query parameter."""
    user = message.from_user
    if user and await db.is_user_blocked(user.id):
        await message.answer("🚫 *You have been restricted from using this service.*")
        return

    command_args = message.text.partition(" ")[2].strip() if message.text else ""
    if not command_args:
        await state.set_state(UserStates.waiting_for_search_query)
        await message.answer(
            "🔍 *Please send the song title you wish to search for:*",
            parse_mode="Markdown"
        )
        return

    await execute_search(message, command_args, state)


async def execute_search(message: Message, query: str, state: FSMContext) -> None:
    """Execute music search and present top 5 results."""
    await state.clear()
    user = message.from_user
    user_id = user.id if user else 0

    if user and await db.is_user_blocked(user_id):
        await message.answer("🚫 *You have been restricted from using this service.*")
        return

    # Record search in database
    await db.record_search(telegram_id=user_id, search_query=query, source="youtube")

    status_msg = await message.answer(f"🔎 *Searching for:* `{query}`...", parse_mode="Markdown")

    results = await music_service.search_music(query, limit=5)
    if not results:
        await status_msg.edit_text(
            f"❌ *No matching music tracks found for:* `{query}`.\n\nPlease check spelling or try another title.",
            parse_mode="Markdown"
        )
        return

    result_lines = [f"🎵 **Search Results for:** `{query}`\n"]
    for idx, item in enumerate(results, start=1):
        result_lines.append(
            f"*{idx}.* **{item['title']}**\n"
            f"   👤 *Artist:* {item['artist']} | ⏱ `{item['duration_str']}`"
        )

    result_lines.append("\n👉 *Click a track below to choose format and quality:*")
    full_text = "\n".join(result_lines)

    await status_msg.edit_text(
        text=full_text,
        reply_markup=get_search_results_keyboard(results),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("sel:"))
async def cb_select_result(callback: CallbackQuery) -> None:
    """User clicked one of the search results."""
    video_id = callback.data.split(":")[1]
    text = (
        "🎯 **Selected Track**\n\n"
        "How would you like to download this media?\n\n"
        "• 🎧 **Audio:** Convert to MP3, M4A, or WAV (128 - 320 kbps)\n"
        "• 🎬 **Video:** Download as MP4 (144p - 1080p genuine qualities)"
    )
    if callback.message:
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=get_media_type_keyboard(video_id),
                parse_mode="Markdown"
            )
        except Exception:
            await callback.message.answer(
                text=text,
                reply_markup=get_media_type_keyboard(video_id),
                parse_mode="Markdown"
            )
    await callback.answer()


@router.message(F.text)
async def handle_user_text(message: Message, state: FSMContext) -> None:
    """Handle general text input, URLs, and active FSM states."""
    text = (message.text or "").strip()
    if not text:
        return

    user = message.from_user
    if user and await db.is_user_blocked(user.id):
        await message.answer("🚫 *You have been restricted from using this service.*")
        return

    # Check for cancel / dismiss commands or phrases
    if is_cancel_intent(text):
        await state.clear()
        user_name = user.first_name if user else "Music Lover"
        from handlers.start import get_welcome_text
        from keyboards.main_menu import get_main_menu_keyboard
        await message.answer(
            text=get_welcome_text(user_name),
            reply_markup=get_main_menu_keyboard(user.id if user else 0),
            parse_mode="Markdown"
        )
        return

    # Check if input looks like a URL
    if text.startswith("http://") or text.startswith("https://"):
        # Anti-Spam Link Cooldown Check
        is_spam, spam_msg = await link_spam_guard.check_link_spam(user.id if user else 0)
        if is_spam:
            await message.answer(spam_msg, parse_mode="Markdown")
            return

        if is_valid_twitter_url(text):
            await state.clear()
            from handlers.twitter import _execute_twitter_download
            await _execute_twitter_download(message, text)
            return

        if is_valid_instagram_url(text):
            await state.clear()
            from handlers.instagram import _execute_instagram_download
            await _execute_instagram_download(message, text)
            return

        # Check if URL is a YouTube Playlist link
        if is_youtube_playlist_url(text):
            await state.clear()
            await message.answer(
                "⚠️ <b>Playlists Are Currently Not Supported</b>\n\n"
                "To ensure lightning-fast downloads, fair server bandwidth, and high-speed delivery for everyone, full playlist downloading is currently disabled.\n\n"
                "💡 <i>Please send the link of an individual video or song instead!</i>",
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML"
            )
            return

        # Strict YouTube & YouTube Music security validation
        if not is_valid_youtube_url(text):
            await message.answer(
                "⚠️ **Unsupported Link**\n\n"
                "Please send a valid **YouTube**, **YouTube Music**, **X (Twitter)**, or **Instagram** link.\n\n"
                "💡 *Example:* `https://youtu.be/ID`, `https://x.com/user/status/ID` or `https://instagram.com/reel/ID`",
                parse_mode="Markdown"
            )
            return

        # It is a valid YouTube or YouTube Music URL
        await state.clear()
        
        is_yt_music = is_youtube_music_url(text)
        status_label = "🎧 *Inspecting YouTube Music track...*" if is_yt_music else "🔍 *Inspecting media availability...*"
        inspect_msg = await message.answer(status_label, parse_mode="Markdown")

        success, info, err = await media_service.get_media_info(text)

        if not success or not info:
            if err and ("access restricted" in err.lower() or "access notice" in err.lower()):
                await inspect_msg.edit_text(
                    err,
                    reply_markup=get_back_to_main_keyboard(),
                    parse_mode="HTML"
                )
            else:
                await inspect_msg.edit_text(
                    f"❌ **Error:** {err or 'Could not process URL.'}",
                    reply_markup=get_back_to_main_keyboard(),
                    parse_mode="Markdown"
                )
            return

        video_id = info["id"]

        if is_yt_music:
            # Dedicated YouTube Music Fast-Download Interface
            desc = (
                f"🎧 **YouTube Music Track**\n\n"
                f"🎵 **Title:** `{info['title']}`\n"
                f"👤 **Artist:** {info['artist']}\n"
                f"⏱ **Duration:** `{info['duration_str']}`\n\n"
                f"⚡ **Tap a quality below to start downloading instantly:**"
            )
            await inspect_msg.edit_text(
                text=desc,
                reply_markup=get_youtube_music_keyboard(video_id),
                parse_mode="Markdown"
            )
        else:
            # Standard YouTube Interface
            desc = (
                f"🎬 **{info['title']}**\n"
                f"👤 *Channel:* {info['artist']}\n"
                f"⏱ *Duration:* `{info['duration_str']}`\n\n"
                f"Select your desired download type below:"
            )
            await inspect_msg.edit_text(
                text=desc,
                reply_markup=get_media_type_keyboard(video_id),
                parse_mode="Markdown"
            )
        return

    # Current FSM state check
    curr_state = await state.get_state()
    if curr_state == UserStates.waiting_for_search_query:
        await execute_search(message, text, state)
        return
    elif curr_state in (UserStates.waiting_for_audio_link, UserStates.waiting_for_video_link):
        await message.answer(
            "⚠️ Please provide a valid YouTube URL starting with `https://`.\n"
            "Or click `/start` to return to the main menu.",
            parse_mode="Markdown"
        )
        return

    # If no state is active, treat plain text as a music search query!
    await execute_search(message, text, state)
