"""
Instagram Media Downloader Service
Extracts high-definition Reels, Single Videos, Photos, and Multi-Item Carousels
from public Instagram posts with multi-layer fallback (Public APIs + yt-dlp).
"""
import os
import uuid
import shutil
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import aiohttp
import yt_dlp

from config import TEMP_DIR
from utils.validators import extract_instagram_info, is_valid_instagram_url

logger = logging.getLogger(__name__)


class InstagramService:
    """Service for extracting and downloading media from Instagram."""

    def __init__(self, temp_base: Optional[Path] = None):
        self.temp_base = temp_base or (TEMP_DIR / "instagram")
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
                logger.debug(f"Cleaned up Instagram temp dir: {job_dir}")
        except Exception as e:
            logger.warning(f"Failed to remove Instagram temp dir {job_dir}: {e}")

    async def get_instagram_media(self, url: str) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """
        Extract media items from an Instagram URL.
        Multi-layer extraction:
        1. Cobalt public API
        2. Direct yt-dlp engine
        """
        if not is_valid_instagram_url(url):
            return False, [], "⚠️ Invalid Instagram link. Please provide a valid Reel or Post link."

        info_tuple = extract_instagram_info(url)
        if not info_tuple:
            return False, [], "⚠️ Could not parse Instagram link."

        media_type, shortcode = info_tuple

        # Strategy 1: Cobalt API extraction
        try:
            items = await self._extract_via_cobalt(url)
            if items:
                return True, items[:self.max_items_limit], None
        except Exception as e:
            logger.debug(f"Cobalt extraction failed for {url}: {e}")

        # Strategy 2: Direct yt-dlp extraction
        try:
            items, err = await self._extract_via_ytdlp(url)
            if items:
                return True, items[:self.max_items_limit], None
            if err:
                return False, [], err
        except Exception as e:
            logger.error(f"yt-dlp Instagram extraction error for {url}: {e}")

        return False, [], "⚠️ Could not retrieve media from this Instagram post. It may be private or restricted."

    async def _extract_via_cobalt(self, url: str) -> Optional[List[Dict[str, Any]]]:
        """Attempt extraction via public Cobalt API instance."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        payload = {
            "url": url,
            "videoQuality": "max"
        }

        # Try popular public cobalt instances
        instances = [
            "https://cobalt-api.kwiatekm.tokyo",
            "https://api.cobalt.tools"
        ]

        for base_api in instances:
            try:
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.post(
                        f"{base_api}/",
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            status = data.get("status")
                            if status in ("stream", "redirect") and data.get("url"):
                                return [{
                                    "type": "video",
                                    "url": data["url"],
                                    "thumbnail_url": None
                                }]
                            elif status == "picker" and data.get("picker"):
                                items = []
                                for item in data["picker"]:
                                    item_type = item.get("type", "video")
                                    item_url = item.get("url")
                                    if item_url:
                                        items.append({
                                            "type": "photo" if item_type == "photo" else "video",
                                            "url": item_url,
                                            "thumbnail_url": item.get("thumb")
                                        })
                                if items:
                                    return items
            except Exception:
                continue

        return None

    async def _extract_via_ytdlp(self, url: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Extract media using yt-dlp native Instagram extractor."""
        loop = asyncio.get_event_loop()

        def _sync_ytdlp():
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "skip_download": True,
                "noplaylist": True,
                "socket_timeout": 20,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await loop.run_in_executor(None, _sync_ytdlp)
            if not info:
                return [], "Could not extract media info."

            # Case A: Playlist / Carousel entries
            if "entries" in info and info["entries"]:
                items = []
                for entry in info["entries"]:
                    if not entry:
                        continue
                    direct_url = entry.get("url")
                    if not direct_url and entry.get("formats"):
                        direct_url = entry["formats"][-1].get("url")
                    if direct_url:
                        is_video = bool(entry.get("vcodec") and entry.get("vcodec") != "none") or (entry.get("ext") == "mp4")
                        items.append({
                            "type": "video" if is_video else "photo",
                            "url": direct_url,
                            "thumbnail_url": entry.get("thumbnail"),
                            "duration": entry.get("duration"),
                            "width": entry.get("width"),
                            "height": entry.get("height")
                        })
                if items:
                    return items, None

            # Case B: Single video or photo
            direct_url = info.get("url")
            if not direct_url and info.get("formats"):
                direct_url = info["formats"][-1].get("url")

            if direct_url:
                is_video = bool(info.get("vcodec") and info.get("vcodec") != "none") or (info.get("ext") == "mp4")
                return [{
                    "type": "video" if is_video else "photo",
                    "url": direct_url,
                    "thumbnail_url": info.get("thumbnail"),
                    "duration": info.get("duration"),
                    "width": info.get("width"),
                    "height": info.get("height")
                }], None

            return [], "No downloadable stream found in this post."
        except Exception as e:
            logger.warning(f"yt-dlp extraction failed for Instagram {url}: {e}")
            return [], f"⚠️ Post extraction failed: {str(e)[:80]}"

    async def download_file(self, url: str, dest_path: Path, tracker: Optional[Any] = None) -> bool:
        """Download remote media file to local destination path with live progress tracking."""
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Download failed with status {resp.status} for {url}")
                        return False
                    total_len = int(resp.headers.get("Content-Length", 0))
                    if tracker:
                        tracker.status = "downloading"
                        if total_len > 0 and tracker.total_bytes == 0:
                            tracker.total_bytes = total_len

                    start_time = asyncio.get_event_loop().time()
                    downloaded = 0

                    with open(dest_path, "wb") as f:
                        while True:
                            chunk = await resp.content.read(64 * 1024)
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
        Download all media items into job_dir concurrently with live progress tracking.
        Returns list of downloaded media item dicts with 'file_path' added.
        """
        if tracker and len(media_items) == 1:
            item = media_items[0]
            item_type = item["type"]
            ext = "jpg" if item_type == "photo" else "mp4"
            dest_file = job_dir / f"media_1.{ext}"
            ok = await self.download_file(item["url"], dest_file, tracker=tracker)
            if ok and dest_file.exists() and dest_file.stat().st_size > 0:
                return [{**item, "file_path": dest_file, "index": 0}]
            return []

        # Multi-item carousel download
        total_items = len(media_items)
        lock = asyncio.Lock()
        total_downloaded = 0
        start_time = asyncio.get_event_loop().time()

        # Probe content lengths quickly to estimate total size accurately
        async def _probe_size(it: Dict[str, Any]) -> int:
            try:
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(connector=connector) as s:
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
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(item["url"], timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status != 200:
                            return None
                        with open(dest_file, "wb") as f:
                            while True:
                                chunk = await resp.content.read(64 * 1024)
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
                logger.error(f"Error downloading carousel item {index}: {e}")
            return None

        tasks = [_download_one(i, item) for i, item in enumerate(media_items)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        downloaded: List[Dict[str, Any]] = []
        for res in results:
            if isinstance(res, dict) and res.get("file_path"):
                downloaded.append(res)

        downloaded.sort(key=lambda x: x.get("index", 0))
        return downloaded


# Global singleton instagram service
instagram_service = InstagramService()
