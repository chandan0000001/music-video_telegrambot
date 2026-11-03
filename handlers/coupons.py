"""
Coupon and Promotional Code Router
Handles admin coupon generation, listing, revocation, and user coupon redemption.
"""
import re
import html
import logging
from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS, SUBSCRIPTION_PLANS
from database import db
from handlers.states import UserStates, AdminStates
from keyboards.main_menu import get_back_to_main_keyboard
from utils.time_utils import format_ist
from utils.progress import send_vanishing_message

logger = logging.getLogger(__name__)
router = Router(name="coupons_router")


def is_admin(user_id: int) -> bool:
    """Check if Telegram user is authorized administrator."""
    return user_id in ADMIN_IDS


def get_coupon_menu_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown when prompting user for coupon code."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="menu:main")
            ]
        ]
    )


def get_admin_coupons_keyboard() -> InlineKeyboardMarkup:
    """Admin dashboard keyboard for coupon management."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Create Coupon", callback_data="admin:coupon_create"),
                InlineKeyboardButton(text="📋 List Coupons", callback_data="admin:coupon_list"),
            ],
            [
                InlineKeyboardButton(text="🚫 Revoke Coupon", callback_data="admin:coupon_revoke"),
                InlineKeyboardButton(text="🔙 Admin Dashboard", callback_data="admin:dashboard"),
            ]
        ]
    )


# ==============================================================================
# USER HANDLERS (Redeem Coupon)
# ==============================================================================

@router.callback_query(F.data == "coupon:redeem")
async def cb_user_redeem_coupon(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to submit coupon code."""
    await callback.answer()
    await state.set_state(UserStates.waiting_for_coupon)

    text = (
        "🎟️ <b>Redeem Coupon Code</b>\n\n"
        "Have a promotional or VIP voucher code?\n"
        "Send your code below to instantly activate free VIP subscription days or unlock HD video access!\n\n"
        "👉 <b>Type or paste your code below:</b>"
    )
    if callback.message:
        sent_msg = None
        try:
            sent_msg = await callback.message.edit_text(text, reply_markup=get_coupon_menu_keyboard(), parse_mode="HTML")
        except Exception:
            sent_msg = await callback.message.answer(text, reply_markup=get_coupon_menu_keyboard(), parse_mode="HTML")
        from utils.selection_session import selection_session_manager
        user_id = callback.from_user.id if callback.from_user else 0
        await selection_session_manager.register_session(
            bot=callback.bot,
            user_id=user_id,
            chat_id=callback.message.chat.id,
            message_id=sent_msg.message_id if sent_msg else callback.message.message_id
        )


@router.message(Command("coupon"))
@router.message(Command("redeem"))
async def cmd_redeem_coupon(message: Message, state: FSMContext) -> None:
    """User command /coupon or /redeem with optional inline code."""
    user = message.from_user
    if not user:
        return

    # Check if user is blocked
    if await db.is_user_blocked(user.id):
        await message.answer("🚫 <i>You have been restricted from using this service.</i>", parse_mode="HTML")
        return

    # Admin subcommands: /coupon create, /coupon revoke, /coupon list
    text = (message.text or "").strip()
    parts = text.split()
    if len(parts) > 1 and parts[1].lower() in ("create", "revoke", "list"):
        if is_admin(user.id):
            await handle_admin_coupon_command(message, parts[1:])
            return

    # If user provided a code directly: /coupon MYCODE
    if len(parts) > 1:
        code_input = parts[1].strip()
        await process_coupon_redemption(message, code_input, state)
        return

    # Prompt user for code
    await state.set_state(UserStates.waiting_for_coupon)
    prompt_text = (
        "🎟️ <b>Redeem Coupon Code</b>\n\n"
        "Have a promotional or VIP voucher code?\n"
        "Send your code below to instantly activate free VIP subscription days or unlock HD video access!\n\n"
        "👉 <b>Type or paste your code below:</b>"
    )
    await message.answer(prompt_text, reply_markup=get_coupon_menu_keyboard(), parse_mode="HTML")


@router.message(UserStates.waiting_for_coupon)
async def handle_coupon_input(message: Message, state: FSMContext) -> None:
    """Handle text input when user is entering coupon code."""
    code_input = (message.text or "").strip()
    await process_coupon_redemption(message, code_input, state)


