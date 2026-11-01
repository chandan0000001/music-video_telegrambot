"""
Payment Service for UPI Dynamic QR Generation and Auto-Verification
Generates branded QR images with pre-filled GPay UPI parameters and verifies transactions.
"""
import io
import logging
from typing import Optional, Tuple
import qrcode
from PIL import Image

from config import (
    PAYMENT_UPI_ID,
    PAYMENT_MERCHANT_NAME,
    PRICE_SINGLE_720P,
    PRICE_SINGLE_1080P,
    SUBSCRIPTION_PLANS,
)
from database import db
from services.gmail_service import gmail_service

logger = logging.getLogger(__name__)


class PaymentService:
    """Handles UPI payment flow, QR generation, and automated verification."""

    @staticmethod
    def get_price(order_type: str, quality: Optional[str] = None) -> float:
        """Get price in INR for given order type or quality."""
        if order_type == "single":
            if quality == "720p":
                return float(PRICE_SINGLE_720P)
            return float(PRICE_SINGLE_1080P)

        plan_key = order_type.replace("sub_", "").lower()
        if plan_key == "weekly":
            plan_key = "go"
        elif plan_key == "monthly":
            plan_key = "pro"

        if plan_key in SUBSCRIPTION_PLANS:
            return float(SUBSCRIPTION_PLANS[plan_key]["price"])

        return float(PRICE_SINGLE_720P)

    @staticmethod
    def generate_upi_uri(amount: float, order_id: str, note: str = "HD Video Download") -> str:
        """Construct standard NPCI UPI URI scheme for GPay and UPI apps."""
        clean_note = note.replace(" ", "%20")
        return (
            f"upi://pay?pa={PAYMENT_UPI_ID}"
            f"&pn={PAYMENT_MERCHANT_NAME}"
            f"&am={amount:.2f}"
            f"&tr={order_id}"
            f"&tn={clean_note}"
            f"&cu=INR"
        )

    def generate_qr_bytes(self, amount: float, order_id: str, note: str = "HD Video Download") -> Tuple[bytes, str]:
        """
        Generate PNG QR code bytes and UPI URI for immediate display.
        Returns (png_bytes, upi_uri).
        """
        upi_uri = self.generate_upi_uri(amount, order_id, note)
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(upi_uri)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), upi_uri

    async def verify_order_utr(self, order_id: str, utr_number: str) -> Tuple[bool, str]:
        """
        Automated verification of submitted 12-digit UTR.
        1. Validates format (12 digits).
        2. Validates uniqueness (anti-replay fraud protection).
        3. Checks Gmail IMAP for confirmation.
        4. If verified, marks order paid and activates entitlement.
        """
        utr_clean = utr_number.strip()
        if not utr_clean.isdigit() or len(utr_clean) != 12:
            return False, "❌ Invalid format: UTR must be exactly 12 numeric digits."

        order = await db.get_order(order_id)
        if not order:
            return False, "❌ Order not found."

        if db.is_order_expired(order):
            return False, "❌ Order Expired: The 10-minute validity period has elapsed. Please start a new request to get a fresh QR."

        if order["status"] == "paid":
            return True, "✅ Order has already been paid and activated."

        # Anti-fraud check: Has this UTR been used in another order?
        if await db.is_utr_already_used(utr_clean):
            return False, "❌ Fraud Alert: This UTR has already been claimed by another transaction."

        # Record UTR against order
        await db.record_order_utr(order_id, utr_clean)

        expected_amount = float(order["amount_inr"])
        verified, msg = await gmail_service.verify_utr_payment(utr_clean, expected_amount)

        if verified:
            await db.mark_order_paid(order_id, utr_clean)
            return True, f"✅ Payment of ₹{expected_amount:.2f} successfully verified!"

        return False, msg


payment_service = PaymentService()
