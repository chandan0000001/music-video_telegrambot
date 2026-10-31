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
    extract_youtube_id,
    sanitize_filename,
    validate_media_limits,
    is_safe_url
)


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

    # Exceeding duration (e.g. 2 hours when limit is 1 hour)
    too_long, err = validate_media_limits(duration_seconds=7200, filesize_bytes=10 * 1024 * 1024)
    assert too_long is False
    assert "Maximum allowed duration" in err

    # Exceeding upload size (e.g. 2100 MB when limit is 2000 MB)
    too_large, err = validate_media_limits(duration_seconds=300, filesize_bytes=2100 * 1024 * 1024)
    assert too_large is False
    assert "exceeds maximum upload limit" in err