async def process_coupon_redemption(message: Message, code_input: str, state: FSMContext) -> None:
    """Core logic for validating and applying coupon code."""
    user = message.from_user
    if not user:
        return

    # Upsert user in database to satisfy foreign keys
    await db.add_or_update_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name
    )

    clean_code = code_input.strip().upper()
    if not clean_code:
        await send_vanishing_message(
            message,
            "⚠️ <b>Khali coupon code bhej rahe ho? 😂 Kuch code toh type karo!</b>",
            delay=90.0,
            parse_mode="HTML"
        )
        return

    success, info, err = await db.redeem_coupon(telegram_id=user.id, code=clean_code)
    await state.clear()

    if not success:
        err_msg = (
            "🕵️‍♂️ <b>Kaha se laate ho aise scheme-baaz coupon? 😂</b>\n\n"
            f"❌ <i>{html.escape(err or 'Ye wala coupon expire ho chuka hai ya galat hai.')}</i>\n\n"
            "💡 Sahi code dhyan se dalo ya VIP subscription choose karo!"
        )
        retry_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔄 Try Another Code", callback_data="coupon:redeem"),
                    InlineKeyboardButton(text="💎 Upgrade VIP", callback_data="menu:upgrade")
                ],
                [
                    InlineKeyboardButton(text="🔙 Main Menu", callback_data="menu:main")
                ]
            ]
        )
        await send_vanishing_message(message, err_msg, delay=90.0, reply_markup=retry_kb, parse_mode="HTML")
        return

    # Success!
    plan_tier = info.get("plan_tier", "go")
    plan_meta = SUBSCRIPTION_PLANS.get(plan_tier, SUBSCRIPTION_PLANS.get("go", {}))
    plan_title = plan_meta.get("badge", f"{plan_tier.upper()} Pass")
    days = info.get("days", 7)
    exp_str = info.get("expiry_date", "N/A")
    exp_ist = format_ist(exp_str) if exp_str != "N/A" else "N/A"
    daily_limit = info.get("daily_video_limit", 20)

    success_text = (
        "🎉 <b>Coupon Redeemed Successfully!</b>\n\n"
        f"🎟️ <b>Coupon Code:</b> <code>{clean_code}</code>\n"
        f"💎 <b>Subscription:</b> {plan_title}\n"
        f"⏳ <b>Days Granted:</b> <b>+{days} Days</b>\n"
        f"📅 <b>Valid Until:</b> <code>{exp_ist}</code>\n"
        f"🎬 <b>Daily HD Limit:</b> <code>{daily_limit} videos/day</code>\n\n"
        "🚀 <i>Your VIP benefits are immediately active. Enjoy high-speed downloads!</i>"
    )

    success_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 Search Music", callback_data="menu:search"),
                InlineKeyboardButton(text="▶️ Video Downloader", callback_data="menu:video")
            ],
            [
                InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="menu:main")
            ]
        ]
    )

    await message.answer(success_text, reply_markup=success_kb, parse_mode="HTML")


# ==============================================================================
# ADMIN HANDLERS (Create, Revoke, List)
# ==============================================================================

