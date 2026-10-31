"""
Start, Help, and About Command Handlers
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database import db
from keyboards.main_menu import get_main_menu_keyboard, get_back_to_main_keyboard

logger = logging.getLogger(__name__)
router = Router(name="start_router")


WELCOME_TEXT = (
    "👋 **Welcome to the Music & Video Downloader Bot!**\n\n"
    "I can help you search for music and download audio/video from authorized YouTube sources with custom format and quality selection.\n\n"
    "✨ **Available Features:**\n"
    "• 🎵 **Search Music:** Search songs and preview matching tracks.\n"
    "• 🎧 **Audio Downloader:** MP3, M4A, WAV (128, 192, 320 kbps).\n"
    "• 🎬 **Video Downloader:** Genuine resolutions from 144p up to 1080p MP4.\n"
    "• 📜 **My History:** View your recent searches and downloads.\n"
    "• ⚙️ **High Performance:** Powered by FFmpeg and non-blocking asynchronous streaming.\n\n"
    "👇 *Choose an option from the menu below, or send me a song name / YouTube link directly!*"
)

HELP_TEXT = (
    "📖 **Bot User Guide & Commands**\n\n"
    "**Commands:**\n"
    "• `/start` - Launch or refresh main menu\n"
    "• `/search <name>` - Search for music by name\n"
    "• `/history` - View your recent search & download records\n"
    "• `/help` - View this help documentation\n"
    "• `/about` - About this bot and licensing notice\n\n"
    "**How to Download:**\n"
    "1️⃣ **By Song Name:** Send any title (e.g., `Tum Hi Ho Arijit Singh`). Choose from the top 5 results.\n"
    "2️⃣ **By YouTube Link:** Send a YouTube video or short URL. Select Audio or Video format.\n\n"
    "**Supported Audio Formats & Bitrates:**\n"
    "• Formats: MP3, M4A, WAV\n"
    "• Bitrates: 128 kbps, 192 kbps, 320 kbps\n\n"
    "**Supported Video Resolutions:**\n"
    "• MP4: 144p, 240p, 360p, 480p, 720p, 1080p (only genuinely available formats)\n\n"
    "⚠️ *Note: Only licensed, public domain, or user-authorized media is permitted for processing.*"
)

ABOUT_TEXT = (
    "ℹ️ **About Telegram Music & Video Downloader**\n\n"
    "• **Bot:** Music & Video Downloader (@songsuperzxbot)\n"
    "• **Developer:** Chandan (@coder_chandan)\n"
    "• **Engine:** Python 3.11+ & aiogram 3\n"
    "• **Media Core:** FFmpeg & yt-dlp\n"
    "• **Database:** SQLite (WAL mode, async transactions)\n"
    "• **Execution:** Linux / Arch Linux via Telegram Long Polling\n\n"
    "👨‍💻 *Support / Contact Developer:* @coder_chandan\n"
    "🔒 *Security & Privacy:* All downloaded files are stored temporarily and automatically cleaned up immediately after delivery."
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Handle /start command."""
    await state.clear()
    user = message.from_user
    if not user:
        return

    # Check if user is blocked
    if await db.is_user_blocked(user.id):
        await message.answer("🚫 *You have been restricted from using this service.*")
        return

    # Upsert user record
    await db.add_or_update_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name
    )

    is_admin = user.id in ADMIN_IDS
    await message.answer(
        text=WELCOME_TEXT,
        reply_markup=get_main_menu_keyboard(is_admin=is_admin),
        parse_mode="Markdown"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle /help command."""
    await message.answer(
        text=HELP_TEXT,
        reply_markup=get_back_to_main_keyboard(),
        parse_mode="Markdown"
    )


@router.message(Command("about"))
async def cmd_about(message: Message) -> None:
    """Handle /about command."""
    await message.answer(
        text=ABOUT_TEXT,
        reply_markup=get_back_to_main_keyboard(),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Return to main menu."""
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0
    is_admin = user_id in ADMIN_IDS

    try:
        if callback.message:
            try:
                await callback.message.edit_text(
                    text=WELCOME_TEXT,
                    reply_markup=get_main_menu_keyboard(is_admin=is_admin),
                    parse_mode="Markdown"
                )
            except Exception:
                await callback.message.answer(
                    text=WELCOME_TEXT,
                    reply_markup=get_main_menu_keyboard(is_admin=is_admin),
                    parse_mode="Markdown"
                )
    finally:
        await callback.answer()


@router.callback_query(F.data == "menu:help")
async def cb_help_menu(callback: CallbackQuery) -> None:
    """Display help from menu."""
    try:
        if callback.message:
            try:
                await callback.message.edit_text(
                    text=HELP_TEXT,
                    reply_markup=get_back_to_main_keyboard(),
                    parse_mode="Markdown"
                )
            except Exception:
                await callback.message.answer(
                    text=HELP_TEXT,
                    reply_markup=get_back_to_main_keyboard(),
                    parse_mode="Markdown"
                )
    finally:
        await callback.answer()


@router.callback_query(F.data == "cancel_action")
async def cb_cancel_action(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel current state and action."""
    await state.clear()
    if callback.message:
        try:
            await callback.message.edit_text("❌ *Operation cancelled.*", parse_mode="Markdown")
        except Exception:
            pass
    await callback.answer("Cancelled")


@router.callback_query(F.data.startswith("cancel_dl"))
async def cb_cancel_active_download(callback: CallbackQuery) -> None:
    """Abort an active in-flight download immediately."""
    from utils.task_registry import task_registry
    parts = callback.data.split(":")
    target_user_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    user_id = callback.from_user.id if callback.from_user else 0

    if target_user_id and user_id != target_user_id:
        await callback.answer("⚠️ You can only cancel your own downloads.", show_alert=True)
        return

    uid_to_cancel = target_user_id or user_id
    cancelled = task_registry.cancel(uid_to_cancel)
    if cancelled:
        await callback.answer("🛑 Download cancelled.")
        if callback.message:
            try:
                await callback.message.edit_text(
                    "🛑 *Download Cancelled*\n"
                    "The download process was stopped and resources freed.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
    else:
        await callback.answer("No active download in progress or already completed.", show_alert=True)
