"""
Two-Tier Queue and Concurrency Manager with VIP Priority Support
Enforces:
1. Server Global Limit: 8 active downloads, 8 active uploads.
2. Tiered Per-User Limits: Free users max 3, Paid users max 10, Admins unlimited.
3. VIP Priority Fast-Track: Admins bypass queue entirely; Paid subscribers jump
   ahead of free users in the queue.
4. Live Dynamic Queue: Real-time countdown updates (#1, #2, #3...) every 1.5 seconds.
"""
import time
import asyncio
import logging
from typing import Dict, List, Optional
from aiogram.types import Message

from utils.vip_manager import (
    vip_manager,
    PRIORITY_FREE,
    PRIORITY_PAID,
    PRIORITY_ADMIN
)

logger = logging.getLogger(__name__)

MAX_GLOBAL_DOWNLOADS = 8
MAX_GLOBAL_UPLOADS = 8
MAX_PER_USER_JOBS_FREE = 3


class QueueItem:
    """Represents a job waiting in the queue for a download slot."""

    def __init__(self, user_id: int, media_title: str, status_msg: Message, priority: int = PRIORITY_FREE):
        self.user_id = user_id
        self.media_title = media_title
        self.status_msg = status_msg
        self.priority = priority
        self.ready_event = asyncio.Event()
        self.is_cancelled = False
        self.enqueued_at = time.time()
        self.last_rendered_text = ""
        self.last_update_time = 0.0