async def handle_admin_coupon_command(message: Message, subargs: list[str]) -> None:
    """Handle admin coupon subcommands: create, revoke, list."""
    subcmd = subargs[0].lower()

    if subcmd == "list":
        await display_coupons_list(message)
        return

    if subcmd == "revoke":
        if len(subargs) < 2:
            await message.answer(
                "⚠️ <b>Usage:</b> <code>/coupon revoke &lt;CODE&gt;</code>\n"
                "<i>Example:</i> <code>/coupon revoke VIPFREE</code>",
                parse_mode="HTML"
            )
            return
        code = subargs[1].strip()
        ok, msg = await db.revoke_coupon(code)
        icon = "✅" if ok else "❌"
        await message.answer(f"{icon} {html.escape(msg)}", parse_mode="HTML")
        return

    if subcmd == "create":
        # /coupon create <CODE> [days=7] [max_uses=1] [expires_hours=None] [plan=go]
        if len(subargs) < 2:
            await message.answer(
                "⚠️ <b>Usage:</b> <code>/coupon create &lt;CODE&gt; [days=7] [max_uses=1] [expires_hours] [plan=go]</code>\n\n"
                "<b>Examples:</b>\n"
                "• <code>/coupon create FREEWEEK 7 50</code> (7 days, 50 users, no expiration)\n"
                "• <code>/coupon create FLASH24 3 100 24 pro</code> (3 days Pro, 100 users, expires in 24 hours)",
                parse_mode="HTML"
            )
            return

        code = subargs[1].strip().upper()
        days = int(subargs[2]) if (len(subargs) > 2 and subargs[2].isdigit()) else 7
        max_uses = int(subargs[3]) if (len(subargs) > 3 and subargs[3].isdigit()) else 1
        expires_hours = int(subargs[4]) if (len(subargs) > 4 and subargs[4].isdigit()) else None
        plan = subargs[5].lower() if len(subargs) > 5 else "go"

        ok, res = await db.create_coupon(
            code=code,
            days=days,
            max_uses=max_uses,
            plan_tier=plan,
            expires_in_hours=expires_hours,
            created_by=message.from_user.id if message.from_user else 0
        )

        if not ok:
            await message.answer(f"❌ {html.escape(str(res))}", parse_mode="HTML")
            return

        coupon = res
        exp_disp = coupon.get("expires_at") or "Never (Lifetime until used)"
        await message.answer(
            "🎟️ <b>Coupon Created Successfully!</b>\n\n"
            f"🔑 <b>Code:</b> <code>{coupon['code']}</code>\n"
            f"💎 <b>Plan:</b> <code>{coupon['plan_tier'].upper()}</code>\n"
            f"⏳ <b>Days:</b> <code>{coupon['days']}</code>\n"
            f"👥 <b>Max Uses:</b> <code>{coupon['max_uses']}</code>\n"
            f"⏰ <b>Expires:</b> <code>{exp_disp}</code>\n\n"
            f"🔗 <i>Users can redeem via:</i> <code>/coupon {coupon['code']}</code>",
            reply_markup=get_admin_coupons_keyboard(),
            parse_mode="HTML"
        )


async def display_coupons_list(message_or_cb: Message | CallbackQuery) -> None:
    """Render list of active and recent coupons."""
    coupons = await db.list_coupons(limit=15)

    if not coupons:
        text = "🎟️ <b>Coupon Management</b>\n\n<i>No coupons created yet. Use 'Create Coupon' to generate one.</i>"
    else:
        lines = ["🎟️ <b>Existing Coupons & Promos:</b>\n"]
        for c in coupons:
            status_icon = "🟢" if c["is_active"] else "🔴"
            if c["is_active"] and c["used_count"] >= c["max_uses"]:
                status_icon = "🔒"

            exp_txt = c["expires_at"][:16] if c["expires_at"] else "Never"
            lines.append(
                f"{status_icon} <code>{c['code']}</code> | <b>{c['plan_tier'].upper()}</b> ({c['days']}d)\n"
                f"   👥 Used: <code>{c['used_count']}/{c['max_uses']}</code> | Exp: <code>{exp_txt}</code>"
            )
        text = "\n".join(lines)

    kb = get_admin_coupons_keyboard()
    if isinstance(message_or_cb, CallbackQuery):
        if message_or_cb.message:
            try:
                await message_or_cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                await message_or_cb.message.answer(text, reply_markup=kb, parse_mode="HTML")
        await message_or_cb.answer()
    else:
        await message_or_cb.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "admin:coupons")
async def cb_admin_coupons_hub(callback: CallbackQuery) -> None:
    """Admin dashboard coupon overview."""
    user = callback.from_user
    if not user or not is_admin(user.id):
        await callback.answer("⛔ Access Denied", show_alert=True)
        return
    await display_coupons_list(callback)


@router.callback_query(F.data == "admin:coupon_list")
async def cb_admin_coupon_list(callback: CallbackQuery) -> None:
    """Admin list coupons callback."""
    user = callback.from_user
    if not user or not is_admin(user.id):
        await callback.answer("⛔ Access Denied", show_alert=True)
        return
    await display_coupons_list(callback)


