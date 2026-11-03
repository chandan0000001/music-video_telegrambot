"""
Quality Selection Session Manager
Handles:
1. 30-Minute TTL for quality/format selection menus.
2. Link supersession: When a user sends Link B, invalidates and updates the pending Link A message.
3. Exemption: Administrators are completely exempt from session expiration.
"""
import time
import logging
from typing import Dict, Any, Optional
from aiogram import Bot

from config import ADMIN_IDS

logger = logging.getLogger(__name__)

SELECTION_TTL_SECONDS = 1800.0  # 30 minutes


class SelectionSessionManager:
    """Tracks active media quality selection menus for users."""

    def __init__(self, ttl_seconds: float = SELECTION_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        # user_id -> dict with message_id, chat_id, video_id, created_at
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
                await bot.edit_message_text(
                    chat_id=old_cid,
                    message_id=old_mid,
                    text="🔄 *This request was superseded by your newer link.*",
                    reply_markup=None,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.debug(f"Could not supersede previous quality message {old_mid}: {e}")

        self._active_sessions[user_id] = {
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
            return False, "🔄 This request was replaced by a newer link. Please use your latest message."

        age = time.time() - session.get("created_at", 0.0)
        if age > self.ttl_seconds:
            return False, "⏳ This quality selection expired after 30 minutes. Please send your link again."

        return True, ""

    def clear_session(self, user_id: int) -> None:
        """Clear session when user proceeds with download."""
        self._active_sessions.pop(user_id, None)


selection_session_manager = SelectionSessionManager()
