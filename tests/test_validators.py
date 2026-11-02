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
    is_safe_url,
    classify_media_restriction
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


def test_classify_media_restriction():
    """Verify classification of Members-Only, Private, Age-restricted, and Live streams."""
    # 1. Members-only error
    err_members = "ERROR: [youtube] dQw4w9WgXcQ: This video is available to this channel's members on level VIP. Join this channel to get access."
    cat, msg = classify_media_restriction(err_members)
    assert cat == "members_only"
    assert "Members-Only Category" in msg

    # 2. Private error
    err_private = "ERROR: [youtube] dQw4w9WgXcQ: Private video. Sign in if you've been granted access to this video"
    cat, msg = classify_media_restriction(err_private)
    assert cat == "private"
    assert "Private Category" in msg

    # 3. Instagram Private
    err_ig_private = "This content belongs to a Private Account and cannot be downloaded directly."
    cat, msg = classify_media_restriction(err_ig_private)
    assert cat == "private"
    assert "Private Category" in msg

    # 4. Age restricted
    err_age = "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm your age. This video may be inappropriate for some users."
    cat, msg = classify_media_restriction(err_age)
    assert cat == "age_restricted"
    assert "Age-Gated Content" in msg

    # 5. Live / Premiere
    err_live = "ERROR: [youtube] dQw4w9WgXcQ: This live event will begin in 2 hours."
    cat, msg = classify_media_restriction(err_live)
    assert cat == "live_premiere"
    assert "Live Stream" in msg

    err_live_active = "ERROR: [youtube] dQw4w9WgXcQ: This video is currently live and recording is not allowed"
    cat, msg = classify_media_restriction(err_live_active)
    assert cat == "live_premiere"
    assert "Active Live Stream" in msg

    # 6. Ended live streams must NOT be classified as active live restrictions
    err_ended_live = "This live event has ended and recording is now processing"
    assert classify_media_restriction(err_ended_live) is None

    # 7. Unrelated error returns None
    assert classify_media_restriction("Network connection timeout 504") is None
    assert classify_media_restriction(None) is None


def test_youtube_playlist_detection():
    """Verify detection of YouTube playlist URLs and single videos with playlist params."""
    from utils.validators import is_youtube_playlist_url

    # Pure playlist links must be detected as playlists
    pure_playlists = [
        "https://www.youtube.com/playlist?list=PLrEnWoR732-BHrPp_QLgkMNl8774J565P",
        "https://youtube.com/playlist?list=PL12345",
        "https://m.youtube.com/playlist?list=PL12345",
        "https://music.youtube.com/playlist?list=PLrEnWoR732-BHrPp_QLgkMNl8774J565P",
        "https://www.youtube.com/watch?list=PLrEnWoR732-BHrPp_QLgkMNl8774J565P",
    ]
    for url in pure_playlists:
        assert is_youtube_playlist_url(url) is True

    # Single videos (even if copied from a playlist with &list=) must NOT be blocked as playlists
    video_with_playlist = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrEnWoR732-BHrPp_QLgkMNl8774J565P&index=1"
    assert is_youtube_playlist_url(video_with_playlist) is False
    assert is_valid_youtube_url(video_with_playlist) is True
    assert extract_youtube_id(video_with_playlist) == "dQw4w9WgXcQ"

    # Standard video links are not playlists
    assert is_youtube_playlist_url("https://youtu.be/dQw4w9WgXcQ") is False
    assert is_youtube_playlist_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False


def test_instagram_audio_and_live_url_extraction():
    """Verify extraction of Instagram audio and live stream links."""
    from utils.validators import extract_instagram_info, is_valid_instagram_url

    # Instagram Audio URLs
    audio_urls = [
        "https://www.instagram.com/reels/audio/879646403986034/",
        "https://www.instagram.com/reel/audio/879646403986034",
        "https://instagram.com/audio/879646403986034?igsh=MWZzZmszbG51NGw3YQ==",
        "https://www.instagram.com/music/879646403986034",
    ]
    for url in audio_urls:
        assert is_valid_instagram_url(url) is True
        cat, ident = extract_instagram_info(url)
        assert cat == "audio"
        assert ident == "879646403986034"

    # Instagram Live URLs
    live_urls = [
        "https://www.instagram.com/cristiano/live/",
        "https://instagram.com/live/17928374928374",
    ]
    for url in live_urls:
        assert is_valid_instagram_url(url) is True
        cat, ident = extract_instagram_info(url)
        assert cat == "live"
        assert ident in ("cristiano", "17928374928374")

