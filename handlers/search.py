"""
Music Search & YouTube Link Processing Handler
"""
import html
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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
            sent_msg = await callback.message.edit_text(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")
        except Exception:
            sent_msg = await callback.message.answer(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")
        from utils.selection_session import selection_session_manager
        await selection_session_manager.register_session(
            bot=callback.bot,
            user_id=callback.from_user.id if callback.from_user else 0,
            chat_id=callback.message.chat.id,
            message_id=sent_msg.message_id if sent_msg else callback.message.message_id
        )


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
            sent_msg = await callback.message.edit_text(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")
        except Exception:
            sent_msg = await callback.message.answer(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")
        from utils.selection_session import selection_session_manager
        await selection_session_manager.register_session(
            bot=callback.bot,
            user_id=callback.from_user.id if callback.from_user else 0,
            chat_id=callback.message.chat.id,
            message_id=sent_msg.message_id if sent_msg else callback.message.message_id
        )


@router.callback_query(F.data == "menu:video")
async def cb_menu_video(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to submit YouTube link for video download."""
    await callback.answer()
    await state.set_state(UserStates.waiting_for_video_link)
    text = (
        "▶️ **YouTube Video Downloader**\n\n"
        "Paste any YouTube video or Shorts link to download.\n\n"
        "⚡ **Resolutions:** 144p, 240p, 360p, 480p, 720p HD, 1080p Full HD\n\n"
        "👉 *Paste your link below:*"
    )
    if callback.message:
        try:
            sent_msg = await callback.message.edit_text(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")
        except Exception:
            sent_msg = await callback.message.answer(text, reply_markup=get_back_to_main_keyboard(), parse_mode="Markdown")
        from utils.selection_session import selection_session_manager
        await selection_session_manager.register_session(
            bot=callback.bot,
            user_id=callback.from_user.id if callback.from_user else 0,
            chat_id=callback.message.chat.id,
            message_id=sent_msg.message_id if sent_msg else callback.message.message_id
        )



@router.message(Command("search", "song"))
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


async def execute_search(
    message: Message,
    query: str,
    state: FSMContext,
    user_id: Optional[int] = None,
    edit_message: bool = False
) -> None:
    """Execute music search and present top 5 results."""
    await state.clear()
    if user_id is None:
        user = message.from_user
        user_id = user.id if user else 0

    if user_id and await db.is_user_blocked(user_id):
        await message.answer("🚫 *You have been restricted from using this service.*")
        return

    # Record search in database using the actual human user_id
    if user_id:
        try:
            await db.record_search(telegram_id=user_id, search_query=query, source="youtube")
        except Exception as e:
            logger.debug(f"Could not record search for user {user_id}: {e}")

    safe_query = html.escape(query)
    if edit_message:
        status_msg = message
        try:
            await status_msg.edit_text(f"🔎 <b>Searching for:</b> <code>{safe_query}</code>...", parse_mode="HTML")
        except Exception:
            try:
                status_msg = await message.answer(f"🔎 Searching for: {query}...", parse_mode=None)
            except Exception:
                status_msg = message
    else:
        try:
            status_msg = await message.answer(f"🔎 <b>Searching for:</b> <code>{safe_query}</code>...", parse_mode="HTML")
        except Exception:
            status_msg = await message.answer(f"🔎 Searching for: {query}...", parse_mode=None)

    results = await music_service.search_music(query, limit=5)
    if not results:
        try:
            await status_msg.edit_text(
                f"❌ <b>No matching music tracks found for:</b> <code>{safe_query}</code>.\n\nPlease check spelling or try another title.",
                parse_mode="HTML"
            )
        except Exception:
            await status_msg.edit_text(
                f"❌ No matching music tracks found for: {query}.\n\nPlease check spelling or try another title.",
                parse_mode=None
            )
        return

    result_lines = [f"🎵 <b>Search Results for:</b> <code>{safe_query}</code>\n"]
    for idx, item in enumerate(results, start=1):
        s_title = html.escape(item.get("title", "Unknown Title"))
        s_artist = html.escape(item.get("artist", "Unknown Artist"))
        s_dur = html.escape(item.get("duration_str", "00:00"))
        result_lines.append(
            f"<b>{idx}.</b> <b>{s_title}</b>\n"
            f"   👤 <i>Artist:</i> {s_artist} | ⏱ <code>{s_dur}</code>"
        )

    result_lines.append("\n👉 <i>Click a track below to choose format and quality:</i>")
    full_text = "\n".join(result_lines)

    try:
        await status_msg.edit_text(
            text=full_text,
            reply_markup=get_search_results_keyboard(results),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Failed to edit search results with HTML: {e}. Falling back to plain text.")
        plain_lines = [f"🎵 Search Results for: {query}\n"]
        for idx, item in enumerate(results, start=1):
            plain_lines.append(
                f"{idx}. {item.get('title', 'Unknown Title')}\n"
                f"   Artist: {item.get('artist', 'Unknown Artist')} | Duration: {item.get('duration_str', '00:00')}"
            )
        plain_lines.append("\nClick a track below to choose format and quality:")
        await status_msg.edit_text(
            text="\n".join(plain_lines),
            reply_markup=get_search_results_keyboard(results),
            parse_mode=None
        )
    from utils.selection_session import selection_session_manager
    chat_id = getattr(getattr(status_msg, "chat", None), "id", user_id)
    await selection_session_manager.register_session(
        bot=status_msg.bot,
        user_id=user_id,
        chat_id=chat_id,
        message_id=getattr(status_msg, "message_id", None)
    )


@router.callback_query(F.data.startswith("sel:"))
async def cb_select_result(callback: CallbackQuery) -> None:
    """User clicked one of the search results."""
    video_id = callback.data.split(":")[1]
    from utils.selection_session import selection_session_manager
    if not await selection_session_manager.enforce_session_validity(callback, video_id):
        return

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
        user_id = callback.from_user.id if callback.from_user else 0
        await selection_session_manager.register_session(
            bot=callback.bot,
            user_id=user_id,
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            video_id=video_id
        )
    await callback.answer()


@router.callback_query(F.data.startswith("dosearch:"))
async def cb_dosearch(callback: CallbackQuery, state: FSMContext) -> None:
    """User confirmed search query from plain text suggestion."""
    from utils.selection_session import selection_session_manager
    if not await selection_session_manager.enforce_session_validity(callback):
        return

    query = callback.data.partition(":")[2]
    await callback.answer()
    user_id = callback.from_user.id if callback.from_user else 0
    if callback.message and query:
        await execute_search(callback.message, query, state, user_id=user_id, edit_message=True)


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

    # Guard against unhandled or mistyped slash commands (e.g. /sub_active typos)
    if text.startswith("/"):
        cmd_name = text.split()[0]
        await message.answer(
            f"❓ **Unknown command:** `{cmd_name}`\n\n"
            "💡 Type `/help` to view all available commands or `/start` to open the Main Menu.",
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
            await _execute_twitter_download(message, text, check_spam=False)
            return

        if is_valid_instagram_url(text):
            await state.clear()
            from handlers.instagram import _execute_instagram_download
            await _execute_instagram_download(message, text, check_spam=False)
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
                "Jaannii gharr akke marunga valid link bhejj 😂👊\n\n"
                "💡 Sirf **YouTube**, **Instagram**, ya **X (Twitter)** ke links bhejo!",
                parse_mode="Markdown"
            )
            return

        # It is a valid YouTube or YouTube Music URL
        await state.clear()
        
        is_yt_music = is_youtube_music_url(text)
        status_label = "🎧 <b>Inspecting YouTube Music track...</b>" if is_yt_music else "🔍 <b>Inspecting media availability...</b>"
        inspect_msg = await message.answer(status_label, parse_mode="HTML")

        try:
            success, info, err = await media_service.get_media_info(text)
        except Exception as e:
            logger.exception(f"Unexpected error in get_media_info for {text}: {e}")
            success, info, err = False, None, f"Could not inspect media: {str(e)[:100]}"

        if not success or not info:
            if err and ("access restricted" in err.lower() or "access notice" in err.lower()):
                await inspect_msg.edit_text(
                    err,
                    reply_markup=get_back_to_main_keyboard(),
                    parse_mode="HTML"
                )
            else:
                await inspect_msg.edit_text(
                    f"❌ <b>Error:</b> {html.escape(err or 'Could not process URL.')}\n\n"
                    f"👉 Hit <b>/start</b> to begin again or send another link.",
                    reply_markup=get_back_to_main_keyboard(),
                    parse_mode="HTML"
                )
            return

        video_id = info["id"]
        safe_title = html.escape(info.get("title", "Unknown Title"))
        safe_artist = html.escape(info.get("artist", "Unknown Artist"))
        safe_duration = html.escape(info.get("duration_str", "00:00"))

        if is_yt_music:
            # Dedicated YouTube Music Fast-Download Interface
            desc = (
                f"🎧 <b>YouTube Music Track</b>\n\n"
                f"🎵 <b>Title:</b> <code>{safe_title}</code>\n"
                f"👤 <b>Artist:</b> {safe_artist}\n"
                f"⏱ <b>Duration:</b> <code>{safe_duration}</code>\n\n"
                f"⚡ <b>Tap a quality below to start downloading instantly:</b>"
            )
            keyboard = get_youtube_music_keyboard(video_id)
        else:
            # Standard YouTube Interface
            desc = (
                f"🎬 <b>{safe_title}</b>\n"
                f"👤 <i>Channel:</i> {safe_artist}\n"
                f"⏱ <i>Duration:</i> <code>{safe_duration}</code>\n\n"
                f"Select your desired download type below:"
            )
            keyboard = get_media_type_keyboard(video_id)

        try:
            await inspect_msg.edit_text(
                text=desc,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Failed to edit inspect_msg with HTML: {e}. Falling back to plain text.")
            plain_desc = (
                f"🎬 {info.get('title', 'Unknown Title')}\n"
                f"👤 Channel: {info.get('artist', 'Unknown Artist')}\n"
                f"⏱ Duration: {info.get('duration_str', '00:00')}\n\n"
                f"Select your desired download type below:"
            )
            await inspect_msg.edit_text(
                text=plain_desc,
                reply_markup=keyboard,
                parse_mode=None
            )

        # Track quality selection session with 10-min TTL and link supersession
        from utils.selection_session import selection_session_manager
        await selection_session_manager.register_session(
            bot=message.bot,
            user_id=user.id if user else 0,
            chat_id=message.chat.id,
            message_id=inspect_msg.message_id,
            video_id=video_id
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

    # If no state is active, offer a 1-tap search button rather than blindly executing an unrequested YouTube search
    clean_query = text[:60]
    suggest_msg = await message.answer(
        f"🎵 **Looking for music?**\n\n"
        f"Would you like to search for `\"{clean_query}\"`?\n\n"
        "💡 *Click below to search or send any YouTube / Instagram / X link directly:*",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"🔍 Search \"{clean_query[:25]}\"", callback_data=f"dosearch:{clean_query[:50]}")],
                [InlineKeyboardButton(text="🔙 Main Menu", callback_data="menu:main")]
            ]
        ),
        parse_mode="Markdown"
    )
    from utils.selection_session import selection_session_manager
    chat_id = getattr(getattr(message, "chat", None), "id", getattr(user, "id", 0)) if user else 0
    await selection_session_manager.register_session(
        bot=message.bot,
        user_id=user.id if user else 0,
        chat_id=chat_id,
        message_id=getattr(suggest_msg, "message_id", None)
    )
