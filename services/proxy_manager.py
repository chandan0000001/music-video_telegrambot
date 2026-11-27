"""
services/proxy_manager.py - Round-robin proxy rotator dedicated to YouTube operations.
Strictly isolated:
- Applied ONLY to YouTube metadata and downloads (never used for Spotify, SoundCloud, JioSaavn, etc.).
- Direct-First: Always hits with the server's Own IP first.
- Fallback: On failure (403 Forbidden, bot challenge, format not available, timeout),
  proceeds through the round-robin pool of proxies.
- Zero-Proxy-Bandwidth design: heavy media streams (googlevideo.com) bypass proxy,
  using proxy strictly for tiny metadata extraction (~50 KB) so quota is never exhausted.
"""

import threading
import logging
from pathlib import Path
from typing import Optional, List, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Default empty proxy pool (no dead or expired hardcoded proxies)
DEFAULT_YOUTUBE_PROXIES: List[str] = []


class YouTubeProxyManager:
    """Thread-safe round-robin proxy rotator specifically for YouTube."""

    def __init__(self, proxies: Optional[List[str]] = None):
        self._lock = threading.Lock()
        self._index = 0
        self._exhausted: Set[str] = set()

        initial = list(proxies) if proxies is not None else self._discover_proxies()
        self._proxies: List[str] = [p.strip() for p in initial if p and p.strip()]

    def _discover_proxies(self) -> List[str]:
        """Discover proxies from proxies.txt, proxy JSON files, or config defaults."""
        collected: List[str] = []

        # 1. Check for proxies.txt in project root or current working dir
        candidates = [
            Path("proxies.txt"),
            Path("/home/chandan/music_bot/proxies.txt"),
            Path("/home/ubuntu/music_bot/proxies.txt"),
        ]
        for p_file in candidates:
            if p_file.exists():
                try:
                    with open(p_file, "r", encoding="utf-8") as f:
                        for line in f:
                            cleaned = line.strip()
                            if cleaned and not cleaned.startswith("#"):
                                collected.append(cleaned)
                    if collected:
                        logger.info(f"Loaded {len(collected)} proxies from {p_file}")
                        return collected
                except Exception as e:
                    logger.warning(f"Could not read {p_file}: {e}")

        # Never auto-import free proxy lists from Downloads: they may be stale
        # and must not receive browser cookies or authenticated requests.
        # 3. Fallback to DEFAULT_YOUTUBE_PROXIES
        return list(DEFAULT_YOUTUBE_PROXIES)

    def get_proxies(self) -> List[str]:
        with self._lock:
            return [p for p in self._proxies if p not in self._exhausted]

    def set_proxies(self, proxies: List[str]) -> None:
        with self._lock:
            self._proxies = [p.strip() for p in proxies if p and p.strip()]
            self._exhausted.clear()
            self._index = 0
            logger.info(f"YouTubeProxyManager updated with {len(self._proxies)} proxies.")

    def mark_exhausted(self, proxy: str) -> None:
        """Mark a proxy as quota-exhausted or permanently dead (e.g. 402 Payment Required)."""
        with self._lock:
            self._exhausted.add(proxy)
            active_count = len([p for p in self._proxies if p not in self._exhausted])
            logger.warning(
                f"Proxy marked exhausted/dead: {proxy}. Active proxies remaining: {active_count}."
            )

    def get_next_proxy(self) -> Optional[str]:
        with self._lock:
            active = [p for p in self._proxies if p not in self._exhausted]
            if not active:
                # If all exhausted, reset to give second chance
                if self._proxies:
                    self._exhausted.clear()
                    active = list(self._proxies)
                else:
                    return None
            proxy = active[self._index % len(active)]
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
            "402",
            "payment required",
            "bandwidthlimit",
        )
        return any(k in msg for k in retry_keywords)


# Global singleton
def _get_configured_proxy_manager() -> YouTubeProxyManager:
    try:
        from config import YOUTUBE_PROXIES
        return YouTubeProxyManager(YOUTUBE_PROXIES)
    except Exception:
        return YouTubeProxyManager()


youtube_proxy_manager = _get_configured_proxy_manager()
