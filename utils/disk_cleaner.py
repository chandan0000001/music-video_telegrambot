"""
Disk Cleaner and Background Garbage Collector
Monitors the temp/ storage directory and automatically removes abandoned
or stale media files older than 30 minutes, preventing EBS disk exhaustion.
"""
import time
import shutil
import asyncio
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any

from config import TEMP_DIR

logger = logging.getLogger(__name__)

STALE_FILE_MAX_AGE_SECONDS = 30 * 60  # 30 minutes
CLEANUP_INTERVAL_SECONDS = 5 * 60     # run every 5 minutes (guarantees strict 30-min purge)

_active_job_lock = threading.Lock()
_active_job_dirs: set[Path] = set()


def _normalize_job_path(job_dir: Path) -> Path:
    try:
        return Path(job_dir).resolve(strict=False)
    except Exception:
        return Path(job_dir)


def register_active_job_dir(job_dir: Optional[Path]) -> None:
    """Protect an active job directory from premature stale/low-disk cleanup."""
    if not job_dir:
        return
    norm = _normalize_job_path(Path(job_dir))
    with _active_job_lock:
        _active_job_dirs.add(norm)


def unregister_active_job_dir(job_dir: Optional[Path]) -> None:
    """Release cleanup protection once a job directory is no longer in use."""
    if not job_dir:
        return
    norm = _normalize_job_path(Path(job_dir))
    with _active_job_lock:
        _active_job_dirs.discard(norm)


def is_job_dir_active(job_dir: Optional[Path]) -> bool:
    """Return True if a job directory is currently registered as active."""
    if not job_dir:
        return False
    norm = _normalize_job_path(Path(job_dir))
    with _active_job_lock:
        return norm in _active_job_dirs


