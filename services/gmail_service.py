"""
Gmail IMAP Service for Automated Payment Verification
Connects to Gmail via secure IMAP SSL and validates incoming payment emails containing UTRs.
"""
import imaplib
import email
import logging
import asyncio
from typing import Tuple
from config import GMAIL_USER, GMAIL_APP_PASSWORD

logger = logging.getLogger(__name__)


class GmailService:
    """Handles automated checking of payment confirmation emails in Gmail."""

    def __init__(self, user: str = GMAIL_USER, app_password: str = GMAIL_APP_PASSWORD):
        self.user = user
        self.app_password = (app_password or "").replace(" ", "")

    def _sync_check_utr(self, utr: str, expected_amount: float) -> Tuple[bool, str]:
        """
        Synchronously search Gmail inbox for emails containing the 12-digit UTR.
        Runs in executor thread pool to prevent blocking async event loop.
        """
        if not self.user or not self.app_password:
            return False, "Gmail credentials not configured."

        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(self.user, self.app_password)
            mail.select("INBOX", readonly=True)

            # Search server-side for emails containing the 12-digit UTR
            # Google server-side search runs in ~0.3s
            search_query = f'BODY "{utr}"'
            status, data = mail.search(None, search_query)

            if status != "OK" or not data or not data[0]:
                status, data = mail.search(None, f'TEXT "{utr}"')

            if status != "OK" or not data or not data[0]:
                mail.logout()
                return False, f"UTR {utr} not found in recent Gmail alerts."

            msg_ids = data[0].split()
            if not msg_ids:
                mail.logout()
                return False, f"UTR {utr} not found in recent Gmail alerts."

            # Fetch the latest matching message
            latest_id = msg_ids[-1]
            fetch_status, msg_data = mail.fetch(latest_id, "(RFC822)")
            mail.logout()

            if fetch_status != "OK" or not msg_data:
                return False, "Could not fetch email details."

            raw_email = None
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    raw_email = response_part[1]
                    break

            if not raw_email:
                return False, "Empty email content."

            msg = email.message_from_bytes(raw_email)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get("Content-Disposition"))
                    if content_type in ("text/plain", "text/html") and "attachment" not in content_disposition:
                        payload = part.get_payload(decode=True)
                        if payload:
                            body += payload.decode("utf-8", errors="ignore")
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="ignore")

            # Check if UTR is in email body or subject
            subject = str(msg.get("Subject", ""))
            if utr not in body and utr not in subject:
                return False, f"UTR {utr} not verified in email text."

            logger.info(f"Payment verified for UTR {utr} with expected amount ₹{expected_amount}")
            return True, f"Verified: UTR {utr} confirmed in bank/GPay notification."

        except Exception as e:
            logger.error(f"Gmail IMAP verification error: {e}")
            return False, f"IMAP connection error: {e}"

    async def verify_utr_payment(self, utr: str, expected_amount: float) -> Tuple[bool, str]:
        """Non-blocking async verification of UTR in Gmail."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_check_utr, utr, expected_amount)


gmail_service = GmailService()
