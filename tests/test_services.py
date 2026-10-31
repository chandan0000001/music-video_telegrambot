"""
Unit and Integration Tests for Media Services
Tests Scenarios:
3. User searches a song.
4. Five search results are displayed.
7. Authorized audio is processed with FFmpeg.
8. Authorized video is processed with FFmpeg.
11. Telegram upload failure is handled.
"""
import pytest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from aiogram.exceptions import TelegramEntityTooLarge, TelegramAPIError

from services.ffmpeg_service import ffmpeg_service
from services.music_service import music_service, format_duration
from services.telegram_service import telegram_service


@pytest.mark.asyncio
async def test_scenarios_3_and_4_search_music():
    """Test scenarios 3 & 4: Search music returns 5 structured results."""
    results = await music_service.search_music("Tum Hi Ho Arijit Singh", limit=5)
    assert len(results) == 5
    for item in results:
        assert "id" in item and len(item["id"]) > 0
        assert "title" in item and len(item["title"]) > 0
        assert "artist" in item
        assert "duration_str" in item
        assert "url" in item
        assert "https://www.youtube.com/watch?v=" in item["url"]


def test_format_duration_helper():
    """Verify duration formatting."""
    assert format_duration(65) == "01:05"
    assert format_duration(3665) == "1:01:05"
    assert format_duration(0) == "00:00"
    assert format_duration(None) == "Unknown"


@pytest.mark.asyncio
async def test_scenario_7_ffmpeg_audio_processing(tmp_path: Path):
    """Test scenario 7: Media engine converts audio to MP3, M4A, and WAV at requested bitrates."""
    # Generate 2-second test tone using ffmpeg lavfi
    src_wav = tmp_path / "tone.wav"
    gen_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", "sine=frequency=440:duration=2",
        str(src_wav)
    ]
    ok, _ = await ffmpeg_service._run_command(gen_cmd)
    assert ok is True
    assert src_wav.exists()

    # Convert to MP3 at 192 kbps
    out_mp3 = tmp_path / "output.mp3"
    converted_mp3 = await ffmpeg_service.convert_audio(src_wav, out_mp3, target_format="mp3", bitrate_kbps="192")
    assert converted_mp3 is True
    assert out_mp3.exists() and out_mp3.stat().st_size > 0

    # Probe MP3 metadata
    probe_mp3 = await ffmpeg_service.probe_media(out_mp3)
    assert probe_mp3 is not None
    assert probe_mp3["format"]["format_name"] == "mp3"

    # Convert to M4A at 128 kbps
    out_m4a = tmp_path / "output.m4a"
    converted_m4a = await ffmpeg_service.convert_audio(src_wav, out_m4a, target_format="m4a", bitrate_kbps="128")
    assert converted_m4a is True
    assert out_m4a.exists() and out_m4a.stat().st_size > 0

    # Convert to WAV
    out_wav = tmp_path / "output_conv.wav"
    converted_wav = await ffmpeg_service.convert_audio(src_wav, out_wav, target_format="wav")
    assert converted_wav is True
    assert out_wav.exists() and out_wav.stat().st_size > 0


@pytest.mark.asyncio
async def test_scenario_8_ffmpeg_video_processing(tmp_path: Path):
    """Test scenario 8: Media engine remuxes video to MP4 and generates thumbnail."""
    # Generate 2-second test video
    src_video = tmp_path / "test_video.mp4"
    gen_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-c:a", "aac",
        str(src_video)
    ]
    ok, _ = await ffmpeg_service._run_command(gen_cmd)
    assert ok is True
    assert src_video.exists()

    # Remux for Telegram
    out_video = tmp_path / "telegram_video.mp4"
    remuxed = await ffmpeg_service.remux_video_for_telegram(
        video_input=src_video,
        audio_input=None,
        output_file=out_video
    )
    assert remuxed is True
    assert out_video.exists() and out_video.stat().st_size > 0

    # Extract thumbnail
    thumb_file = tmp_path / "thumb.jpg"
    thumb_ok = await ffmpeg_service.extract_thumbnail(out_video, thumb_file)
    assert thumb_ok is True
    assert thumb_file.exists() and thumb_file.stat().st_size > 0


@pytest.mark.asyncio
async def test_scenario_11_telegram_upload_failure_handling(tmp_path: Path):
    """Test scenario 11: Telegram file upload failure is safely handled with feedback."""
    mock_bot = MagicMock()
    mock_bot.send_audio = AsyncMock(side_effect=TelegramEntityTooLarge(method="sendAudio", message="File too large"))
    mock_bot.send_message = AsyncMock()

    dummy_file = tmp_path / "test_song.mp3"
    dummy_file.write_bytes(b"mock_audio_content")

    sent = await telegram_service.send_audio_file(
        bot=mock_bot,
        chat_id=12345,
        file_path=dummy_file,
        title="Test Song",
        artist="Test Artist",
        duration=120
    )
    assert sent is False
    mock_bot.send_message.assert_awaited_once()
    assert "File too large" in mock_bot.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_ffmpeg_split_video_into_chunks(tmp_path: Path):
    """Test splitting large video into smaller playable chunks without quality loss."""
    src_video = tmp_path / "test_split.mp4"
    gen_cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=10:size=320x240:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
        "-c:v", "libx264", "-g", "10", "-c:a", "aac",
        str(src_video)
    ]
    ok, _ = await ffmpeg_service._run_command(gen_cmd)
    assert ok is True

    chunks_dir = tmp_path / "chunks"
    parts = await ffmpeg_service.split_video_into_chunks(
        input_file=src_video,
        output_dir=chunks_dir,
        duration=10,
        target_chunk_bytes=50 * 1024  # small threshold to trigger splitting
    )
    assert len(parts) >= 2
    for p in parts:
        assert p.exists() and p.stat().st_size > 0
        probe = await ffmpeg_service.probe_media(p)
        assert probe is not None
        assert "streams" in probe
