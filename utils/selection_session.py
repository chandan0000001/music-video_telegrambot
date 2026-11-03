"""
Quality & Interactive Window Session Manager
Handles:
1. 10-Minute TTL for ALL interactive windows across YouTube, Instagram, Twitter, Search, Formats, and Payments.
2. Window supersession: When a user performs a newer action or sends a new link, invalidates and updates the prior pending message.
3. Exemption: Administrators are 100% exempt from session expiration and cooldowns.
4. Auto-expiry background cleaner: Automatically updates stale Telegram messages after 10 minutes to prompt /start.
5. Reactive fallback: Validates message timestamps directly on callback queries even across bot restarts.
"""
import time
import html
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Union
from aiogram import Bot
from aiogram.types import CallbackQuery

from config import ADMIN_IDS
from keyboards.main_menu import get_back_to_main_keyboard, get_session_expired_keyboard

logger = logging.getLogger(__name__)

SELECTION_TTL_SECONDS = 600.0  # 10 minutes (600 seconds)


class SelectionSessionManager:
    """Tracks active interactive menus and selection windows for users with 10-minute TTL."""

    def __init__(self, ttl_seconds: float = SELECTION_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        # user_id -> dict with bot, message_id, chat_id, video_id, is_photo, created_at
        self._active_sessions: Dict[int, Dict[str, Any]] = {}

    async def register_session(
        self,
        bot: Optional[Bot],
        user_id: int,
        chat_id: int,
        message_id: Optional[int],
        video_id: str = "",
        is_photo: bool = False
    ) -> None:
        """
        Register a new interactive selection or mode message.
        If user had a prior uncompleted interactive window, gracefully invalidate it.
        """
        if not user_id or not message_id:
            return

        old_session = self._active_sessions.get(user_id)
        if old_session and old_session.get("message_id") != message_id:
            old_mid = old_session.get("message_id")
            old_cid = old_session.get("chat_id")
            old_bot = old_session.get("bot") or bot
            if old_bot and old_cid and old_mid:
                try:
                    supersede_text = (
                        "🔄 <b>This request was superseded by your newer action.</b>\n\n"
                        "👉 Hit <b>/start</b> or tap below to begin again."
                    )
                    if old_session.get("is_photo"):
                        await old_bot.edit_message_caption(
                            chat_id=old_cid,
                            message_id=old_mid,
                            caption=supersede_text,
                            reply_markup=get_back_to_main_keyboard(),
                            parse_mode="HTML"
                        )
                    else:
                        await old_bot.edit_message_text(
                            chat_id=old_cid,
                            message_id=old_mid,
                            text=supersede_text,
                            reply_markup=get_back_to_main_keyboard(),
                            parse_mode="HTML"
                        )
                except Exception as e:
                    logger.debug(f"Could not supersede previous message {old_mid}: {e}")

        self._active_sessions[user_id] = {
            "bot": bot,
            "message_id": message_id,
            "chat_id": chat_id,
            "video_id": video_id,
            "is_photo": is_photo,
            "created_at": time.time()
        }

    def check_validity(
        self,
        user_id: Union[CallbackQuery, int, Any] = 0,
        video_id: str = ""
    ) -> tuple[bool, str]:
        """
        Verify if an interaction on an interactive window or menu is still valid.
        Returns (is_valid, error_reason).
        Admins are 100% exempt.
        """
        callback_msg = None
        if isinstance(user_id, CallbackQuery):
            target_user_id = user_id.from_user.id if user_id.from_user else 0
            callback_msg = user_id.message
        elif isinstance(user_id, int):
            target_user_id = user_id
        else:
            target_user_id = getattr(user_id, "id", 0)

        # Administrators are 100% exempt from all expiration
        if target_user_id in ADMIN_IDS:
            return True, ""

        # First, check Telegram message timestamp if available (survives restarts)
        if callback_msg and hasattr(callback_msg, "date") and callback_msg.date:
            try:
                msg_age = (datetime.now(timezone.utc) - callback_msg.date).total_seconds()
                if msg_age > self.ttl_seconds:
                    return False, "⏰ This window has expired (10 minutes elapsed). Hit /start to begin again."
            except Exception:
                pass

        session = self._active_sessions.get(target_user_id)
        if not session:
            # If session not tracked in memory and message age is acceptable, allow fallback
            return True, ""

        # If user is clicking buttons for an older, replaced video
        if video_id and session.get("video_id") and session.get("video_id") != video_id:
            return False, "🔄 This request was replaced by a newer link. Please hit /start to begin again."

        # If callback message doesn't match active registered message
        if callback_msg and session.get("message_id") and callback_msg.message_id != session.get("message_id"):
            return False, "🔄 This request was superseded by a newer action. Please hit /start to begin again."

        age = time.time() - session.get("created_at", 0.0)
        if age > self.ttl_seconds:
            return False, "⏰ This window has expired (10 minutes elapsed). Hit /start to begin again."

        return True, ""

    async def enforce_session_validity(
        self,
        callback: CallbackQuery,
        video_id: str = ""
    ) -> bool:
        """
        Enforce 10-minute session validity on callback queries.
        If invalid, automatically alerts user, updates the expired message, and returns False.
        """
        is_valid, reason = self.check_validity(callback, video_id=video_id)
        if not is_valid:
            try:
                await callback.answer(reason, show_alert=True)
            except Exception:
                pass

            if callback.message:
                try:
                    expire_text = (
                        "⏰ <b>Session Expired (10 minutes elapsed)</b>\n\n"
                        f"{html.escape(reason)}\n\n"
                        "👉 Hit <b>/start</b> or tap below to begin again."
                    )
                    is_photo = bool(getattr(callback.message, "photo", None))
                    if is_photo:
                        await callback.message.edit_caption(
                            caption=expire_text,
                            reply_markup=get_session_expired_keyboard(),
                            parse_mode="HTML"
                        )
                    else:
                        await callback.message.edit_text(
                            text=expire_text,
                            reply_markup=get_session_expired_keyboard(),
                            parse_mode="HTML"
                        )
                except Exception:
                    pass
            return False
        return True

    def clear_session(self, user_id: int) -> None:
        """Clear active session when user proceeds with download or returns to main menu."""
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
                    is_photo = s.get("is_photo", False)
                    if bot and cid and mid:
                        try:
                            expire_notice = (
                                "⏰ <b>Session Expired (10 minutes elapsed)</b>\n\n"
                                "This window has timed out due to inactivity.\n\n"
                                "👉 Hit <b>/start</b> or tap below to begin again."
                            )
                            if is_photo:
                                await bot.edit_message_caption(
                                    chat_id=cid,
                                    message_id=mid,
                                    caption=expire_notice,
                                    reply_markup=get_session_expired_keyboard(),
                                    parse_mode="HTML"
                                )
                            else:
                                await bot.edit_message_text(
                                    chat_id=cid,
                                    message_id=mid,
                                    text=expire_notice,
                                    reply_markup=get_session_expired_keyboard(),
                                    parse_mode="HTML"
                                )
                        except Exception as e:
                            logger.debug(f"Could not auto-expire stale message {mid}: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in selection session periodic cleaner: {e}")


selection_session_manager = SelectionSessionManager()
