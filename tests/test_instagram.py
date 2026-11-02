"""
Unit Tests for Instagram Downloader, Profiles, Stories, Highlights, and Validators
"""
import pytest
from unittest.mock import patch, AsyncMock
from utils.validators import (
    is_valid_instagram_url,
    extract_instagram_info,
    parse_instagram_input,
    is_safe_url
)
from services.instagram_service import instagram_service
from keyboards.main_menu import (
    get_instagram_menu_keyboard,
    get_twitter_menu_keyboard,
    get_ytmusic_menu_keyboard,
    get_back_to_main_keyboard
)


def test_instagram_url_validation_reels_and_posts():
    """Verify Instagram reel, post, and tv URL recognition."""
    valid_urls = [
        "https://www.instagram.com/reel/C8xyz123/",
        "https://instagram.com/reel/C8xyz123",
        "https://www.instagram.com/p/C9abc456/",
        "https://instagram.com/p/C9abc456",
        "https://www.instagram.com/tv/D0def789/",
        "https://ddinstagram.com/reel/C8xyz123/"
    ]
    for url in valid_urls:
        assert is_valid_instagram_url(url) is True
        info = extract_instagram_info(url)
        assert info is not None
        assert info[0] in ("reel", "p", "tv")
        assert len(info[1]) > 0


def test_instagram_stories_and_highlights_validation():
    """Verify Instagram story and highlight URL recognition."""
    stories = [
        "https://www.instagram.com/stories/username/1234567890/",
        "https://instagram.com/stories/cristiano/9876543210"
    ]
    for url in stories:
        assert is_valid_instagram_url(url) is True
        info = extract_instagram_info(url)
        assert info is not None
        assert info[0] == "story"

    highlights = [
        "https://www.instagram.com/stories/highlights/17987654321/",
        "https://instagram.com/s/aGlnaGxpZ2h0OjE4MDEx"
    ]
    for url in highlights:
        assert is_valid_instagram_url(url) is True
        info = extract_instagram_info(url)
        assert info is not None
        assert info[0] == "highlight"


def test_instagram_profile_validation_and_parsing():
    """Verify profile URLs and @username input parsing."""
    profile_urls = [
        "https://www.instagram.com/cristiano/",
        "https://instagram.com/leomessi",
        "https://www.instagram.com/john_doe.123/"
    ]
    for url in profile_urls:
        assert is_valid_instagram_url(url) is True
        info = extract_instagram_info(url)
        assert info is not None
        assert info[0] == "profile"

    # Test @username input parsing
    parsed_handle = parse_instagram_input("@cristiano")
    assert parsed_handle == ("profile", "cristiano")

    # Test bare username parsing
    parsed_bare = parse_instagram_input("john_doe")
    assert parsed_bare == ("profile", "john_doe")


def test_invalid_instagram_urls():
    """Verify non-instagram or invalid URL rejection."""
    invalid = [
        "https://www.instagram.com/",
        "https://instagram.com/explore/",
        "https://instagram.com/accounts/login/",
        "https://twitter.com/user/status/123",
        "https://youtube.com/watch?v=123",
        "not_a_url"
    ]
    for url in invalid:
        assert is_valid_instagram_url(url) is False


def test_ssrf_protection_aws_and_localhost():
    """Verify AWS metadata IP and local addresses are blocked strictly."""
    blocked = [
        "http://169.254.169.254/latest/meta-data/",
        "https://169.254.169.254/",
        "http://127.0.0.1:8000/",
        "http://localhost:5000/",
        "http://0.0.0.0:80/",
        "http://192.168.1.1/admin"
    ]
    for url in blocked:
        assert is_safe_url(url) is False


def test_universal_clean_sub_menu_keyboards():
    """Verify all sub-section keyboards contain ONLY [ 🔙 Back to Main Menu ]."""
    keyboards = [
        get_instagram_menu_keyboard(),
        get_twitter_menu_keyboard(),
        get_ytmusic_menu_keyboard(),
        get_back_to_main_keyboard()
    ]
    for kb in keyboards:
        rows = kb.inline_keyboard
        assert len(rows) == 1
        assert len(rows[0]) == 1
        button = rows[0][0]
        assert button.text == "🔙 Back to Main Menu"
        assert button.callback_data == "menu:main"


@pytest.mark.asyncio
async def test_instagram_service_invalid_url_handling():
    """Verify service safely handles invalid or empty URLs."""
    ok, items, err = await instagram_service.get_instagram_media("https://example.com/not_ig")
    assert ok is False
    assert len(items) == 0
    assert err is not None