@router.callback_query(F.data == "admin:coupon_create")
async def cb_admin_coupon_create_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin for coupon creation parameters."""
    user = callback.from_user
    if not user or not is_admin(user.id):
        await callback.answer("⛔ Access Denied", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_coupon_create)
    await callback.answer()

    text = (
        "➕ <b>Create New Coupon Code</b>\n\n"
        "Send coupon parameters in this format:\n"
        "<code>&lt;CODE&gt; &lt;DAYS&gt; &lt;MAX_USES&gt; [EXPIRES_HOURS] [PLAN]</code>\n\n"
        "<b>Examples:</b>\n"
        "• <code>VIPWEEK 7 100</code> (7 Days VIP Go, 100 uses, no expiry)\n"
        "• <code>PROMO30 30 50 48 pro</code> (30 Days Pro, 50 uses, expires in 48 hours)\n"
        "• <code>SPECIAL 14 1 0 ultra</code> (14 Days Ultra, 1 user, no expiry)\n\n"
        "👉 <i>Send your coupon details below:</i>"
    )

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="admin:coupons")]
        ]
    )

    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=cancel_kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=cancel_kb, parse_mode="HTML")


@router.message(AdminStates.waiting_for_coupon_create)
async def handle_admin_coupon_create_input(message: Message, state: FSMContext) -> None:
    """Handle text input for coupon creation."""
    user = message.from_user
    if not user or not is_admin(user.id):
        return

    parts = (message.text or "").strip().split()
    if not parts:
        await message.answer("⚠️ Please provide coupon details.", parse_mode="HTML")
        return

    code = parts[0].strip().upper()
    days = int(parts[1]) if (len(parts) > 1 and parts[1].isdigit()) else 7
    max_uses = int(parts[2]) if (len(parts) > 2 and parts[2].isdigit()) else 1
    expires_hours = int(parts[3]) if (len(parts) > 3 and parts[3].isdigit() and int(parts[3]) > 0) else None
    plan = parts[4].lower() if len(parts) > 4 else "go"

    ok, res = await db.create_coupon(
        code=code,
        days=days,
        max_uses=max_uses,
        plan_tier=plan,
        expires_in_hours=expires_hours,
        created_by=user.id
    )
    await state.clear()

    if not ok:
        await message.answer(f"❌ {html.escape(str(res))}", reply_markup=get_admin_coupons_keyboard(), parse_mode="HTML")
        return

    coupon = res
    exp_disp = coupon.get("expires_at") or "Never (Lifetime until used)"
    await message.answer(
        "🎟️ <b>Coupon Created Successfully!</b>\n\n"
        f"🔑 <b>Code:</b> <code>{coupon['code']}</code>\n"
        f"💎 <b>Plan:</b> <code>{coupon['plan_tier'].upper()}</code>\n"
        f"⏳ <b>Days:</b> <code>{coupon['days']}</code>\n"
        f"👥 <b>Max Uses:</b> <code>{coupon['max_uses']}</code>\n"
        f"⏰ <b>Expires:</b> <code>{exp_disp}</code>\n\n"
        f"🔗 <i>Users can redeem via:</i> <code>/coupon {coupon['code']}</code>",
        reply_markup=get_admin_coupons_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:coupon_revoke")
async def cb_admin_coupon_revoke_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin for coupon code to revoke."""
    user = callback.from_user
    if not user or not is_admin(user.id):
        await callback.answer("⛔ Access Denied", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_coupon_revoke)
    await callback.answer()

    text = (
        "🚫 <b>Revoke / Terminate Coupon</b>\n\n"
        "Send the exact coupon code to deactivate.\n"
        "Users will immediately be unable to redeem it.\n\n"
        "👉 <i>Send coupon code below:</i>"
    )

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="admin:coupons")]
        ]
    )

    if callback.message:
        try:
            await callback.message.edit_text(text, reply_markup=cancel_kb, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=cancel_kb, parse_mode="HTML")


@router.message(AdminStates.waiting_for_coupon_revoke)
async def handle_admin_coupon_revoke_input(message: Message, state: FSMContext) -> None:
    """Handle text input for coupon revocation."""
    user = message.from_user
    if not user or not is_admin(user.id):
        return

    code = (message.text or "").strip().upper()
    await state.clear()

    ok, msg = await db.revoke_coupon(code)
    icon = "✅" if ok else "❌"
    await message.answer(f"{icon} {html.escape(msg)}", reply_markup=get_admin_coupons_keyboard(), parse_mode="HTML")
