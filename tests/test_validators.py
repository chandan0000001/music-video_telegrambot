"""
Unit Tests for URL, Input, and Filename Validators
Tests Scenarios:
9. Invalid YouTube URL is rejected.
10. Unsupported download source is handled.
Security: Path traversal protection, SSRF protection, size/duration limit checks.
"""
import pytest
from utils.validators import (
    is_valid_youtube_url,
    is_youtube_music_url,
    extract_youtube_id,
    sanitize_filename,
    validate_media_limits,
    is_safe_url
)
from config import MAX_DURATION_SECONDS


def test_valid_youtube_urls():
    """Verify accepted YouTube URL structures."""
    valid_urls = [
        "https://www.youtube.com/watch?v=BjL7AuPsmEk",
        "http://youtube.com/watch?v=BjL7AuPsmEk",
        "https://youtu.be/BjL7AuPsmEk",
        "https://m.youtube.com/watch?v=BjL7AuPsmEk",
        "https://music.youtube.com/watch?v=BjL7AuPsmEk",
        "https://www.youtube.com/shorts/BjL7AuPsmEk",
        "https://youtube.com/v/BjL7AuPsmEk"
    ]
    for url in valid_urls:
        assert is_valid_youtube_url(url) is True
        assert extract_youtube_id(url) == "BjL7AuPsmEk"


def test_scenario_9_invalid_youtube_urls():
    """Test scenario 9: Invalid YouTube URL is rejected."""
    invalid_urls = [
        "https://www.youtube.com/watch?v=invalid_short_id",
        "https://www.youtube.com/watch?v=toolongvideoid12345",
        "https://www.youtube.com/about",
        "https://www.youtube.com/",
        "https://youtu.be/",
        "not_a_url_at_all",
        "youtube.com/watch?v=BjL7AuPsmEk",  # missing scheme
        ""
    ]
    for url in invalid_urls:
        assert is_valid_youtube_url(url) is False


def test_scenario_10_unsupported_download_sources():
    """Test scenario 10: Unsupported download source is rejected (SSRF & non-YouTube domains)."""
    unsupported_urls = [
        "https://vimeo.com/12345678",
        "https://soundcloud.com/artist/track",
        "https://facebook.com/watch/?v=12345",
        "https://tiktok.com/@user/video/123",
        "http://127.0.0.1:8000/malicious.mp4",
        "http://localhost:5000/private",
        "http://192.168.1.1/secret",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd"
    ]
    for url in unsupported_urls:
        assert is_valid_youtube_url(url) is False


def test_sanitize_filename_prevents_traversal():
    """Ensure safe filenames without path traversal characters."""
    bad_inputs = [
        "../../../../etc/passwd",
        "song/with/slashes",
        'song*with?forbidden"chars<>|',
        "..\\..\\windows\\system32\\cmd.exe",
        "   normal song title   "
    ]
    for inp in bad_inputs:
        cleaned = sanitize_filename(inp)
        assert "/" not in cleaned
        assert "\\" not in cleaned
        assert ".." not in cleaned
        assert len(cleaned) > 0


def test_validate_media_limits():
    """Ensure limits on maximum duration and file size are enforced."""
    # Under limits
    ok, err = validate_media_limits(duration_seconds=300, filesize_bytes=10 * 1024 * 1024)
    assert ok is True
    assert err is None

    # Exceeding duration
    too_long, err = validate_media_limits(duration_seconds=MAX_DURATION_SECONDS + 60, filesize_bytes=10 * 1024 * 1024)
    assert too_long is False
    assert "Maximum allowed duration" in err

    # Exceeding upload size (e.g. 2100 MB when limit is 2000 MB)
    too_large, err = validate_media_limits(duration_seconds=300, filesize_bytes=2100 * 1024 * 1024)
    assert too_large is False
    assert "exceeds maximum upload limit" in err


def test_youtube_music_urls():
    """Verify specific detection and validation of YouTube Music links."""
    music_urls = [
        "https://music.youtube.com/watch?v=BjL7AuPsmEk",
        "https://music.youtube.com/watch?v=BjL7AuPsmEk&list=RDAMVMBjL7AuPsmEk",
        "https://music.youtube.com/watch?v=BjL7AuPsmEk&si=share_token_123",
        "https://music.youtube.com/watch?v=BjL7AuPsmEk&feature=share",
        "https://m.music.youtube.com/watch?v=BjL7AuPsmEk"
    ]
    for url in music_urls:
        assert is_valid_youtube_url(url) is True
        assert is_youtube_music_url(url) is True
        assert extract_youtube_id(url) == "BjL7AuPsmEk"

    # Standard YouTube links are valid YouTube URLs, but NOT YouTube Music URLs
    standard_urls = [
        "https://www.youtube.com/watch?v=BjL7AuPsmEk",
        "https://youtu.be/BjL7AuPsmEk",
        "https://www.youtube.com/shorts/BjL7AuPsmEk"
    ]
    for url in standard_urls:
        assert is_valid_youtube_url(url) is True
        assert is_youtube_music_url(url) is False

    # Untrusted / spoofed domains must be rejected completely
    malicious = [
        "https://music.youtube.com.attacker.com/watch?v=BjL7AuPsmEk",
        "https://attacker.com/?https://music.youtube.com/watch?v=BjL7AuPsmEk",
        "https://spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
    ]
    for url in malicious:
        assert is_valid_youtube_url(url) is False
        assert is_youtube_music_url(url) is False


def test_youtube_music_fast_keyboard():
    """Verify YouTube Music fast-download keyboard generation."""
    from keyboards.quality_keyboard import get_youtube_music_keyboard
    kb = get_youtube_music_keyboard("BjL7AuPsmEk")
    rows = kb.inline_keyboard
    assert len(rows) == 4
    # First row: MP3 320k and MP3 192k
    assert rows[0][0].callback_data == "adl:BjL7AuPsmEk:mp3:320"
    assert rows[0][1].callback_data == "adl:BjL7AuPsmEk:mp3:192"
    # Second row: MP3 128k and M4A
    assert rows[1][0].callback_data == "adl:BjL7AuPsmEk:mp3:128"
    assert rows[1][1].callback_data == "adl:BjL7AuPsmEk:m4a:192"
    # Third row: WAV and Video option
    assert rows[2][0].callback_data == "adl:BjL7AuPsmEk:wav:320"
    assert rows[2][1].callback_data == "opt_video:BjL7AuPsmEk"
    # Fourth row: Cancel
    assert rows[3][0].callback_data == "cancel_action"
