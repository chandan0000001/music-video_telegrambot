"""
Rate Limiter and Concurrency Control
Implements per-user request throttling and concurrency limits for media processing.
"""
import time
import asyncio
from collections import defaultdict, deque
from typing import Callable, Dict, Any, Awaitable, Optional

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from config import (
    RATE_LIMIT_REQUESTS,
    RATE_LIMIT_WINDOW,
    MAX_CONCURRENT_DOWNLOADS,
    ADMIN_IDS
)


class UserRateLimiter:
    """Sliding-window per-user rate limiter."""

    def __init__(self, max_requests: int = RATE_LIMIT_REQUESTS, window_seconds: int = RATE_LIMIT_WINDOW):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._user_requests: Dict[int, deque] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def is_rate_limited(self, user_id: int) -> tuple[bool, float]:
        """
        Check if user exceeded request threshold.
        Returns (is_limited, retry_after_seconds).
        """
        # Admin bypass
        if user_id in ADMIN_IDS:
            return False, 0.0

        now = time.time()
        async with self._lock:
            timestamps = self._user_requests[user_id]

            # Evict timestamps outside current window
            while timestamps and (now - timestamps[0]) > self.window_seconds:
                timestamps.popleft()

            if len(timestamps) >= self.max_requests:
                earliest = timestamps[0]
                retry_after = round(self.window_seconds - (now - earliest), 1)
                return True, max(0.5, retry_after)

            timestamps.append(now)
            return False, 0.0


class ConcurrencyManager:
    """Manages system-wide concurrent media processing operations."""

    def __init__(self, max_concurrent: int = MAX_CONCURRENT_DOWNLOADS):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self.max_concurrent = max_concurrent
        self._current_jobs = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        async with self._lock:
            self._current_jobs += 1

    def release(self) -> None:
        self._semaphore.release()
        # Decrement non-async safely
        if self._current_jobs > 0:
            self._current_jobs -= 1

    @property
    def active_jobs_count(self) -> int:
        return self._current_jobs


# Global singletons
rate_limiter = UserRateLimiter()
concurrency_manager = ConcurrencyManager()


class ThrottlingMiddleware(BaseMiddleware):
    """Aiogram middleware for per-user rate limiting."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user_id: Optional[int] = None
        target_message: Optional[Message] = None

        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            target_message = event
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            target_message = event.message if isinstance(event.message, Message) else None

        if user_id:
            is_limited, retry_after = await rate_limiter.is_rate_limited(user_id)
            if is_limited:
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        f"⏳ Please slow down! Wait {retry_after}s.",
                        show_alert=True
                    )
                elif target_message:
                    await target_message.answer(
                        f"⏳ **Rate limit reached!** Please wait `{retry_after}` seconds before trying again."
                    )
                return None

        return await handler(event, data)
