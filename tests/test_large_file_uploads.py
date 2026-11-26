"""
Unit Tests for Large File (> 48MB / 50MB) Upload Handling Across All Media Modes.
Ensures that files exceeding Telegram Bot API's 50MB HTTP limit are routed
via MTProto (up to 2GB) or lossless chunk splitting without crashing.
"""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock, PropertyMock

from services.telegram_service import TelegramService
from services.mtproto_service import mtproto_service, MTProtoService


@pytest.mark.asyncio
async def test_large_video_upload_routes_to_mtproto(tmp_path: Path):
    """Test that a 85MB video uses MTProto 2GB engine when MTProto is ready."""
    test_video = tmp_path / "large_video.mp4"
    with open(test_video, "wb") as f:
        f.seek(85 * 1024 * 1024)
        f.write(b"0")

    mock_bot = MagicMock()
    mock_status_msg = AsyncMock()

    with patch.object(MTProtoService, "is_configured", new_callable=PropertyMock(return_value=True)), \
         patch.object(mtproto_service, "is_ready", AsyncMock(return_value=True)), \
         patch.object(mtproto_service, "send_video", AsyncMock(return_value=True)) as mock_send_video:

        sent = await TelegramService.send_video_file(
            bot=mock_bot,
            chat_id=12345,
            file_path=test_video,
            title="Large 85MB Video",
            duration=120,
            width=1920,
            height=1080,
            status_message=mock_status_msg
        )

        assert sent is True
        mock_send_video.assert_called_once()
        assert mock_send_video.call_args[1]["chat_id"] == 12345
        assert mock_send_video.call_args[1]["height"] == 1080


@pytest.mark.asyncio
async def test_large_video_upload_splits_chunks_when_mtproto_disabled(tmp_path: Path):
    """Test that when MTProto is not available, a >48MB video splits into lossless chunks."""
    test_video = tmp_path / "large_video_split.mp4"
    with open(test_video, "wb") as f:
        f.seek(80 * 1024 * 1024)
        f.write(b"0")

    part1 = tmp_path / "part_001.mp4"
    part2 = tmp_path / "part_002.mp4"
    with open(part1, "wb") as f:
        f.seek(40 * 1024 * 1024)
        f.write(b"0")
    with open(part2, "wb") as f:
        f.seek(40 * 1024 * 1024)
        f.write(b"0")

    mock_bot = AsyncMock()
    mock_bot.send_video = AsyncMock(return_value=MagicMock())
    mock_status_msg = AsyncMock()

    with patch.object(MTProtoService, "is_configured", new_callable=PropertyMock(return_value=False)), \
         patch("services.telegram_service.ffmpeg_service.split_video_into_chunks", AsyncMock(return_value=[part1, part2])):

        sent = await TelegramService.send_video_file(
            bot=mock_bot,
            chat_id=12345,
            file_path=test_video,
            title="Large Video For Chunking",
            duration=200,
            width=1920,
            height=1080,
            status_message=mock_status_msg
        )

        assert sent is True
        assert mock_bot.send_video.call_count == 2


@pytest.mark.asyncio
async def test_large_audio_upload_routes_to_mtproto(tmp_path: Path):
    """Test that a 65MB audio file uses MTProto 2GB engine when MTProto is ready."""
    test_audio = tmp_path / "large_podcast.mp3"
    with open(test_audio, "wb") as f:
        f.seek(65 * 1024 * 1024)
        f.write(b"0")

    mock_bot = MagicMock()
    mock_status_msg = AsyncMock()

    with patch.object(MTProtoService, "is_configured", new_callable=PropertyMock(return_value=True)), \
         patch.object(mtproto_service, "is_ready", AsyncMock(return_value=True)), \
         patch.object(mtproto_service, "send_audio", AsyncMock(return_value=True)) as mock_send_audio:

        sent = await TelegramService.send_audio_file(
            bot=mock_bot,
            chat_id=12345,
            file_path=test_audio,
            title="Large Audio 65MB",
            artist="Podcaster",
            duration=3600,
            status_message=mock_status_msg
        )

        assert sent is True
        mock_send_audio.assert_called_once()
        assert mock_send_audio.call_args[1]["chat_id"] == 12345
        assert mock_send_audio.call_args[1]["title"] == "Large Audio 65MB"


@pytest.mark.asyncio
async def test_large_audio_upload_splits_chunks_when_mtproto_disabled(tmp_path: Path):
    """Test that when MTProto is not available, a >48MB audio splits into lossless chunks."""
    test_audio = tmp_path / "large_audio_split.mp3"
    with open(test_audio, "wb") as f:
        f.seek(90 * 1024 * 1024)
        f.write(b"0")

    part1 = tmp_path / "audio_part_001.mp3"
    part2 = tmp_path / "audio_part_002.mp3"
    with open(part1, "wb") as f:
        f.seek(44 * 1024 * 1024)
        f.write(b"0")
    with open(part2, "wb") as f:
        f.seek(44 * 1024 * 1024)
        f.write(b"0")

    mock_bot = AsyncMock()
    mock_bot.send_audio = AsyncMock(return_value=MagicMock())
    mock_status_msg = AsyncMock()

    with patch.object(MTProtoService, "is_configured", new_callable=PropertyMock(return_value=False)), \
         patch("services.telegram_service.ffmpeg_service.split_audio_into_chunks", AsyncMock(return_value=[part1, part2])):

        sent = await TelegramService.send_audio_file(
            bot=mock_bot,
            chat_id=12345,
            file_path=test_audio,
            title="Long Audiobook",
            artist="Author",
            duration=7200,
            status_message=mock_status_msg
        )

        assert sent is True
        assert mock_bot.send_audio.call_count == 2
