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

import time
import threading
import logging
from pathlib import Path
from typing import Optional, List, Set, Dict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Default empty proxy pool (no dead or expired hardcoded proxies)
DEFAULT_YOUTUBE_PROXIES: List[str] = []

_COOLDOWN_SECONDS = 60.0


def mask_proxy_url(proxy: str) -> str:
    """Mask embedded credentials in proxy URLs before logging."""
    try:
        parsed = urlparse(proxy)
        if parsed.username or parsed.password:
            host_port = parsed.hostname or ""
            if parsed.port:
                host_port = f"{host_port}:{parsed.port}"
            scheme = f"{parsed.scheme}://" if parsed.scheme else ""
            return f"{scheme}***@{host_port}"
    except Exception:
        pass
    return proxy


class YouTubeProxyManager:
    """Thread-safe round-robin proxy rotator specifically for YouTube."""

    def __init__(self, proxies: Optional[List[str]] = None):
        self._lock = threading.Lock()
        self._index = 0
        self._exhausted: Set[str] = set()
        self._cooldowns: Dict[str, float] = {}
        self._explicit_mode = proxies is not None
        self._proxies_file: Optional[Path] = None
        self._proxies_file_mtime: float = 0.0

        initial = list(proxies) if proxies is not None else self._discover_proxies()
        self._proxies: List[str] = [p.strip() for p in initial if p and p.strip()]

    def _candidate_proxy_files(self) -> List[Path]:
        from config import BASE_DIR
        return [
            BASE_DIR / "proxies.txt",
            Path("proxies.txt"),
            Path("/home/chandan/music_bot/proxies.txt"),
            Path("/home/ubuntu/music_bot/proxies.txt"),
        ]

    def _load_file(self, p_file: Path) -> List[str]:
        collected: List[str] = []
        with open(p_file, "r", encoding="utf-8") as f:
            for line in f:
                cleaned = line.strip()
                if cleaned and not cleaned.startswith("#"):
                    collected.append(cleaned)
        return collected

    def _maybe_reload_proxies_unlocked(self) -> None:
        """Hot-reload proxies.txt if added or modified at runtime."""
        if self._explicit_mode:
            return
        for p_file in self._candidate_proxy_files():
            try:
                if p_file.exists():
                    mtime = p_file.stat().st_mtime
                    if p_file != self._proxies_file or mtime > self._proxies_file_mtime:
                        loaded = self._load_file(p_file)
                        self._proxies_file = p_file
                        self._proxies_file_mtime = mtime
                        self._proxies = loaded
                        self._exhausted.clear()
                        self._cooldowns.clear()
                        self._index = 0
                        logger.info(f"Hot-reloaded {len(loaded)} YouTube proxy(s) from {p_file}")
                    return
            except Exception as e:
                logger.debug(f"Could not inspect {p_file}: {e}")

    def _discover_proxies(self) -> List[str]:
        """Discover proxies from proxies.txt or config defaults."""
        for p_file in self._candidate_proxy_files():
            if p_file.exists():
                try:
                    collected = self._load_file(p_file)
                    self._proxies_file = p_file
                    self._proxies_file_mtime = p_file.stat().st_mtime
                    if collected:
                        logger.info(f"Loaded {len(collected)} proxies from {p_file}")
                        return collected
                except Exception as e:
                    logger.warning(f"Could not read {p_file}: {e}")

        # Never auto-import free proxy lists from Downloads: they may be stale
        # and must not receive browser cookies or authenticated requests.
        return list(DEFAULT_YOUTUBE_PROXIES)

    def _active_proxies_unlocked(self) -> List[str]:
        now = time.monotonic()
        active: List[str] = []
        for p in self._proxies:
            if p in self._exhausted:
                continue
            cool_until = self._cooldowns.get(p, 0.0)
            if now >= cool_until:
                self._cooldowns.pop(p, None)
                active.append(p)
        return active

    def get_proxies(self) -> List[str]:
        with self._lock:
            self._maybe_reload_proxies_unlocked()
            active = self._active_proxies_unlocked()
            if not active and self._proxies and not self._exhausted:
                # Clear transient cooldowns if all non-exhausted proxies are cooling
                self._cooldowns.clear()
                active = list(self._proxies)
            return active

    def set_proxies(self, proxies: List[str]) -> None:
        with self._lock:
            self._explicit_mode = True
            self._proxies = [p.strip() for p in proxies if p and p.strip()]
            self._exhausted.clear()
            self._cooldowns.clear()
            self._index = 0
            logger.info(f"YouTubeProxyManager updated with {len(self._proxies)} proxies.")

    def mark_cooldown(self, proxy: str, seconds: float = _COOLDOWN_SECONDS) -> None:
        """Temporarily cool down a proxy after transient timeout or 429 error."""
        with self._lock:
            self._cooldowns[proxy] = time.monotonic() + max(5.0, seconds)

    def mark_exhausted(self, proxy: str) -> None:
        """Mark a proxy as quota-exhausted or permanently dead (e.g. 402 Payment Required)."""
        with self._lock:
            self._exhausted.add(proxy)
            self._cooldowns.pop(proxy, None)
            active_count = len([p for p in self._proxies if p not in self._exhausted])
            logger.warning(
                f"Proxy marked exhausted/dead: {mask_proxy_url(proxy)}. Active proxies remaining: {active_count}."
            )

    def get_next_proxy(self) -> Optional[str]:
        with self._lock:
            self._maybe_reload_proxies_unlocked()
            active = self._active_proxies_unlocked()
            if not active:
                # If proxies exist but all are temporarily cooling, clear cooldowns
                if self._proxies and len(self._exhausted) < len(self._proxies):
                    self._cooldowns.clear()
                    active = self._active_proxies_unlocked()
                elif self._proxies:
                    # Last resort if all were exhausted: reset pool for fresh retry cycle
                    self._exhausted.clear()
                    self._cooldowns.clear()
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
        """Check whether the error indicates a Google/YouTube IP block, bot check, or transient transport drop."""
        msg = str(exc_or_msg).lower()
        if "private video" in msg or "removed by the uploader" in msg or "members-only" in msg:
            return False
        retry_keywords = (
            "403",
            "forbidden",
            "not a bot",
            "confirm you're not a bot",
            "sign in to confirm",
            "login_required",
            "automated queries",
            "unusual traffic",
            "we're sorry",
            "cookies are no longer valid",
            "requested format is not available",
            "format is not available",
            "too many requests",
            "429",
            "timed out",
            "timeout",
            "connection reset",
            "remote end closed",
            "sabr",
            "network is unreachable",
            "temporary failure",
            "unable to download api page",
            "unable to extract initial data",
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
