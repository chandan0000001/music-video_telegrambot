"""
Shared Application-Wide HTTP Client & Async Streaming Helpers
Reuses pooled aiohttp.ClientSession connections safely per running event loop,
ensures explicit lifecycle shutdown cleanup, and eliminates blocking file writes.
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import aiohttp

from utils.metrics import metrics

logger = logging.getLogger(__name__)


class HTTPClientManager:
    """Manages reusable pooled aiohttp ClientSession instances keyed cleanly by event loop."""

    def __init__(self) -> None:
        self._sessions: Dict[int, aiohttp.ClientSession] = {}
        self._locks: Dict[int, asyncio.Lock] = {}

    async def get_session(self) -> aiohttp.ClientSession:
        """Return an active shared ClientSession bound to the current event loop."""
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        session = self._sessions.get(loop_id)
        if session is not None and not session.closed:
            return session

        lock = self._locks.setdefault(loop_id, asyncio.Lock())
        async with lock:
            session = self._sessions.get(loop_id)
            if session is not None and not session.closed:
                return session

            connector = aiohttp.TCPConnector(
                limit=64,
                limit_per_host=12,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
            )
            default_timeout = aiohttp.ClientTimeout(total=60, connect=10)
            session = aiohttp.ClientSession(connector=connector, timeout=default_timeout)
            self._sessions[loop_id] = session
            return session

    async def close_loop_session(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Close the ClientSession associated with the given (or current) event loop."""
        target_loop = loop or asyncio.get_running_loop()
        loop_id = id(target_loop)
        session = self._sessions.pop(loop_id, None)
        self._locks.pop(loop_id, None)
        if session is not None and not session.closed:
            await session.close()

    async def close_all(self) -> None:
        """Close all tracked sessions cleanly during application shutdown."""
        for loop_id, session in list(self._sessions.items()):
            self._sessions.pop(loop_id, None)
            self._locks.pop(loop_id, None)
            if session is not None and not session.closed:
                try:
                    await session.close()
                except Exception as e:
                    logger.debug(f"Error closing HTTP session for loop {loop_id}: {e}")


http_client = HTTPClientManager()


async def probe_content_length(
    url: str,
    timeout_seconds: float = 5.0,
    headers: Optional[Dict[str, str]] = None,
) -> int:
    """Probe remote Content-Length via HEAD using the shared HTTP session."""
    start = time.perf_counter()
    try:
        session = await http_client.get_session()
        metrics.incr("http_requests")
        async with session.head(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            allow_redirects=True,
        ) as resp:
            return max(0, int(resp.headers.get("Content-Length", 0) or 0))
    except Exception:
        return 0
    finally:
        metrics.record_timing("http", (time.perf_counter() - start) * 1000.0)


async def stream_url_to_file(
    url: str,
    dest_path: Path,
    *,
    timeout_seconds: float = 60.0,
    chunk_size: int = 256 * 1024,
    max_bytes: int = 0,
    headers: Optional[Dict[str, str]] = None,
    on_chunk: Optional[Any] = None,
) -> tuple[bool, int]:
    """
    Stream HTTP GET response to dest_path using non-blocking aiofiles I/O.
    Returns (success, bytes_written). Enforces max_bytes if > 0.
    """
    start = time.perf_counter()
    bytes_written = 0
    try:
        session = await http_client.get_session()
        metrics.incr("http_requests")
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout_seconds),
        ) as resp:
            if resp.status != 200:
                logger.warning(f"HTTP download failed ({resp.status}) for {url}")
                return False, 0

            header_len = int(resp.headers.get("Content-Length", 0) or 0)
            if max_bytes > 0 and header_len > max_bytes:
                logger.warning(f"Aborted download exceeding size cap ({header_len} > {max_bytes} bytes)")
                return False, 0

            async with aiofiles.open(dest_path, "wb") as f:
                while True:
                    chunk = await resp.content.read(chunk_size)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if max_bytes > 0 and bytes_written > max_bytes:
                        logger.warning(f"Stream exceeded byte cap ({max_bytes} bytes) for {dest_path.name}")
                        return False, bytes_written
                    await f.write(chunk)
                    if on_chunk is not None:
                        res = on_chunk(len(chunk), bytes_written, header_len)
                        if asyncio.iscoroutine(res):
                            res = await res
                        if res is False:
                            logger.warning(f"Stream aborted due to aggregate size budget for {dest_path.name}")
                            return False, bytes_written
        return True, bytes_written
    except Exception as e:
        logger.error(f"Error streaming {url} to {dest_path}: {e}")
        return False, bytes_written
    finally:
        metrics.record_timing("http", (time.perf_counter() - start) * 1000.0)


