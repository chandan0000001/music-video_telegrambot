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
    """Generate modern, clean welcome text."""
    return (
        f"👋 *Hey {user_name}!* Welcome to **Music & Video Bot**\n\n"
        "⚡ *High-speed downloader for YouTube, Instagram & X (Twitter)*\n\n"
        "🎵 **Music:** MP3 (320k Studio), M4A & Lossless WAV\n"
        "🎬 **Video:** 144p to 1080p Full HD MP4\n"
        "📸 **Instagram:** Reels, Carousels, Photos & Videos\n"
        "🐦 **X (Twitter):** Original raw photos, videos & GIFs\n\n"
        "💡 *How to use:* Send any **song title**, **YouTube**, **Instagram**, or **X link** directly below, or use the menu:"
    )


WELCOME_TEXT = get_welcome_text("Music Lover")

HELP_TEXT = (
    "📖 **Bot Features & User Guide**\n\n"
    "🎯 **Quick Ways to Download:**\n"
    "• **Song Search:** Send any title or artist (e.g. `Blinding Lights`)\n"
    "• **YouTube:** Send any YouTube or YouTube Music link\n"
    "• **Instagram:** Send any Reel, Post, or Carousel link\n"
    "• **X / Twitter:** Send any tweet link for original HD media\n\n"
    "⚡ **Formats & Quality:**\n"
    "• **Audio:** MP3 (128k, 192k, 320k), M4A (AAC), Lossless WAV\n"
    "• **Video:** MP4 from 144p up to 1080p Full HD\n"
    "• **Social Media:** Original HD Reels, Carousels, Photos & GIFs\n\n"
    "💡 *Core Commands:* `/start` • `/ytmusic` • `/instagram` • `/twitter` • `/history`"
)

ABOUT_TEXT = (
    "ℹ️ **About This Bot**\n\n"
    "• **Bot:** Music & Video Downloader (@songsuperzxbot)\n"
    "• **Engine:** Python 3.11+, aiogram 3, yt-dlp & FFmpeg\n"
    "• **Developer:** @coder_chandan\n"
    "• **Source:** [GitHub](https://github.com/chandan0000001/music-video_telegrambot)\n\n"
    "🔒 *Privacy Guarantee: Temporary files are wiped immediately upon delivery.*"
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
            if callback.message.photo:
                try:
                    await callback.message.delete()
                except Exception:
                    pass
                await callback.message.answer(
                    text=welcome,
                    reply_markup=get_main_menu_keyboard(is_admin=is_admin),
                    parse_mode="Markdown"
                )
            else:
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
            if callback.message.photo:
                try:
                    await callback.message.delete()
                except Exception:
                    pass
                await callback.message.answer(
                    text=HELP_TEXT,
                    reply_markup=get_back_to_main_keyboard(),
                    parse_mode="Markdown"
                )
            else:
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
    """Cancel current state and action, cleanly returning to main menu."""
    await state.clear()
    user_id = callback.from_user.id if callback.from_user else 0
    first_name = (callback.from_user.first_name or "Music Lover") if callback.from_user else "Music Lover"
    is_admin = user_id in ADMIN_IDS
    welcome = get_welcome_text(first_name)

    if callback.message:
        if callback.message.photo:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(
                text=f"❌ *Action cancelled.*\n\n{welcome}",
                reply_markup=get_main_menu_keyboard(is_admin=is_admin),
                parse_mode="Markdown"
            )
        else:
            try:
                await callback.message.edit_text(
                    text=f"❌ *Action cancelled.*\n\n{welcome}",
                    reply_markup=get_main_menu_keyboard(is_admin=is_admin),
                    parse_mode="Markdown"
                )
            except Exception:
                await callback.message.answer(
                    text=f"❌ *Action cancelled.*\n\n{welcome}",
                    reply_markup=get_main_menu_keyboard(is_admin=is_admin),
                    parse_mode="Markdown"
                )
    await callback.answer("Cancelled")


@router.callback_query(F.data.startswith("cancel_dl"))
async def cb_cancel_active_download(callback: CallbackQuery) -> None:
    """Abort an active in-flight download immediately."""
    from utils.task_registry import task_registry
    from utils.queue_manager import queue_manager
    parts = callback.data.split(":")
    target_user_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    user_id = callback.from_user.id if callback.from_user else 0

    if target_user_id and user_id != target_user_id:
        await callback.answer("⚠️ You can only cancel your own downloads.", show_alert=True)
        return

    uid_to_cancel = target_user_id or user_id
    queue_cancelled = queue_manager.cancel_queue_item(uid_to_cancel)
    cancelled = task_registry.cancel(uid_to_cancel) or queue_cancelled
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
