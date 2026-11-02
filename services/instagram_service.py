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

from config import TEMP_DIR, get_instagram_cookie_file, INSTAGRAM_SESSION_ID, YTDLP_PROXY
from utils.validators import extract_instagram_info, is_valid_instagram_url

logger = logging.getLogger(__name__)


class InstagramService:
    """Service for extracting and downloading media and profiles from Instagram."""

    def __init__(self, temp_base: Optional[Path] = None):
        self.temp_base = temp_base or (TEMP_DIR / "instagram")
        self.temp_base.mkdir(parents=True, exist_ok=True)
        # No artificial carousel item limit: download all existing media items

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
        if INSTAGRAM_SESSION_ID:
            headers["Cookie"] = f"sessionid={INSTAGRAM_SESSION_ID.strip()};"
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
            cookie_file = get_instagram_cookie_file()
            if cookie_file:
                opts["cookiefile"] = str(cookie_file)
            if YTDLP_PROXY:
                opts["proxy"] = YTDLP_PROXY
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
        - Posts & Reels (Single photo, video, multi-item mixed carousels)
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

        canonical_url = f"https://www.instagram.com/{category}/{identifier}/"
        last_error = None

        # Strategy 1: Direct yt-dlp extraction with CustomInstagramIE (full support for photos, reels, mixed carousels)
        try:
            items, err = await self._extract_via_ytdlp(canonical_url)
            if items:
                return True, items, None
            if err:
                last_error = err
        except Exception as e:
            logger.debug(f"yt-dlp extraction error for {canonical_url}: {e}")

        # Strategy 2: Direct Embed / Web HTML / GraphQL extraction
        try:
            items = await self._extract_via_embed_and_html(identifier, category)
            if items:
                return True, items, None
        except Exception as e:
            logger.debug(f"Direct embed/HTML extraction failed for {identifier}: {e}")

        # Strategy 3: Public Cobalt API extraction
        try:
            items = await self._extract_via_cobalt(canonical_url)
            if items:
                return True, items, None
        except Exception as e:
            logger.debug(f"Cobalt extraction failed for {canonical_url}: {e}")

        # Strategy 4: ddinstagram mirror extraction for multi-media carousels
        try:
            items = await self._extract_via_ddinstagram(identifier)
            if items:
                return True, items, None
        except Exception as e:
            logger.debug(f"ddinstagram extraction failed for {identifier}: {e}")

        if category in ("story", "highlight"):
            return False, [], "⚠️ Could not access this Instagram Story or Highlight. It may be expired (stories last 24h) or requires login session."

        if last_error:
            if "this account is private" in last_error.lower() or "account is private" in last_error.lower() or "only available for registered users" in last_error.lower():
                return False, [], "🔒 This content belongs to a Private Account and cannot be downloaded directly."
            if "login" in last_error.lower() or "sign in" in last_error.lower() or "checkpoint" in last_error.lower() or "rate-limit for accessing posts anonymously" in last_error.lower():
                return False, [], "⚠️ Instagram login session required for this post. Add your sessionid or cookies.txt in .env to bypass."

        return False, [], "⚠️ Could not retrieve media from this Instagram post. The post may be restricted or requires Instagram cookies."

    async def _extract_via_embed_and_html(self, shortcode: str, category: str = "p") -> Optional[List[Dict[str, Any]]]:
        """
        Extract photos, videos, and mixed carousels directly from Instagram's public embed and web endpoints.
        Bypasses 'No video formats found' issues for photo and mixed carousels.
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if INSTAGRAM_SESSION_ID:
            headers["Cookie"] = f"sessionid={INSTAGRAM_SESSION_ID.strip()};"

        urls_to_try = [
            f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
            f"https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis",
            f"https://www.instagram.com/{category}/{shortcode}/",
        ]

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            for fetch_url in urls_to_try:
                try:
                    async with session.get(fetch_url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status != 200:
                            continue
                        content_type = resp.headers.get("Content-Type", "")

                        # 1. Check if JSON response (__a=1)
                        if "json" in content_type:
                            data = await resp.json()
                            items = self._parse_instagram_json_payload(data)
                            if items:
                                return items

                        # 2. Check HTML response
                        html_text = await resp.text()
                        items = self._parse_instagram_html_text(html_text)
                        if items:
                            return items
                except Exception as e:
                    logger.debug(f"Error fetching {fetch_url}: {e}")
                    continue

        return None

    def _parse_instagram_json_payload(self, data: dict) -> List[Dict[str, Any]]:
        """Parse structured Instagram API JSON items."""
        items: List[Dict[str, Any]] = []
        raw_items = data.get("items") or []
        if not raw_items:
            # Check graphql shortcode_media
            media = data.get("graphql", {}).get("shortcode_media") or {}
            if media:
                return self._parse_graphql_media_node(media)
            return items

        first = raw_items[0]
        # Check carousel
        if first.get("carousel_media"):
            for c_item in first["carousel_media"]:
                if c_item.get("video_versions"):
                    v_url = c_item["video_versions"][0]["url"]
                    items.append({"type": "video", "url": v_url})
                elif c_item.get("image_versions2", {}).get("candidates"):
                    img_url = c_item["image_versions2"]["candidates"][0]["url"]
                    items.append({"type": "photo", "url": img_url})
            return items

        # Single video
        if first.get("video_versions"):
            return [{"type": "video", "url": first["video_versions"][0]["url"]}]
        # Single photo
        if first.get("image_versions2", {}).get("candidates"):
            return [{"type": "photo", "url": first["image_versions2"]["candidates"][0]["url"]}]

        return items

    def _parse_graphql_media_node(self, node: dict) -> List[Dict[str, Any]]:
        """Parse GraphQL shortcode_media node."""
        items: List[Dict[str, Any]] = []
        sidecar = node.get("edge_sidecar_to_children", {}).get("edges", [])
        if sidecar:
            for edge in sidecar:
                child = edge.get("node", {})
                w = child.get("dimensions", {}).get("width", 0)
                h = child.get("dimensions", {}).get("height", 0)
                if child.get("is_video") and child.get("video_url"):
                    items.append({"type": "video", "url": child["video_url"], "width": w, "height": h})
                elif child.get("display_url"):
                    best_url = child["display_url"]
                    if child.get("display_resources"):
                        best_res = max(child["display_resources"], key=lambda r: r.get("config_width", 0))
                        best_url = best_res.get("src") or best_url
                    items.append({"type": "photo", "url": best_url, "width": w, "height": h})
            return items

        if node.get("is_video") and node.get("video_url"):
            return [{"type": "video", "url": node["video_url"]}]
        if node.get("display_url"):
            return [{"type": "photo", "url": node["display_url"]}]

        return items

    def _parse_instagram_html_text(self, html_text: str) -> List[Dict[str, Any]]:
        """Parse Instagram HTML embed or post page for photos, videos, and mixed carousels."""
        items: List[Dict[str, Any]] = []

        # 1. Search for edge_sidecar_to_children in script tags
        import json
        sidecar_match = re.search(r'\"edge_sidecar_to_children\":\s*\{\s*\"edges\":\s*(\[.*?\])\s*\}', html_text, re.DOTALL)
        if sidecar_match:
            try:
                edges = json.loads(sidecar_match.group(1))
                for edge in edges:
                    node = edge.get("node", {})
                    w = node.get("dimensions", {}).get("width", 0)
                    h = node.get("dimensions", {}).get("height", 0)
                    if node.get("is_video") and node.get("video_url"):
                        items.append({"type": "video", "url": node["video_url"], "width": w, "height": h})
                    elif node.get("display_url"):
                        best_url = node["display_url"]
                        if node.get("display_resources"):
                            best_res = max(node["display_resources"], key=lambda r: r.get("config_width", 0))
                            best_url = best_res.get("src") or best_url
                        items.append({"type": "photo", "url": best_url, "width": w, "height": h})
                if items:
                    return items
            except Exception:
                pass

        # 2. Check single video_url or display_url in JSON-LD / scripts
        vid_match = re.search(r'\"video_url\":\s*\"([^\"]+)\"', html_text)
        if vid_match:
            try:
                vid_url = vid_match.group(1).encode().decode("unicode-escape").replace(r"\/", "/")
                items.append({"type": "video", "url": vid_url})
                return items
            except Exception:
                pass

        img_match = re.search(r'\"display_url\":\s*\"([^\"]+)\"', html_text)
        if img_match:
            try:
                img_url = img_match.group(1).encode().decode("unicode-escape").replace(r"\/", "/")
                items.append({"type": "photo", "url": img_url})
                return items
            except Exception:
                pass

        # 3. OpenGraph tags in HTML
        og_vid = re.search(r'<meta\s+(?:property|name)=[\"\']og:video(?::secure_url)?[\"\']\s+content=[\"\']([^\"\']+)[\"\']', html_text)
        if og_vid:
            items.append({"type": "video", "url": og_vid.group(1).replace("&amp;", "&")})
            return items

        og_img = re.search(r'<meta\s+(?:property|name)=[\"\']og:image[\"\']\s+content=[\"\']([^\"\']+)[\"\']', html_text)
        if og_img:
            items.append({"type": "photo", "url": og_img.group(1).replace("&amp;", "&")})
            return items

        return items

    async def _extract_via_ddinstagram(self, shortcode: str) -> Optional[List[Dict[str, Any]]]:
        """Attempt extraction via ddinstagram mirror for multi-media carousels."""
        url = f"https://www.ddinstagram.com/p/{shortcode}/"
        headers = {
            "User-Agent": "TelegramBot (like TwitterBot)",
            "Accept": "text/html,application/xhtml+xml",
        }
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        html_text = await resp.text()
                        videos = re.findall(r'<meta\s+(?:property|name)=[\"\']og:video(?::secure_url)?[\"\']\s+content=[\"\']([^\"\']+)[\"\']', html_text)
                        images = re.findall(r'<meta\s+(?:property|name)=[\"\']og:image[\"\']\s+content=[\"\']([^\"\']+)[\"\']', html_text)
                        items = []
                        for v in videos:
                            items.append({"type": "video", "url": v.replace("&amp;", "&")})
                        for im in images:
                            items.append({"type": "photo", "url": im.replace("&amp;", "&")})
                        if items:
                            return items
        except Exception:
            pass
        return None

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
        """
        Extract media using yt-dlp native Instagram extractor with custom product_info
        interceptor for flawless extraction of photos, reels, and mixed multi-item carousels.
        """
        loop = asyncio.get_event_loop()

        def _sync_ytdlp():
            captured = []
            from yt_dlp.extractor.instagram import InstagramIE

            class CustomInstagramIE(InstagramIE):
                def _extract_product(self, product_info, video_id=None, get_comments=False):
                    if product_info:
                        captured.append(product_info)
                    return super()._extract_product(product_info, video_id, get_comments)

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "socket_timeout": 20,
            }
            cookie_file = get_instagram_cookie_file()
            if cookie_file:
                ydl_opts["cookiefile"] = str(cookie_file)
            if YTDLP_PROXY:
                ydl_opts["proxy"] = YTDLP_PROXY

            ydl = yt_dlp.YoutubeDL(ydl_opts)
            ie = CustomInstagramIE(ydl)
            last_err = None
            try:
                ie.extract(url)
            except Exception as e:
                last_err = str(e)

            # If we intercepted raw product_info, extract original HD media directly
            if captured:
                p_info = captured[0]
                if isinstance(p_info, list):
                    p_info = p_info[0]

                items = []
                carousel = p_info.get("carousel_media")
                if carousel:
                    for idx, item in enumerate(carousel):
                        v_versions = item.get("video_versions")
                        img_candidates = item.get("image_versions2", {}).get("candidates", [])
                        if v_versions:
                            best_vid = max(v_versions, key=lambda v: (v.get("width", 0) * v.get("height", 0)))
                            items.append({
                                "type": "video",
                                "url": best_vid["url"],
                                "width": best_vid.get("width") or item.get("original_width"),
                                "height": best_vid.get("height") or item.get("original_height"),
                            })
                        elif img_candidates:
                            best_img = max(img_candidates, key=lambda i: (i.get("width", 0) * i.get("height", 0)))
                            items.append({
                                "type": "photo",
                                "url": best_img["url"],
                                "width": best_img.get("width") or item.get("original_width"),
                                "height": best_img.get("height") or item.get("original_height"),
                            })
                        elif item.get("display_uri"):
                            items.append({
                                "type": "photo",
                                "url": item["display_uri"],
                                "width": item.get("original_width"),
                                "height": item.get("original_height"),
                            })
                else:
                    v_versions = p_info.get("video_versions")
                    img_candidates = p_info.get("image_versions2", {}).get("candidates", [])
                    if v_versions:
                        best_vid = max(v_versions, key=lambda v: (v.get("width", 0) * v.get("height", 0)))
                        items.append({
                            "type": "video",
                            "url": best_vid["url"],
                            "width": best_vid.get("width") or p_info.get("original_width"),
                            "height": best_vid.get("height") or p_info.get("original_height"),
                        })
                    elif img_candidates:
                        best_img = max(img_candidates, key=lambda i: (i.get("width", 0) * i.get("height", 0)))
                        items.append({
                            "type": "photo",
                            "url": best_img["url"],
                            "width": best_img.get("width") or p_info.get("original_width"),
                            "height": best_img.get("height") or p_info.get("original_height"),
                        })
                    elif p_info.get("display_uri"):
                        items.append({
                            "type": "photo",
                            "url": p_info["display_uri"],
                            "width": p_info.get("original_width"),
                            "height": p_info.get("original_height"),
                        })

                if items:
                    return items, None

            # Fallback to standard yt-dlp extraction if CustomInstagramIE did not capture product
            try:
                info = ydl.extract_info(url, download=False)
                if info:
                    if "entries" in info and info["entries"]:
                        items = []
                        for entry in info["entries"]:
                            if not entry:
                                continue
                            direct_url = entry.get("url")
                            if not direct_url and entry.get("formats"):
                                direct_url = entry["formats"][-1].get("url")
                            if not direct_url and entry.get("thumbnail"):
                                direct_url = entry.get("thumbnail")
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

                    direct_url = info.get("url")
                    if not direct_url and info.get("formats"):
                        direct_url = info["formats"][-1].get("url")
                    if not direct_url and info.get("thumbnail"):
                        direct_url = info.get("thumbnail")
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
            except Exception as e:
                if not last_err:
                    last_err = str(e)

            return [], last_err or "No downloadable stream found in this post."

        try:
            items, err = await loop.run_in_executor(None, _sync_ytdlp)
            if items:
                return items, None
            return [], err
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
                if item_type == "video":
                    from services.ffmpeg_service import ffmpeg_service
                    capped_file = job_dir / f"capped_{dest_file.name}"
                    did_cap = await ffmpeg_service.cap_video_to_1080p(dest_file, capped_file)
                    if did_cap and capped_file.exists() and capped_file.stat().st_size > 0:
                        dest_file.unlink(missing_ok=True)
                        capped_file.rename(dest_file)
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

        # Cap any videos exceeding 1080p to 1080p using FFmpeg; leave photos untouched at original quality
        from services.ffmpeg_service import ffmpeg_service
        for it in downloaded:
            if it.get("type") == "video" and it.get("file_path"):
                v_file = it["file_path"]
                capped_file = job_dir / f"capped_{v_file.name}"
                did_cap = await ffmpeg_service.cap_video_to_1080p(v_file, capped_file)
                if did_cap and capped_file.exists() and capped_file.stat().st_size > 0:
                    v_file.unlink(missing_ok=True)
                    capped_file.rename(v_file)

        return downloaded


# Global singleton instagram service
instagram_service = InstagramService()
