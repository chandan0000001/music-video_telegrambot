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


@pytest.mark.asyncio
async def test_selection_session_manager():
    """Verify 30-min TTL expiry, link supersession, and admin exemption."""
    from utils.selection_session import SelectionSessionManager
    from unittest.mock import AsyncMock, MagicMock
    from aiogram import Bot

    manager = SelectionSessionManager(ttl_seconds=1.0)
    bot = MagicMock(spec=Bot)
    bot.edit_message_text = AsyncMock()

    user_id = 555666
    admin_id = next(iter(ADMIN_IDS))

    # 1. Register Session for Video A
    await manager.register_session(bot, user_id=user_id, chat_id=123, message_id=10, video_id="VID_A")
    valid, _ = manager.check_validity(user_id, "VID_A")
    assert valid is True

    # 2. Register Session for Video B (Supersedes Video A)
    await manager.register_session(bot, user_id=user_id, chat_id=123, message_id=20, video_id="VID_B")
    bot.edit_message_text.assert_called_once()
    # VID_A is now invalid because it was superseded
    valid_a, reason_a = manager.check_validity(user_id, "VID_A")
    assert valid_a is False
    assert "replaced by a newer link" in reason_a

    # VID_B is valid
    valid_b, _ = manager.check_validity(user_id, "VID_B")
    assert valid_b is True

    # 3. Wait for TTL expiry (> 1.0s)
    await asyncio.sleep(1.1)
    valid_b_expired, reason_exp = manager.check_validity(user_id, "VID_B")
    assert valid_b_expired is False
    assert "expired" in reason_exp

    # 4. Admin exemption: Admin never expires
    await manager.register_session(bot, user_id=admin_id, chat_id=123, message_id=30, video_id="ADMIN_VID")
    await asyncio.sleep(1.1)
    admin_valid, _ = manager.check_validity(admin_id, "ADMIN_VID")
    assert admin_valid is True


@pytest.mark.asyncio
async def test_queue_manager_active_jobs_auto_release():
    """Verify active jobs decrement to 0 on release and allow consecutive downloads."""
    from utils.queue_manager import DownloadQueueManager
    from unittest.mock import MagicMock, AsyncMock
    from aiogram.types import Message

    qm = DownloadQueueManager(max_downloads=8, max_uploads=8)
    msg = MagicMock(spec=Message)
    msg.edit_text = AsyncMock()
    user_id = 777123

    # Run 5 consecutive downloads, releasing each
    for _ in range(5):
        can, _ = await qm.can_user_start_job(user_id)
        assert can is True
        await qm.acquire_download_slot(user_id, "Track", msg)
        assert qm._user_active_jobs.get(user_id, 0) == 1
        qm.release_download_slot(user_id)
        await asyncio.sleep(0.01)
        assert qm._user_active_jobs.get(user_id, 0) == 0

    # User can still start 6th download
    can, _ = await qm.can_user_start_job(user_id)
    assert can is True