class DownloadQueueManager:
    """
    Coordinates global bandwidth limits, per-user limits, and dynamic
    VIP priority queue notifications with real-time Telegram message updates.
    """

    def __init__(
        self,
        max_downloads: int = MAX_GLOBAL_DOWNLOADS,
        max_uploads: int = MAX_GLOBAL_UPLOADS
    ):
        self.max_downloads = max_downloads
        self.max_uploads = max_uploads

        self._active_downloads: int = 0
        self._active_uploads: int = 0
        self._user_active_jobs: Dict[int, int] = {}
        self._user_job_timestamps: Dict[int, List[float]] = {}
        self._waiting_queue: List[QueueItem] = []

        self._lock = asyncio.Lock()
        self._queue_worker_task: Optional[asyncio.Task] = None

    def start_worker(self) -> None:
        """Ensure the background queue position updater loop is running."""
        if not self._queue_worker_task or self._queue_worker_task.done():
            self._queue_worker_task = asyncio.create_task(self._queue_loop())

    def _purge_stale_user_jobs(self, ttl_seconds: float = 600.0) -> None:
        """Purge any active job timestamps older than 10 minutes so users can never be permanently locked out."""
        now = time.time()
        for uid in list(self._user_job_timestamps.keys()):
            valid = [t for t in self._user_job_timestamps[uid] if (now - t) < ttl_seconds]
            if len(valid) != len(self._user_job_timestamps[uid]):
                if valid:
                    self._user_job_timestamps[uid] = valid
                    self._user_active_jobs[uid] = len(valid)
                else:
                    self._user_job_timestamps.pop(uid, None)
                    self._user_active_jobs.pop(uid, None)

    async def can_user_start_job(self, user_id: int) -> tuple[bool, str]:
        """Check if user has exceeded their tiered concurrent active jobs limit."""
        from config import ADMIN_IDS
        if user_id in ADMIN_IDS:
            return True, ""

        priority = await vip_manager.get_user_priority(user_id)
        if priority >= PRIORITY_ADMIN:
            return True, ""

        # Auto-heal any stale jobs older than 10 minutes
        self._purge_stale_user_jobs()

        max_allowed = vip_manager.get_max_concurrent_jobs(priority)

        async with self._lock:
            active = self._user_active_jobs.get(user_id, 0)
            if active >= max_allowed:
                tier_label = "VIP Pass" if priority >= PRIORITY_PAID else "Free Tier"
                return False, (
                    f"⚠️ **Concurrent Limit Reached!** ({tier_label})\n\n"
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
        - Admins: Absolute queue bypass.
        - Paid Subscribers: Priority Fast-Track ahead of free users.
        - Free Users: Standard FIFO queue.
        """
        self.start_worker()
        self._purge_stale_user_jobs()
        priority = await vip_manager.get_user_priority(user_id)

        now = time.time()
        async with self._lock:
            # 1. Admin absolute bypass: Never wait in queue
            if priority >= PRIORITY_ADMIN:
                self._active_downloads += 1
                self._user_active_jobs[user_id] = self._user_active_jobs.get(user_id, 0) + 1
                self._user_job_timestamps.setdefault(user_id, []).append(now)
                return True

            # 2. If slots are open and no one with higher/equal priority is waiting
            has_higher_waiting = any(q.priority >= priority and not q.is_cancelled for q in self._waiting_queue)
            if self._active_downloads < self.max_downloads and not has_higher_waiting:
                self._active_downloads += 1
                self._user_active_jobs[user_id] = self._user_active_jobs.get(user_id, 0) + 1
                self._user_job_timestamps.setdefault(user_id, []).append(now)
                return True

            # 3. Enter queue with priority weighting
            item = QueueItem(
                user_id=user_id,
                media_title=media_title,
                status_msg=status_msg,
                priority=priority
            )
            self._waiting_queue.append(item)
            # Higher priority items jump ahead of lower priority items
            self._sort_queue()

            position = self._get_item_position(item)
            active_now = self._active_downloads

        # Render initial queue screen immediately
        await self._render_queue_message(item, position, active_now)

        # Wait until our turn arrives or cancellation happens
        await item.ready_event.wait()

        if item.is_cancelled:
            async with self._lock:
                self._active_downloads = max(0, self._active_downloads - 1)
            return False

        async with self._lock:
            self._user_active_jobs[user_id] = self._user_active_jobs.get(user_id, 0) + 1
            self._user_job_timestamps.setdefault(user_id, []).append(time.time())

        return True

    def _sort_queue(self) -> None:
        """Sort waiting queue: highest priority first, then FIFO by enqueued time."""
        self._waiting_queue.sort(key=lambda x: (-x.priority, x.enqueued_at))

    def _get_item_position(self, item: QueueItem) -> int:
        """Get 1-based position of item among active waiting items."""
        pos = 1
        for q in self._waiting_queue:
            if q is item:
                return pos
            if not q.is_cancelled:
                pos += 1
        return pos

    def release_download_slot(self, user_id: int) -> None:
        """Release a download slot, decrement user's active job counter, and advance queue."""
        asyncio.create_task(self._release_download_async(user_id))

    async def _release_download_async(self, user_id: int) -> None:
        async with self._lock:
            # 1. Decrement user's active job counter
            if user_id in self._user_active_jobs:
                self._user_active_jobs[user_id] = max(0, self._user_active_jobs[user_id] - 1)
                if self._user_active_jobs[user_id] == 0:
                    del self._user_active_jobs[user_id]
            if user_id in self._user_job_timestamps and self._user_job_timestamps[user_id]:
                self._user_job_timestamps[user_id].pop(0)
                if not self._user_job_timestamps[user_id]:
                    del self._user_job_timestamps[user_id]

            # 2. Decrement global active downloads
            self._active_downloads = max(0, self._active_downloads - 1)
            self._sort_queue()

            # Find next waiting item that is not cancelled
            while self._waiting_queue:
                next_item = self._waiting_queue.pop(0)
                if not next_item.is_cancelled:
                    self._active_downloads += 1
                    is_vip = next_item.priority >= PRIORITY_PAID
                    msg_prefix = "👑 *VIP Fast-Track:* " if is_vip else "🚀 "
                    try:
                        await next_item.status_msg.edit_text(
                            f"{msg_prefix}*Slot opened! Starting download now...*",
                            parse_mode="Markdown"
                        )
                    except Exception:
                        pass
                    next_item.ready_event.set()
                    break

    async def acquire_upload_slot(self, user_id: int) -> None:
        """Wait for 1 of the 8 global upload slots."""
        from config import ADMIN_IDS
        if user_id in ADMIN_IDS:
            return

        priority = await vip_manager.get_user_priority(user_id)
        # Admin immediate upload bypass
        if priority >= PRIORITY_ADMIN:
            async with self._lock:
                self._active_uploads += 1
                return

        while True:
            async with self._lock:
                if self._active_uploads < self.max_uploads:
                    self._active_uploads += 1
                    return
            await asyncio.sleep(0.4)

    def release_upload_slot(self, user_id: int = 0) -> None:
        """Release an upload slot."""
        asyncio.create_task(self._release_upload_async())

    async def _release_upload_async(self) -> None:
        async with self._lock:
            self._active_uploads = max(0, self._active_uploads - 1)

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
                self._sort_queue()
                items_snapshot = [q for q in self._waiting_queue if not q.is_cancelled]
                active_count = self._active_downloads

            if not items_snapshot:
                continue

            for idx, item in enumerate(items_snapshot):
                pos = idx + 1
                await self._render_queue_message(item, pos, active_count)

    async def _render_queue_message(self, item: QueueItem, position: int, active_count: int = 8) -> None:
        title = item.media_title[:38] + "..." if len(item.media_title) > 38 else item.media_title
        ahead = position - 1
        wait_est = max(2, ahead * 8)

        is_vip = item.priority >= PRIORITY_PAID
        badge = "👑 *VIP Fast-Track Active*" if is_vip else "👥 *Standard Queue*"
        pos_suffix = " (VIP Priority)" if is_vip else ""

        text = (
            f"⏳ *Server Busy: You are in Queue*\n"
            f"🎬 *Media:* `{title}`\n\n"
            f"{badge}\n"
            f"📊 *Your Queue Position:* `#{position}{pos_suffix}`\n"
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
