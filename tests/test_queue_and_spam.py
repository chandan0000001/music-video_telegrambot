"""
Unit Tests for Queue Manager and Link Spam Guard
Tests:
1. Per-user active limit (max 3 for free users, 10 for paid).
2. Global server limit (8 downloads, 8 uploads).
3. Dynamic VIP priority queue jump (paid subscribers jump ahead of free users).
4. Admin queue bypass (admins never wait).
5. Link spam guard 6-second cooldown & "Ladle, don't spam" warning.
6. Link spam guard 10-minute temporary ban after 3 fast violations.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message

from utils.queue_manager import DownloadQueueManager
from utils.rate_limiter import LinkSpamGuard
from utils.vip_manager import (
    vip_manager,
    PRIORITY_FREE,
    PRIORITY_PAID,
    PRIORITY_ADMIN
)


@pytest.mark.asyncio
async def test_per_user_limit_max_3_jobs():
    """Verify a free user cannot exceed 3 concurrent downloads."""
    qm = DownloadQueueManager(max_downloads=8, max_uploads=8)
    user_id = 99991

    can_start, _ = await qm.can_user_start_job(user_id)
    assert can_start is True

    # Simulate 3 active jobs
    qm._user_active_jobs[user_id] = 3

    can_start, err_msg = await qm.can_user_start_job(user_id)
    assert can_start is False
    assert "Concurrent Limit Reached" in err_msg


@pytest.mark.asyncio
async def test_global_download_slots_and_queue():
    """Verify jobs enter the queue when 2 slots are busy and auto-advance on release."""
    qm = DownloadQueueManager(max_downloads=2, max_uploads=2)

    msg1 = MagicMock(spec=Message)
    msg1.edit_text = AsyncMock()
    msg2 = MagicMock(spec=Message)
    msg2.edit_text = AsyncMock()
    msg3 = MagicMock(spec=Message)
    msg3.edit_text = AsyncMock()

    # Acquire slot 1 and 2 (immediate)
    res1 = await qm.acquire_download_slot(user_id=101, media_title="Item 1", status_msg=msg1)
    res2 = await qm.acquire_download_slot(user_id=102, media_title="Item 2", status_msg=msg2)
    assert res1 is True
    assert res2 is True
    assert qm._active_downloads == 2

    # Job 3 should be queued
    task3 = asyncio.create_task(
        qm.acquire_download_slot(user_id=103, media_title="Item 3", status_msg=msg3)
    )
    await asyncio.sleep(0.1)

    assert len(qm._waiting_queue) == 1
    assert qm._waiting_queue[0].user_id == 103

    # Release slot from job 1 -> job 3 should automatically be released and start
    qm.release_download_slot(user_id=101)
    res3 = await asyncio.wait_for(task3, timeout=2.0)
    assert res3 is True
    assert len(qm._waiting_queue) == 0


@pytest.mark.asyncio
async def test_vip_priority_queue_jump():
    """Verify a paid subscriber jumps ahead of free users in the queue."""
    qm = DownloadQueueManager(max_downloads=1, max_uploads=1)

    msg_active = MagicMock(spec=Message)
    msg_active.edit_text = AsyncMock()

    msg_free1 = MagicMock(spec=Message)
    msg_free1.edit_text = AsyncMock()

    msg_vip = MagicMock(spec=Message)
    msg_vip.edit_text = AsyncMock()

    # 1. Slot is occupied
    await qm.acquire_download_slot(user_id=301, media_title="Busy Item", status_msg=msg_active)

    # 2. Free user 302 enters queue (pos 1)
    task_free = asyncio.create_task(
        qm.acquire_download_slot(user_id=302, media_title="Free Item", status_msg=msg_free1)
    )
    await asyncio.sleep(0.1)
    assert len(qm._waiting_queue) == 1
    assert qm._waiting_queue[0].user_id == 302

    # 3. VIP user 303 enters queue with PRIORITY_PAID
    with patch.object(vip_manager, "get_user_priority", return_value=PRIORITY_PAID):
        task_vip = asyncio.create_task(
            qm.acquire_download_slot(user_id=303, media_title="VIP Item", status_msg=msg_vip)
        )
        await asyncio.sleep(0.1)

    # VIP user should now be at index 0 (ahead of free user 302)!
    assert len(qm._waiting_queue) == 2
    assert qm._waiting_queue[0].user_id == 303
    assert qm._waiting_queue[1].user_id == 302

    # 4. Release download slot -> VIP user 303 must be served first!
    qm.release_download_slot(user_id=301)
    res_vip = await asyncio.wait_for(task_vip, timeout=2.0)
    assert res_vip is True

    # Free user remains waiting
    assert len(qm._waiting_queue) == 1
    assert qm._waiting_queue[0].user_id == 302

    # Cleanup
    qm.release_download_slot(user_id=303)
    await asyncio.wait_for(task_free, timeout=2.0)


@pytest.mark.asyncio
async def test_admin_queue_bypass():
    """Verify admins bypass the queue completely even if all slots are full."""
    qm = DownloadQueueManager(max_downloads=1, max_uploads=1)

    msg1 = MagicMock(spec=Message)
    msg1.edit_text = AsyncMock()
    msg_admin = MagicMock(spec=Message)
    msg_admin.edit_text = AsyncMock()

    # Fill slots
    await qm.acquire_download_slot(user_id=401, media_title="Item 1", status_msg=msg1)

    # Admin request
    with patch.object(vip_manager, "get_user_priority", return_value=PRIORITY_ADMIN):
        res_admin = await qm.acquire_download_slot(user_id=999999999, media_title="Admin Item", status_msg=msg_admin)
        assert res_admin is True
        # Did not enter queue
        assert len(qm._waiting_queue) == 0


@pytest.mark.asyncio
async def test_queue_cancellation():
    """Verify user can cancel while in queue."""
    qm = DownloadQueueManager(max_downloads=1, max_uploads=1)

    msg1 = MagicMock(spec=Message)
    msg1.edit_text = AsyncMock()
    msg2 = MagicMock(spec=Message)
    msg2.edit_text = AsyncMock()

    await qm.acquire_download_slot(user_id=201, media_title="Item 1", status_msg=msg1)

    task2 = asyncio.create_task(
        qm.acquire_download_slot(user_id=202, media_title="Item 2", status_msg=msg2)
    )
    await asyncio.sleep(0.1)
    assert len(qm._waiting_queue) == 1

    # Cancel user 202
    cancelled = qm.cancel_queue_item(user_id=202)
    assert cancelled is True
    res2 = await asyncio.wait_for(task2, timeout=2.0)
    assert res2 is False


@pytest.mark.asyncio
async def test_link_spam_guard_cooldown_and_warnings():
    """Verify 6-second cooldown, Ladle warning message, and 10-min ban on 3 violations."""
    guard = LinkSpamGuard(cooldown_seconds=4.0)
    user_id = 77771

    # 1st request -> allowed
    blocked, _ = await guard.check_link_spam(user_id)
    assert blocked is False

    # Immediate 2nd request -> blocked with warning
    blocked, warn_msg = await guard.check_link_spam(user_id)
    assert blocked is True
    assert "Ladle, don't spam here!" in warn_msg

    # Immediate 3rd request -> blocked with warning
    blocked, warn_msg2 = await guard.check_link_spam(user_id)
    assert blocked is True
    assert "Ladle, don't spam here!" in warn_msg2

    # 4th fast request -> 3rd violation within 30s triggers 10-minute ban
    blocked, ban_msg = await guard.check_link_spam(user_id)
    assert blocked is True
    assert "Temporary Ban Activated" in ban_msg

    # Subsequent request is immediately rejected due to active ban
    blocked, active_ban_msg = await guard.check_link_spam(user_id)
    assert blocked is True
    assert "Access Temporarily Restricted" in active_ban_msg


@pytest.mark.asyncio
async def test_vip_exempt_from_spam_guard():
    """Verify VIP paid subscribers and admins are exempt from the link spam guard."""
    guard = LinkSpamGuard(cooldown_seconds=4.0)
    vip_user = 88881

    with patch.object(vip_manager, "is_vip", return_value=True):
        # 1st request -> allowed
        blocked1, _ = await guard.check_link_spam(vip_user)
        assert blocked1 is False

        # Immediate 2nd request -> still allowed for VIP!
        blocked2, _ = await guard.check_link_spam(vip_user)
        assert blocked2 is False


@pytest.mark.asyncio
async def test_selection_session_10min_expiry():
    """Verify selection sessions expire after 10 minutes and notify user."""
    from utils.selection_session import SelectionSessionManager
    import time

    mgr = SelectionSessionManager(ttl_seconds=600.0)
    bot = MagicMock()
    bot.edit_message_text = AsyncMock()

    # Register session
    await mgr.register_session(bot, user_id=9999, chat_id=123, message_id=456, video_id="vid_test")
    assert mgr.check_validity(user_id=9999, video_id="vid_test")[0] is True

    # Simulate 11 minutes later (660 seconds)
    mgr._active_sessions[9999]["created_at"] = time.time() - 660.0

    # User clicks after 11 minutes -> expired!
    valid, reason = mgr.check_validity(user_id=9999, video_id="vid_test")
    assert valid is False
    assert "10 minutes" in reason
    assert "/start" in reason
