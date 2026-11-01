"""
Payment and Subscription Router for Google Pay UPI
Handles paywall selection, dynamic QR generation, 12-digit UTR submission, and automated verification.
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS, PAYMENT_UPI_ID
from database import db
from services.payment_service import payment_service
from services.media_service import media_service
from keyboards.quality_keyboard import (
    get_payment_qr_keyboard,
    get_utr_check_again_keyboard,
    get_admin_payment_alert_keyboard,
)
from handlers.states import UserStates

logger = logging.getLogger(__name__)
router = Router(name="payment_router")


@router.callback_query(F.data.startswith("pay:"))
async def cb_select_payment_plan(callback: CallbackQuery) -> None:
    """User selected a payment plan (single, weekly, or monthly) from paywall."""
    parts = callback.data.split(":")
    plan_type = parts[1]        # 'single', 'weekly', 'monthly'
    video_id = parts[2]
    quality = parts[3] if len(parts) > 3 else "720p"

    user = callback.from_user
    user_id = user.id if user else 0

    # Calculate amount
    order_type = f"sub_{plan_type}" if plan_type in ("weekly", "monthly") else "single"
    amount = payment_service.get_price(order_type, quality)

    # Get video title
    info_success, info, _ = await media_service.get_media_info(f"https://www.youtube.com/watch?v={video_id}")
    video_title = info.get("title", f"YouTube_{video_id}") if info_success and info else f"YouTube_{video_id}"

    # Create pending order in database
    order_id = await db.create_order(
        telegram_id=user_id,
        order_type=order_type,
        amount_inr=amount,
        media_id=video_id,
        media_title=video_title,
        quality=quality
    )

    # Generate Dynamic UPI QR Code
    plan_name = f"{quality} HD Video" if plan_type == "single" else f"{plan_type.capitalize()} Pass"
    qr_bytes, upi_uri = payment_service.generate_qr_bytes(
        amount=amount,
        order_id=order_id,
        note=f"{plan_name} Download"
    )

    caption = (
        f"🧾 **Order ID:** `{order_id}`\n"
        f"💎 **Plan:** {plan_name}\n"
        f"💰 **Amount to Pay:** `₹{amount:.2f}`\n"
        f"📱 **Google Pay UPI:** `{PAYMENT_UPI_ID}`\n\n"
        f"**How to Complete Payment:**\n"
        f"1️⃣ Scan the QR code above using **Google Pay** (or tap the button below to open GPay).\n"
        f"2️⃣ Pay the exact amount of **₹{amount:.2f}**.\n"
        f"3️⃣ After paying, tap **✅ Submit 12-Digit UTR** below and send your transaction ID.\n\n"
        f"⚡ *Payment is verified automatically in 2 seconds!*"
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
    await state.set_state(UserStates.waiting_for_utr)
    await state.update_data(order_id=order_id)

    prompt = (
        "📝 **Enter 12-Digit UPI UTR Number**\n\n"
        "Please copy the 12-digit **UTR / UPI Ref ID** from your Google Pay payment receipt and paste it below.\n\n"
        "💡 *Example:* `412356789012`\n\n"
        "👉 *Send your 12-digit UTR now:*"
    )
    if callback.message:
        await callback.message.answer(prompt, parse_mode="Markdown")
    await callback.answer()


@router.message(UserStates.waiting_for_utr)
async def handle_utr_input(message: Message, state: FSMContext) -> None:
    """Handle 12-digit UTR text sent by user and run automated Gmail verification."""
    text = (message.text or "").strip()
    if not text.isdigit() or len(text) != 12:
        await message.answer(
            "⚠️ **Invalid UTR Format**\n\n"
            "The UPI Reference ID must be exactly **12 numeric digits**.\n"
            "Please check your Google Pay transaction history and try again.",
            parse_mode="Markdown"
        )
        return

    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await state.clear()
        await message.answer("❌ Order expired or not found. Please click `/start` to try again.")
        return

    order = await db.get_order(order_id)
    if not order:
        await state.clear()
        await message.answer("❌ Order record not found.")
        return

    checking_msg = await message.answer(
        f"⏳ *Verifying UTR* `{text}` *with Google Pay records...*",
        parse_mode="Markdown"
    )

    # Automated check via Gmail IMAP
    verified, msg = await payment_service.verify_order_utr(order_id, text)

    if verified:
        await state.clear()
        await checking_msg.edit_text(
            f"🎉 **Payment Verified Successfully!**\n\n"
            f"• **Amount:** `₹{order['amount_inr']:.2f}`\n"
            f"• **UTR:** `{text}`\n\n"
            f"🚀 *Starting high-speed video download now...*",
            parse_mode="Markdown"
        )
        # Dispatch video fulfillment
        await fulfill_paid_order(message, order)
    else:
        # Not found in Gmail yet (bank notification delay)
        user_name = message.from_user.username or message.from_user.first_name if message.from_user else "User"
        user_id = message.from_user.id if message.from_user else 0

        await checking_msg.edit_text(
            f"⏳ **Verification Pending**\n\n"
            f"UTR `{text}` was received, but the bank/Google Pay email has not reached the inbox yet.\n\n"
            f"👉 Banks sometimes take 10-30 seconds. Tap **🔄 Check Again** below, or contact admin if you need immediate assistance.",
            reply_markup=get_utr_check_again_keyboard(order_id, text),
            parse_mode="Markdown"
        )

        # Notify Admin for transparency and optional 1-tap manual approval
        admin_alert = (
            f"🔔 **New Payment Submitted (Pending Auto-Confirm)**\n"
            f"• **User:** @{user_name} (`{user_id}`)\n"
            f"• **Order:** `{order_id}`\n"
            f"• **Amount:** ₹{order['amount_inr']:.2f}\n"
            f"• **UTR:** `{text}`"
        )
        for admin_id in ADMIN_IDS:
            try:
                await message.bot.send_message(
                    chat_id=admin_id,
                    text=admin_alert,
                    reply_markup=get_admin_payment_alert_keyboard(order_id, text),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.warning(f"Could not alert admin {admin_id}: {e}")


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

    if callback.message:
        await callback.message.edit_text(
            f"🔄 *Re-checking Gmail for UTR* `{utr}`...",
            parse_mode="Markdown"
        )

    verified, msg = await payment_service.verify_order_utr(order_id, utr)
    if verified:
        await state.clear()
        if callback.message:
            await callback.message.edit_text(
                f"🎉 **Payment Verified Successfully!**\n\n"
                f"• **Amount:** `₹{order['amount_inr']:.2f}`\n"
                f"• **UTR:** `{utr}`\n\n"
                f"🚀 *Starting high-speed video download now...*",
                parse_mode="Markdown"
            )
            await fulfill_paid_order(callback.message, order)
    else:
        await callback.answer("Still pending. Please wait 15 seconds.", show_alert=True)
        if callback.message:
            await callback.message.edit_text(
                f"⏳ **Verification Pending**\n\n"
                f"UTR `{utr}` has not arrived in the Gmail alerts yet.\n"
                f"Please try again in 15 seconds or contact admin.",
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
        await callback.message.edit_text(
            f"✅ **Order {order_id} Approved by Admin**\nUTR: `{utr}`",
            parse_mode="Markdown"
        )

    # Deliver to user
    try:
        target_chat_id = order["telegram_id"]
        success_msg = await callback.bot.send_message(
            chat_id=target_chat_id,
            text=f"🎉 **Payment Approved by Admin!**\nStarting your download now...",
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
    if callback.message:
        await callback.message.edit_text(f"❌ **Order {order_id} Rejected.**")
    await callback.answer("Rejected.")


async def fulfill_paid_order(message: Message, order: dict) -> None:
    """Deliver video or notify subscription activation."""
    media_id = order.get("media_id")
    quality = order.get("quality") or "720p"

    if media_id:
        from handlers.video import execute_video_download_pipeline
        # Trigger background download pipeline directly
        await execute_video_download_pipeline(
            status_msg=message,
            video_id=media_id,
            resolution=quality,
            user_id=order["telegram_id"]
        )
    else:
        await message.answer(
            "👑 **Subscription Active!**\n"
            "You can now download videos up to 1080p Full HD every day!\n"
            "Send any YouTube link to start.",
            parse_mode="Markdown"
        )
