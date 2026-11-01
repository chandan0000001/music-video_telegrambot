"""
Music Search Service
Handles query searching across supported music sources and returns metadata.
"""
import asyncio
import logging
from typing import Optional, Any
import yt_dlp

from utils.validators import sanitize_filename

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
        from config import get_cookie_file
        cookie_file = get_cookie_file()

        ydl_opts: dict[str, Any] = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 15,
            "remote_components": ["ejs:github"],
        }
        if cookie_file:
            ydl_opts["cookiefile"] = str(cookie_file)

        search_query = f"ytsearch{limit}:{query}"
        results = []

        try:
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(search_query, download=False)
            except Exception as e:
                err_msg = str(e).lower()
                if "not a bot" in err_msg or "sign in" in err_msg:
                    fallback_opts = dict(ydl_opts)
                    fallback_opts["extractor_args"] = {
                        "youtube": {
                            "player_client": ["android", "ios"]
                        }
                    }
                    with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                        info = ydl.extract_info(search_query, download=False)
                else:
                    raise

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
        except Exception as e:
            logger.error(f"Search error for query '{query}': {e}")
            return []

    async def search_music(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Non-blocking music search with thread delegation."""
        if not query or not query.strip():
            return []
        clean_query = query.strip()
        return await asyncio.to_thread(self._sync_search, clean_query, limit)


music_service = MusicService()
