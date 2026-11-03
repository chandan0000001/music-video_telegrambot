"""
Twitter / X Media Downloader Service
Extracts original quality photos, videos, and GIFs from public X/Twitter posts.
"""
import os
import uuid
import shutil
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import aiohttp
import yt_dlp

from config import TEMP_DIR
from utils.validators import (
    extract_twitter_info,
    is_valid_twitter_url,
    classify_media_restriction
)

logger = logging.getLogger(__name__)


class TwitterService:
    """Service for extracting and downloading media from X (Twitter)."""

    def __init__(self, temp_base: Optional[Path] = None):
        self.temp_base = temp_base or (TEMP_DIR / "twitter")
        self.temp_base.mkdir(parents=True, exist_ok=True)
        self.max_items_limit = 15

    def _get_job_dir(self) -> Path:
        """Create a dedicated temporary directory for a download job."""
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.temp_base / f"job_{job_id}"
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def cleanup_job_dir(self, job_dir: Path) -> None:
        """Safely remove temporary job directory."""
        try:
            if job_dir.exists() and job_dir.is_dir():
                shutil.rmtree(job_dir, ignore_errors=True)
                logger.debug(f"Cleaned up Twitter temp dir: {job_dir}")
        except Exception as e:
            logger.warning(f"Failed to remove Twitter temp dir {job_dir}: {e}")

    def _make_orig_photo_url(self, photo_url: str) -> str:
        """Ensure Twitter/X photo URL requests maximum original quality (?name=orig)."""
        if not photo_url:
            return photo_url
        try:
            parsed = urlparse(photo_url)
            if "twimg.com" in parsed.netloc:
                query = parse_qs(parsed.query)
                query["name"] = ["orig"]
                new_query = urlencode(query, doseq=True)
                return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
            return photo_url
        except Exception:
            return photo_url

    async def get_tweet_media(self, url: str) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """
        Extract media items from a public Twitter/X post.
        Returns (success, media_items, error_message).
        Each media item: {'type': 'photo'|'video'|'gif', 'url': str, ...}
        """
        info = extract_twitter_info(url)
        if not info:
            return False, [], "⚠️ Invalid or unsupported X (Twitter) URL. Please provide a valid post link."

        username, tweet_id = info
        endpoints = [
            (f"https://api.vxtwitter.com/{username}/status/{tweet_id}", "vxtwitter"),
            (f"https://api.fxtwitter.com/{username}/status/{tweet_id}", "fxtwitter"),
            (f"https://api.fixupx.com/{username}/status/{tweet_id}", "fxtwitter")
        ]
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0; +https://t.me/songsuperzxbot)"
        }

        for api_url, api_kind in endpoints:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=4, connect=2.5)) as resp:
                        if resp.status == 404:
                            return False, [], (
                                "🔒 <b>Access Restricted: Private Category</b>\n\n"
                                "⚠️ This X (Twitter) post was not found, has been deleted, or belongs to a <b>Private / Protected</b> account.\n\n"
                                "<i>Private posts cannot be accessed or downloaded without authorized follower access.</i>"
                            )

                        if resp.status == 200:
                            data = await resp.json()
                            if api_kind == "vxtwitter" and ("media_extended" in data or "mediaURLs" in data):
                                ok, items, err = self._parse_vxtwitter_response(data)
                            else:
                                ok, items, err = self._parse_fxtwitter_response(data)
                            if ok and items:
                                return ok, items, err
            except Exception as e:
                logger.debug(f"{api_kind} failed for {url} ({api_url}): {e}")
                continue

        # Fallback to yt-dlp with strict fast timeouts
        return await asyncio.to_thread(self._fallback_extract_with_ytdlp, url)

    def _parse_vxtwitter_response(self, data: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """Parse structured JSON from vxTwitter API."""
        if not data:
            return False, [], "⚠️ Could not retrieve tweet information."

        media_ext = data.get("media_extended") or []
        parsed_items: List[Dict[str, Any]] = []

        for item in media_ext:
            m_type = item.get("type", "").lower()
            m_url = item.get("url")
            if not m_url:
                continue

            size = item.get("size") or {}
            width = size.get("width")
            height = size.get("height")
            duration = item.get("duration_millis")
            if duration:
                duration = duration / 1000.0

            if m_type in ("image", "photo"):
                orig_url = self._make_orig_photo_url(m_url)
                parsed_items.append({
                    "type": "photo",
                    "url": orig_url,
                    "width": width,
                    "height": height
                })
            elif m_type == "video":
                parsed_items.append({
                    "type": "video",
                    "url": m_url,
                    "thumbnail_url": item.get("thumbnail_url"),
                    "duration": duration,
                    "width": width,
                    "height": height
                })
            elif m_type == "gif":
                parsed_items.append({
                    "type": "gif",
                    "url": m_url,
                    "thumbnail_url": item.get("thumbnail_url")
                })

        if not parsed_items:
            for m_url in data.get("mediaURLs", []):
                if any(m_url.lower().endswith(ext) for ext in (".mp4", ".mov", ".m4v")):
                    parsed_items.append({"type": "video", "url": m_url})
                elif any(m_url.lower().endswith(ext) for ext in (".gif",)):
                    parsed_items.append({"type": "gif", "url": m_url})
                else:
                    parsed_items.append({"type": "photo", "url": self._make_orig_photo_url(m_url)})

        if not parsed_items:
            return False, [], "⚠️ No supported media found in this X/Twitter post."

        return True, parsed_items[:self.max_items_limit], None

    def _parse_fxtwitter_response(self, data: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """Parse structured JSON from FxTwitter API."""
        if not data or data.get("code") != 200:
            msg = data.get("message", "Could not retrieve post metadata.")
            return False, [], f"⚠️ {msg}"

        tweet = data.get("tweet")
        if not tweet:
            return False, [], "⚠️ Could not retrieve tweet information."

        # Filter out sensitive/18+ media per policy
        if tweet.get("possibly_sensitive") is True:
            return False, [], "⚠️ This X/Twitter post contains sensitive or age-restricted media and cannot be downloaded."

        media_data = tweet.get("media")
        if not media_data:
            return False, [], "⚠️ No photos, videos, or GIFs found in this X/Twitter post."

        # Gather all items from 'all' array or combine 'photos' and 'videos'
        raw_items = media_data.get("all")
        if not raw_items:
            raw_items = []
            for photo in media_data.get("photos", []):
                raw_items.append({"type": "photo", **photo})
            for video in media_data.get("videos", []):
                raw_items.append(video)

        if not raw_items:
            return False, [], "⚠️ No supported media found in this X/Twitter post."

        parsed_items: List[Dict[str, Any]] = []
        for item in raw_items:
            item_type = item.get("type", "").lower()
            media_url = item.get("url")
            if not media_url:
                continue

            if item_type == "photo":
                orig_url = self._make_orig_photo_url(media_url)
                parsed_items.append({
                    "type": "photo",
                    "url": orig_url,
                    "width": item.get("width"),
                    "height": item.get("height")
                })
            elif item_type == "video":
                parsed_items.append({
                    "type": "video",
                    "url": media_url,
                    "thumbnail_url": item.get("thumbnail_url"),
                    "duration": item.get("duration"),
                    "width": item.get("width"),
                    "height": item.get("height")
                })
            elif item_type == "gif":
                parsed_items.append({
                    "type": "gif",
                    "url": media_url,
                    "thumbnail_url": item.get("thumbnail_url")
                })

        if not parsed_items:
            return False, [], "⚠️ No downloadable media found in this tweet."

        # Respect user limit of maximum 15 items per tweet
        return True, parsed_items[:self.max_items_limit], None

    def _fallback_extract_with_ytdlp(self, url: str) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """Fallback extractor using yt-dlp for video extraction with fast timeout protection."""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 8,
            "retries": 1,
            "extractor_retries": 1,
            "noplaylist": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return False, [], "⚠️ Could not extract media from this post."

                # Check if it has formats or direct url
                direct_url = info.get("url")
                if not direct_url and "formats" in info:
                    formats = [f for f in info["formats"] if f.get("url") and f.get("ext") == "mp4"]
                    if formats:
                        direct_url = formats[-1]["url"]

                if not direct_url:
                    return False, [], "⚠️ No downloadable video stream found."

                return True, [{
                    "type": "video",
                    "url": direct_url,
                    "thumbnail_url": info.get("thumbnail"),
                    "duration": info.get("duration"),
                    "width": info.get("width"),
                    "height": info.get("height")
                }], None
        except Exception as e:
            logger.error(f"yt-dlp fallback error for {url}: {e}")
            classified = classify_media_restriction(str(e))
            if classified:
                return False, [], classified[1]
            return False, [], f"⚠️ Failed to process X post: {str(e)[:80]}"

    async def download_file(self, url: str, dest_path: Path, tracker: Optional[Any] = None) -> bool:
        """Download remote media file to local destination path with live progress tracking."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Download failed with status {resp.status} for {url}")
                        return False
                    total_len = int(resp.headers.get("Content-Length", 0))
                    if tracker:
                        tracker.status = "downloading"
                        if total_len > 0:
                            tracker.total_bytes = total_len

                    start_time = asyncio.get_event_loop().time()
                    downloaded = 0

                    with open(dest_path, "wb") as f:
                        while True:
                            chunk = await resp.content.read(256 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)

                            if tracker:
                                tracker.downloaded_bytes = downloaded
                                if tracker.total_bytes > 0:
                                    tracker.percentage = min(100.0, (downloaded / tracker.total_bytes) * 100.0)
                                elapsed = asyncio.get_event_loop().time() - start_time
                                if elapsed > 0:
                                    tracker.speed = downloaded / elapsed
                                    if tracker.speed > 0 and tracker.total_bytes > downloaded:
                                        tracker.eta = int((tracker.total_bytes - downloaded) / tracker.speed)

                    if tracker:
                        tracker.percentage = 100.0
                        tracker.status = "processing"
                    return True
        except Exception as e:
            logger.error(f"Error downloading {url} to {dest_path}: {e}")
            return False

    async def download_all_media(
        self,
        media_items: List[Dict[str, Any]],
        job_dir: Path,
        tracker: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Download all media items into job_dir concurrently with progress tracking.
        Returns list of downloaded media item dicts with 'file_path' added.
        """
        downloaded: List[Dict[str, Any]] = []

        if tracker and len(media_items) == 1:
            # Single item: tracker directly tracks the file
            item = media_items[0]
            item_type = item["type"]
            ext = "jpg" if item_type == "photo" else "mp4"
            dest_file = job_dir / f"media_1.{ext}"
            ok = await self.download_file(item["url"], dest_file, tracker=tracker)
            if ok and dest_file.exists() and dest_file.stat().st_size > 0:
                return [{**item, "file_path": dest_file, "index": 0}]
            return []

        # Multi-item download
        total_items = len(media_items)
        lock = asyncio.Lock()
        total_downloaded = 0
        start_time = asyncio.get_event_loop().time()

        # Probe content lengths quickly to estimate total size accurately
        async def _probe_size(it: Dict[str, Any]) -> int:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.head(it["url"], timeout=aiohttp.ClientTimeout(total=5)) as r:
                        return int(r.headers.get("Content-Length", 0))
            except Exception:
                return 0

        sizes = await asyncio.gather(*[_probe_size(it) for it in media_items])
        sum_sizes = sum(s for s in sizes if s > 0)

        if tracker:
            tracker.status = "downloading"
            tracker.total_bytes = sum_sizes if sum_sizes > 0 else (total_items * 1024 * 1024)

        async def _download_one(index: int, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            nonlocal total_downloaded
            item_type = item["type"]
            ext = "jpg" if item_type == "photo" else "mp4"
            dest_file = job_dir / f"media_{index + 1}.{ext}"

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(item["url"], timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status != 200:
                            return None
                        with open(dest_file, "wb") as f:
                            while True:
                                chunk = await resp.content.read(256 * 1024)
                                if not chunk:
                                    break
                                f.write(chunk)
                                if tracker:
                                    async with lock:
                                        total_downloaded += len(chunk)
                                        tracker.downloaded_bytes = total_downloaded
                                        if tracker.total_bytes > 0:
                                            tracker.percentage = min(100.0, (total_downloaded / tracker.total_bytes) * 100.0)
                                        elapsed = asyncio.get_event_loop().time() - start_time
                                        if elapsed > 0:
                                            tracker.speed = total_downloaded / elapsed
                                            if tracker.speed > 0 and tracker.total_bytes > total_downloaded:
                                                tracker.eta = int((tracker.total_bytes - total_downloaded) / tracker.speed)

                if dest_file.exists() and dest_file.stat().st_size > 0:
                    return {
                        **item,
                        "file_path": dest_file,
                        "index": index
                    }
            except Exception as e:
                logger.error(f"Error downloading album item {index}: {e}")
            return None

        tasks = [_download_one(i, item) for i, item in enumerate(media_items)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, dict) and res.get("file_path"):
                downloaded.append(res)

        downloaded.sort(key=lambda x: x.get("index", 0))
        return downloaded


twitter_service = TwitterService()
