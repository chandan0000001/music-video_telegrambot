"""
Referral System Router
Handles user invite link generation, tracking, milestone rewards (Invite 3 -> 1 Week VIP),
and social sharing.
"""
import urllib.parse
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from database import db
from keyboards.main_menu import get_back_to_main_keyboard

logger = logging.getLogger(__name__)
router = Router(name="referral_router")

BOT_USERNAME = "songsuperzxbot"


def generate_progress_bar(cycle_progress: int) -> str:
    """Generate visual 3-step progress bar."""
    if cycle_progress == 1:
        return "[ 🟩 ⬜ ⬜ ] 1/3"
    elif cycle_progress == 2:
        return "[ 🟩 🟩 ⬜ ] 2/3"
    return "[ ⬜ ⬜ ⬜ ] 0/3"


def build_referral_panel(user_id: int, stats: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Generate modern, attractive referral panel text and inline keyboard."""
    total_invited = stats.get("total_invited", 0)
    total_days = stats.get("total_days_earned", 0)
    needed = stats.get("needed_for_next", 3)
    progress_num = stats.get("current_cycle_progress", 0)
    progress_bar = generate_progress_bar(progress_num)

    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"

    share_text = (
        "🎵 Download high quality 320kbps MP3s and 1080p Full HD videos for free with @songsuperzxbot!"
    )
    encoded_share_url = (
        f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote(share_text)}"
    )

    text = (
        "🎁 <b>Invite Friends & Earn Free VIP Access!</b>\n\n"
        "Share your unique invite link with friends, groups, or channels.\n"
        "For <b>every 3 friends</b> who start the bot, you automatically receive <b>7 Days of VIP Access</b> completely FREE!\n\n"
        "📊 <b>Your Referral Statistics:</b>\n"
        f"• 👥 <b>Friends Joined:</b> <code>{total_invited}</code>\n"
        f"• 💎 <b>Total VIP Earned:</b> <code>{total_days} Days</code>\n"
        f"• 🎯 <b>Next Reward Progress:</b> {progress_bar}\n"
        f"• ⚡ <b>Needed for Next VIP:</b> <code>{needed} more friend(s)</code>\n\n"
        "🔗 <b>Your Personal Invite Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "💡 <i>Tip: Tap the button below to share directly with your Telegram chats!</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Share Invite Link", url=encoded_share_url),
            ],
            [
                InlineKeyboardButton(text="🔄 Refresh Stats", callback_data="referral:refresh"),
                InlineKeyboardButton(text="🎟️ Redeem Coupon", callback_data="coupon:redeem"),
            ],
            [
                InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="menu:main")
            ]
        ]
    )

    return text, kb


@router.message(Command("referral"))
@router.message(Command("invite"))
async def cmd_referral(message: Message) -> None:
    """Display user referral dashboard."""
    user = message.from_user
    if not user:
        return

    # Check if user is blocked
    if await db.is_user_blocked(user.id):
        await message.answer("🚫 <i>You have been restricted from using this service.</i>", parse_mode="HTML")
        return

    # Upsert user record
    await db.add_or_update_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name
    )

    stats = await db.get_referral_stats(user.id)
    text, kb = build_referral_panel(user.id, stats)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "menu:referral")
async def cb_menu_referral(callback: CallbackQuery) -> None:
    """Open referral panel from main menu."""
    user = callback.from_user
    if not user:
        await callback.answer()
        return

    await db.add_or_update_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name
    )

    stats = await db.get_referral_stats(user.id)
    text, kb = build_referral_panel(user.id, stats)

    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "referral:refresh")
async def cb_referral_refresh(callback: CallbackQuery) -> None:
    """Refresh referral statistics live."""
    user = callback.from_user
    if not user:
        await callback.answer()
        return

    stats = await db.get_referral_stats(user.id)
    text, kb = build_referral_panel(user.id, stats)

    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    await callback.answer("🔄 Referral stats refreshed!")
