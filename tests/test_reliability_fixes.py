"""
Regression tests for P0/P1 reliability, concurrency, privacy, and payment safety fixes.
"""
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database import Database
from handlers.history import cmd_history
from handlers.payment import fulfill_paid_order
from utils.disk_cleaner import (
    cleanup_dir_async,
    purge_stale_temp_files,
    register_active_job_dir,
)
from utils.queue_manager import DownloadQueueManager
from utils.vip_manager import PRIORITY_ADMIN, PRIORITY_FREE, vip_manager


@pytest.mark.asyncio
async def test_mark_order_paid_rejects_expired_and_duplicate_utr_no_fallback(tmp_path):
    """P0: mark_order_paid must reject expired orders and conflicting UTRs instead of overriding status."""
    test_db = Database(db_path=tmp_path / "pay.db")
    await test_db.init_db()
    await test_db.add_or_update_user(101, "buyer", "Buyer")

    # 1. Expired order cannot be marked paid (with or without UTR)
    exp_id = await test_db.create_order(telegram_id=101, order_type="sub_go", amount_inr=35.0)
    past = (datetime.now(timezone.utc) - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
    async with test_db._get_connection() as conn:
        await conn.execute("UPDATE orders SET expires_at = ? WHERE order_id = ?", (past, exp_id))
        await conn.commit()

    assert await test_db.mark_order_paid(exp_id, "123456789012") is None
    assert await test_db.mark_order_paid(exp_id) is None
    assert await test_db.get_user_subscription(101) is None

    # 2. Duplicate UTR on a second order must return None rather than falling back to legacy update
    ord1 = await test_db.create_order(telegram_id=101, order_type="single_1080", amount_inr=27.0)
    ord2 = await test_db.create_order(telegram_id=101, order_type="single_1080", amount_inr=27.0)
    shared_utr = "999888777666"
    assert (await test_db.mark_order_paid(ord1, shared_utr))["status"] == "paid"
    assert await test_db.mark_order_paid(ord2, shared_utr) is None
    ord2_row = await test_db.get_order(ord2)
    assert ord2_row["status"] == "pending"


@pytest.mark.asyncio
async def test_claim_order_fulfillment_and_fulfill_once(tmp_path):
    """P0: Concurrent fulfill_paid_order calls only deliver once per paid order."""
    test_db = Database(db_path=tmp_path / "fulfill.db")
    await test_db.init_db()
    await test_db.add_or_update_user(202, "vip", "VIP")
    order_id = await test_db.create_order(
        telegram_id=202,
        order_type="single_1080",
        amount_inr=27.0,
        media_id="vid_xyz",
        quality="1080p",
    )
    paid = await test_db.mark_order_paid(order_id, "111222333444")
    assert paid["status"] == "paid"

    msg = MagicMock()
    msg.answer = AsyncMock()
    pipeline_mock = AsyncMock()

    with patch("handlers.payment.db", test_db), patch(
        "handlers.video.execute_video_download_pipeline", pipeline_mock
    ):
        await asyncio.gather(
            fulfill_paid_order(msg, dict(paid)),
            fulfill_paid_order(msg, dict(paid)),
        )

    assert pipeline_mock.await_count == 1
    fresh = await test_db.get_order(order_id)
    assert fresh["fulfilled_at"] is not None


@pytest.mark.asyncio
async def test_admin_upload_release_does_not_corrupt_slot_counter():
    """P1: Releasing after an Admin upload bypass must not decrement another user's active upload count."""
    qm = DownloadQueueManager(max_downloads=2, max_uploads=1)

    async def _fake_prio(uid: int) -> int:
        return PRIORITY_ADMIN if uid == 999 else PRIORITY_FREE

    with patch("config.ADMIN_IDS", [999]), patch.object(
        vip_manager, "get_user_priority", side_effect=_fake_prio
    ):
        # Normal user acquires the single counted upload slot
        await qm.acquire_upload_slot(100)
        assert qm._active_uploads == 1

        # Second normal user blocks waiting for the slot
        waiter = asyncio.create_task(qm.acquire_upload_slot(101))
        await asyncio.sleep(0.02)
        assert not waiter.done()

        # Admin uploads (bypassed) and releases: must NOT touch _active_uploads or wake user 101!
        await qm.acquire_upload_slot(999)
        await qm._release_upload_async(999)
        await asyncio.sleep(0.02)
        assert qm._active_uploads == 1
        assert not waiter.done()

        # Releasing user 100 wakes user 101 cleanly
        await qm._release_upload_async(100)
        await asyncio.wait_for(waiter, timeout=1.0)
        assert qm._active_uploads == 1
        await qm._release_upload_async(101)
        assert qm._active_uploads == 0


@pytest.mark.asyncio
async def test_concurrent_last_quota_unit_only_claimed_once(tmp_path):
    """P1: Two concurrent video requests for the final daily quota unit must allow exactly 1 claim."""
    test_db = Database(db_path=tmp_path / "quota.db")
    await test_db.init_db()
    await test_db.add_or_update_user(303, "subuser", "Sub")
    await test_db.activate_subscription(303, "go", custom_days=7)  # 20/day

    # Burn 19 units leaving exactly 1 remaining
    async with test_db._get_connection() as conn:
        await conn.execute(
            "UPDATE user_subscriptions SET today_video_used = 19 WHERE telegram_id = 303"
        )
        await conn.commit()

    results = await asyncio.gather(
        test_db.use_subscription_video_quota(303),
        test_db.use_subscription_video_quota(303),
        test_db.use_subscription_video_quota(303),
    )
    assert results.count(True) == 1
    assert results.count(False) == 2

    sub = await test_db.get_user_subscription(303)
    assert sub["today_used"] == 20
    assert sub["today_remaining"] == 0


@pytest.mark.asyncio
async def test_history_in_group_routes_to_dm_only():
    """P1: /history inside a group chat sends history via private DM instead of posting publicly in group."""
    msg = MagicMock()
    msg.from_user = MagicMock(id=505)
    msg.chat = MagicMock(id=-100999, type="supergroup")
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))

    with patch("handlers.history.render_user_history", AsyncMock(return_value="<b>My Secret</b>")), patch(
        "utils.selection_session.selection_session_manager.register_session", AsyncMock()
    ):
        await cmd_history(msg)

    msg.bot.send_message.assert_awaited_once()
    dm_kwargs = msg.bot.send_message.await_args.kwargs
    assert dm_kwargs["chat_id"] == 505
    assert "My Secret" in dm_kwargs["text"]
    msg.answer.assert_awaited_once()
    assert "My Secret" not in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_low_disk_cleaner_skips_active_job_directory(tmp_path):
    """P1: Active job directories must not be pruned by low-disk sweep even if older than max_age."""
    active_dir = tmp_path / "job_active_999"
    stale_dir = tmp_path / "job_abandoned_111"
    active_dir.mkdir()
    stale_dir.mkdir()

    active_file = active_dir / "ready_upload.mp4"
    stale_file = stale_dir / "orphan.mp4"
    active_file.write_bytes(b"keep-me")
    stale_file.write_bytes(b"delete-me")

    old_ts = time.time() - 300  # 5 minutes old (> 120s low-disk sweep)
    os.utime(active_file, (old_ts, old_ts))
    os.utime(active_dir, (old_ts, old_ts))
    os.utime(stale_file, (old_ts, old_ts))
    os.utime(stale_dir, (old_ts, old_ts))

    register_active_job_dir(active_dir)
    try:
        pruned = purge_stale_temp_files(temp_dir=tmp_path, max_age=120)
        assert pruned == 1
        assert active_dir.exists()
        assert active_file.exists()
        assert not stale_dir.exists()
    finally:
        await cleanup_dir_async(active_dir)

    assert not active_dir.exists()


