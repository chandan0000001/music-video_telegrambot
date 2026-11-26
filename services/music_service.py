"""
Music Search Service
Handles query searching across supported music sources and returns metadata.
"""
import asyncio
import logging
from typing import Optional, Any
import yt_dlp

from utils.validators import sanitize_filename
from services.proxy_manager import youtube_proxy_manager

logger = logging.getLogger(__name__)


def format_duration(seconds: Optional[int | float]) -> str:
    """Format seconds into readable MM:SS or HH:MM:SS."""
    if seconds is None:
        return "Unknown"
    seconds = int(seconds)
    if seconds <= 0:
        return "00:00"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    rem_seconds = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{rem_seconds:02d}"
    return f"{minutes:02d}:{rem_seconds:02d}"


class MusicService:
    """Music search and metadata retrieval service."""

    def _sync_search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Synchronous search executed in thread pool."""
        from config import get_cookie_file, YTDLP_PROXY
        cookie_file = get_cookie_file()

        ydl_opts: dict[str, Any] = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 15,
            "remote_components": ["ejs:github"],
        }
        if YTDLP_PROXY:
            ydl_opts["proxy"] = YTDLP_PROXY

        chrome_profile = Path.home() / ".config" / "google-chrome"
        if not chrome_profile.exists():
            chrome_profile = Path("/home/ubuntu/.config/google-chrome")

        if chrome_profile.exists():
            ydl_opts["cookiesfrombrowser"] = ("chrome",)
        elif cookie_file:
            ydl_opts["cookiefile"] = str(cookie_file)

        search_query = f"ytsearch{limit}:{query}"
        results = []

        info = None
        # Attempt 1: Direct Own IP (fast unauthenticated check)
        direct_opts = dict(ydl_opts)
        direct_opts["socket_timeout"] = 3
        direct_opts["retries"] = 0
        direct_opts["extractor_retries"] = 0
        direct_opts["extractor_args"] = {"youtube": {"player_client": ["android"]}}
        direct_opts.pop("cookiesfrombrowser", None)
        direct_opts.pop("cookiefile", None)
        try:
            with yt_dlp.YoutubeDL(direct_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
        except Exception as e:
            logger.warning(f"Own IP YouTube search failed for '{query}': {e}. Proceeding to round-robin proxy...")

        # Attempt 2: Round-robin proxy fallback (authenticated with Chrome session)
        if not info or "entries" not in info:
            for attempt in range(3):
                proxy = youtube_proxy_manager.get_next_proxy()
                if not proxy:
                    break
                logger.info(f"Retrying YouTube search via round-robin proxy {proxy} (attempt {attempt + 1}/3)...")
                proxy_opts = dict(ydl_opts)
                proxy_opts["proxy"] = proxy
                proxy_opts["socket_timeout"] = 12
                try:
                    with yt_dlp.YoutubeDL(proxy_opts) as ydl:
                        info = ydl.extract_info(search_query, download=False)
                        if info and "entries" in info:
                            break
                except Exception as pe:
                    logger.warning(f"Proxy search attempt failed: {pe}. Rotating to next proxy...")

        if not info or "entries" not in info:
            return []

        for entry in info["entries"]:
            if not entry:
                continue
            video_id = entry.get("id")
            if not video_id:
                continue

            raw_duration = entry.get("duration")
            thumbnails = entry.get("thumbnails", [])
            thumbnail_url = thumbnails[-1]["url"] if thumbnails else None

            title = entry.get("title", "Unknown Title")
            artist = entry.get("uploader") or entry.get("channel") or "Unknown Artist"

            results.append({
                "id": video_id,
                "title": title,
                "artist": artist,
                "duration": raw_duration,
                "duration_str": format_duration(raw_duration),
                "thumbnail": thumbnail_url,
                "url": f"https://www.youtube.com/watch?v={video_id}"
            })

        return results

    async def search_music(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Non-blocking music search with thread delegation."""
        if not query or not query.strip():
            return []
        clean_query = query.strip()
        return await asyncio.to_thread(self._sync_search, clean_query, limit)


music_service = MusicService()
