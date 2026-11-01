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
from utils.validators import is_valid_youtube_url, is_safe_url, is_youtube_music_url
from handlers.states import UserStates

logger = logging.getLogger(__name__)
router = Router(name="search_router")


@router.callback_query(F.data == "menu:search")
async def cb_menu_search(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to enter music search query."""
    await state.set_state(UserStates.waiting_for_search_query)
    text = (
        "🔍 **Search Music**\n\n"
        "Please type the name of the song or artist you want to search for.\n\n"
        "💡 *Example:* `Tum Hi Ho Arijit Singh`"
    )
    if callback.message:
        try:
            await callback.message.edit_text(text, parse_mode="Markdown")
        except Exception:
            await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "menu:audio")
async def cb_menu_audio(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to submit YouTube link for audio extraction."""
    await state.set_state(UserStates.waiting_for_audio_link)
    text = (
        "🎧 **Download Audio**\n\n"
        "Please send a valid YouTube link (video or Shorts) to extract and convert audio.\n\n"
        "💡 *Example:* `https://www.youtube.com/watch?v=VIDEO_ID`"
    )
    if callback.message:
        try:
            await callback.message.edit_text(text, parse_mode="Markdown")
        except Exception:
            await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data == "menu:video")
async def cb_menu_video(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to submit YouTube link for video download."""
    await state.set_state(UserStates.waiting_for_video_link)
    text = (
        "🎬 **Download Video**\n\n"
        "Please send a valid YouTube link to download video in your desired quality.\n\n"
        "💡 *Example:* `https://www.youtube.com/watch?v=VIDEO_ID`"
    )
    if callback.message:
        try:
            await callback.message.edit_text(text, parse_mode="Markdown")
        except Exception:
            await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()


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

    # Check if input looks like a URL
    if text.startswith("http://") or text.startswith("https://"):
        # Strict YouTube & YouTube Music security validation
        if not is_valid_youtube_url(text):
            await message.answer(
                "🔒 **Security Alert: Unsupported or Restricted URL!**\n\n"
                "This bot exclusively processes authorized **YouTube** & **YouTube Music** links.\n"
                "All external, unknown, or untrusted websites (e.g. Spotify, Instagram, phishing links) are strictly blocked for security.\n\n"
                "✅ **Accepted Link Formats:**\n"
                "• `https://music.youtube.com/watch?v=VIDEO_ID` (YouTube Music)\n"
                "• `https://www.youtube.com/watch?v=VIDEO_ID` (Standard YouTube)\n"
                "• `https://youtu.be/VIDEO_ID` (Short links)\n"
                "• `https://www.youtube.com/shorts/VIDEO_ID` (YouTube Shorts)",
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
            await inspect_msg.edit_text(f"❌ **Error:** {err or 'Could not process URL.'}", parse_mode="Markdown")
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