@pytest.mark.asyncio
async def test_instagram_and_twitter_preserve_original_max_quality(tmp_path):
    """Instagram and X must select highest uploaded resolution/bitrate without 1080p downscaling."""
    from services.instagram_service import InstagramService
    from services.twitter_service import TwitterService

    ig = InstagramService(temp_base=tmp_path / "ig")
    payload = {
        "items": [
            {
                "video_versions": [
                    {"width": 720, "height": 1280, "url": "https://cdn.ig/720.mp4"},
                    {"width": 2160, "height": 3840, "url": "https://cdn.ig/4k.mp4"},
                    {"width": 1080, "height": 1920, "url": "https://cdn.ig/1080.mp4"},
                ]
            }
        ]
    }
    parsed_ig = ig._parse_instagram_json_payload(payload)
    assert parsed_ig[0]["url"] == "https://cdn.ig/4k.mp4"

    # Single video download must NOT invoke cap_video_to_1080p
    tracker = MagicMock()
    job_dir = tmp_path / "ig_job"
    job_dir.mkdir()

    async def _fake_dl(url, dest_path, tracker=None):
        dest_path.write_bytes(b"original-4k-bytes")
        return True

    with patch.object(ig, "download_file", side_effect=_fake_dl), patch(
        "services.ffmpeg_service.ffmpeg_service.cap_video_to_1080p", AsyncMock()
    ) as cap_mock:
        res = await ig.download_all_media(parsed_ig, job_dir, tracker=tracker)
    assert len(res) == 1
    assert cap_mock.await_count == 0

    # Twitter picks highest bitrate variant from fxtwitter and highest resolution from yt-dlp fallback
    tw = TwitterService(temp_base=tmp_path / "tw")
    ok, items, _ = tw._parse_fxtwitter_response({
        "code": 200,
        "tweet": {
            "media": {
                "videos": [
                    {
                        "type": "video",
                        "url": "https://video.twimg.com/720.mp4",
                        "variants": [
                            {"content_type": "video/mp4", "bitrate": 832000, "url": "https://video.twimg.com/720.mp4"},
                            {"content_type": "video/mp4", "bitrate": 10368000, "url": "https://video.twimg.com/4k.mp4"},
                        ],
                    }
                ]
            }
        },
    })
    assert ok is True
    assert items[0]["url"] == "https://video.twimg.com/4k.mp4"
