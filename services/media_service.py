"""
Media Processing Service
Handles yt-dlp metadata extraction, media downloading, conversion coordination, and resource cleanup.
"""
import os
import time
import shutil
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import yt_dlp
import aiohttp

from config import (
    TEMP_DIR,
    MAX_DURATION_SECONDS,
    MAX_UPLOAD_SIZE_BYTES,
    MAX_UPLOAD_SIZE_MB,
    SUPPORTED_AUDIO_FORMATS,
    SUPPORTED_AUDIO_QUALITIES,
    SUPPORTED_VIDEO_RESOLUTIONS
)
from services.ffmpeg_service import ffmpeg_service
from services.proxy_manager import youtube_proxy_manager, mask_proxy_url
from utils.http_client import stream_url_to_file
from utils.metrics import metrics
from utils.validators import (
    extract_youtube_id,
    sanitize_filename,
    validate_media_limits,
    classify_media_restriction
)
from services.music_service import format_duration

logger = logging.getLogger(__name__)


STANDARD_TIER_WIDTHS: Dict[int, int] = {
    144: 256,
    240: 426,
    360: 640,
    480: 854,
    720: 1280,
    1080: 1920,
    1440: 2560,
    2160: 3840
}


def _notify_status(status_callback: Optional[Any], text: str, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """Safely deliver status update from worker thread to asyncio event loop."""
    if not status_callback:
        return
    try:
        if loop and loop.is_running():
            if asyncio.iscoroutinefunction(status_callback):
                asyncio.run_coroutine_threadsafe(status_callback(text), loop)
            else:
                def _run():
                    res = status_callback(text)
                    if asyncio.iscoroutine(res):
                        asyncio.create_task(res)
                loop.call_soon_threadsafe(_run)
        else:
            res = status_callback(text)
            if asyncio.iscoroutine(res):
                asyncio.run(res)
    except Exception as e:
        logger.debug(f"Could not deliver status notification: {e}")


class MediaService:
    """Manages media downloads, quality inspection, and conversions."""

    _INFO_CACHE_TTL_SECONDS = 300.0
    _INFO_CACHE_MAX_ENTRIES = 128

    def __init__(self, temp_base: Path = TEMP_DIR):
        self.temp_base = temp_base
        self._raw_info_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def _get_cached_raw_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        entry = self._raw_info_cache.get(video_id)
        if not entry:
            return None
        ts, raw = entry
        if (time.monotonic() - ts) > self._INFO_CACHE_TTL_SECONDS:
            self._raw_info_cache.pop(video_id, None)
            return None
        return raw

    def _set_cached_raw_info(self, video_id: str, raw_info: Dict[str, Any]) -> None:
        if not video_id or not raw_info:
            return
        if len(self._raw_info_cache) >= self._INFO_CACHE_MAX_ENTRIES:
            oldest_key = min(self._raw_info_cache, key=lambda k: self._raw_info_cache[k][0])
            self._raw_info_cache.pop(oldest_key, None)
        self._raw_info_cache[video_id] = (time.monotonic(), raw_info)

    def _get_job_dir(self) -> Path:
        """Create a dedicated unique temporary folder for a download job."""
        from utils.disk_cleaner import register_active_job_dir

        job_id = uuid.uuid4().hex
        job_dir = self.temp_base / f"job_{job_id}"
        job_dir.mkdir(parents=True, exist_ok=True)
        register_active_job_dir(job_dir)
        return job_dir

    def cleanup_job_dir(self, job_dir: Path) -> None:
        """LOW-4: Completely clean up job directory and log any cleanup errors."""
        from utils.disk_cleaner import unregister_active_job_dir

        unregister_active_job_dir(job_dir)
        try:
            if job_dir.exists() and job_dir.is_dir():
                shutil.rmtree(job_dir)
                logger.debug(f"Cleaned up temporary directory: {job_dir}")
        except OSError as e:
            logger.warning(f"Failed to remove temp dir {job_dir}: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error removing temp dir {job_dir}: {e}")

    def _get_common_ydl_opts(self, with_cookies: bool = False, proxy: Optional[str] = None) -> Dict[str, Any]:
        """Base options for yt-dlp metadata and downloads."""
        from config import get_cookie_file, YTDLP_PROXY
        opts: Dict[str, Any] = {
            "remote_components": ["ejs:github"],
        }
        node_bin = shutil.which("node") or shutil.which("nodejs")
        if node_bin:
            opts["js_runtimes"] = {"node": {"path": node_bin}}
        opts["proxy"] = proxy if proxy is not None else (YTDLP_PROXY or "")
        opts["noplaylist"] = True
        # Use yt-dlp's current client defaults; do not silently read a browser profile.
        if with_cookies:
            cookie_file = get_cookie_file()
            if cookie_file:
                opts["cookiefile"] = str(cookie_file)
        return opts

    def _apply_turbo_downloader(self, opts: Dict[str, Any]) -> None:
        """Enable multi-connection parallel downloads via aria2c if available."""
        from config import USE_ARIA2
        if USE_ARIA2:
            aria2_bin = shutil.which("aria2c")
            if aria2_bin:
                opts["external_downloader"] = "aria2c"
                opts["external_downloader_args"] = {
                    "aria2c": [
                        "-s", "8",
                        "-x", "8",
                        "-k", "1M",
                        "--min-split-size=1M",
                        "--file-allocation=none",
                        "--summary-interval=0"
                    ]
                }

    def _sync_extract_info(
        self,
        url: str,
        status_callback: Optional[Any] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> Dict[str, Any]:
        """Extract metadata and analyze available video and audio formats.
        Direct-first with round-robin proxy fallback:
        1. First hit: Own IP WITH COOKIES (direct authenticated request, no proxy).
        2. If Own IP encounters a bot block / 403 and proxies exist:
           Notify user that proxy routing is active (delays may occur).
           Rotate through available proxies!
        """
        is_yt = youtube_proxy_manager.is_youtube_url(url)
        vid_id = extract_youtube_id(url) if is_yt else None
        if vid_id:
            cached = self._get_cached_raw_info(vid_id)
            if cached:
                metrics.incr("cache_hits")
                return cached
        metrics.incr("cache_misses")
        metrics.incr("metadata_extractions")
        meta_start = time.perf_counter()

        direct_error = None
        # Try the configured connection with current yt-dlp client defaults.
        clean_opts = {
            **self._get_common_ydl_opts(with_cookies=True),
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 6 if is_yt else 15,
            "retries": 1 if is_yt else 10,
            "extractor_retries": 1 if is_yt else 3,
            "noplaylist": True,
            "check_formats": False,
        }
        try:
            with yt_dlp.YoutubeDL(clean_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                fmts = [f for f in info.get("formats", []) if f.get("vcodec") != "none"]
                if len(fmts) > 0 or not is_yt:
                    metrics.record_timing("metadata", (time.perf_counter() - meta_start) * 1000.0)
                    if vid_id and len(fmts) > 0 and info.get("is_live") is not True and (info.get("live_status") or "").lower() not in ("is_live", "is_upcoming"):
                        self._set_cached_raw_info(vid_id, info)
                    return info
        except Exception as e:
            if not is_yt:
                raise
            if not youtube_proxy_manager.is_retryable_error(e):
                raise
            direct_error = e
            logger.warning(
                f"Direct Own IP extraction encountered challenge/block for YouTube {url}: {e}."
            )

        # Check if proxies are available
        proxies = youtube_proxy_manager.get_proxies()
        if not proxies:
            logger.info("No fallback proxies available in proxy pool.")
            if direct_error is not None:
                raise direct_error
            raise RuntimeError("No playable video formats returned by YouTube.")

        # Notify user that proxy routing is active
        _notify_status(
            status_callback,
            "🔄 <i>Direct connection challenged by YouTube. Routing via proxy pool (please wait a moment...)...</i>",
            loop
        )

        # 2. Proceed to Round-Robin YouTube Proxies (strictly isolated to YouTube)
        last_error = None
        for attempt in range(min(4, len(proxies))):
            proxy = youtube_proxy_manager.get_next_proxy()
            if not proxy:
                break
            logger.info(f"Extracting YouTube metadata via round-robin proxy {mask_proxy_url(proxy)} (attempt {attempt + 1})...")
            proxy_opts = {
                **self._get_common_ydl_opts(with_cookies=True, proxy=proxy),
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "socket_timeout": 8,
                "retries": 0,
                "extractor_retries": 0,
                "check_formats": False,
            }
            try:
                with yt_dlp.YoutubeDL(proxy_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    fmts = [f for f in info.get("formats", []) if f.get("vcodec") != "none"]
                    if len(fmts) > 0:
                        metrics.record_timing("metadata", (time.perf_counter() - meta_start) * 1000.0)
                        if vid_id and info.get("is_live") is not True and (info.get("live_status") or "").lower() not in ("is_live", "is_upcoming"):
                            self._set_cached_raw_info(vid_id, info)
                        return info
            except Exception as pe:
                last_error = pe
                err_str = str(pe).lower()
                if "402" in err_str or "bandwidthlimit" in err_str or "payment required" in err_str:
                    youtube_proxy_manager.mark_exhausted(proxy)
                else:
                    youtube_proxy_manager.mark_cooldown(proxy)
                logger.warning(f"Proxy extraction failed via {mask_proxy_url(proxy)}: {pe}. Rotating to next proxy...")

        if last_error:
            raise last_error
        raise RuntimeError("Could not retrieve media formats via direct connection or YouTube proxy pool.")

    async def get_media_info(
        self,
        url_or_id: str,
        status_callback: Optional[Any] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Validate URL, extract metadata, and determine genuine available qualities.
        Returns (success, info_dict, error_message).
        """
        video_id = extract_youtube_id(url_or_id) if "http" in url_or_id else url_or_id
        if not video_id:
            return False, None, "Invalid or unsupported YouTube URL. Please provide a valid YouTube video link."

        canonical_url = f"https://www.youtube.com/watch?v={video_id}"
        loop = asyncio.get_running_loop()

        try:
            raw_info = await asyncio.wait_for(
                asyncio.to_thread(self._sync_extract_info, canonical_url, status_callback, loop),
                timeout=45.0
            )
        except asyncio.TimeoutError:
            logger.error(f"Extraction timed out after 45s for {canonical_url}")
            return False, None, "YouTube did not respond within 45 seconds. Please try again shortly."
        except Exception as e:
            logger.error(f"Failed to extract info for {canonical_url}: {e}")
            classified = classify_media_restriction(str(e))
            if classified:
                return False, None, classified[1]
            return False, None, f"Could not retrieve video information: {str(e)[:120]}"

        if not raw_info:
            return False, None, "No metadata returned by the source."

        # Check if the video is an active live stream or upcoming premiere
        is_live = raw_info.get("is_live") is True
        live_status = (raw_info.get("live_status") or "").lower()

        # 1. Block active live broadcasts in progress
        if is_live or live_status == "is_live":
            import html
            title_escaped = html.escape(raw_info.get("title", "Live Event"))
            return False, None, (
                "🔴 <b>Access Restricted: Active Live Stream</b>\n\n"
                f"🎬 <b>Title:</b> <code>{title_escaped}</code>\n\n"
                "⚠️ This video is currently <b>Broadcasting Live</b> and cannot be downloaded while in progress.\n\n"
                "<i>Please wait until the live stream finishes and archives as a complete video before downloading!</i>"
            )

        # 2. Block upcoming premieres or scheduled live streams not yet aired
        if live_status == "is_upcoming":
            import html
            title_escaped = html.escape(raw_info.get("title", "Upcoming Event"))
            return False, None, (
                "⏳ <b>Access Notice: Upcoming Premiere / Live Event</b>\n\n"
                f"🎬 <b>Title:</b> <code>{title_escaped}</code>\n\n"
                "⚠️ This event has not premiered or started broadcasting yet.\n\n"
                "<i>Please check back and download after the premiere has concluded!</i>"
            )

        # 3. Post-live processing check: if stream just concluded and formats are not yet ready
        formats = raw_info.get("formats", [])
        has_video_formats = any(f.get("height") and f.get("vcodec") and f.get("vcodec") != "none" for f in formats)
        if live_status == "post_live" and not has_video_formats:
            import html
            title_escaped = html.escape(raw_info.get("title", "Live Event"))
            return False, None, (
                "⏳ <b>Access Notice: Live Stream Processing</b>\n\n"
                f"🎬 <b>Title:</b> <code>{title_escaped}</code>\n\n"
                "⚠️ This live broadcast recently concluded and YouTube is currently processing the recorded archive.\n\n"
                "<i>Please try again in a few minutes once processing completes!</i>"
            )

        duration = raw_info.get("duration")
        is_valid, err_msg = validate_media_limits(duration, None)
        if not is_valid:
            return False, None, err_msg

        # Filter genuinely available video resolutions (low to high)
        formats = raw_info.get("formats", [])
        video_dims = []
        available_heights = set()
        for f in formats:
            vcodec = f.get("vcodec")
            if not vcodec or vcodec == "none":
                continue
            w = f.get("width")
            h = f.get("height")
            if h:
                available_heights.add(h)
            if w or h:
                video_dims.append((w, h))

        # Standard supported resolutions matching available dimensions (supports standard 16:9, widescreen 2.35:1, vertical 9:16, 4:3)
        available_resolutions: List[str] = []
        for res_str in SUPPORTED_VIDEO_RESOLUTIONS:
            tier_h = int(res_str.replace("p", ""))
            tier_w = STANDARD_TIER_WIDTHS.get(tier_h, int(tier_h * 16 / 9))

            matched = False
            for w, h in video_dims:
                # 1. Direct height match
                if h and abs(h - tier_h) <= 20:
                    matched = True
                    break
                # 2. Direct width match (e.g. widescreen cinema where width defines resolution tier)
                if w and abs(w - tier_w) <= 30:
                    matched = True
                    break
                # 3. Aspect-ratio adjusted effective height
                if w and h:
                    effective_h = max(h, int(w * 9 / 16)) if w >= h else min(w, h)
                    if abs(effective_h - tier_h) <= 25:
                        matched = True
                        break
            if matched:
                available_resolutions.append(res_str)

        # If no standard matched but video formats exist, add available heights directly
        if not available_resolutions and available_heights:
            sorted_h = sorted(list(available_heights))
            available_resolutions = [f"{h}p" for h in sorted_h if h <= 2160]

        # Ensure low-to-high order
        available_resolutions.sort(key=lambda x: int(x.replace("p", "")))

        # Approximate sizes for audio bitrates
        approx_audio_sizes: Dict[str, str] = {}
        if duration:
            for br in SUPPORTED_AUDIO_QUALITIES:
                size_mb = (duration * int(br) * 1000) / (8 * 1024 * 1024)
                approx_audio_sizes[br] = f"{size_mb:.1f} MB"

        thumbnails = raw_info.get("thumbnails", [])
        thumbnail_url = thumbnails[-1]["url"] if thumbnails else None

        info = {
            "id": video_id,
            "url": canonical_url,
            "title": raw_info.get("title", "Unknown Title"),
            "artist": raw_info.get("uploader") or raw_info.get("channel") or "Unknown Artist",
            "duration": duration,
            "duration_str": format_duration(duration),
            "thumbnail": thumbnail_url,
            "available_resolutions": available_resolutions,
            "audio_formats": SUPPORTED_AUDIO_FORMATS,
            "audio_qualities": SUPPORTED_AUDIO_QUALITIES,
            "approx_audio_sizes": approx_audio_sizes
        }

        return True, info, None

    def _sync_download(
        self,
        ydl_opts: Dict[str, Any],
        url: str,
        status_callback: Optional[Any] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None
    ) -> None:
        """Synchronous yt-dlp download invocation.
        Direct-first with round-robin proxy fallback for YouTube:
        1. Attempt download with Own IP using cookies.
        2. If Own IP fails on YouTube (403, bot challenge) and proxies exist:
           Notify user that proxy routing is active (delays may occur).
           Extract info_dict using proxy (~50 KB), then stream directly from googlevideo.com
           without proxy for 0-quota consumption!
        """
        is_yt = youtube_proxy_manager.is_youtube_url(url)
        metrics.incr("downloads")
        dl_start = time.perf_counter()
        direct_error = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                metrics.record_timing("download", (time.perf_counter() - dl_start) * 1000.0)
                return
        except Exception as e:
            if not is_yt or not youtube_proxy_manager.is_retryable_error(e):
                raise
            direct_error = e
            logger.warning(
                f"Direct download failed for YouTube {url} ({e}). Checking proxy pool fallback..."
            )

        proxies = youtube_proxy_manager.get_proxies()
        if not proxies:
            logger.info("No fallback proxies available in proxy pool.")
            raise direct_error

        # Notify user that proxy routing is active
        _notify_status(
            status_callback,
            "🔄 <b>Direct connection restricted. Routing download via proxy pool (please wait a moment...)...</b>",
            loop
        )

        # Retry with round-robin proxies (strictly isolated to YouTube)
        # ZERO-PROXY-BANDWIDTH ARCHITECTURE:
        # 1. Use proxy strictly to extract info_dict (metadata only ~50 KB).
        # 2. Download media streams directly from googlevideo.com WITHOUT proxy (0 MB proxy quota consumed!).
        # 3. Only if direct stream download fails, fallback to full proxy streaming as safety net.
        last_dl_error = None
        for attempt in range(min(4, len(proxies))):
            proxy = youtube_proxy_manager.get_next_proxy()
            if not proxy:
                break
            logger.info(f"Extracting YouTube download metadata via proxy (attempt {attempt + 1})...")
            extract_opts = dict(ydl_opts)
            extract_opts["proxy"] = proxy
            extract_opts["skip_download"] = True
            extract_opts["socket_timeout"] = 25
            # Pop extractor_args so player_client list doesn't cause yt-dlp to skip clients when cookies are active
            extract_opts.pop("extractor_args", None)

            # Retain only explicitly configured cookie files from ydl_opts.

            # Strictly preserve requested format selector (e.g. height<=2160)
            if "format" in ydl_opts:
                extract_opts["format"] = ydl_opts["format"]

            try:
                # Step A: Extract stream URLs via proxy (~50 KB)
                with yt_dlp.YoutubeDL(extract_opts) as ydl_extract:
                    info_dict = ydl_extract.extract_info(url, download=False)

                # Step B: Download media streams directly without proxy (0 MB proxy quota consumed!)
                direct_opts = dict(ydl_opts)
                direct_opts["proxy"] = ""
                direct_opts["socket_timeout"] = 30
                try:
                    logger.info("Directly streaming media from googlevideo.com (bypassing proxy for 0-quota consumption)...")
                    with yt_dlp.YoutubeDL(direct_opts) as ydl_direct:
                        ydl_direct.process_ie_result(info_dict, download=True)
                        return
                except Exception as direct_err:
                    logger.warning(
                        f"Direct stream download failed ({direct_err}). Falling back to proxy tunnel for download..."
                    )
                    # Step C: Fallback to full proxy download if direct streaming fails
                    proxy_opts = dict(ydl_opts)
                    proxy_opts["proxy"] = proxy
                    proxy_opts["socket_timeout"] = 30
                    with yt_dlp.YoutubeDL(proxy_opts) as ydl_proxy_stream:
                        ydl_proxy_stream.process_ie_result(info_dict, download=True)
                        return
            except Exception as pe:
                last_dl_error = pe
                err_str = str(pe).lower()
                if "402" in err_str or "bandwidthlimit" in err_str or "payment required" in err_str:
                    youtube_proxy_manager.mark_exhausted(proxy)
                else:
                    youtube_proxy_manager.mark_cooldown(proxy)
                logger.warning(f"Download attempt via proxy {mask_proxy_url(proxy)} failed: {pe}. Rotating to next proxy...")

        if last_dl_error:
            raise last_dl_error
        raise RuntimeError("All download attempts (Own IP and Proxies) failed.")

    async def download_thumbnail(self, thumbnail_url: str, dest_path: Path) -> bool:
        """Download remote thumbnail image to local file using shared HTTP session and aiofiles."""
        if not thumbnail_url:
            return False
        ok, _ = await stream_url_to_file(thumbnail_url, dest_path, timeout_seconds=15.0)
        return ok

    async def process_audio(
        self,
        video_url: str,
        output_format: str,
        bitrate_kbps: str,
        job_dir: Optional[Path] = None,
        tracker: Optional[Any] = None,
        status_callback: Optional[Any] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str], Path]:
        """
        Download and convert audio to requested format and bitrate.
        Returns (success, result_dict, error_msg, job_dir).
        The caller is responsible for cleaning up job_dir after delivery.
        """
        job_dir = job_dir or self._get_job_dir()
        output_format = output_format.lower()

        # yt-dlp options for best audio with universal fallback
        download_template = str(job_dir / "input_audio.%(ext)s")
        ydl_opts: Dict[str, Any] = {
            **self._get_common_ydl_opts(with_cookies=True),
            "format": "bestaudio/best/b",
            "outtmpl": download_template,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "concurrent_fragment_downloads": 8,
            "buffersize": 4 * 1024 * 1024,
            "http_chunk_size": 10485760,
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "nocheckcertificate": False,
        }
        if tracker and hasattr(tracker, "ytdlp_hook"):
            ydl_opts["progress_hooks"] = [tracker.ytdlp_hook]

        self._apply_turbo_downloader(ydl_opts)

        # Retry loop for resilience
        download_success = False
        last_error = ""
        loop = asyncio.get_running_loop()
        for attempt in range(1, 3):
            try:
                await asyncio.to_thread(self._sync_download, ydl_opts, video_url, status_callback, loop)
                download_success = True
                break
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Audio download attempt {attempt} failed for {video_url}: {e}")
                if attempt < 2:
                    await asyncio.sleep(1.5)

        if not download_success:
            classified = classify_media_restriction(last_error)
            if classified:
                return False, None, classified[1], job_dir
            return False, None, f"Audio download failed: {last_error[:100]}", job_dir

        # Identify downloaded file
        downloaded_files = list(job_dir.glob("input_audio.*"))
        if not downloaded_files:
            return False, None, "Downloaded audio file not found on disk.", job_dir

        raw_audio_file = downloaded_files[0]
        output_file = job_dir / f"audio_{uuid.uuid4().hex[:8]}.{output_format}"

        # Notify processing with FFmpeg
        if status_callback:
            try:
                await status_callback("⚙️ *Converting audio to high-fidelity " + output_format.upper() + " with FFmpeg...*")
            except Exception:
                pass

        # Convert using FFmpeg
        converted = await ffmpeg_service.convert_audio(
            input_file=raw_audio_file,
            output_file=output_file,
            target_format=output_format,
            bitrate_kbps=bitrate_kbps
        )

        if not converted or not output_file.exists():
            return False, None, "FFmpeg audio conversion failed.", job_dir

        file_size = output_file.stat().st_size
        if file_size > MAX_UPLOAD_SIZE_BYTES:
            size_mb = file_size / (1024 * 1024)
            return (
                False,
                None,
                f"Generated audio file ({size_mb:.1f} MB) exceeds Telegram's {MAX_UPLOAD_SIZE_MB} MB upload limit.",
                job_dir
            )

        # Probe duration
        probe = await ffmpeg_service.probe_media(output_file)
        duration = 0
        if probe and "format" in probe and "duration" in probe["format"]:
            try:
                duration = int(float(probe["format"]["duration"]))
            except (ValueError, TypeError):
                duration = 0

        # Try to download thumbnail if available (prefer cached metadata before network fetch)
        thumb_path = job_dir / "thumb.jpg"
        has_thumb = False
        try:
            vid_id = extract_youtube_id(video_url) or ""
            raw_info = self._get_cached_raw_info(vid_id) if vid_id else None
            if not raw_info:
                raw_info = await asyncio.to_thread(self._sync_extract_info, video_url)
                if vid_id and raw_info:
                    self._set_cached_raw_info(vid_id, raw_info)
            title = raw_info.get("title", "audio")
            artist = raw_info.get("uploader") or raw_info.get("channel") or "Unknown"
            thumbnails = raw_info.get("thumbnails", [])
            if thumbnails:
                has_thumb = await self.download_thumbnail(thumbnails[-1]["url"], thumb_path)
        except Exception:
            title = "audio"
            artist = "Unknown"

        result = {
            "file_path": output_file,
            "title": title,
            "artist": artist,
            "duration": duration,
            "thumbnail_path": thumb_path if has_thumb and thumb_path.exists() else None,
            "file_size": file_size,
            "format": output_format,
            "bitrate": bitrate_kbps
        }

        return True, result, None, job_dir

    async def process_video(
        self,
        video_url: str,
        resolution: str,
        job_dir: Optional[Path] = None,
        tracker: Optional[Any] = None,
        status_callback: Optional[Any] = None,
        video_title: Optional[str] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str], Path]:
        """
        Download and remux video to Telegram-compatible MP4 at specified resolution.
        Returns (success, result_dict, error_msg, job_dir).
        The caller is responsible for cleaning up job_dir after delivery.
        """
        job_dir = job_dir or self._get_job_dir()
        height_val = int(resolution.replace("p", ""))
        width_val = STANDARD_TIER_WIDTHS.get(height_val, int(height_val * 16 / 9))

        TIERS = [144, 240, 360, 480, 720, 1080, 1440, 2160]
        prev_h = 0
        for t in TIERS:
            if t < height_val:
                prev_h = t
        prev_w = STANDARD_TIER_WIDTHS.get(prev_h, 0)

        download_template = str(job_dir / "input_media.%(ext)s")
        # Tier-accurate selector:
        # Prioritize video matching the EXACT requested tier range (height > prev_h and <= height_val)
        # or width range (width > prev_w and <= width_val for ultrawide cinema videos).
        # This guarantees 4K (2160p), 1440p, 1080p are NEVER downgraded to 360p or lower codecs!
        format_selector = (
            f"bestvideo[height<={height_val}][height>{prev_h}][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={height_val}][height>{prev_h}]+bestaudio/"
            f"bestvideo[width<={width_val}][width>{prev_w}][vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[width<={width_val}][width>{prev_w}]+bestaudio/"
            f"bestvideo[height<={height_val}]+bestaudio/"
            f"bestvideo[width<={width_val}]+bestaudio/"
            f"bv*[height<={height_val}]+ba/"
            f"b[height<={height_val}]"
        )

        ydl_opts: Dict[str, Any] = {
            **self._get_common_ydl_opts(with_cookies=True),
            "format": format_selector,
            "outtmpl": download_template,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "merge_output_format": "mp4",
            "concurrent_fragment_downloads": 8,
            "buffersize": 4 * 1024 * 1024,
            "http_chunk_size": 10485760,
            "retries": 10,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "nocheckcertificate": False,
        }
        if tracker and hasattr(tracker, "ytdlp_hook"):
            ydl_opts["progress_hooks"] = [tracker.ytdlp_hook]

        self._apply_turbo_downloader(ydl_opts)

        # Retry loop for resilience against intermittent drops
        download_success = False
        last_error = ""
        loop = asyncio.get_running_loop()
        for attempt in range(1, 3):
            try:
                await asyncio.to_thread(self._sync_download, ydl_opts, video_url, status_callback, loop)
                download_success = True
                break
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Video download attempt {attempt} failed for {video_url} at {resolution}: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)

        if not download_success:
            classified = classify_media_restriction(last_error)
            if classified:
                return False, None, classified[1], job_dir
            return False, None, f"Video download failed: {last_error[:100]}", job_dir

        downloaded_files = list(job_dir.glob("input_media.*"))
        if not downloaded_files:
            return False, None, "Downloaded video file not found on disk.", job_dir

        downloaded_video = downloaded_files[0]
        final_mp4 = job_dir / f"video_{uuid.uuid4().hex[:8]}.mp4"

        # Notify remuxing phase
        if status_callback:
            try:
                await status_callback("⚙️ <b>Optimizing video container with FFmpeg for Telegram streaming...</b>")
            except Exception:
                pass

        # Remux with FFmpeg to guarantee faststart and Telegram streamability
        remuxed = await ffmpeg_service.remux_video_for_telegram(
            video_input=downloaded_video,
            audio_input=None,
            output_file=final_mp4
        )

        target_file = final_mp4 if (remuxed and final_mp4.exists()) else downloaded_video

        # Probe video dimensions and duration
        probe = await ffmpeg_service.probe_media(target_file)
        width, height, duration = 1280, 720, 0
        if probe:
            if "format" in probe and "duration" in probe["format"]:
                try:
                    duration = int(float(probe["format"]["duration"]))
                except (ValueError, TypeError):
                    pass
            streams = probe.get("streams", [])
            for s in streams:
                if s.get("codec_type") == "video":
                    width = s.get("width", width)
                    height = s.get("height", height)
                    break

        file_size = target_file.stat().st_size

        # Validate strictly against configured MAX_UPLOAD_SIZE_MB (from env, e.g. 2000 MB)
        if file_size > MAX_UPLOAD_SIZE_BYTES:
            size_mb = file_size / (1024 * 1024)
            return (
                False,
                None,
                f"Generated video file ({size_mb:.1f} MB) exceeds maximum upload limit of {MAX_UPLOAD_SIZE_MB} MB.",
                job_dir
            )

        # Extract video thumbnail
        thumb_path = job_dir / "video_thumb.jpg"
        has_thumb = await ffmpeg_service.extract_thumbnail(target_file, thumb_path)

        # Get metadata (check cache before hitting YouTube again)
        title = video_title or "Video"
        if not video_title:
            try:
                vid_id = extract_youtube_id(video_url) or ""
                raw_info = self._get_cached_raw_info(vid_id) if vid_id else None
                if not raw_info:
                    raw_info = await asyncio.to_thread(self._sync_extract_info, video_url)
                    if vid_id and raw_info:
                        self._set_cached_raw_info(vid_id, raw_info)
                title = raw_info.get("title", "Video")
            except Exception:
                title = "Video"

        result = {
            "file_path": target_file,
            "title": title,
            "duration": duration,
            "width": width,
            "height": height,
            "thumbnail_path": thumb_path if has_thumb and thumb_path.exists() else None,
            "file_size": file_size,
            "resolution": resolution
        }

        return True, result, None, job_dir


media_service = MediaService()
