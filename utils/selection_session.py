"""
Quality Selection Session Manager
Handles:
1. 10-Minute TTL for all quality, format, and search interactive windows.
2. Link supersession: When a user sends Link B, invalidates and updates the pending Link A message.
3. Exemption: Administrators are completely exempt from session expiration.
4. Auto-expiry background cleaner: Automatically updates stale Telegram messages after 10 minutes.
"""
import time
import asyncio
import logging
from typing import Dict, Any, Optional
from aiogram import Bot

from config import ADMIN_IDS

logger = logging.getLogger(__name__)

SELECTION_TTL_SECONDS = 600.0  # 10 minutes


class SelectionSessionManager:
    """Tracks active media quality selection menus for users with 10-minute TTL."""

    def __init__(self, ttl_seconds: float = SELECTION_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        # user_id -> dict with bot, message_id, chat_id, video_id, created_at
        self._active_sessions: Dict[int, Dict[str, Any]] = {}

    async def register_session(
        self,
        bot: Bot,
        user_id: int,
        chat_id: int,
        message_id: int,
        video_id: str
    ) -> None:
        """
        Register a new quality selection message.
        If user had a prior uncompleted selection message, gracefully invalidate it.
        """
        old_session = self._active_sessions.get(user_id)
        if old_session and old_session.get("message_id") != message_id:
            old_mid = old_session.get("message_id")
            old_cid = old_session.get("chat_id")
            try:
                from keyboards.main_menu import get_back_to_main_keyboard
                await bot.edit_message_text(
                    chat_id=old_cid,
                    message_id=old_mid,
                    text="🔄 <b>This request was superseded by your newer link.</b>\n\n👉 Hit /start to begin a new request.",
                    reply_markup=get_back_to_main_keyboard(),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.debug(f"Could not supersede previous quality message {old_mid}: {e}")

        self._active_sessions[user_id] = {
            "bot": bot,
            "message_id": message_id,
            "chat_id": chat_id,
            "video_id": video_id,
            "created_at": time.time()
        }

    def check_validity(self, user_id: int, video_id: str) -> tuple[bool, str]:
        """
        Verify if an interaction on a quality menu is still valid.
        Returns (is_valid, error_reason).
        Admins are 100% exempt.
        """
        if user_id in ADMIN_IDS:
            return True, ""

        session = self._active_sessions.get(user_id)
        if not session:
            # If session not tracked in memory, allow fallback
            return True, ""

        # If user is clicking buttons for an older, replaced video
        if session.get("video_id") and session.get("video_id") != video_id:
            return False, "🔄 This request was replaced by a newer link. Please hit /start to begin again."

        age = time.time() - session.get("created_at", 0.0)
        if age > self.ttl_seconds:
            return False, "⏰ This session has expired (10 minutes elapsed). Hit /start to begin again."

        return True, ""

    def clear_session(self, user_id: int) -> None:
        """Clear session when user proceeds with download."""
        self._active_sessions.pop(user_id, None)

    async def periodic_cleaner_task(self, interval: float = 30.0) -> None:
        """
        Background task that runs periodically.
        Scans all active selection sessions and automatically updates messages
        older than 10 minutes to notify user to hit /start.
        """
        while True:
            try:
                await asyncio.sleep(interval)
                now = time.time()
                expired = []
                for uid, s in list(self._active_sessions.items()):
                    if uid in ADMIN_IDS:
                        continue
                    age = now - s.get("created_at", 0.0)
                    if age >= self.ttl_seconds:
                        expired.append((uid, s))

                for uid, s in expired:
                    self._active_sessions.pop(uid, None)
                    bot: Optional[Bot] = s.get("bot")
                    cid = s.get("chat_id")
                    mid = s.get("message_id")
                    if bot and cid and mid:
                        try:
                            from keyboards.main_menu import get_back_to_main_keyboard
                            await bot.edit_message_text(
                                chat_id=cid,
                                message_id=mid,
                                text=(
                                    "⏰ <b>Session Expired (10 minutes elapsed)</b>\n\n"
                                    "This window has timed out due to inactivity.\n\n"
                                    "👉 Hit <b>/start</b> or tap below to begin again."
                                ),
                                reply_markup=get_back_to_main_keyboard(),
                                parse_mode="HTML"
                            )
                        except Exception:
                            pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in selection session periodic cleaner: {e}")


selection_session_manager = SelectionSessionManager()
