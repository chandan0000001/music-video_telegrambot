"""
Unit Tests for Download Progress Tracker & Loading Screen Generator
"""
import pytest
from utils.progress import (
    make_progress_bar,
    format_bytes,
    format_seconds,
    DownloadProgressTracker
)
from keyboards.quality_keyboard import get_retry_keyboard


def test_make_progress_bar():
    """Verify Unicode progress bar rendering."""
    bar_0 = make_progress_bar(0.0, length=10)
    assert bar_0 == "▱▱▱▱▱▱▱▱▱▱"

    bar_50 = make_progress_bar(50.0, length=10)
    assert bar_50 == "▰▰▰▰▰▱▱▱▱▱"

    bar_100 = make_progress_bar(100.0, length=10)
    assert bar_100 == "▰▰▰▰▰▰▰▰▰▰"

    # Clamping tests
    assert make_progress_bar(-10.0) == "▱▱▱▱▱▱▱▱▱▱"
    assert make_progress_bar(150.0) == "▰▰▰▰▰▰▰▰▰▰"


def test_format_bytes():
    """Verify human-readable byte sizing."""
    assert format_bytes(0) == "0 B"
    assert format_bytes(500) == "500.0 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(10 * 1024 * 1024) == "10.0 MB"
    assert format_bytes(2.5 * 1024 * 1024 * 1024) == "2.5 GB"


def test_format_seconds():
    """Verify human-readable duration formatting."""
    assert format_seconds(None) == "--:--"
    assert format_seconds(-5) == "--:--"
    assert format_seconds(45) == "00:45"
    assert format_seconds(125) == "02:05"
    assert format_seconds(3665) == "01:01:05"


def test_download_progress_tracker():
    """Verify yt-dlp hook processing and card rendering."""
    tracker = DownloadProgressTracker(
        media_title="Sample Video Headshots 34 Kill",
        quality_label="1080p",
        media_type="video"
    )

    # Simulate yt-dlp hook call
    hook_data = {
        "status": "downloading",
        "downloaded_bytes": 50 * 1024 * 1024,
        "total_bytes": 100 * 1024 * 1024,
        "speed": 5 * 1024 * 1024,
        "eta": 10
    }
    tracker.ytdlp_hook(hook_data)

    assert tracker.status == "downloading"
    assert tracker.percentage == 50.0
    assert tracker.speed == 5 * 1024 * 1024
    assert tracker.eta == 10

    card = tracker.render_download_card()
    assert "Downloading Video (1080p)" in card
    assert "50.0%" in card
    assert "5.0 MB/s" in card
    assert "50.0 MB / 100.0 MB" in card
    assert "00:10" in card


def test_retry_keyboard():
    """Verify retry keyboard has retry and back buttons."""
    kb = get_retry_keyboard(
        retry_callback="vdl:Xkqy6u0KJso:1080p",
        back_callback="opt_video:Xkqy6u0KJso"
    )
    buttons = kb.inline_keyboard
    assert len(buttons) == 2
    assert buttons[0][0].text == "🔄 Retry Download"
    assert buttons[0][0].callback_data == "vdl:Xkqy6u0KJso:1080p"
    assert buttons[1][0].text == "🔙 Change Quality"
    assert buttons[1][0].callback_data == "opt_video:Xkqy6u0KJso"
    assert buttons[1][1].text == "❌ Cancel"
