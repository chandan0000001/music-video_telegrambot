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


def get_welcome_text(user_name: str = "Music Lover") -> str:
    """Generate modern, interactive welcome text."""
    return (
        f"🎧 ━━━━━━━━━━━━━━━━━━━ 🎧\n"
        f"   🔥 **ULTRA MUSIC & VIDEO BOT** 🔥\n"
        f"   *Studio Quality • High-Speed Engine*\n"
        f"🎧 ━━━━━━━━━━━━━━━━━━━ 🎧\n\n"
        f"👋 Welcome, **{user_name}**!\n\n"
        f"🚀 **Engine Specs & Live Status:**\n"
        f" 🟢 **Core Engine:** `ONLINE (v2.4 High Speed)`\n"
        f" 🔴 **YouTube Music:** `1-Tap Studio Audio (320k)`\n"
        f" 🎵 **Audio Formats:** `MP3 • M4A • Studio WAV`\n"
        f" 🎬 **Video Formats:** `144p to 1080p 60fps MP4`\n"
        f" ⚡ **Upload Limit:** `Up to 2000 MB (2 GB)`\n\n"
        f"💡 **Quick Ways to Download:**\n"
        f"• 🎵 Send any **Song Title** to search (e.g. `Starboy`)\n"
        f"• 🔗 Paste any **YouTube** or **YouTube Music** link\n"
        f"• 🔴 Tap **YouTube Music Downloader** below for 1-tap studio quality\n\n"
        f"👇 *Choose an option from the menu below:*"
    )


WELCOME_TEXT = get_welcome_text("Music Lover")

HELP_TEXT = (
    "📖 ━━━━━━━━━━━━━━━━━━━ 📖\n"
    "   ✨ **BOT FEATURES & USER GUIDE** ✨\n"
    "📖 ━━━━━━━━━━━━━━━━━━━ 📖\n\n"
    "⚡ **Core Commands:**\n"
    "• `/start` — Launch or refresh the main menu\n"
    "• `/ytmusic` — Dedicated YouTube Music Studio Downloader\n"
    "• `/search <name>` — Search music tracks across YouTube\n"
    "• `/history` — View your recent searches & download records\n"
    "• `/help` — View this complete documentation\n"
    "• `/about` — Technical architecture and developer credits\n\n"
    "🎯 **How to Download:**\n"
    "1️⃣ **By Song Title:** Type any song name (e.g. `Kesariya Arijit Singh`). Choose from the top 5 matches.\n"
    "2️⃣ **By YouTube Music:** Paste any `music.youtube.com` track for instant 1-tap studio download.\n"
    "3️⃣ **By YouTube Video / Shorts:** Send any video link to inspect and select exact video or audio quality.\n\n"
    "🎧 **Supported Audio Formats & Bitrates:**\n"
    "• `MP3`: 128 kbps (Fast), 192 kbps (Standard), 320 kbps (Studio Quality)\n"
    "• `M4A (AAC)`: Direct lossless remux, crystal-clear mobile playback\n"
    "• `WAV`: Uncompressed master audio\n\n"
    "🎬 **Supported Video Resolutions:**\n"
    "• `MP4`: 144p, 240p, 360p, 480p, 720p HD, 1080p Full HD 60fps\n\n"
    "🔒 **Strict Security Protection:**\n"
    "Only official YouTube and YouTube Music URLs are accepted. All external, untrusted, or malicious URLs are strictly blocked."
)

ABOUT_TEXT = (
    "ℹ️ ━━━━━━━━━━━━━━━━━━━ ℹ️\n"
    "   🎵 **ABOUT THIS TELEGRAM BOT** 🎵\n"
    "ℹ️ ━━━━━━━━━━━━━━━━━━━ ℹ️\n\n"
    "• **Bot Name:** Music & Video Downloader (@songsuperzxbot)\n"
    "• **Developer:** Chandan (@coder_chandan)\n"
    "• **Repository:** [GitHub](https://github.com/chandan0000001/music-video_telegrambot)\n"
    "• **Architecture:** Python 3.11+, aiogram 3, SQLite WAL\n"
    "• **Media Engine:** FFmpeg Intel VAAPI & yt-dlp multi-stream\n"
    "• **Capacity:** Up to 2000 MB (2 GB) Native MTProto Streaming\n\n"
    "👨‍💻 **Developer Contact:** @coder_chandan\n"
    "🔒 **Privacy Guarantee:** Temporary download files are immediately wiped after delivery."
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

    first_name = (user.first_name or "Music Lover")
    is_admin = user.id in ADMIN_IDS
    await message.answer(
        text=get_welcome_text(first_name),
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
    first_name = (callback.from_user.first_name or "Music Lover") if callback.from_user else "Music Lover"
    is_admin = user_id in ADMIN_IDS
    welcome = get_welcome_text(first_name)

    try:
        if callback.message:
            try:
                await callback.message.edit_text(
                    text=welcome,
                    reply_markup=get_main_menu_keyboard(is_admin=is_admin),
                    parse_mode="Markdown"
                )
            except Exception:
                await callback.message.answer(
                    text=welcome,
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
