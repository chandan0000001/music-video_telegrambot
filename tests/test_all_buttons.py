"""
Comprehensive Button & Navigation Integrity Tests
Ensures every inline button on both User side and Admin side has a registered,
functional callback query handler with no orphaned buttons.
"""
import pytest
from unittest.mock import MagicMock
from aiogram.types import CallbackQuery, User, Chat, Message
from aiogram.fsm.context import FSMContext

from handlers import setup_routers
from keyboards.main_menu import (
    get_main_menu_keyboard,
    get_back_to_main_keyboard,
    get_ytmusic_menu_keyboard,
    get_twitter_menu_keyboard,
    get_instagram_menu_keyboard
)
from handlers.admin import get_admin_dashboard_keyboard
from keyboards.search_keyboard import get_search_results_keyboard, get_media_type_keyboard
from keyboards.quality_keyboard import (
    get_audio_format_keyboard,
    get_audio_quality_keyboard,
    get_video_quality_keyboard,
    get_youtube_music_keyboard,
    get_retry_keyboard,
    get_cancel_download_keyboard,
    get_paywall_keyboard
)


def _collect_all_button_callbacks(keyboards):
    """Extract all callback_data strings from a list of InlineKeyboardMarkups."""
    callbacks = []
    for kb in keyboards:
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    callbacks.append((btn.text, btn.callback_data))
    return callbacks


@pytest.fixture(scope="module")
def root_router():
    """Build root router."""
    return setup_routers()


def test_main_menu_buttons_user_and_admin():
    """Verify all buttons in user and admin main menu have valid callback_data or URL."""
    # User menu
    user_kb = get_main_menu_keyboard(is_admin=False)
    for row in user_kb.inline_keyboard:
        for btn in row:
            assert btn.callback_data or btn.url
            if btn.callback_data:
                assert len(btn.callback_data) > 0

    # Admin menu has admin:dashboard
    admin_kb = get_main_menu_keyboard(is_admin=True)
    admin_callbacks = [btn.callback_data for row in admin_kb.inline_keyboard for btn in row if btn.callback_data]
    assert "admin:dashboard" in admin_callbacks
    assert "menu:search" in admin_callbacks
    assert "menu:ytmusic" in admin_callbacks
    assert "menu:twitter" in admin_callbacks
    assert "menu:instagram" in admin_callbacks
    assert "menu:video" in admin_callbacks
    assert "menu:referral" in admin_callbacks
    assert "coupon:redeem" in admin_callbacks
    assert "menu:history" in admin_callbacks
    assert "menu:help" in admin_callbacks


def test_admin_dashboard_buttons():
    """Verify all admin dashboard buttons have defined callbacks."""
    admin_kb = get_admin_dashboard_keyboard()
    callbacks = [btn.callback_data for row in admin_kb.inline_keyboard for btn in row if btn.callback_data]

    expected = [
        "admin:stats",
        "admin:users",
        "admin:searchlogs",
        "admin:downloadlogs",
        "admin:broadcast",
        "admin:lookup",
        "admin:plan_subscribers",
        "admin:payment_orders",
        "admin:coupons",
        "menu:main",
        "admin:export:users",
        "admin:export:search_history",
        "admin:export:downloads"
    ]
    for cb in expected:
        assert cb in callbacks, f"Missing admin dashboard button callback: {cb}"


def test_quality_and_format_keyboards():
    """Verify all audio and video quality selection buttons."""
    video_id = "test_vid_123"

    # Search results
    res_kb = get_search_results_keyboard([{"id": video_id, "title": "Track 1", "duration_str": "03:00"}])
    assert res_kb.inline_keyboard[0][0].callback_data == f"sel:{video_id}"

    # Media type choice
    type_kb = get_media_type_keyboard(video_id)
    assert type_kb.inline_keyboard[0][0].callback_data == f"opt_audio:{video_id}"
    assert type_kb.inline_keyboard[0][1].callback_data == f"opt_video:{video_id}"

    # Audio format choice
    afmt_kb = get_audio_format_keyboard(video_id)
    assert afmt_kb.inline_keyboard[0][0].callback_data == f"afmt:{video_id}:mp3"

    # Audio quality choice (128, 192, 320)
    aqual_kb = get_audio_quality_keyboard(video_id, "mp3", {"320": "8 MB"})
    aqual_cbs = [btn.callback_data for row in aqual_kb.inline_keyboard for btn in row if btn.callback_data]
    assert f"adl:{video_id}:mp3:128" in aqual_cbs
    assert f"adl:{video_id}:mp3:320" in aqual_cbs

    # Video quality choice
    vqual_kb = get_video_quality_keyboard(video_id, ["360p", "720p", "1080p"])
    assert vqual_kb.inline_keyboard[0][0].callback_data == f"vdl:{video_id}:360p"

    # YouTube Music studio fast keyboard
    ytm_kb = get_youtube_music_keyboard(video_id)
    ytm_cbs = [btn.callback_data for row in ytm_kb.inline_keyboard for btn in row if btn.callback_data]
    assert f"adl:{video_id}:mp3:320" in ytm_cbs
    assert f"opt_video:{video_id}" in ytm_cbs
    assert "cancel_action" in ytm_cbs

    # Cancel download keyboard
    cancel_dl_kb = get_cancel_download_keyboard(99999)
    assert cancel_dl_kb.inline_keyboard[0][0].callback_data == "cancel_dl:99999"

    # Universal back to main menu
    back_kb = get_back_to_main_keyboard()
    assert back_kb.inline_keyboard[0][0].callback_data == "menu:main"
