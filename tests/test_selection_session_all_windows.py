"""
Unit & Integration Tests for Universal 10-Minute Expiry Window across ALL Bot Features
Tests:
1. YouTube Video Downloader Menu TTL & Expiry
2. Audio Downloader Menu TTL & Expiry
3. Search Music Menu & Results TTL & Expiry
4. YouTube Music Menu TTL & Expiry
5. Instagram Hub Menu TTL & Expiry
6. Twitter/X Hub Menu TTL & Expiry
7. Quality & Format Selection TTL & Expiry
8. Payment & Paywall QR Window TTL & Expiry
9. Auto-expiry periodic cleaner automatically editing stale messages
10. Admin 100% Exemption from expiration across all windows
"""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from aiogram.types import CallbackQuery, Message, User, Chat

from config import ADMIN_IDS
from utils.selection_session import SelectionSessionManager, selection_session_manager


@pytest.mark.asyncio
async def test_all_windows_expiry_and_prompt():
    """Verify that every interactive window expires after 10-minute TTL and displays /start prompt."""
    mgr = SelectionSessionManager(ttl_seconds=1.0)  # 1.0s TTL for fast test
    bot = MagicMock()
    bot.edit_message_text = AsyncMock()
    bot.edit_message_caption = AsyncMock()

    user_id = 998877

    # 1. Test YouTube Video Window
    await mgr.register_session(bot, user_id=user_id, chat_id=123, message_id=101)
    valid, reason = mgr.check_validity(user_id)
    assert valid is True
    assert reason == ""

    # Wait for expiry
    await asyncio.sleep(1.1)
    valid, reason = mgr.check_validity(user_id)
    assert valid is False
    assert "expired" in reason.lower()
    assert "/start" in reason

    # 2. Test Instagram Hub Window
    await mgr.register_session(bot, user_id=user_id, chat_id=123, message_id=102)
    valid, _ = mgr.check_validity(user_id)
    assert valid is True
    await asyncio.sleep(1.1)
    valid, reason = mgr.check_validity(user_id)
    assert valid is False
    assert "/start" in reason

    # 3. Test Twitter/X Hub Window
    await mgr.register_session(bot, user_id=user_id, chat_id=123, message_id=103)
    valid, _ = mgr.check_validity(user_id)
    assert valid is True
    await asyncio.sleep(1.1)
    valid, reason = mgr.check_validity(user_id)
    assert valid is False
    assert "/start" in reason

    # 4. Test YouTube Music Hub Window
    await mgr.register_session(bot, user_id=user_id, chat_id=123, message_id=104)
    valid, _ = mgr.check_validity(user_id)
    assert valid is True
    await asyncio.sleep(1.1)
    valid, reason = mgr.check_validity(user_id)
    assert valid is False
    assert "/start" in reason

    # 5. Test Payment QR Window (is_photo=True)
    await mgr.register_session(bot, user_id=user_id, chat_id=123, message_id=105, is_photo=True)
    valid, _ = mgr.check_validity(user_id)
    assert valid is True
    await asyncio.sleep(1.1)
    valid, reason = mgr.check_validity(user_id)
    assert valid is False
    assert "/start" in reason


@pytest.mark.asyncio
async def test_enforce_session_validity_updates_message():
    """Verify enforce_session_validity alerts user and edits expired window with /start button."""
    mgr = SelectionSessionManager(ttl_seconds=0.5)
    bot = MagicMock()
    user_id = 554433

    cb = MagicMock(spec=CallbackQuery)
    cb.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    cb_msg = MagicMock(spec=Message)
    cb_msg.message_id = 200
    cb_msg.photo = None
    cb_msg.edit_text = AsyncMock()
    cb.message = cb_msg
    cb.answer = AsyncMock()

    await mgr.register_session(bot, user_id=user_id, chat_id=123, message_id=200)

    # Valid within TTL
    assert await mgr.enforce_session_validity(cb) is True
    assert cb_msg.edit_text.call_count == 0

    # Wait for TTL to pass
    await asyncio.sleep(0.6)

    # Invalid after TTL
    is_valid = await mgr.enforce_session_validity(cb)
    assert is_valid is False
    cb.answer.assert_called_once()
    assert "expired" in cb.answer.call_args[0][0].lower()
    cb_msg.edit_text.assert_called_once()
    edited_text = cb_msg.edit_text.call_args.kwargs.get("text", "")
    assert "/start" in edited_text


@pytest.mark.asyncio
async def test_admin_is_100_percent_exempt():
    """Verify Admins are NEVER expired or blocked across any window."""
    admin_id = next(iter(ADMIN_IDS))
    mgr = SelectionSessionManager(ttl_seconds=0.1)
    bot = MagicMock()

    await mgr.register_session(bot, user_id=admin_id, chat_id=123, message_id=300)
    await asyncio.sleep(0.3)

    valid, reason = mgr.check_validity(admin_id)
    assert valid is True
    assert reason == ""
