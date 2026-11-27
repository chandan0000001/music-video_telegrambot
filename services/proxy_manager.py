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
import json
from pathlib import Path
from typing import Optional, List, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# The default WebShare proxies
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
        self._lock = threading.Lock()
        self._index = 0
        self._exhausted: Set[str] = set()

        initial = list(proxies) if proxies else self._discover_proxies()
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

        # 2. Check for Free_Proxy_List.json or custom JSON files
        json_candidates = [
            Path("Free_Proxy_List.json"),
            Path("/home/chandan/Downloads/Free_Proxy_List.json"),
            Path("/home/chandan/music_bot/Free_Proxy_List.json"),
            Path("/home/ubuntu/music_bot/Free_Proxy_List.json"),
        ]
        for j_file in json_candidates:
            if j_file.exists():
                try:
                    with open(j_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            for item in data:
                                if isinstance(item, dict) and "ip" in item and "port" in item:
                                    # Prefer elite / anonymous proxies
                                    anon = item.get("anonymityLevel", "")
                                    proto = item.get("protocols", ["http"])[0]
                                    ip = item.get("ip")
                                    port = item.get("port")
                                    collected.append(f"{proto}://{ip}:{port}")
                    if collected:
                        logger.info(f"Loaded {len(collected)} proxies from JSON {j_file}")
                        return collected
                except Exception as e:
                    logger.warning(f"Could not read {j_file}: {e}")

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
