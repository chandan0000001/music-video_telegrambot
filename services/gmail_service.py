"""
Gmail IMAP Service for Automated Payment Verification
Connects to Gmail via secure IMAP SSL and validates incoming payment emails containing UTRs.
"""
import re
import imaplib
import email
import logging
import asyncio
from typing import Tuple
from config import GMAIL_USER, GMAIL_APP_PASSWORD, PAYMENT_UPI_ID

logger = logging.getLogger(__name__)


class GmailService:
    """Handles automated checking of payment confirmation emails in Gmail."""

    def __init__(self, user: str = GMAIL_USER, app_password: str = GMAIL_APP_PASSWORD):
        self.user = user
        self.app_password = (app_password or "").replace(" ", "")

    def _sync_check_utr(self, utr: str, expected_amount: float) -> Tuple[bool, str]:
        """
        Synchronously search Gmail inbox for emails containing the 12-digit UTR and verify amount.
        Runs in executor thread pool to prevent blocking async event loop.
        """
        if not self.user or not self.app_password:
            return False, "Gmail credentials not configured."

        utr = (utr or "").strip()
        # MED-2: Strict validation of UTR format to prevent IMAP search query injection
        if not re.fullmatch(r"\d{12}", utr):
            return False, f"Invalid UTR format: '{utr}'. Expected exactly 12 numeric digits."

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

            subject = str(msg.get("Subject", ""))
            full_text = f"{subject}\n{body}"

            # Check if UTR is in email body or subject
            if utr not in full_text:
                return False, f"UTR {utr} not verified in email text."

            # CRIT-1: Check that this is a credit notification, not a debit notification
            debit_pattern = re.compile(r'\b(debited|debited\s+for|sent\s+from|transferred\s+from)\b', re.IGNORECASE)
            credit_pattern = re.compile(r'\b(credited|received|deposited|credit|received\s+from|payment\s+received)\b', re.IGNORECASE)
            if debit_pattern.search(full_text) and not credit_pattern.search(full_text):
                return False, f"Email for UTR {utr} indicates a debit/outward transfer, not a credit received."

            # CRIT-1: Verify that amount in email matches expected_amount
            if expected_amount and expected_amount > 0:
                amount_pattern = re.compile(r'(?:₹|INR|Rs\.?)\s*([\d,]+(?:\.\d{1,2})?)', re.IGNORECASE)
                raw_amounts = amount_pattern.findall(full_text)
                parsed_amounts = []
                for a in raw_amounts:
                    try:
                        clean_a = a.replace(",", "")
                        parsed_amounts.append(float(clean_a))
                    except ValueError:
                        continue

                fallback_pattern = re.compile(r'(?:amount|paid|sum|received)[\s:]*([0-9]+(?:\.[0-9]{1,2})?)', re.IGNORECASE)
                for fb in fallback_pattern.findall(full_text):
                    try:
                        parsed_amounts.append(float(fb))
                    except ValueError:
                        continue

                matched = any(abs(pa - expected_amount) <= 1.0 for pa in parsed_amounts)
                if not matched and parsed_amounts:
                    return False, f"Amount mismatch: expected ₹{expected_amount:.2f}, but email records amounts: {parsed_amounts}."

            # CRIT-1: Verify recipient matches our UPI ID if present in email text
            if PAYMENT_UPI_ID and "@" in PAYMENT_UPI_ID:
                recipient_pattern = re.compile(r'(?:paid\s+to|transferred\s+to|sent\s+to|to\s+VPA)\s+([a-zA-Z0-9._-]+@[a-zA-Z]+)', re.IGNORECASE)
                recipients = recipient_pattern.findall(full_text)
                if recipients:
                    upi_user = PAYMENT_UPI_ID.lower()
                    if not any(upi_user in r.lower() for r in recipients):
                        logger.warning(f"Recipient mismatch: expected {PAYMENT_UPI_ID}, found {recipients}")
                        return False, f"Payment not directed to our UPI ID ({PAYMENT_UPI_ID})."

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
