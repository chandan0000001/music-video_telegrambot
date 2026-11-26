"""
Legacy Gmail payment verification interface
Email-only approval is disabled; payment proofs require administrator review.
"""
import re
import imaplib
import logging
import asyncio
from typing import Tuple
from config import GMAIL_USER, GMAIL_APP_PASSWORD

logger = logging.getLogger(__name__)


class GmailService:
    """Retains the payment interface while rejecting untrusted email proof."""

    def __init__(self, user: str = GMAIL_USER, app_password: str = GMAIL_APP_PASSWORD):
        self.user = user
        self.app_password = (app_password or "").replace(" ", "")

    def _sync_check_utr(self, utr: str, expected_amount: float) -> Tuple[bool, str]:
        """
        Validate the reference format and require independent payment review.
        """
        if not self.user or not self.app_password:
            return False, "Gmail credentials not configured."

        utr = (utr or "").strip()
        # MED-2: Strict validation of UTR format to prevent IMAP search query injection
        if not re.fullmatch(r"\d{12}", utr):
            return False, f"Invalid UTR format: '{utr}'. Expected exactly 12 numeric digits."

        # Inbox text and sender headers are attacker-controlled. A signed payment
        # provider event is required before automated entitlement can be safe.
        return False, "Manual payment review required; email text is not payment proof."

    async def verify_utr_payment(self, utr: str, expected_amount: float) -> Tuple[bool, str]:
        """Compatibility entry point; email proof cannot activate an order."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_check_utr, utr, expected_amount)


gmail_service = GmailService()
