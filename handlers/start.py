"""
Start, Help, and About Command Handlers
"""
import html
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database import db
from keyboards.main_menu import get_main_menu_keyboard, get_back_to_main_keyboard
from keyboards.search_keyboard import get_media_type_keyboard
from services.media_service import media_service

logger = logging.getLogger(__name__)
router = Router(name="start_router")


def get_welcome_text(user_name: str = "Music Lover") -> str:
    """Generate modern, clean welcome text with direct link guidance."""
    return (
        f"👋 *Hey {user_name}!* Welcome to **Music & Video Bot**\n\n"
        "⚡ *Download high quality music and videos instantly with original fidelity!*\n\n"
        "📥 **Send any kind of link directly below:**\n"
        "• 📸 **Instagram:** Reels, Posts, Carousels, Stories & Audio\n"
        "• 🔴 **YouTube:** Videos, Shorts & YouTube Music\n"
        "• 🐦 **X (Twitter):** Videos, HD Photos & GIFs\n"
        "• 🎵 **Song Name:** Type any song title or artist to search\n\n"
        "👇 *Or use the quick menu buttons below to get started:*"
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
    """Handle /start command with deep-linking support for referrals and inline downloads."""
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

    # Inspect deep link payload if present
    payload = ""
    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            payload = parts[1].strip()

    # Case 1: Download Deep Link from Inline Query (@songsuperzxbot)
    if payload.startswith("dl_"):
        video_id = payload[3:].strip()
        if video_id:
            try:
                info_success, info, _ = await media_service.get_media_info(f"https://www.youtube.com/watch?v={video_id}")
                title = info.get("title", f"Track {video_id}") if (info_success and info) else f"Track {video_id}"
                duration = info.get("duration_string", "N/A") if (info_success and info) else "N/A"
            except Exception:
                title = f"Track {video_id}"
                duration = "N/A"

            card_text = (
                f"🎯 **Selected Media:** {title}\n"
                f"⏱ **Duration:** `{duration}`\n\n"
                "How would you like to download this media?\n\n"
                "• 🎧 **Audio:** Convert to MP3 (128-320k), M4A, or WAV\n"
                "• 🎬 **Video:** Download MP4 from 144p to 1080p HD"
            )
            await message.answer(
                text=card_text,
                reply_markup=get_media_type_keyboard(video_id),
                parse_mode="Markdown"
            )
            return

    # Case 2: Referral Invite Deep Link (ref_<inviter_id>)
    welcome_prefix = ""
    if payload.startswith("ref_"):
        inviter_str = payload[4:].strip()
        if inviter_str.isdigit():
            inviter_id = int(inviter_str)
            if inviter_id != user.id:
                success, total_refs, milestone_unlocked = await db.record_referral(inviter_id, user.id)
                if success:
                    welcome_prefix = "🎁 *Welcome! You joined using a friend's invite link.*\n\n"
                    # Notify inviter of friend join / VIP milestone
                    try:
                        if milestone_unlocked:
                            await message.bot.send_message(
                                chat_id=inviter_id,
                                text=(
                                    "🎉 <b>Referral Milestone Unlocked!</b>\n\n"
                                    f"Your friend <b>{html.escape(first_name)}</b> just joined using your invite link!\n"
                                    f"🏆 Total friends invited: <b>{total_refs}</b>\n"
                                    "🎁 <b>+7 Days of VIP Access</b> have been automatically credited to your account!\n\n"
                                    "Enjoy high-speed Full HD downloads! 🚀"
                                ),
                                parse_mode="HTML"
                            )
                        else:
                            needed = 3 - (total_refs % 3)
                            await message.bot.send_message(
                                chat_id=inviter_id,
                                text=(
                                    "👤 <b>New Friend Joined!</b>\n\n"
                                    f"<b>{html.escape(first_name)}</b> joined using your invite link.\n"
                                    f"📊 Total invited: <b>{total_refs}</b>\n"
                                    f"🎯 Invite <b>{needed} more</b> friend(s) to unlock <b>7 Days VIP free</b>!"
                                ),
                                parse_mode="HTML"
                            )
                    except Exception as e:
                        logger.warning(f"Failed to notify inviter {inviter_id}: {e}")

    await message.answer(
        text=welcome_prefix + get_welcome_text(first_name),
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