async def download_carousel_batch(
    media_items: List[Dict[str, Any]],
    job_dir: Path,
    *,
    max_items: int = 20,
    max_total_bytes: int = 500 * 1024 * 1024,
    concurrency: int = 4,
    tracker: Optional[Any] = None,
    timeout_seconds: float = 60.0,
) -> List[Dict[str, Any]]:
    """
    Download multi-item media batches with bounded concurrency (semaphore),
    shared pooled HTTP session, async file writes, and aggregate byte limits.
    """
    if not media_items:
        return []

    bounded_items = list(media_items[:max(1, max_items)])
    if len(media_items) > len(bounded_items):
        logger.info(f"Carousel truncated from {len(media_items)} to {len(bounded_items)} items (cap).")

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _bounded_probe(it: Dict[str, Any]) -> int:
        async with sem:
            return await probe_content_length(it["url"], timeout_seconds=5.0)

    sizes = await asyncio.gather(*[_bounded_probe(it) for it in bounded_items])

    # Select items within aggregate byte budget
    selected: List[tuple[int, Dict[str, Any], int]] = []
    probed_budget = 0
    for idx, (item, size) in enumerate(zip(bounded_items, sizes)):
        if max_total_bytes > 0 and size > 0 and (probed_budget + size) > max_total_bytes and selected:
            logger.info(f"Skipping carousel item {idx} to stay within {max_total_bytes} byte aggregate cap.")
            continue
        if max_total_bytes > 0 and size > max_total_bytes:
            logger.info(f"Skipping oversized carousel item {idx} ({size} > {max_total_bytes} bytes).")
            continue
        selected.append((idx, item, size))
        probed_budget += max(0, size)

    if not selected:
        return []

    total_estimated = sum(s for _, _, s in selected)
    start_time = time.monotonic()
    lock = asyncio.Lock()
    total_downloaded = 0

    if tracker:
        tracker.status = "downloading"
        tracker.total_bytes = total_estimated if total_estimated > 0 else (len(selected) * 1024 * 1024)

    async def _download_one(index: int, item: Dict[str, Any], est_size: int) -> Optional[Dict[str, Any]]:
        nonlocal total_downloaded
        item_type = item.get("type", "photo")
        ext = "jpg" if item_type == "photo" else "mp4"
        dest_file = job_dir / f"media_{index + 1}.{ext}"

        async with sem:
            async with lock:
                remaining_budget = max(0, max_total_bytes - total_downloaded) if max_total_bytes > 0 else 0
            if max_total_bytes > 0 and remaining_budget == 0:
                return None

            async def _on_chunk(chunk_len: int, _written: int, _total: int) -> Optional[bool]:
                nonlocal total_downloaded
                if not tracker and max_total_bytes <= 0:
                    return True
                async with lock:
                    total_downloaded += chunk_len
                    if max_total_bytes > 0 and total_downloaded > max_total_bytes:
                        return False
                    if tracker:
                        tracker.downloaded_bytes = total_downloaded
                        if tracker.total_bytes > 0:
                            tracker.percentage = min(100.0, (total_downloaded / tracker.total_bytes) * 100.0)
                        elapsed = time.monotonic() - start_time
                        if elapsed > 0:
                            tracker.speed = total_downloaded / elapsed
                            if tracker.speed > 0 and tracker.total_bytes > total_downloaded:
                                tracker.eta = int((tracker.total_bytes - total_downloaded) / tracker.speed)
                return True

            ok, written = await stream_url_to_file(
                item["url"],
                dest_file,
                timeout_seconds=timeout_seconds,
                chunk_size=64 * 1024,
                max_bytes=remaining_budget,
                on_chunk=_on_chunk,
            )
            if ok and dest_file.exists() and written > 0:
                return {**item, "file_path": dest_file, "index": index}
            if dest_file.exists():
                dest_file.unlink(missing_ok=True)
            return None

    results = await asyncio.gather(
        *[_download_one(idx, item, sz) for idx, item, sz in selected],
        return_exceptions=True,
    )
    downloaded = [r for r in results if isinstance(r, dict) and r.get("file_path")]
    downloaded.sort(key=lambda x: x.get("index", 0))
    return downloaded