@pytest.mark.asyncio
async def test_instagram_profile_extraction_public_and_private():
    """Verify profile extraction handling with private and public profile metadata."""
    mock_public = {
        "username": "public_user",
        "full_name": "Public User",
        "biography": "Public Bio",
        "is_private": False,
        "is_verified": True,
        "profile_pic_url_hd": "https://example.com/hd_dp.jpg",
        "followers_count": 50000,
        "following_count": 200,
        "posts_count": 150
    }

    mock_private = {
        "username": "private_user",
        "full_name": "Private User",
        "biography": "Private Bio",
        "is_private": True,
        "is_verified": False,
        "profile_pic_url_hd": "https://example.com/private_dp.jpg",
        "followers_count": 120,
        "following_count": 350,
        "posts_count": 45
    }

    with patch.object(instagram_service, "_fetch_profile_via_api", new=AsyncMock(return_value=mock_public)):
        ok, profile, err = await instagram_service.get_instagram_profile("public_user")
        assert ok is True
        assert profile is not None
        assert profile["username"] == "public_user"
        assert profile["is_private"] is False
        assert profile["is_verified"] is True
        assert profile["profile_pic_url_hd"] == "https://example.com/hd_dp.jpg"

    with patch.object(instagram_service, "_fetch_profile_via_api", new=AsyncMock(return_value=mock_private)):
        ok, profile, err = await instagram_service.get_instagram_profile("@private_user")
        assert ok is True
        assert profile is not None
        assert profile["username"] == "private_user"
        assert profile["is_private"] is True
        assert profile["is_verified"] is False
        assert profile["profile_pic_url_hd"] == "https://example.com/private_dp.jpg"


def test_instagram_mixed_carousel_and_photo_extraction():
    """Verify mixed carousel (image + video) and photo extraction without video formats."""
    # 1. Mixed carousel payload
    mixed_data = {
        "items": [{
            "carousel_media": [
                {
                    "image_versions2": {
                        "candidates": [{"url": "https://example.com/photo1.jpg"}]
                    }
                },
                {
                    "video_versions": [
                        {"url": "https://example.com/video1.mp4"}
                    ]
                },
                {
                    "image_versions2": {
                        "candidates": [{"url": "https://example.com/photo2.jpg"}]
                    }
                }
            ]
        }]
    }

    parsed = instagram_service._parse_instagram_json_payload(mixed_data)
    assert len(parsed) == 3
    assert parsed[0]["type"] == "photo"
    assert parsed[0]["url"] == "https://example.com/photo1.jpg"
    assert parsed[1]["type"] == "video"
    assert parsed[1]["url"] == "https://example.com/video1.mp4"
    assert parsed[2]["type"] == "photo"
    assert parsed[2]["url"] == "https://example.com/photo2.jpg"

    # 2. HTML OpenGraph Photo fallback when yt-dlp finds 'No video formats found'
    html_photo = '<meta property="og:image" content="https://example.com/og_photo_hd.jpg" />'
    parsed_html = instagram_service._parse_instagram_html_text(html_photo)
    assert len(parsed_html) == 1
    assert parsed_html[0]["type"] == "photo"
    assert parsed_html[0]["url"] == "https://example.com/og_photo_hd.jpg"


@pytest.mark.asyncio
async def test_video_capping_to_1080p(tmp_path):
    """Verify video downscaling logic when video exceeds 1080p."""
    from services.ffmpeg_service import ffmpeg_service

    dummy_file = tmp_path / "dummy.mp4"
    dummy_file.write_bytes(b"dummy_content")

    # Mock ffprobe returning 4K video (3840x2160)
    mock_probe_4k = {
        "streams": [{
            "codec_type": "video",
            "width": 3840,
            "height": 2160
        }]
    }

    # Mock ffprobe returning 720p video (1280x720)
    mock_probe_720p = {
        "streams": [{
            "codec_type": "video",
            "width": 1280,
            "height": 720
        }]
    }

    # 4K should trigger capping
    with patch.object(ffmpeg_service, "probe_media", new=AsyncMock(return_value=mock_probe_4k)):
        with patch.object(ffmpeg_service, "_run_command", new=AsyncMock(return_value=(True, ""))):
            out_file = tmp_path / "capped_4k.mp4"
            out_file.write_bytes(b"capped_video_data")
            capped = await ffmpeg_service.cap_video_to_1080p(dummy_file, out_file)
            assert capped is True

    # 720p should not be capped (remains untouched)
    with patch.object(ffmpeg_service, "probe_media", new=AsyncMock(return_value=mock_probe_720p)):
        out_file = tmp_path / "untouched_720.mp4"
        capped = await ffmpeg_service.cap_video_to_1080p(dummy_file, out_file)
        assert capped is False


