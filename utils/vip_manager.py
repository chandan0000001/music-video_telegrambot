"""
VIP & Paid Subscriber Priority Manager
Provides priority classification, queue bypass, elevated concurrent limits,
and anti-spam immunity for Admins and Paid Plan Subscribers (Go, Pro, Ultra, Max).
Kept cleanly separated, modular, and performant.
"""
import logging
from typing import Optional, Dict, Any
from config import ADMIN_IDS
from database import db

logger = logging.getLogger(__name__)

# Priority levels
PRIORITY_FREE = 0
PRIORITY_PAID = 1
PRIORITY_ADMIN = 2

# Maximum concurrent active jobs by tier
MAX_CONCURRENT_FREE = 3
MAX_CONCURRENT_PAID = 10
MAX_CONCURRENT_ADMIN = 999


class VIPManager:
    """Manages VIP benefits, priority resolution, and queue bypass privileges."""

    @staticmethod
    async def get_user_priority(user_id: int) -> int:
        """
        Determine user's priority tier:
        - PRIORITY_ADMIN (2): Admin (Highest priority, absolute queue bypass)
        - PRIORITY_PAID (1): Paid Subscriber (Go, Pro, Ultra, Max, etc. - VIP Priority)
        - PRIORITY_FREE (0): Free User (Standard queue)
        """
        if user_id in ADMIN_IDS:
            return PRIORITY_ADMIN

        try:
            sub = await db.get_user_subscription(user_id)
            if sub and sub.get("is_active"):
                tier = (sub.get("plan_tier") or "").lower()
                if tier in ("go", "pro", "ultra", "max", "weekly", "monthly"):
                    return PRIORITY_PAID
        except Exception as e:
            logger.debug(f"Error checking user priority for {user_id}: {e}")

        return PRIORITY_FREE

    @staticmethod
    async def is_vip(user_id: int) -> bool:
        """Returns True if user is Admin or has any active paid subscription."""
        priority = await VIPManager.get_user_priority(user_id)
        return priority > PRIORITY_FREE

    @staticmethod
    def get_max_concurrent_jobs(priority: int) -> int:
        """Return the maximum allowed simultaneous downloads for this priority level."""
        if priority >= PRIORITY_ADMIN:
            return MAX_CONCURRENT_ADMIN
        if priority >= PRIORITY_PAID:
            return MAX_CONCURRENT_PAID
        return MAX_CONCURRENT_FREE


# Global singleton instance
vip_manager = VIPManager()
