"""
Dedicated YouTube Music Studio Downloader Router
Provides dedicated 1-tap audio downloading for music.youtube.com and high-fidelity music search.
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from database import db
from services.music_service import music_service
from services.media_service import media_service
from keyboards.quality_keyboard import get_youtube_music_keyboard
from keyboards.main_menu import get_ytmusic_menu_keyboard, get_back_to_main_keyboard
from keyboards.search_keyboard import get_search_results_keyboard
from utils.validators import is_valid_youtube_url, is_youtube_music_url, is_youtube_playlist_url, is_cancel_intent
from handlers.states import UserStates

logger = logging.getLogger(__name__)
router = Router(name="ytmusic_router")

YTMUSIC_HUB_TEXT = (
    "▶️ **YouTube Music Studio**\n"
    "*1-Tap Studio Audio (320 kbps MP3 & M4A)*\n\n"
    "Send any `music.youtube.com` track link or song name to download.\n\n"
    "👉 *Paste link or send song name below:*"
)


@router.callback_query(F.data == "menu:ytmusic")
async def cb_menu_ytmusic(callback: CallbackQuery, state: FSMContext) -> None:
    """Open dedicated YouTube Music Downloader screen."""
    user = callback.from_user
    if user and await db.is_user_blocked(user.id):
        await callback.answer("🚫 You are restricted from using this service.", show_alert=True)
        return

    await state.set_state(UserStates.waiting_for_ytmusic)
    if callback.message:
        sent_msg = None
        try:
            sent_msg = await callback.message.edit_text(
                text=YTMUSIC_HUB_TEXT,
                reply_markup=get_ytmusic_menu_keyboard(),
                parse_mode="Markdown"
            )
        except Exception:
            sent_msg = await callback.message.answer(
                text=YTMUSIC_HUB_TEXT,
                reply_markup=get_ytmusic_menu_keyboard(),
                parse_mode="Markdown"
            )
        from utils.selection_session import selection_session_manager
        await selection_session_manager.register_session(
            bot=callback.bot,
            user_id=user.id if user else 0,
            chat_id=callback.message.chat.id,
            message_id=sent_msg.message_id if sent_msg else callback.message.message_id
        )
    await callback.answer()


@router.message(Command("ytmusic"))
@router.message(Command("music"))
async def cmd_ytmusic(message: Message, state: FSMContext) -> None:
    """Handle /ytmusic or /music command."""
    user = message.from_user
    if user and await db.is_user_blocked(user.id):
        await message.answer("🚫 *You have been restricted from using this service.*")
        return

    command_args = message.text.partition(" ")[2].strip() if message.text else ""
    if not command_args:
        await state.set_state(UserStates.waiting_for_ytmusic)
        sent = await message.answer(
            text=YTMUSIC_HUB_TEXT,
            reply_markup=get_ytmusic_menu_keyboard(),
            parse_mode="Markdown"
        )
        from utils.selection_session import selection_session_manager
        chat_id = getattr(getattr(message, "chat", None), "id", getattr(user, "id", 0))
        await selection_session_manager.register_session(
            bot=message.bot,
            user_id=user.id if user else 0,
            chat_id=chat_id,
            message_id=getattr(sent, "message_id", None)
        )
        return

    await process_ytmusic_input(message, command_args, state)


@router.message(UserStates.waiting_for_ytmusic)
async def handle_ytmusic_state_input(message: Message, state: FSMContext) -> None:
    """Handle text or URL input specifically when in YouTube Music mode."""
    text = (message.text or "").strip()
    if not text:
        return

    user = message.from_user
    if user and await db.is_user_blocked(user.id):
        await message.answer("🚫 *You have been restricted from using this service.*")
        return

    await process_ytmusic_input(message, text, state)


async def process_ytmusic_input(message: Message, text: str, state: FSMContext) -> None:
    """Common processing for YouTube Music input (URL or search query)."""
    user = message.from_user
    user_id = user.id if user else 0

    # Check for cancel / dismiss commands or phrases
    if is_cancel_intent(text):
        await state.clear()
        user_name = user.first_name if user else "Music Lover"
        from handlers.start import get_welcome_text
        from keyboards.main_menu import get_main_menu_keyboard
        await message.answer(
            text=get_welcome_text(user_name),
            reply_markup=get_main_menu_keyboard(user_id),
            parse_mode="Markdown"
        )
        return

    # Case 1: Input is a URL
    if text.startswith("http://") or text.startswith("https://"):
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

        await state.clear()
        inspect_msg = await message.answer(
            "🎧 <b>Inspecting YouTube Music track & audio streams...</b>",
            parse_mode="HTML"
        )

        success, info, err = await media_service.get_media_info(text)
        if not success or not info:
            safe_err = html.escape(err or "Could not process YouTube Music URL.")
            try:
                await inspect_msg.edit_text(
                    f"❌ <b>Error:</b> {safe_err}",
                    parse_mode="HTML"
                )
            except Exception:
                await inspect_msg.edit_text(
                    f"❌ Error: {err or 'Could not process YouTube Music URL.'}",
                    parse_mode=None
                )
            return

        video_id = info["id"]
        safe_title = html.escape(info.get("title", "Unknown Title"))
        safe_artist = html.escape(info.get("artist", "Unknown Artist"))
        safe_dur = html.escape(info.get("duration_str", "00:00"))

        desc = (
            f"🎧 <b>{safe_title}</b>\n"
            f"👤 <i>Artist:</i> {safe_artist} | ⏱ <code>{safe_dur}</code>\n\n"
            f"⚡ <i>Select studio quality to download instantly:</i>"
        )
        try:
            await inspect_msg.edit_text(
                text=desc,
                reply_markup=get_youtube_music_keyboard(video_id),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Failed to edit ytmusic inspect_msg with HTML: {e}. Falling back to plain text.")
            plain_desc = (
                f"🎧 {info.get('title', 'Unknown Title')}\n"
                f"👤 Artist: {info.get('artist', 'Unknown Artist')} | ⏱ {info.get('duration_str', '00:00')}\n\n"
                f"Select studio quality to download instantly:"
            )
            await inspect_msg.edit_text(
                text=plain_desc,
                reply_markup=get_youtube_music_keyboard(video_id),
                parse_mode=None
            )

        from utils.selection_session import selection_session_manager
        chat_id = getattr(getattr(message, "chat", None), "id", user_id)
        await selection_session_manager.register_session(
            bot=message.bot,
            user_id=user_id,
            chat_id=chat_id,
            message_id=getattr(inspect_msg, "message_id", None),
            video_id=video_id
        )
        return

    # Case 2: Plain text search query
    await state.clear()
    await db.record_search(telegram_id=user_id, search_query=text, source="youtube_music")

    safe_text = html.escape(text)
    try:
        status_msg = await message.answer(
            f"🔎 <b>Searching YouTube Music for:</b> <code>{safe_text}</code>...",
            parse_mode="HTML"
        )
    except Exception:
        status_msg = await message.answer(
            f"🔎 Searching YouTube Music for: {text}...",
            parse_mode=None
        )

    results = await music_service.search_music(text, limit=5)
    if not results:
        try:
            await status_msg.edit_text(
                f"❌ <b>No matching music tracks found for:</b> <code>{safe_text}</code>.\n\nPlease check spelling or try another title.",
                parse_mode="HTML"
            )
        except Exception:
            await status_msg.edit_text(
                f"❌ No matching music tracks found for: {text}.\n\nPlease check spelling or try another title.",
                parse_mode=None
            )
        return

    result_lines = [
        f"🎧 <b>YouTube Music Results:</b> <code>{safe_text}</code>\n"
    ]
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
        logger.warning(f"Failed to edit ytmusic results with HTML: {e}. Falling back to plain text.")
        plain_lines = [f"🎧 YouTube Music Results: {text}\n"]
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