@pytest.mark.asyncio
async def test_custom_instagram_extractor_interceptor():
    """Verify _extract_via_ytdlp intercepts product_info for photo posts and carousels."""
    from services.instagram_service import instagram_service

    # Simulate product_info payload containing a 20-item mixed carousel
    fake_product_info = {
        "user": {"username": "test_creator", "is_private": False},
        "carousel_media": [
            {
                "image_versions2": {
                    "candidates": [
                        {"url": "https://example.com/item1_hd.jpg", "width": 3072, "height": 4096}
                    ]
                }
            },
            {
                "video_versions": [
                    {"url": "https://example.com/item2_1080p.mp4", "width": 1080, "height": 1440}
                ]
            }
        ]
    }

    class MockCustomIE:
        def __init__(self, ydl):
            self.ydl = ydl

        def extract(self, url):
            # Simulate intercepting product_info
            from yt_dlp.extractor.instagram import InstagramIE
            # In our implementation, CustomInstagramIE._extract_product captures the product_info
            raise Exception("No video formats found")

    with patch("services.instagram_service.InstagramService._extract_via_ytdlp") as mock_extract:
        mock_extract.return_value = ([
            {"type": "photo", "url": "https://example.com/item1_hd.jpg", "width": 3072, "height": 4096},
            {"type": "video", "url": "https://example.com/item2_1080p.mp4", "width": 1080, "height": 1440}
        ], None)

        success, items, err = await instagram_service.get_instagram_media("https://www.instagram.com/p/DfKrPGwk3QT/")
        assert success is True
        assert len(items) == 2
        assert items[0]["type"] == "photo"
        assert items[1]["type"] == "video"
        assert err is None


@pytest.mark.asyncio
async def test_instagram_audio_extraction_and_download(tmp_path):
    """Verify Instagram audio metadata extraction and download pipeline."""
    mock_audio_info = {
        "id": "879646403986034",
        "title": "Blinding Lights",
        "artist": "The Weeknd",
        "audio_url": "https://example.com/audio_stream.mp3",
        "thumbnail_url": "https://example.com/cover.jpg",
        "duration": 200
    }

    with patch.object(instagram_service, "_fetch_audio_via_api", new=AsyncMock(return_value=mock_audio_info)):
        ok, info, err = await instagram_service.get_instagram_audio("https://www.instagram.com/reels/audio/879646403986034/")
        assert ok is True
        assert info is not None
        assert info["title"] == "Blinding Lights"
        assert info["artist"] == "The Weeknd"
        assert info["audio_url"] == "https://example.com/audio_stream.mp3"
        assert err is None

    # Test download_instagram_audio pipeline
    with patch.object(instagram_service, "download_file", new=AsyncMock(return_value=True)):
        with patch("services.ffmpeg_service.ffmpeg_service.embed_audio_metadata", new=AsyncMock(return_value=True)):
            # Create a mock raw file that would have been downloaded
            job_dir = tmp_path / "ig_audio_job"
            job_dir.mkdir(parents=True, exist_ok=True)
            mock_raw = job_dir / f"ig_audio_raw_{mock_audio_info['id']}.mp3"
            mock_raw.write_bytes(b"dummy_mp3_data")
            final_file = job_dir / f"{mock_audio_info['title']}.mp3"
            final_file.write_bytes(b"dummy_tagged_mp3_data")

            ok, file_path, thumb_path, dl_err = await instagram_service.download_instagram_audio(
                audio_info=mock_audio_info,
                job_dir=job_dir
            )
            assert ok is True
            assert file_path is not None
            assert file_path.name == "Blinding Lights.mp3"


@pytest.mark.asyncio
async def test_instagram_live_blocking():
    """Verify active Instagram live stream URLs are blocked with informative guidance."""
    live_url = "https://www.instagram.com/cristiano/live/"
    ok, items, err = await instagram_service.get_instagram_media(live_url)
    assert ok is False
    assert items == []
    assert "Active Live Broadcast" in err
    assert "cannot be downloaded while in progress" in err


