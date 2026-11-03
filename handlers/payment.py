"""
Payment and Subscription Router for Google Pay UPI
Handles paywall selection, dynamic QR generation (10-min validity), 12-digit UTR submission,
and automated verification with permanent admin proof logging.
"""
import logging
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS, PAYMENT_UPI_ID, SUBSCRIPTION_PLANS, ORDER_EXPIRY_MINUTES
from database import db
from services.payment_service import payment_service
from services.media_service import media_service
from keyboards.quality_keyboard import (
    get_payment_qr_keyboard,
    get_utr_input_keyboard,
    get_utr_check_again_keyboard,
    get_admin_payment_alert_keyboard,
    get_video_quality_keyboard,
)
from handlers.states import UserStates
from utils.time_utils import get_current_ist_str

logger = logging.getLogger(__name__)
router = Router(name="payment_router")


def format_plan_name(order_type: str, quality: str = "720p") -> str:
    """Format human-readable plan name."""
    if order_type.startswith("sub_"):
        key = order_type.replace("sub_", "").lower()
        if key in SUBSCRIPTION_PLANS:
            return SUBSCRIPTION_PLANS[key]["badge"]
        return f"{key.capitalize()} Pass"
    return f"Single {quality} HD Video"


@router.callback_query(F.data.startswith("pay:"))
async def cb_select_payment_plan(callback: CallbackQuery) -> None:
    """User selected a payment plan (single, go, pro, ultra, max) from paywall."""
    parts = callback.data.split(":")
    plan_type = parts[1]        # 'single', 'go', 'pro', 'ultra', 'max'
    video_id = parts[2]
    quality = parts[3] if len(parts) > 3 else "720p"

    user = callback.from_user
    user_id = user.id if user else 0

    # Admin bypass: Never charge or show paywall to admin
    if user_id in ADMIN_IDS:
        await callback.answer("👑 Admin free bypass active! Downloading video...", show_alert=False)
        from handlers.video import execute_video_download_pipeline
        if callback.message:
            await execute_video_download_pipeline(
                status_msg=callback.message,
                video_id=video_id,
                resolution=quality,
                user_id=user_id,
                bot=callback.bot
            )
        return

    # Determine order_type and amount
    if plan_type in ("go", "pro", "ultra", "max", "weekly", "monthly"):
        plan_key = "go" if plan_type == "weekly" else ("pro" if plan_type == "monthly" else plan_type)
        order_type = f"sub_{plan_key}"
        amount = payment_service.get_price(order_type)
        plan_display = format_plan_name(order_type)
    else:
        order_type = "single"
        amount = payment_service.get_price("single", quality)
        plan_display = format_plan_name("single", quality)

    # Get video title
    info_success, info, _ = await media_service.get_media_info(f"https://www.youtube.com/watch?v={video_id}")
    video_title = info.get("title", f"YouTube_{video_id}") if info_success and info else f"YouTube_{video_id}"

    # Create pending order in database (10-min validity)
    order_id = await db.create_order(
        telegram_id=user_id,
        order_type=order_type,
        amount_inr=amount,
        media_id=video_id,
        media_title=video_title,
        quality=quality
    )

    # Generate Dynamic UPI QR Code
    qr_bytes, upi_uri = payment_service.generate_qr_bytes(
        amount=amount,
        order_id=order_id,
        note=f"{plan_display} Download"
    )

    caption = (
        f"🧾 **Order ID:** `{order_id}`\n"
        f"💎 **Selected Plan:** {plan_display}\n"
        f"💰 **Amount to Pay:** `₹{amount:.2f}`\n"
        f"⏰ **Valid For:** `{ORDER_EXPIRY_MINUTES} Minutes` (Complete payment before expiry)\n\n"
        f"📱 **Pay with ANY UPI App:**\n"
        f"• Google Pay • PhonePe • Paytm • FamPay • BHIM • CRED • Any Bank App\n"
        f"• **UPI ID (Tap to Copy):** `{PAYMENT_UPI_ID}`\n\n"
        f"**How to Complete Payment:**\n"
        f"1️⃣ Scan this QR code or send payment to `{PAYMENT_UPI_ID}`.\n"
        f"2️⃣ Pay the exact amount of **₹{amount:.2f}**.\n"
        f"3️⃣ Tap **✅ Submit 12-Digit UTR** below and submit your 12-digit transaction number.\n\n"
        f"⚡ *Once verified, your video is downloaded & delivered to you automatically!*"
    )

    qr_file = BufferedInputFile(qr_bytes, filename=f"payment_{order_id}.png")

    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer_photo(
            photo=qr_file,
            caption=caption,
            reply_markup=get_payment_qr_keyboard(order_id, upi_uri),
            parse_mode="Markdown"
        )
    await callback.answer()


