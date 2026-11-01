"""
Unit and Handler Tests
Tests Scenarios:
5. User selects a result.
6. User selects audio quality.
12. Admin accesses /admin.
13. Normal user attempts /admin.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, CallbackQuery, User, Chat

from config import ADMIN_IDS
from handlers.admin import cmd_admin, is_admin
from keyboards.search_keyboard import get_search_results_keyboard, get_media_type_keyboard
from keyboards.quality_keyboard import (
    get_audio_format_keyboard,
    get_audio_quality_keyboard,
    get_video_quality_keyboard
)


def test_scenario_5_and_6_keyboard_generation():
    """Test scenarios 5 & 6: Result selection and audio/video quality keyboards."""
    # 5 results keyboard
    sample_results = [
        {"id": f"vid_{i}", "title": f"Song {i}", "duration_str": "03:30"}
        for i in range(1, 6)
    ]
    results_kb = get_search_results_keyboard(sample_results)
    # 5 items + 1 cancel button = 6 rows
    assert len(results_kb.inline_keyboard) == 6
    assert results_kb.inline_keyboard[0][0].callback_data == "sel:vid_1"

    # User selects result -> media type choice
    media_kb = get_media_type_keyboard("vid_1")
    assert media_kb.inline_keyboard[0][0].callback_data == "opt_audio:vid_1"
    assert media_kb.inline_keyboard[0][1].callback_data == "opt_video:vid_1"

    # Audio format keyboard
    fmt_kb = get_audio_format_keyboard("vid_1")
    assert fmt_kb.inline_keyboard[0][0].callback_data == "afmt:vid_1:mp3"
    assert fmt_kb.inline_keyboard[0][1].callback_data == "afmt:vid_1:m4a"
    assert fmt_kb.inline_keyboard[0][2].callback_data == "afmt:vid_1:wav"

    # Audio quality keyboard (128, 192, 320 kbps)
    approx_sizes = {"128": "3.5 MB", "192": "5.2 MB", "320": "8.8 MB"}
    qual_kb = get_audio_quality_keyboard("vid_1", "mp3", approx_sizes)
    assert "128" in qual_kb.inline_keyboard[0][0].text
    assert qual_kb.inline_keyboard[0][0].callback_data == "adl:vid_1:mp3:128"
    assert "320" in qual_kb.inline_keyboard[2][0].text

    # Video quality keyboard (sorted low to high)
    vid_kb = get_video_quality_keyboard("vid_1", ["144p", "360p", "720p", "1080p"])
    assert "144p" in vid_kb.inline_keyboard[0][0].text
    assert vid_kb.inline_keyboard[0][0].callback_data == "vdl:vid_1:144p"


def test_admin_authorization_logic():
    """Test scenario 12 & 13 authorization filter."""
    admin_id = next(iter(ADMIN_IDS))
    normal_user_id = 999999999

    assert is_admin(admin_id) is True
    assert is_admin(normal_user_id) is False


@pytest.mark.asyncio
async def test_scenario_12_and_13_admin_command():
    """Test scenarios 12 & 13: Normal user gets denied, admin gets dashboard."""
    from database import db
    await db.init_db()

    admin_id = next(iter(ADMIN_IDS))
    normal_id = 999999999

    mock_state = MagicMock()
    mock_state.clear = AsyncMock()

    # Normal user attempt
    normal_msg = MagicMock(spec=Message)
    normal_msg.from_user = User(id=normal_id, is_bot=False, first_name="NormalUser")
    normal_msg.answer = AsyncMock()

    await cmd_admin(normal_msg, mock_state)
    normal_msg.answer.assert_awaited_once()
    assert "Access Denied" in normal_msg.answer.call_args[0][0]

    # Admin user attempt
    admin_msg = MagicMock(spec=Message)
    admin_msg.from_user = User(id=admin_id, is_bot=False, first_name="AdminUser")
    admin_msg.answer = AsyncMock()

    await cmd_admin(admin_msg, mock_state)
    admin_msg.answer.assert_awaited_once()
    assert "Administrator Control Dashboard" in admin_msg.answer.call_args[1]["text"]


def test_main_menu_keyboard_has_ytmusic():
    """Verify main menu includes YouTube Music Downloader and dedicated hub keyboard."""
    from keyboards.main_menu import get_main_menu_keyboard, get_ytmusic_menu_keyboard
    menu_kb = get_main_menu_keyboard(is_admin=False)
    
    # Check that YouTube Music button exists with callback menu:ytmusic
    all_callbacks = [btn.callback_data for row in menu_kb.inline_keyboard for btn in row if btn.callback_data]
    assert "menu:ytmusic" in all_callbacks
    assert "menu:search" in all_callbacks
    assert "menu:audio" in all_callbacks
    assert "menu:video" in all_callbacks

    yt_kb = get_ytmusic_menu_keyboard()
    yt_callbacks = [btn.callback_data for row in yt_kb.inline_keyboard for btn in row if btn.callback_data]
    assert "menu:main" in yt_callbacks


def test_welcome_text_personalization():
    """Verify welcome message is visually engaging and personalized."""
    from handlers.start import get_welcome_text
    text = get_welcome_text("Chandan")
    assert "Chandan" in text
    assert "ULTRA MUSIC & VIDEO BOT" in text
    assert "YouTube Music" in text
    assert "2000 MB" in text


@pytest.mark.asyncio
async def test_ytmusic_command_prompts_hub():
    """Verify /ytmusic command displays YouTube Music hub and sets state."""
    from handlers.ytmusic import cmd_ytmusic
    from handlers.states import UserStates

    mock_state = MagicMock()
    mock_state.set_state = AsyncMock()

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=1234567, is_bot=False, first_name="Tester")
    msg.text = "/ytmusic"
    msg.answer = AsyncMock()

    await cmd_ytmusic(msg, mock_state)
    mock_state.set_state.assert_awaited_once_with(UserStates.waiting_for_ytmusic)
    msg.answer.assert_awaited_once()
    assert "YOUTUBE MUSIC DOWNLOADER" in msg.answer.call_args[1]["text"]


def test_video_quality_keyboard_badges():
    """Verify video keyboard marks 144p-480p Free and 720p+ with premium price."""
    from keyboards.quality_keyboard import get_video_quality_keyboard, get_paywall_keyboard
    kb = get_video_quality_keyboard("test_vid", ["144p", "360p", "480p", "720p", "1080p"])
    
    button_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("144p (Free)" in t for t in button_texts)
    assert any("360p (Free)" in t for t in button_texts)
    assert any("480p (Free)" in t for t in button_texts)
    assert any("720p (₹19)" in t for t in button_texts)
    assert any("1080p (₹27)" in t for t in button_texts)

    # Admin view: NO money amounts or prices shown!
    admin_kb = get_video_quality_keyboard("test_vid", ["144p", "360p", "480p", "720p", "1080p"], is_admin=True)
    admin_btn_texts = [btn.text for row in admin_kb.inline_keyboard for btn in row]
    assert not any("₹" in t for t in admin_btn_texts), "Admin buttons must never show monetary amounts!"
    assert any("Admin VIP" in t for t in admin_btn_texts), "Admin buttons must show VIP badge!"

    # Active subscriber view: VIP Pass shown, no lock price
    sub_kb = get_video_quality_keyboard("test_vid", ["144p", "360p", "480p", "720p", "1080p"], has_active_sub=True)
    sub_btn_texts = [btn.text for row in sub_kb.inline_keyboard for btn in row]
    assert not any("₹" in t for t in sub_btn_texts), "Subscriber buttons must not show price tags!"
    assert any("VIP Pass" in t for t in sub_btn_texts)

    pay_kb = get_paywall_keyboard("test_vid", "720p")
    pay_callbacks = [btn.callback_data for row in pay_kb.inline_keyboard for btn in row if btn.callback_data]
    assert any("pay:single:test_vid:720p" in c for c in pay_callbacks)
    assert any("pay:go:test_vid:720p" in c for c in pay_callbacks)
    assert any("pay:pro:test_vid:720p" in c for c in pay_callbacks)
    assert any("pay:ultra:test_vid:720p" in c for c in pay_callbacks)
    assert any("pay:max:test_vid:720p" in c for c in pay_callbacks)


def test_payment_pricing_and_qr_generation():
    """Verify pricing logic and dynamic QR byte generation."""
    from services.payment_service import payment_service
    from config import PAYMENT_UPI_ID

    assert payment_service.get_price("single", "720p") == 19.0
    assert payment_service.get_price("single", "1080p") == 27.0
    assert payment_service.get_price("sub_weekly") == 35.0
    assert payment_service.get_price("sub_monthly") == 100.0

    qr_bytes, upi_uri = payment_service.generate_qr_bytes(19.0, "ORD_TEST_123")
    assert len(qr_bytes) > 200
    assert PAYMENT_UPI_ID in upi_uri
    assert "19.00" in upi_uri


@pytest.mark.asyncio
async def test_database_orders_and_subscriptions():
    """Verify order creation, UTR anti-fraud, and subscription quota enforcement."""
    from database import db
    await db.init_db()

    test_user = 888777666
    await db.add_or_update_user(test_user, "testuser", "Test")
    order_id = await db.create_order(
        telegram_id=test_user,
        order_type="single_720",
        amount_inr=19.0,
        media_id="vid_test_123",
        quality="720p"
    )
    assert order_id.startswith("ORD_")

    order = await db.get_order(order_id)
    assert order["status"] == "pending"
    assert order["amount_inr"] == 19.0

    # Mark paid with UTR
    import uuid
    test_utr = f"99{uuid.uuid4().int % 10000000000:010d}"
    assert await db.is_utr_already_used(test_utr) is False
    await db.mark_order_paid(order_id, test_utr)
    assert await db.is_utr_already_used(test_utr) is True

    # Test Subscription activation & quota
    sub_order_id = await db.create_order(
        telegram_id=test_user,
        order_type="sub_weekly",
        amount_inr=35.0
    )
    sub_utr = f"88{uuid.uuid4().int % 10000000000:010d}"
    await db.mark_order_paid(sub_order_id, sub_utr)

    sub = await db.get_user_subscription(test_user)
    assert sub["is_active"] is True
    assert sub["plan_tier"] == "weekly"
    assert sub["today_remaining"] == 20

    # Use 1 quota
    await db.use_subscription_video_quota(test_user)
    sub_after = await db.get_user_subscription(test_user)
    assert sub_after["today_remaining"] == 19


def test_subscription_tier_plans_config():
    """Verify Go, Pro, Ultra, Max plan configurations and pricing."""
    from config import SUBSCRIPTION_PLANS
    assert "normal" in SUBSCRIPTION_PLANS
    assert "go" in SUBSCRIPTION_PLANS
    assert "pro" in SUBSCRIPTION_PLANS
    assert "ultra" in SUBSCRIPTION_PLANS
    assert "max" in SUBSCRIPTION_PLANS

    assert SUBSCRIPTION_PLANS["go"]["price"] == 35
    assert SUBSCRIPTION_PLANS["pro"]["price"] == 100
    assert SUBSCRIPTION_PLANS["ultra"]["price"] == 299
    assert SUBSCRIPTION_PLANS["max"]["price"] == 799


@pytest.mark.asyncio
async def test_order_10_minute_expiry_logic():
    """Verify 10-minute order expiry validation."""
    from database import db
    from datetime import datetime, timezone, timedelta

    order_valid = {"expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")}
    assert db.is_order_expired(order_valid) is False

    order_expired = {"expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")}
    assert db.is_order_expired(order_expired) is True


@pytest.mark.asyncio
async def test_admin_subscription_activate_and_revoke():
    """Verify Admin can activate any tier and revoke subscription for any user."""
    from database import db
    target_user = 555444333
    await db.add_or_update_user(target_user, "target", "TargetUser")

    # Activate Pro plan
    res = await db.activate_subscription(target_user, "pro", custom_days=30)
    assert res["plan_tier"] == "pro"
    assert res["daily_video_limit"] == 45

    sub = await db.get_user_subscription(target_user)
    assert sub["is_active"] is True
    assert sub["plan_tier"] == "pro"

    # Revoke subscription
    await db.deactivate_subscription(target_user)
    sub_revoked = await db.get_user_subscription(target_user)
    assert sub_revoked is None


def test_payment_qr_keyboard_no_unsupported_urls():
    """Verify QR keyboard has only valid Telegram-supported URL schemes (never upi://)."""
    from keyboards.quality_keyboard import get_payment_qr_keyboard
    kb = get_payment_qr_keyboard("ORD_TEST_999", "upi://pay?pa=test@okaxis")
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.url:
                assert btn.url.startswith(("http://", "https://", "tg://")), f"Invalid URL scheme in button: {btn.url}"