def get_disk_usage_stats(target_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Get total, used, free disk space in GB and percentage."""
    target = target_dir or TEMP_DIR
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
    total, used, free = shutil.disk_usage(target)
    free_mb = free / (1024 * 1024)
    free_gb = free / (1024 * 1024 * 1024)
    total_gb = total / (1024 * 1024 * 1024)
    used_gb = used / (1024 * 1024 * 1024)
    free_percent = (free / total) * 100 if total > 0 else 0
    return {
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "free_mb": free_mb,
        "free_percent": free_percent
    }


def check_disk_space_available(min_free_mb: int = 1000, target_dir: Optional[Path] = None) -> tuple[bool, float]:
    """
    Check if minimum required free space is available on the disk volume.
    If space is critically low, aggressively purges any stale temp files first.
    Returns (has_sufficient_space, free_mb).
    """
    stats = get_disk_usage_stats(target_dir)
    if stats["free_mb"] < min_free_mb:
        # Aggressively prune files older than 2 minutes to free immediate space
        purge_stale_temp_files(max_age=120)
        stats = get_disk_usage_stats(target_dir)

    return stats["free_mb"] >= min_free_mb, stats["free_mb"]


def _effective_entry_mtime(item: Path) -> float:
    """Return latest modification timestamp of entry or any direct child inside a directory."""
    mtime = item.stat().st_mtime
    if item.is_dir():
        try:
            for child in item.iterdir():
                try:
                    child_mtime = child.stat().st_mtime
                    if child_mtime > mtime:
                        mtime = child_mtime
                except OSError:
                    pass
        except OSError:
            pass
    return mtime


def _prune_single_entry(item: Path, now: float, max_age: float) -> int:
    """Remove an inactive file/directory older than max_age; return 1 if pruned else 0."""
    try:
        if item.is_dir() and is_job_dir_active(item):
            return 0
        mtime = _effective_entry_mtime(item)
        if (now - mtime) > max_age:
            if item.is_dir():
                try:
                    shutil.rmtree(item, ignore_errors=True)
                except OSError as e:
                    logger.warning(f"Could not remove stale temp directory {item}: {e}")
            else:
                item.unlink(missing_ok=True)
            return 1
    except Exception as e:
        logger.debug(f"Could not process temp item {item}: {e}")
    return 0


def _prune_directory_entries(directory: Path, now: float, max_age: float) -> int:
    pruned = 0
    for item in directory.iterdir():
        pruned += _prune_single_entry(item, now, max_age)
    return pruned


def purge_stale_temp_files(temp_dir: Optional[Path] = None, max_age: float = STALE_FILE_MAX_AGE_SECONDS) -> int:
    """
    Synchronously scan and remove inactive files/folders older than max_age from temp_dir
    and nested service buckets (instagram/, twitter/).
    Returns number of pruned items.
    """
    target = temp_dir or TEMP_DIR
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
        return 0

    now = time.time()
    pruned_count = 0

    for item in target.iterdir():
        if item.is_dir() and item.name in ("instagram", "twitter"):
            pruned_count += _prune_directory_entries(item, now, max_age)
            continue
        pruned_count += _prune_single_entry(item, now, max_age)

    if pruned_count > 0:
        logger.info(f"🧹 Disk Cleaner: Pruned {pruned_count} abandoned temp file(s)/directory(ies).")

    return pruned_count


async def purge_stale_temp_files_async(temp_dir: Optional[Path] = None, max_age: float = STALE_FILE_MAX_AGE_SECONDS) -> int:
    """PERF-3: Non-blocking async version of purge_stale_temp_files running in thread pool."""
    return await asyncio.to_thread(purge_stale_temp_files, temp_dir, max_age)


async def get_disk_usage_stats_async(target_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Non-blocking async wrapper for get_disk_usage_stats."""
    return await asyncio.to_thread(get_disk_usage_stats, target_dir)


async def check_disk_space_available_async(
    min_free_mb: int = 1000, target_dir: Optional[Path] = None
) -> tuple[bool, float]:
    """Non-blocking async wrapper for check_disk_space_available."""
    return await asyncio.to_thread(check_disk_space_available, min_free_mb, target_dir)


async def cleanup_dir_async(job_dir: Optional[Path]) -> None:
    """Non-blocking temporary directory removal executed off the event loop."""
    if not job_dir:
        return
    unregister_active_job_dir(job_dir)

    def _rm() -> None:
        try:
            if job_dir.exists() and job_dir.is_dir():
                shutil.rmtree(job_dir, ignore_errors=True)
        except Exception as e:
            logger.debug(f"Failed async cleanup for {job_dir}: {e}")

    await asyncio.to_thread(_rm)


def get_temp_dir_size_bytes(target_dir: Optional[Path] = None) -> int:
    """Calculate total temporary directory byte usage synchronously."""
    target = target_dir or TEMP_DIR
    if not target.exists():
        return 0
    total = 0
    try:
        for p in target.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except Exception:
        pass
    return total


async def get_temp_dir_size_bytes_async(target_dir: Optional[Path] = None) -> int:
    """Non-blocking temp directory byte size calculation."""
    return await asyncio.to_thread(get_temp_dir_size_bytes, target_dir)


def get_user_disk_usage(user_id: int, temp_dir: Optional[Path] = None) -> int:
    """MED-3: Calculate total temporary disk usage in bytes across all jobs for a specific user."""
    target = temp_dir or TEMP_DIR
    if not target.exists():
        return 0
    user_pattern = f"*_{user_id}_*"
    total_bytes = 0
    try:
        for p in target.glob(user_pattern):
            if p.is_file():
                total_bytes += p.stat().st_size
            elif p.is_dir():
                total_bytes += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:
        pass
    return total_bytes


async def periodic_disk_cleaner_task(interval: float = CLEANUP_INTERVAL_SECONDS) -> None:
    """Background asyncio task that runs periodically to keep disk space free."""
    while True:
        try:
            await asyncio.sleep(interval)
            await purge_stale_temp_files_async()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic disk cleaner: {e}")
