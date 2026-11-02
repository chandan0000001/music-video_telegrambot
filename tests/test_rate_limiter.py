"""
Unit Tests for Rate Limiter and Concurrency Control
"""
import pytest
import asyncio
from utils.rate_limiter import UserRateLimiter, ConcurrencyManager
from config import ADMIN_IDS


@pytest.mark.asyncio
async def test_user_rate_limiting():
    """Verify that requests exceeding threshold in window are limited."""
    limiter = UserRateLimiter(max_requests=3, window_seconds=2)
    user_id = 999111

    # First 3 requests should pass
    for _ in range(3):
        is_limited, _ = await limiter.is_rate_limited(user_id)
        assert is_limited is False

    # 4th request should be limited
    is_limited, retry_after = await limiter.is_rate_limited(user_id)
    assert is_limited is True
    assert retry_after > 0


@pytest.mark.asyncio
async def test_admin_bypasses_rate_limit():
    """Ensure configured administrators are never rate-limited."""
    limiter = UserRateLimiter(max_requests=2, window_seconds=10)
    admin_id = next(iter(ADMIN_IDS))

    # Many requests should all pass
    for _ in range(10):
        is_limited, _ = await limiter.is_rate_limited(admin_id)
        assert is_limited is False


@pytest.mark.asyncio
async def test_concurrency_manager():
    """Verify that concurrency manager tracks active jobs properly."""
    manager = ConcurrencyManager(max_concurrent=2)
    assert manager.active_jobs_count == 0

    await manager.acquire()
    assert manager.active_jobs_count == 1

    await manager.acquire()
    assert manager.active_jobs_count == 2

    manager.release()
    assert manager.active_jobs_count == 1

    manager.release()
    assert manager.active_jobs_count == 0
