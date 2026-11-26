"""
services/proxy_manager.py - Round-robin proxy rotator dedicated to YouTube operations.
Strictly isolated:
- Applied ONLY to YouTube metadata and downloads (never used for Spotify, SoundCloud, JioSaavn, etc.).
- Direct-First: Always hits with the server's Own IP first.
- Fallback: On failure (403 Forbidden, bot challenge, format not available, timeout),
  proceeds through the round-robin pool of high-speed proxies.
"""

import threading
import logging
from typing import Optional, List
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# The 10 WebShare proxies provided by the user
DEFAULT_YOUTUBE_PROXIES: List[str] = [
    "http://gpzyewzw:7yo2ov1b9hfp@45.38.107.97:6014",
    "http://gpzyewzw:7yo2ov1b9hfp@64.137.96.74:6641",
    "http://gpzyewzw:7yo2ov1b9hfp@198.23.243.226:6361",
    "http://gpzyewzw:7yo2ov1b9hfp@38.154.185.97:6370",
    "http://gpzyewzw:7yo2ov1b9hfp@84.247.60.125:6095",
    "http://gpzyewzw:7yo2ov1b9hfp@191.96.254.138:6185",
    "http://gpzyewzw:7yo2ov1b9hfp@198.46.161.42:5092",
    "http://gpzyewzw:7yo2ov1b9hfp@64.137.10.153:5803",
    "http://gpzyewzw:7yo2ov1b9hfp@104.164.49.38:7693",
    "http://gpzyewzw:7yo2ov1b9hfp@23.26.71.145:5628",
]

class YouTubeProxyManager:
    """Thread-safe round-robin proxy rotator specifically for YouTube."""

    def __init__(self, proxies: Optional[List[str]] = None):
        self._proxies = list(proxies) if proxies else list(DEFAULT_YOUTUBE_PROXIES)
        self._lock = threading.Lock()
        self._index = 0

    def get_proxies(self) -> List[str]:
        with self._lock:
            return list(self._proxies)

    def set_proxies(self, proxies: List[str]) -> None:
        with self._lock:
            self._proxies = [p.strip() for p in proxies if p and p.strip()]
            self._index = 0
            logger.info(f"YouTubeProxyManager updated with {len(self._proxies)} proxies.")

    def get_next_proxy(self) -> Optional[str]:
        with self._lock:
            if not self._proxies:
                return None
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            return proxy

    @staticmethod
    def is_youtube_url(url: str) -> bool:
        """Return True if the URL or query corresponds to YouTube."""
        if not url:
            return False
        url_lower = url.lower()
        if "ytsearch" in url_lower:
            return True
        try:
            parsed = urlparse(url)
            netloc = parsed.netloc.lower()
            return any(d in netloc for d in ("youtube.com", "youtu.be", "youtube-nocookie.com"))
        except Exception:
            return "youtube.com" in url_lower or "youtu.be" in url_lower

    @staticmethod
    def is_retryable_error(exc_or_msg: Exception | str) -> bool:
        """Check whether the error is indicative of an IP block or bot challenge."""
        msg = str(exc_or_msg).lower()
        if "private video" in msg or "removed by the uploader" in msg or "members-only" in msg:
            return False
        retry_keywords = (
            "403",
            "forbidden",
            "not a bot",
            "confirm you're not a bot",
            "sign in to confirm",
            "requested format is not available",
            "format is not available",
            "too many requests",
            "429",
            "timed out",
            "timeout",
            "connection reset",
            "sabr",
            "network is unreachable",
            "temporary failure",
            "unable to download api page",
        )
        return any(k in msg for k in retry_keywords)

# Global singleton
def _get_configured_proxy_manager() -> YouTubeProxyManager:
    try:
        from config import YOUTUBE_PROXIES
        return YouTubeProxyManager(YOUTUBE_PROXIES)
    except Exception:
        return YouTubeProxyManager(DEFAULT_YOUTUBE_PROXIES)

youtube_proxy_manager = _get_configured_proxy_manager()
