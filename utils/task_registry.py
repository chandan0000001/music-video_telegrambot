"""
Task Registry for Active Media Downloads and Jobs
Enables tracking and instant in-flight cancellation across concurrent downloads per user.
"""
import asyncio
import logging
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class TaskRegistry:
    """Tracks per-user active asyncio download tasks and their progress trackers."""

    def __init__(self):
        # user_id -> {task_id: (task, tracker)}
        self._active_jobs: Dict[int, Dict[int, tuple[asyncio.Task, Optional[Any]]]] = {}

    def register(self, user_id: int, task: asyncio.Task, tracker: Optional[Any] = None) -> None:
        """Register or update a user's active processing task and progress tracker."""
        jobs = self._active_jobs.setdefault(user_id, {})
        jobs[id(task)] = (task, tracker)
        logger.debug(f"Registered active task {id(task)} for user {user_id} (total: {len(jobs)})")

    def unregister(self, user_id: int, task: Optional[asyncio.Task] = None) -> None:
        """Unregister a finished processing task for a user."""
        jobs = self._active_jobs.get(user_id)
        if not jobs:
            return

        if task is not None:
            jobs.pop(id(task), None)
        else:
            try:
                cur = asyncio.current_task()
            except RuntimeError:
                cur = None
            if cur is not None and id(cur) in jobs:
                jobs.pop(id(cur), None)
            else:
                # Prune done tasks; if none explicitly matched (legacy callers), clear all
                remaining = {tid: pair for tid, pair in jobs.items() if not pair[0].done()}
                if len(remaining) < len(jobs):
                    jobs = remaining
                    self._active_jobs[user_id] = jobs
                else:
                    jobs.clear()

        if not jobs:
            self._active_jobs.pop(user_id, None)
        logger.debug(f"Unregistered task for user {user_id}")

    def cancel(self, user_id: int) -> bool:
        """
        Instantly cancel all active in-flight downloads for a user.
        Signals trackers to raise DownloadCancelled in yt-dlp hook,
        and aborts running asyncio Tasks.
        """
        jobs = self._active_jobs.get(user_id)
        if not jobs:
            return False

        cancelled_any = False
        for task, tracker in list(jobs.values()):
            if tracker and hasattr(tracker, "cancel"):
                try:
                    tracker.cancel()
                    cancelled_any = True
                except Exception as e:
                    logger.warning(f"Error signalling tracker cancel for user {user_id}: {e}")

            if not task.done():
                task.cancel()
                logger.info(f"Cancelled in-flight asyncio task {id(task)} for user {user_id}")
                cancelled_any = True

        return cancelled_any

    def is_active(self, user_id: int) -> bool:
        """Check if user has at least one active download running."""
        jobs = self._active_jobs.get(user_id)
        if not jobs:
            return False
        # Auto-prune stale done tasks
        live = {tid: pair for tid, pair in jobs.items() if not pair[0].done()}
        if not live:
            self._active_jobs.pop(user_id, None)
            return False
        self._active_jobs[user_id] = live
        return True


task_registry = TaskRegistry()
