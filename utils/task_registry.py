"""
Task Registry for Active Media Downloads and Jobs
Enables tracking and instant in-flight cancellation of active downloads.
"""
import asyncio
import logging
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class TaskRegistry:
    """Thread-safe registry mapping user IDs to their running asyncio tasks and trackers."""

    def __init__(self):
        self._active_tasks: Dict[int, asyncio.Task] = {}
        self._active_trackers: Dict[int, Any] = {}

    def register(self, user_id: int, task: asyncio.Task, tracker: Optional[Any] = None) -> None:
        """Register a user's active processing task and progress tracker."""
        self._active_tasks[user_id] = task
        if tracker:
            self._active_trackers[user_id] = tracker
        logger.debug(f"Registered active task for user {user_id}")

    def unregister(self, user_id: int) -> None:
        """Unregister a user's task when processing finishes."""
        self._active_tasks.pop(user_id, None)
        self._active_trackers.pop(user_id, None)
        logger.debug(f"Unregistered task for user {user_id}")

    def cancel(self, user_id: int) -> bool:
        """
        Instantly cancel an in-flight download for a user.
        Signals tracker to raise DownloadCancelled in yt-dlp hook,
        and aborts the asyncio Task running the job.
        """
        cancelled_any = False
        tracker = self._active_trackers.get(user_id)
        if tracker and hasattr(tracker, "cancel"):
            try:
                tracker.cancel()
                cancelled_any = True
            except Exception as e:
                logger.warning(f"Error signalling tracker cancel for user {user_id}: {e}")

        task = self._active_tasks.get(user_id)
        if task and not task.done():
            task.cancel()
            logger.info(f"Cancelled in-flight asyncio task for user {user_id}")
            cancelled_any = True

        return cancelled_any

    def is_active(self, user_id: int) -> bool:
        """Check if user has an active download running."""
        task = self._active_tasks.get(user_id)
        return bool(task and not task.done())


task_registry = TaskRegistry()
