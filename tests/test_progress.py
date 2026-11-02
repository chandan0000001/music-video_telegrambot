"""
Unit Tests for Download Progress Tracker & Loading Screen Generator
"""
import asyncio
import pytest
from pathlib import Path
from utils.progress import (
    make_progress_bar,
    format_bytes,
    format_seconds,
    DownloadProgressTracker,
    UploadProgressTracker,
    TrackedFSInputFile
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


def test_upload_progress_tracker():
    """Verify upload progress tracking calculations and rendering."""
    total_bytes = 40 * 1024 * 1024
    tracker = UploadProgressTracker(
        media_title="Sample Video Headshots 34 Kill",
        total_bytes=total_bytes,
        media_type="video",
        part_info="Part 1 of 3"
    )

    # Read 20 MB chunk
    tracker.on_bytes_read(20 * 1024 * 1024)
    assert tracker.uploaded_bytes == 20 * 1024 * 1024
    assert tracker.percentage == 50.0

    card = tracker.render_upload_card()
    assert "Uploading Video" in card
    assert "Part 1 of 3" in card
    assert "50.0%" in card
    assert "20.0 MB / 40.0 MB" in card


@pytest.mark.asyncio
async def test_tracked_fs_input_file(tmp_path: Path):
    """Verify TrackedFSInputFile correctly reads chunks and notifies tracker."""
    test_file = tmp_path / "test_media.dat"
    test_data = b"X" * (1024 * 1024)  # 1 MB
    test_file.write_bytes(test_data)

    tracker = UploadProgressTracker(
        media_title="Test Media",
        total_bytes=len(test_data),
        media_type="audio"
    )

    tracked_file = TrackedFSInputFile(
        path=test_file,
        tracker=tracker,
        chunk_size=256 * 1024
    )

    read_data = bytearray()
    async for chunk in tracked_file.read(bot=None):  # bot is not used for file streaming
        read_data.extend(chunk)

    assert bytes(read_data) == test_data
    assert tracker.uploaded_bytes == len(test_data)
    assert tracker.percentage == 100.0


@pytest.mark.asyncio
async def test_task_registry_cancellation():
    """Verify task registry can register, signal tracker, and cancel active task."""
    from utils.task_registry import task_registry
    from keyboards.quality_keyboard import get_cancel_download_keyboard

    kb = get_cancel_download_keyboard(user_id=12345)
    assert kb.inline_keyboard[0][0].callback_data == "cancel_dl:12345"

    tracker = DownloadProgressTracker("Test Media", "720p")
    cancelled_event = asyncio.Event()

    async def dummy_job():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled_event.set()
            raise

    task = asyncio.create_task(dummy_job())
    await asyncio.sleep(0.01)  # allow dummy_job to start and await
    task_registry.register(12345, task, tracker=tracker)

    assert task_registry.is_active(12345) is True

    # Cancel task
    result = task_registry.cancel(12345)
    assert result is True
    assert tracker._is_cancelled is True

    await asyncio.sleep(0.05)
    assert cancelled_event.is_set()

    task_registry.unregister(12345)
    assert task_registry.is_active(12345) is False


@pytest.mark.asyncio
async def test_vanish_status_message_success():
    """Verify vanish_status_message deletes message cleanly."""
    from unittest.mock import AsyncMock
    from utils.progress import vanish_status_message

    msg = AsyncMock()
    result = await vanish_status_message(msg, delay=0.0)
    assert result is True
    msg.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_vanish_status_message_already_deleted():
    """Verify vanish_status_message succeeds gracefully if message was already deleted."""
    from unittest.mock import AsyncMock
    from aiogram.exceptions import TelegramBadRequest
    from utils.progress import vanish_status_message

    msg = AsyncMock()
    msg.delete.side_effect = TelegramBadRequest(
        method="deleteMessage",
        message="Bad Request: message to delete not found"
    )
    result = await vanish_status_message(msg, delay=0.0)
    assert result is True


@pytest.mark.asyncio
async def test_vanish_status_message_retry_after():
    """Verify vanish_status_message handles Telegram rate-limiting retry."""
    from unittest.mock import AsyncMock
    from aiogram.exceptions import TelegramRetryAfter
    from utils.progress import vanish_status_message

    msg = AsyncMock()
    retry_err = TelegramRetryAfter(method="deleteMessage", message="Flood control", retry_after=0.01)
    msg.delete.side_effect = [retry_err, True]
    result = await vanish_status_message(msg, delay=0.0)
    assert result is True
    assert msg.delete.await_count == 2


@pytest.mark.asyncio
async def test_reporter_vanish():
    """Verify AsyncProgressReporter.vanish cleanly stops task and vanishes message."""
    from unittest.mock import AsyncMock
    from utils.progress import AsyncProgressReporter

    msg = AsyncMock()
    tracker = DownloadProgressTracker("Test Media", "720p")
    reporter = AsyncProgressReporter(msg, tracker)
    reporter.start()
    await asyncio.sleep(0.02)
    vanished = await reporter.vanish(delay=0.0)
    assert vanished is True
    msg.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_reporter_vanish():
    """Verify AsyncUploadProgressReporter.vanish cleanly stops task and vanishes message."""
    from unittest.mock import AsyncMock
    from utils.progress import AsyncUploadProgressReporter

    msg = AsyncMock()
    tracker = UploadProgressTracker("Test Media", 1024)
    reporter = AsyncUploadProgressReporter(msg, tracker)
    reporter.start()
    await asyncio.sleep(0.02)
    vanished = await reporter.vanish(delay=0.0)
    assert vanished is True
    msg.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_reporter_stop_no_final_render():
    """Verify reporter.stop(render_final=False) does not edit message."""
    from unittest.mock import AsyncMock
    from utils.progress import AsyncProgressReporter

    msg = AsyncMock()
    tracker = DownloadProgressTracker("Test Media", "720p")
    reporter = AsyncProgressReporter(msg, tracker)
    await reporter.stop(render_final=False)
    msg.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_animation_helper_lifecycle():
    """Verify animation helper sends random animation and vanishes it on delivery."""
    from unittest.mock import AsyncMock, MagicMock
    from utils.animation_helper import get_available_animations, send_download_animation, vanish_download_animation

    animations = get_available_animations()
    assert len(animations) > 0

    mock_bot = MagicMock()
    mock_anim_msg = AsyncMock()
    mock_bot.send_animation = AsyncMock(return_value=mock_anim_msg)

    # Test send
    sent_msg = await send_download_animation(mock_bot, 123456)
    assert sent_msg is mock_anim_msg
    mock_bot.send_animation.assert_awaited_once()

    # Test vanish
    vanished = await vanish_download_animation(sent_msg, delay=0.0)
    assert vanished is True
    mock_anim_msg.delete.assert_awaited_once()


