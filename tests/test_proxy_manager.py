"""
Unit tests for YouTubeProxyManager:
- Verifies round-robin cycling through proxies.
- Verifies strict URL filtering (only YouTube, not Instagram/Twitter/Spotify).
- Verifies retryable error detection (403 Forbidden, bot challenge, etc.).
"""

import pytest
from services.proxy_manager import YouTubeProxyManager, DEFAULT_YOUTUBE_PROXIES

def test_round_robin_rotation():
    test_proxies = ["http://proxy1:8080", "http://proxy2:8080", "http://proxy3:8080"]
    mgr = YouTubeProxyManager(test_proxies)
    
    # Verify cyclic rotation
    assert mgr.get_next_proxy() == "http://proxy1:8080"
    assert mgr.get_next_proxy() == "http://proxy2:8080"
    assert mgr.get_next_proxy() == "http://proxy3:8080"
    assert mgr.get_next_proxy() == "http://proxy1:8080"
    assert mgr.get_next_proxy() == "http://proxy2:8080"

def test_is_youtube_url_detection():
    mgr = YouTubeProxyManager()
    
    # YouTube URLs
    assert mgr.is_youtube_url("https://www.youtube.com/watch?v=HahvIxrXIi0") is True
    assert mgr.is_youtube_url("https://youtu.be/HahvIxrXIi0") is True
    assert mgr.is_youtube_url("https://m.youtube.com/watch?v=0S5rmEiFSoA") is True
    assert mgr.is_youtube_url("ytsearch5:Alan Walker") is True
    
    # Non-YouTube URLs (must be False!)
    assert mgr.is_youtube_url("https://www.instagram.com/p/C_abc123/") is False
    assert mgr.is_youtube_url("https://twitter.com/user/status/123456") is False
    assert mgr.is_youtube_url("https://x.com/user/status/123456") is False
    assert mgr.is_youtube_url("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT") is False
    assert mgr.is_youtube_url("https://www.jiosaavn.com/song/xyz/123") is False
    assert mgr.is_youtube_url("https://soundcloud.com/artist/track") is False
    assert mgr.is_youtube_url("") is False

def test_is_retryable_error():
    mgr = YouTubeProxyManager()
    
    assert mgr.is_retryable_error("HTTP Error 403: Forbidden") is True
    assert mgr.is_retryable_error("Sign in to confirm you're not a bot") is True
    assert mgr.is_retryable_error("Requested format is not available") is True
    assert mgr.is_retryable_error("Connection timed out (>25s)") is True
    assert mgr.is_retryable_error("HTTP Error 429: Too Many Requests") is True
    assert mgr.is_retryable_error("HTTP Error 402: Payment Required") is True
    assert mgr.is_retryable_error("X-Webshare-Reason: bandwidthlimit") is True
    
    # Non-retryable user errors
    assert mgr.is_retryable_error("Video unavailable: This video has been removed by the uploader") is False
    assert mgr.is_retryable_error("Private video. Sign in if you've been granted access") is False

def test_mark_exhausted_proxy():
    test_proxies = ["http://proxy1:8080", "http://proxy2:8080"]
    mgr = YouTubeProxyManager(test_proxies)
    
    # Mark proxy1 exhausted (e.g. 402 bandwidth limit)
    mgr.mark_exhausted("http://proxy1:8080")
    assert mgr.get_proxies() == ["http://proxy2:8080"]
    assert mgr.get_next_proxy() == "http://proxy2:8080"
    assert mgr.get_next_proxy() == "http://proxy2:8080"

