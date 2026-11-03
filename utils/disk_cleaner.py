"""
Disk Cleaner and Background Garbage Collector
Monitors the temp/ storage directory and automatically removes abandoned
or stale media files older than 30 minutes, preventing EBS disk exhaustion.
"""
import time
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from config import TEMP_DIR

logger = logging.getLogger(__name__)

STALE_FILE_MAX_AGE_SECONDS = 30 * 60  # 30 minutes
CLEANUP_INTERVAL_SECONDS = 15 * 60     # run every 15 minutes


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


def purge_stale_temp_files(temp_dir: Optional[Path] = None, max_age: float = STALE_FILE_MAX_AGE_SECONDS) -> int:
    """
    Synchronously scan and remove files/folders older than max_age from temp_dir.
    Returns number of pruned items.
    """
    target = temp_dir or TEMP_DIR
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
        return 0

    now = time.time()
    pruned_count = 0

    for item in target.iterdir():
        try:
            mtime = item.stat().st_mtime
            if (now - mtime) > max_age:
                if item.is_dir():
                    try:
                        shutil.rmtree(item)
                    except OSError as e:
                        logger.warning(f"Could not remove stale temp directory {item}: {e}")
                else:
                    item.unlink(missing_ok=True)
                pruned_count += 1
        except Exception as e:
            logger.debug(f"Could not process temp item {item}: {e}")

    if pruned_count > 0:
        logger.info(f"🧹 Disk Cleaner: Pruned {pruned_count} abandoned temp file(s)/directory(ies).")

    return pruned_count


async def purge_stale_temp_files_async(temp_dir: Optional[Path] = None, max_age: float = STALE_FILE_MAX_AGE_SECONDS) -> int:
    """PERF-3: Non-blocking async version of purge_stale_temp_files running in thread pool."""
    return await asyncio.to_thread(purge_stale_temp_files, temp_dir, max_age)


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
