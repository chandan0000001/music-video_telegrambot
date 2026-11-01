"""
Unit Tests for Twitter / X Downloader Service, Validators, and Keyboards
"""
import pytest
from unittest.mock import patch, MagicMock

from utils.validators import is_valid_twitter_url, extract_twitter_info
from services.twitter_service import twitter_service
from keyboards.main_menu import get_main_menu_keyboard, get_twitter_menu_keyboard


def test_twitter_url_validation():
    """Test valid and invalid Twitter/X URL patterns."""
    valid_urls = [
        "https://x.com/SpaceX/status/1852000000000000000",
        "https://twitter.com/SpaceX/status/1852000000000000000",
        "https://www.x.com/elonmusk/status/1234567890?s=20",
        "https://mobile.twitter.com/NASA/status/9876543210",
        "https://x.com/i/status/123456789",
        "https://x.com/username/status/1122334455/photo/1",
        "https://x.com/username/status/1122334455/video/1",
    ]
    for url in valid_urls:
        assert is_valid_twitter_url(url) is True, f"Failed for {url}"

    invalid_urls = [
        "https://x.com/home",
        "https://x.com/explore",
        "https://twitter.com/settings",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://instagram.com/p/ABC",
        "http://127.0.0.1/status/123",
        "not a url",
        "",
        "https://x.com/user/notstatus/123"
    ]
    for url in invalid_urls:
        assert is_valid_twitter_url(url) is False, f"Should be invalid: {url}"


def test_twitter_info_extraction():
    """Test extracting username and status ID from URLs."""
    url = "https://x.com/SpaceX/status/1852000000000000000?s=20&t=xyz"
    info = extract_twitter_info(url)
    assert info is not None
    assert info[0] == "SpaceX"
    assert info[1] == "1852000000000000000"


def test_make_orig_photo_url():
    """Test that photo URLs are upgraded to original uncompressed quality (?name=orig)."""
    # Case 1: Query param with name=large
    url1 = "https://pbs.twimg.com/media/ExamplePhotoKey.jpg?format=jpg&name=large"
    orig1 = twitter_service._make_orig_photo_url(url1)
    assert "name=orig" in orig1

    # Case 2: No query param
    url2 = "https://pbs.twimg.com/media/ExamplePhotoKey.jpg"
    orig2 = twitter_service._make_orig_photo_url(url2)
    assert "name=orig" in orig2


def test_fxtwitter_parsing_photos():
    """Test parsing FxTwitter JSON response for multiple photos."""
    mock_data = {
        "code": 200,
        "message": "OK",
        "tweet": {
            "possibly_sensitive": False,
            "media": {
                "photos": [
                    {"url": "https://pbs.twimg.com/media/photo1.jpg", "width": 1200, "height": 800},
                    {"url": "https://pbs.twimg.com/media/photo2.jpg", "width": 1080, "height": 1080}
                ]
            }
        }
    }
    success, items, err = twitter_service._parse_fxtwitter_response(mock_data)
    assert success is True
    assert err is None
    assert len(items) == 2
    assert items[0]["type"] == "photo"
    assert "name=orig" in items[0]["url"]
    assert items[1]["type"] == "photo"


def test_fxtwitter_parsing_videos_and_gifs():
    """Test parsing FxTwitter JSON response for videos and GIFs."""
    mock_data = {
        "code": 200,
        "message": "OK",
        "tweet": {
            "possibly_sensitive": False,
            "media": {
                "videos": [
                    {
                        "type": "video",
                        "url": "https://video.twimg.com/vid1.mp4",
                        "thumbnail_url": "https://pbs.twimg.com/thumb.jpg",
                        "duration": 45.2
                    },
                    {
                        "type": "gif",
                        "url": "https://video.twimg.com/gif1.mp4",
                        "thumbnail_url": "https://pbs.twimg.com/thumb2.jpg"
                    }
                ]
            }
        }
    }
    success, items, err = twitter_service._parse_fxtwitter_response(mock_data)
    assert success is True
    assert err is None
    assert len(items) == 2
    assert items[0]["type"] == "video"
    assert items[0]["url"] == "https://video.twimg.com/vid1.mp4"
    assert items[1]["type"] == "gif"
    assert items[1]["url"] == "https://video.twimg.com/gif1.mp4"


def test_fxtwitter_sensitive_media_blocked():
    """Test that 18+/sensitive media is rejected with clean error message."""
    mock_data = {
        "code": 200,
        "message": "OK",
        "tweet": {
            "possibly_sensitive": True,
            "media": {
                "videos": [{"type": "video", "url": "https://video.twimg.com/sensitive.mp4"}]
            }
        }
    }
    success, items, err = twitter_service._parse_fxtwitter_response(mock_data)
    assert success is False
    assert len(items) == 0
    assert "sensitive or age-restricted" in err


def test_fxtwitter_max_15_items_limit():
    """Test that tweets with many items are capped at maximum 15 items."""
    mock_photos = [{"url": f"https://pbs.twimg.com/photo_{i}.jpg"} for i in range(25)]
    mock_data = {
        "code": 200,
        "message": "OK",
        "tweet": {
            "possibly_sensitive": False,
            "media": {
                "photos": mock_photos
            }
        }
    }
    success, items, err = twitter_service._parse_fxtwitter_response(mock_data)
    assert success is True
    assert len(items) == 15


def test_twitter_keyboards():
    """Test main menu and Twitter hub keyboard generation."""
    main_kb = get_main_menu_keyboard(is_admin=False)
    button_callbacks = [
        btn.callback_data
        for row in main_kb.inline_keyboard
        for btn in row
        if btn.callback_data
    ]
    assert "menu:twitter" in button_callbacks

    twitter_kb = get_twitter_menu_keyboard()
    twitter_callbacks = [
        btn.callback_data
        for row in twitter_kb.inline_keyboard
        for btn in row
        if btn.callback_data
    ]
    assert "menu:main" in twitter_callbacks
