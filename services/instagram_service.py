"""
Instagram Media Downloader Service
Extracts high-definition Reels, Single Videos, Photos, and Multi-Item Carousels
from public Instagram posts with multi-layer fallback (Public APIs + yt-dlp).
"""
import os
import re
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
    """Service for extracting and downloading media and profiles from Instagram."""

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

    async def get_instagram_profile(self, username_or_url: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Fetch public profile info and original HD DP (Profile Picture) for any Instagram account.
        Works for both Public and Private accounts.
        Returns: (success, profile_data_dict, error_message)
        """
        clean_user = username_or_url.strip().lstrip("@")
        if "instagram.com" in clean_user:
            parsed_info = extract_instagram_info(username_or_url)
            if parsed_info and parsed_info[0] == "profile":
                clean_user = parsed_info[1]
            else:
                clean_user = clean_user.rstrip("/").split("/")[-1]
        clean_user = clean_user.strip("/").strip()

        if not clean_user:
            return False, None, "⚠️ Invalid username provided."

        # Strategy 1: Instagram Official Web Client API (web_profile_info)
        try:
            profile = await self._fetch_profile_via_api(clean_user)
            if profile:
                return True, profile, None
        except Exception as e:
            logger.debug(f"Instagram web_profile_info failed for @{clean_user}: {e}")

        # Strategy 2: Web Scraping / OpenGraph meta tags fallback
        try:
            profile = await self._fetch_profile_via_web(clean_user)
            if profile:
                return True, profile, None
        except Exception as e:
            logger.debug(f"Instagram web scraping failed for @{clean_user}: {e}")

        # Strategy 3: yt-dlp native extraction fallback
        try:
            profile = await self._fetch_profile_via_ytdlp(clean_user)
            if profile:
                return True, profile, None
        except Exception as e:
            logger.debug(f"yt-dlp profile extraction failed for @{clean_user}: {e}")

        return False, None, f"⚠️ Could not find Instagram profile for <code>@{clean_user}</code>. The account may not exist or is restricted."

    async def _fetch_profile_via_api(self, username: str) -> Optional[Dict[str, Any]]:
        """Fetch profile details via Instagram web client API."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "X-IG-App-ID": "936619743392459",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Site": "same-origin",
        }
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    u = data.get("data", {}).get("user")
                    if u:
                        return {
                            "username": u.get("username", username),
                            "full_name": u.get("full_name") or username,
                            "biography": u.get("biography") or "",
                            "is_private": bool(u.get("is_private", False)),
                            "is_verified": bool(u.get("is_verified", False)),
                            "profile_pic_url_hd": u.get("profile_pic_url_hd") or u.get("profile_pic_url"),
                            "followers_count": u.get("edge_followed_by", {}).get("count", 0),
                            "following_count": u.get("edge_follow", {}).get("count", 0),
                            "posts_count": u.get("edge_owner_to_timeline_media", {}).get("count", 0),
                        }
        return None

    async def _fetch_profile_via_web(self, username: str) -> Optional[Dict[str, Any]]:
        """Fallback to extracting OpenGraph and meta properties from web HTML."""
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        url = f"https://www.instagram.com/{username}/"
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    html_text = await resp.text()
                    img_match = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html_text)
                    if not img_match:
                        img_match = re.search(r'content=["\']([^"\']+)["\']\s+property=["\']og:image["\']', html_text)

                    if img_match:
                        pic_url = img_match.group(1).replace("&amp;", "&")
                        title_match = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', html_text)
                        full_name = title_match.group(1) if title_match else username

                        desc_match = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', html_text)
                        desc = desc_match.group(1) if desc_match else ""

                        followers = 0
                        following = 0
                        posts = 0
                        f_match = re.search(r'([\d.,KMkm]+)\s*Followers', desc, re.I)
                        if f_match:
                            try:
                                followers = int(f_match.group(1).replace(",", "").replace(".", ""))
                            except Exception:
                                pass

                        is_private = 'is_private":true' in html_text or "This Account is Private" in html_text or "Private Account" in desc

                        return {
                            "username": username,
                            "full_name": full_name,
                            "biography": desc,
                            "is_private": is_private,
                            "is_verified": "verified" in html_text.lower(),
                            "profile_pic_url_hd": pic_url,
                            "followers_count": followers,
                            "following_count": following,
                            "posts_count": posts
                        }
        return None

    async def _fetch_profile_via_ytdlp(self, username: str) -> Optional[Dict[str, Any]]:
        """Fallback to yt-dlp native user channel extractor."""
        loop = asyncio.get_event_loop()

        def _sync():
            opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(f"https://www.instagram.com/{username}/", download=False)

        try:
            info = await loop.run_in_executor(None, _sync)
            if info and info.get("thumbnail"):
                return {
                    "username": username,
                    "full_name": info.get("uploader") or username,
                    "biography": info.get("description") or "",
                    "is_private": False,
                    "is_verified": False,
                    "profile_pic_url_hd": info.get("thumbnail"),
                    "followers_count": info.get("channel_follower_count") or 0,
                    "following_count": 0,
                    "posts_count": 0,
                }
        except Exception:
            pass
        return None

    async def get_instagram_media(self, url: str) -> Tuple[bool, List[Dict[str, Any]], Optional[str]]:
        """
        Extract media items from an Instagram URL.
        Supports:
        - Posts & Reels (Single photo, video, multi-item carousels)
        - Stories
        - Highlights
        """
        if not is_valid_instagram_url(url):
            return False, [], "⚠️ Invalid Instagram link. Please provide a valid Reel, Post, Story, or Highlight link."

        info_tuple = extract_instagram_info(url)
        if not info_tuple:
            return False, [], "⚠️ Could not parse Instagram link."

        category, identifier = info_tuple
        if category == "profile":
            return False, [], "ℹ️ This is a profile URL. Use the Profile Downloader feature to view details and HD DP."

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
                if "private" in err.lower() or "login" in err.lower():
                    return False, [], "🔒 This content belongs to a Private Account and cannot be downloaded directly."
                return False, [], err
        except Exception as e:
            logger.error(f"yt-dlp Instagram extraction error for {url}: {e}")

        if category in ("story", "highlight"):
            return False, [], "⚠️ Could not access this Instagram Story or Highlight. It may be expired (stories last 24h) or from a private account."

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
