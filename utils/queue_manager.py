"""
Two-Tier Queue and Concurrency Manager
Enforces:
1. Server Global Limit: 8 active downloads, 8 active uploads.
2. Per-User Limit: Max 3 active concurrent jobs per user.
3. Live Dynamic FIFO Queue: Auto-updates queue position (#1, #2, #3...) every 1.5 seconds.
"""
import time
import asyncio
import logging
from typing import Dict, List, Optional
from aiogram.types import Message

logger = logging.getLogger(__name__)

MAX_GLOBAL_DOWNLOADS = 8
MAX_GLOBAL_UPLOADS = 8
MAX_PER_USER_JOBS = 3


class QueueItem:
    """Represents a job waiting in the FIFO queue for a download slot."""

    def __init__(self, user_id: int, media_title: str, status_msg: Message):
        self.user_id = user_id
        self.media_title = media_title
        self.status_msg = status_msg
        self.ready_event = asyncio.Event()
        self.is_cancelled = False
        self.enqueued_at = time.time()
        self.last_rendered_text = ""
        self.last_update_time = 0.0


class DownloadQueueManager:
    """
    Coordinates global bandwidth limits, per-user limits, and dynamic
    queue position notifications with real-time Telegram message updates.
    """

    def __init__(
        self,
        max_downloads: int = MAX_GLOBAL_DOWNLOADS,
        max_uploads: int = MAX_GLOBAL_UPLOADS,
        max_per_user: int = MAX_PER_USER_JOBS
    ):
        self.max_downloads = max_downloads
        self.max_uploads = max_uploads
        self.max_per_user = max_per_user

        self._active_downloads: int = 0
        self._active_uploads: int = 0
        self._user_active_jobs: Dict[int, int] = {}
        self._waiting_queue: List[QueueItem] = []

        self._lock = asyncio.Lock()
        self._queue_worker_task: Optional[asyncio.Task] = None

    def start_worker(self) -> None:
        """Ensure the background queue position updater loop is running."""
        if not self._queue_worker_task or self._queue_worker_task.done():
            self._queue_worker_task = asyncio.create_task(self._queue_loop())

    async def can_user_start_job(self, user_id: int) -> tuple[bool, str]:
        """Check if user has exceeded their max 3 concurrent active jobs."""
        async with self._lock:
            active = self._user_active_jobs.get(user_id, 0)
            if active >= self.max_per_user:
                return False, (
                    f"⚠️ **Concurrent Limit Reached!**\n\n"
                    f"You already have **{active} downloads** running at the same time.\n"
                    f"Please wait for one of your downloads to complete before starting a new one."
                )
            return True, ""

    async def acquire_download_slot(
        self,
        user_id: int,
        media_title: str,
        status_msg: Message
    ) -> bool:
        """
        Acquire 1 of the 8 global download slots.
        If all slots are occupied, enters the FIFO queue and updates position
        every 1.5 seconds until a slot becomes available.
        Returns True if acquired, False if cancelled by user.
        """
        self.start_worker()

        async with self._lock:
            # If slot is immediately available and no one is waiting in queue
            if self._active_downloads < self.max_downloads and len(self._waiting_queue) == 0:
                self._active_downloads += 1
                self._user_active_jobs[user_id] = self._user_active_jobs.get(user_id, 0) + 1
                return True

            # Otherwise, must wait in the FIFO queue
            item = QueueItem(user_id=user_id, media_title=media_title, status_msg=status_msg)
            self._waiting_queue.append(item)
            position = len(self._waiting_queue)
            active_now = self._active_downloads

        # Render initial queue screen immediately
        await self._render_queue_message(item, position, active_now)

        # Wait until our turn arrives or cancellation happens
        await item.ready_event.wait()

        if item.is_cancelled:
            return False

        async with self._lock:
            self._user_active_jobs[user_id] = self._user_active_jobs.get(user_id, 0) + 1

        return True

    def release_download_slot(self, user_id: int) -> None:
        """Release a download slot and advance the waiting queue immediately."""
        asyncio.create_task(self._release_download_async())

    async def _release_download_async(self) -> None:
        async with self._lock:
            self._active_downloads = max(0, self._active_downloads - 1)
            # Find next waiting item that is not cancelled
            while self._waiting_queue:
                next_item = self._waiting_queue.pop(0)
                if not next_item.is_cancelled:
                    self._active_downloads += 1
                    try:
                        await next_item.status_msg.edit_text(
                            "🚀 *Slot opened! Starting download now...*",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass
                    next_item.ready_event.set()
                    break

    async def acquire_upload_slot(self, user_id: int) -> None:
        """Wait for 1 of the 8 global upload slots."""
        while True:
            async with self._lock:
                if self._active_uploads < self.max_uploads:
                    self._active_uploads += 1
                    return
            await asyncio.sleep(0.5)

    def release_upload_slot(self, user_id: int) -> None:
        """Release an upload slot and decrement user's active job counter."""
        asyncio.create_task(self._release_upload_async(user_id))

    async def _release_upload_async(self, user_id: int) -> None:
        async with self._lock:
            self._active_uploads = max(0, self._active_uploads - 1)
            if user_id in self._user_active_jobs:
                self._user_active_jobs[user_id] = max(0, self._user_active_jobs[user_id] - 1)
                if self._user_active_jobs[user_id] == 0:
                    del self._user_active_jobs[user_id]

    def cancel_queue_item(self, user_id: int) -> bool:
        """Remove a user's item from the queue if they click cancel."""
        cancelled = False
        for item in list(self._waiting_queue):
            if item.user_id == user_id and not item.is_cancelled:
                item.is_cancelled = True
                item.ready_event.set()
                if item in self._waiting_queue:
                    self._waiting_queue.remove(item)
                cancelled = True
        return cancelled

    async def _queue_loop(self) -> None:
        """Background loop updating all waiting users' queue positions every 1.5 seconds."""
        while True:
            await asyncio.sleep(1.5)
            async with self._lock:
                items_snapshot = list(self._waiting_queue)
                active_count = self._active_downloads

            if not items_snapshot:
                continue

            for idx, item in enumerate(items_snapshot):
                if item.is_cancelled:
                    continue
                pos = idx + 1
                await self._render_queue_message(item, pos, active_count)

    async def _render_queue_message(self, item: QueueItem, position: int, active_count: int = 8) -> None:
        title = item.media_title[:38] + "..." if len(item.media_title) > 38 else item.media_title
        ahead = position - 1
        wait_est = max(3, ahead * 8)
        text = (
            f"⏳ *Server Busy: You are in Queue*\n"
            f"🎬 *Media:* `{title}`\n\n"
            f"👥 *Your Queue Position:* `#{position}`\n"
            f"⏳ *Downloads ahead of you:* `{ahead}`\n"
            f"⚡ *Server Load:* `{active_count}/8 active downloads`\n"
            f"⏱ *Estimated wait:* `~{wait_est}s`\n\n"
            f"🔄 *Queue updates automatically every 1-2s...*"
        )
        if text != item.last_rendered_text:
            try:
                await item.status_msg.edit_text(text, parse_mode="Markdown")
                item.last_rendered_text = text
            except Exception:
                pass


# Global singleton queue manager
queue_manager = DownloadQueueManager()
