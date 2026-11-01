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

