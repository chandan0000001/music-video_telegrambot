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
from typing import Optional

from config import TEMP_DIR

logger = logging.getLogger(__name__)

STALE_FILE_MAX_AGE_SECONDS = 30 * 60  # 30 minutes
CLEANUP_INTERVAL_SECONDS = 15 * 60     # run every 15 minutes


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
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
                pruned_count += 1
        except Exception as e:
            logger.debug(f"Could not remove temp item {item}: {e}")

    if pruned_count > 0:
        logger.info(f"🧹 Disk Cleaner: Pruned {pruned_count} abandoned temp file(s)/directory(ies).")

    return pruned_count


async def periodic_disk_cleaner_task(interval: float = CLEANUP_INTERVAL_SECONDS) -> None:
    """Background asyncio task that runs periodically to keep disk space free."""
    while True:
        try:
            await asyncio.sleep(interval)
            purge_stale_temp_files()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic disk cleaner: {e}")