@router.callback_query(F.data.startswith("submit_utr:"))
async def cb_prompt_utr_submission(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to input 12-digit UTR number."""
    order_id = callback.data.split(":")[1]
    order = await db.get_order(order_id)
    if not order or db.is_order_expired(order):
        await callback.answer("❌ This order has expired. Please select a plan again.", show_alert=True)
        return

    await state.set_state(UserStates.waiting_for_utr)
    await state.update_data(order_id=order_id)

    prompt = (
        "📝 **Enter 12-Digit UPI UTR / Reference ID**\n\n"
        "Please copy the 12-digit **UTR number** from your Google Pay, PhonePe, or Paytm receipt and paste it below.\n\n"
        "💡 *Example:* `412356789012`\n\n"
        "👉 *Send your 12-digit UTR now:*"
    )
    if callback.message:
        await callback.message.answer(
            prompt,
            reply_markup=get_utr_input_keyboard(order_id),
            parse_mode="Markdown"
        )
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_order:"))
async def cb_cancel_order(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel pending payment order and cleanly return back to video options or main menu."""
    await state.clear()
    parts = callback.data.split(":")
    order_id = parts[1] if len(parts) > 1 else ""
    order = await db.get_order(order_id) if order_id else None

    # Cancel pending order in database
    if order and order.get("status") == "pending":
        async with db._get_connection() as conn:
            await conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ?", (order_id,))
            await conn.commit()

    user = callback.from_user
    user_id = user.id if user else 0
    is_admin = user_id in ADMIN_IDS

    # Cleanly remove the QR code photo message
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass

    # If this order was for a specific video, return back to that video's quality selection!
    media_id = order.get("media_id") if order else None
    if media_id and callback.message:
        success, info, _ = await media_service.get_media_info(f"https://www.youtube.com/watch?v={media_id}")
        if success and info:
            resolutions = info.get("available_resolutions", [])
            sub = await db.get_user_subscription(user_id)
            has_active_sub = bool(sub and sub.get("is_active") and sub.get("today_remaining", 0) > 0)
            admin_banner = "👑 **Admin Access:** All resolutions unlocked (Free & Unlimited)\n" if is_admin else (
                f"⚡ **VIP Pass Active ({sub['plan_tier'].capitalize()}):** {sub['today_remaining']}/{sub['daily_limit']} HD videos remaining today\n" if has_active_sub else ""
            )
            text = (
                f"❌ *Order `{order_id}` was cancelled.*\n\n"
                f"🎬 **Video Resolution Selection**\n\n"
                f"**Title:** {info['title']}\n"
                f"**Duration:** `{info['duration_str']}`\n\n"
                f"{admin_banner}"
                f"Select a genuinely available resolution below (low to high):"
            )
            await callback.message.answer(
                text=text,
                reply_markup=get_video_quality_keyboard(
                    video_id=media_id,
                    available_resolutions=resolutions,
                    is_admin=is_admin,
                    has_active_sub=has_active_sub
                ),
                parse_mode="Markdown"
            )
            await callback.answer("Order cancelled. Returned to video options.")
            return

    # Fallback / General cancel: Return to Main Menu
    if callback.message:
        from handlers.start import get_welcome_text
        from keyboards.main_menu import get_main_menu_keyboard
        first_name = (user.first_name or "Music Lover") if user else "Music Lover"
        welcome = get_welcome_text(first_name)
        await callback.message.answer(
            text=f"❌ *Order cancelled.*\n\n{welcome}",
            reply_markup=get_main_menu_keyboard(is_admin=is_admin),
            parse_mode="Markdown"
        )
    await callback.answer("Order cancelled.")


@router.message(UserStates.waiting_for_utr)
async def handle_utr_input(message: Message, state: FSMContext) -> None:
    """Handle 12-digit UTR text sent by user and run automated Gmail verification."""
    text = (message.text or "").strip()
    if not text.isdigit() or len(text) != 12:
        await message.answer(
            "⚠️ **Invalid UTR Format**\n\n"
            "The UPI Reference ID must be exactly **12 numeric digits**.\n"
            "Please check your transaction history and send the 12 digits.",
            parse_mode="Markdown"
        )
        return

    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await state.clear()
        await message.answer("❌ Order session expired. Please type `/start` to begin again.")
        return

    order = await db.get_order(order_id)
    if not order:
        await state.clear()
        await message.answer("❌ Order record not found.")
        return

    if db.is_order_expired(order):
        await state.clear()
        await message.answer(
            "❌ **Order Expired**\n\n"
            "The 10-minute validity period for this order has elapsed.\n"
            "Please select the video quality again to generate a fresh QR code.",
            parse_mode="Markdown"
        )
        return

    checking_msg = await message.answer(
        f"⏳ *Verifying UTR* `{text}` *with Google Pay / Bank records...*",
        parse_mode="Markdown"
    )

    # Automated check via Gmail IMAP
    verified, msg = await payment_service.verify_order_utr(order_id, text)

    user = message.from_user
    user_name = user.username or user.first_name if user else "User"
    user_id = user.id if user else 0

    if verified:
        await state.clear()
        await checking_msg.edit_text(
            f"🎉 **Payment Verified Successfully!**\n\n"
            f"• **Amount:** `₹{order['amount_inr']:.2f}`\n"
            f"• **UTR:** `{text}`\n\n"
            f"🚀 *Starting fulfillment now...*",
            parse_mode="Markdown"
        )
        # Notify Admin with permanent proof receipt
        await notify_admin_payment_proof(message.bot, order, text, user_name, user_id, auto_verified=True)
        # Dispatch video fulfillment or subscription activation
        await fulfill_paid_order(message, order)
    else:
        # Not found in Gmail yet (bank notification delay)
        await checking_msg.edit_text(
            f"⏳ **Verification Pending**\n\n"
            f"UTR `{text}` was received, but the bank/Google Pay email has not arrived in the inbox yet.\n\n"
            f"👉 Banks sometimes take 15–30 seconds to deliver alerts. Tap **🔄 Check Again** below, or contact admin if you need immediate assistance.",
            reply_markup=get_utr_check_again_keyboard(order_id, text),
            parse_mode="Markdown"
        )

        # Notify Admin with manual verification guide
        await alert_admin_unverified_payment(message.bot, order, text, user_name, user_id)


@router.callback_query(F.data.startswith("recheck_utr:"))
async def cb_recheck_utr(callback: CallbackQuery, state: FSMContext) -> None:
    """User tapped 'Check Again' button."""
    parts = callback.data.split(":")
    order_id = parts[1]
    utr = parts[2]

    order = await db.get_order(order_id)
    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    # If the order is ALREADY marked as paid (e.g. approved by Admin), deliver immediately!
    if order.get("status") == "paid":
        await state.clear()
        if callback.message:
            await callback.message.edit_text(
                f"🎉 **Payment Verified Successfully!**\n\n"
                f"• **Amount:** `₹{order['amount_inr']:.2f}`\n"
                f"• **UTR:** `{utr}`\n\n"
                f"🚀 *Starting fulfillment now...*",
                parse_mode="Markdown"
            )
            user = callback.from_user
            user_name = user.username or user.first_name if user else "User"
            user_id = user.id if user else 0
            await notify_admin_payment_proof(callback.bot, order, utr, user_name, user_id, auto_verified=False)
            await fulfill_paid_order(callback.message, order)
        return

    if db.is_order_expired(order):
        await callback.answer("Order expired. Please create a new request.", show_alert=True)
        return

    if callback.message:
        await callback.message.edit_text(
            f"🔄 *Re-checking Gmail for UTR* `{utr}`...",
            parse_mode="Markdown"
        )

    verified, msg = await payment_service.verify_order_utr(order_id, utr)
    user = callback.from_user
    user_name = user.username or user.first_name if user else "User"
    user_id = user.id if user else 0

    if verified:
        await state.clear()
        if callback.message:
            await callback.message.edit_text(
                f"🎉 **Payment Verified Successfully!**\n\n"
                f"• **Amount:** `₹{order['amount_inr']:.2f}`\n"
                f"• **UTR:** `{utr}`\n\n"
                f"🚀 *Starting fulfillment now...*",
                parse_mode="Markdown"
            )
            await notify_admin_payment_proof(callback.bot, order, utr, user_name, user_id, auto_verified=True)
            await fulfill_paid_order(callback.message, order)
    else:
        await callback.answer("Still pending. Please wait 15 seconds.", show_alert=True)
        if callback.message:
            await callback.message.edit_text(
                f"⏳ **Verification Pending**\n\n"
                f"UTR `{utr}` has not arrived in the Gmail alerts yet.\n"
                f"Please tap **🔄 Check Again** in 15 seconds, tap **✏️ Re-enter UTR** to correct any typos, or contact admin.",
                reply_markup=get_utr_check_again_keyboard(order_id, utr),
                parse_mode="Markdown"
            )


@router.callback_query(F.data.startswith("admin_approve:"))
async def cb_admin_force_approve(callback: CallbackQuery) -> None:
    """Admin clicks 1-tap Force Approve."""
    user = callback.from_user
    if not user or user.id not in ADMIN_IDS:
        await callback.answer("Unauthorized.", show_alert=True)
        return

    parts = callback.data.split(":")
    order_id = parts[1]
    utr = parts[2]

    order = await db.mark_order_paid(order_id, utr)
    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    await callback.answer("✅ Order approved successfully!")
    if callback.message:
        try:
            await callback.message.edit_text(
                f"✅ **Order** `{order_id}` **Force-Approved by Admin**\n"
                f"• UTR: `{utr}`\n"
                f"• User ID: `{order['telegram_id']}`\n"
                f"• Amount: `₹{order['amount_inr']:.2f}`",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Could not edit admin message: {e}")

    # Deliver to user
    try:
        target_chat_id = order["telegram_id"]
        success_msg = await callback.bot.send_message(
            chat_id=target_chat_id,
            text=f"🎉 **Payment Approved by Admin!**\nStarting your delivery now...",
            parse_mode="Markdown"
        )
        await fulfill_paid_order(success_msg, order)
    except Exception as e:
        logger.error(f"Error sending fulfillment message to user: {e}")


@router.callback_query(F.data.startswith("admin_reject:"))
async def cb_admin_reject(callback: CallbackQuery) -> None:
    """Admin rejects invalid payment."""
    user = callback.from_user
    if not user or user.id not in ADMIN_IDS:
        await callback.answer("Unauthorized.", show_alert=True)
        return

    order_id = callback.data.split(":")[1]
    order = await db.get_order(order_id)
    if order and order.get("status") == "pending":
        async with db._get_connection() as conn:
            await conn.execute("UPDATE orders SET status = 'rejected' WHERE order_id = ?", (order_id,))
            await conn.commit()

    if callback.message:
        try:
            await callback.message.edit_text(
                f"❌ **Order** `{order_id}` **Rejected by Admin.**",
                parse_mode="Markdown"
            )
        except Exception:
            pass
    await callback.answer("Rejected.")

    if order:
        try:
            await callback.bot.send_message(
                chat_id=order["telegram_id"],
                text=(
                    f"❌ **Payment Not Verified**\n\n"
                    f"Your payment for order `{order_id}` could not be verified by the administrator.\n"
                    f"If you completed the payment, please contact @coder_chandan directly with your payment receipt screenshot."
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Could not notify user of order rejection: {e}")


async def notify_admin_payment_proof(bot, order: dict, utr: str, user_name: str, user_id: int, auto_verified: bool = True) -> None:
    """Send permanent proof receipt to administrator for logging and user support."""
    now_str = get_current_ist_str()
    plan_desc = format_plan_name(order.get("order_type", ""), order.get("quality", "720p"))
    status_label = "✅ 100% Automated Instant Verification" if auto_verified else "✅ Admin Force-Approved"

    receipt = (
        f"💰 ━━━━━━━━━━━━━━━━━━━ 💰\n"
        f"   ✅ **PAYMENT CONFIRMED (PROOF LOG)**\n"
        f"💰 ━━━━━━━━━━━━━━━━━━━ 💰\n\n"
        f"• **Order ID:** `{order['order_id']}`\n"
        f"• **User:** @{user_name} (ID: `{user_id}`)\n"
        f"• **Plan:** {plan_desc}\n"
        f"• **Amount Paid:** `₹{order['amount_inr']:.2f}`\n"
        f"• **UTR Number:** `{utr}`\n"
        f"• **Payment Method:** Google Pay / UPI\n"
        f"• **Timestamp:** `{now_str}`\n"
        f"• **Status:** `{status_label}`\n\n"
        f"💾 *Saved permanently in database.*"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=receipt, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Could not deliver payment proof to admin {admin_id}: {e}")


async def alert_admin_unverified_payment(bot, order: dict, utr: str, user_name: str, user_id: int) -> None:
    """Alert admin when automated check is pending with clear manual verification instructions."""
    plan_desc = format_plan_name(order.get("order_type", ""), order.get("quality", "720p"))
    order_id = order["order_id"]
    amount = order["amount_inr"]

    admin_alert = (
        f"⚠️ ━━━━━━━━━━━━━━━━━━━ ⚠️\n"
        f"   ⏳ **PAYMENT PENDING GMAIL VERIFICATION**\n"
        f"⚠️ ━━━━━━━━━━━━━━━━━━━ ⚠️\n\n"
        f"A user submitted a UTR, but it has not reached Gmail yet.\n\n"
        f"• **User:** @{user_name} (`{user_id}`)\n"
        f"• **Order ID:** `{order_id}`\n"
        f"• **Plan:** {plan_desc}\n"
        f"• **Amount Due:** `₹{amount:.2f}`\n"
        f"• **Submitted UTR:** `{utr}`\n\n"
        f"💡 **Admin Verification Guide:**\n"
        f"1. Open your **Google Pay** (or Axis Bank) app on your phone.\n"
        f"2. Go to **Transaction History**.\n"
        f"3. Verify if **₹{amount:.2f}** was credited with UTR `{utr}`.\n"
        f"4. If credited, tap **[ ✅ Force Approve & Deliver ]** below.\n"
        f"5. If fraudulent or not received, tap **[ ❌ Reject ]**."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=admin_alert,
                reply_markup=get_admin_payment_alert_keyboard(order_id, utr),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Could not alert admin {admin_id}: {e}")


async def fulfill_paid_order(message: Message, order: dict) -> None:
    """Deliver video or notify subscription activation."""
    media_id = order.get("media_id")
    quality = order.get("quality") or "720p"
    order_type = order.get("order_type", "")

    if order_type.startswith("sub_"):
        plan_key = order_type.replace("sub_", "").lower()
        plan_info = SUBSCRIPTION_PLANS.get(plan_key, SUBSCRIPTION_PLANS["go"])
        await message.answer(
            f"👑 **{plan_info['badge']} Activated!**\n\n"
            f"• **Daily HD Quota:** `{plan_info['daily_limit']} Videos/day`\n"
            f"• **Duration:** `{plan_info['days']} Days`\n"
            f"• **Features:** {plan_info['description']}\n\n"
            f"Enjoy unlimited high-speed downloads!",
            parse_mode="Markdown"
        )

    if media_id:
        from handlers.video import execute_video_download_pipeline
        await execute_video_download_pipeline(
            status_msg=message,
            video_id=media_id,
            resolution=quality,
            user_id=order["telegram_id"]
        )
