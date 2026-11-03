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
        Admins and VIP users receive elevated/unrestricted limits.
        """
        # Admin bypass: 100% unrestricted
        if user_id in ADMIN_IDS:
            return False, 0.0

        from utils.vip_manager import vip_manager
        is_vip = await vip_manager.is_vip(user_id)
        effective_max = 25 if is_vip else self.max_requests

        now = time.time()
        async with self._lock:
            timestamps = self._user_requests[user_id]

            # Evict timestamps outside current window
            while timestamps and (now - timestamps[0]) > self.window_seconds:
                timestamps.popleft()

            if len(timestamps) >= effective_max:
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


class LinkSpamGuard:
    """
    Enforces a strict 6-second cooldown between links and download triggers.
    Warns users on fast repeat links, and temporarily bans if violated repeatedly.
    """

    def __init__(self, cooldown_seconds: float = 6.0):
        self.cooldown_seconds = cooldown_seconds
        self._last_link_time: Dict[int, float] = {}
        self._spam_violations: Dict[int, list[float]] = defaultdict(list)
        self._temp_bans: Dict[int, float] = {}
        self._lock = asyncio.Lock()

    async def check_link_spam(self, user_id: int) -> tuple[bool, str]:
        """
        Check if user is spamming links.
        Returns (is_blocked, message_to_user).
        Admins and VIP users are completely exempt.
        """
        if user_id in ADMIN_IDS:
            return False, ""

        from utils.vip_manager import vip_manager
        if await vip_manager.is_vip(user_id):
            return False, ""

        now = time.time()
        async with self._lock:
            # Check if user is currently under a temporary spam ban
            ban_until = self._temp_bans.get(user_id, 0.0)
            if now < ban_until:
                mins_left = int((ban_until - now) // 60) + 1
                return True, (
                    f"🚫 **Access Temporarily Restricted**\n\n"
                    f"You were restricted for repeated link spamming.\n"
                    f"Please try again in **{mins_left} minute(s)**."
                )

            # Check cooldown against last link
            last_time = self._last_link_time.get(user_id, 0.0)
            elapsed = now - last_time

            if elapsed < self.cooldown_seconds:
                remaining = int(self.cooldown_seconds - elapsed) + 1
                violations = self._spam_violations[user_id]
                # Keep violations from the last 30 seconds
                violations = [v for v in violations if (now - v) < 30.0]
                violations.append(now)
                self._spam_violations[user_id] = violations

                # If 3 or more violations within 30 seconds, trigger 10-minute temp ban
                if len(violations) >= 3:
                    self._temp_bans[user_id] = now + 600.0  # 10 minutes
                    self._spam_violations[user_id] = []
                    return True, (
                        "🚫 **Temporary Ban Activated (10 Minutes)**\n\n"
                        "You sent too many links too quickly despite warnings.\n"
                        "Your access has been temporarily paused for 10 minutes."
                    )

                return True, (
                    f"⚠️ **Ladle, don't spam here! Otherwise you will be banned.**\n\n"
                    f"Please wait **{remaining} second(s)** before sending another link."
                )

            # Update last link time
            self._last_link_time[user_id] = now
            return False, ""


# Global singletons
rate_limiter = UserRateLimiter()
concurrency_manager = ConcurrencyManager()
link_spam_guard = LinkSpamGuard()


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

            # Skip throttling for inline menu navigation callbacks to allow natural browsing
            cb_data = (event.data or "")
            if any(cb_data.startswith(prefix) for prefix in (
                "menu:", "opt_", "fmt_", "page:", "vdl:", "adl:", "back", "cancel", "coupon:", "plan:", "dosearch:", "sel:"
            )):
                return await handler(event, data)

        if user_id:
            # Administrators have zero rate limits
            if user_id in ADMIN_IDS:
                return await handler(event, data)

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
