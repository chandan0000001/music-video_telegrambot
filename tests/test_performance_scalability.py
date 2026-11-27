"""
Async concurrency, HTTP pooling, caching, database, disk cleanup, queue slot,
and performance benchmark tests.
"""
import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from database import Database
from services.instagram_service import InstagramService
from services.media_service import MediaService
from services.twitter_service import TwitterService
from utils.disk_cleaner import (
    check_disk_space_available_async,
    cleanup_dir_async,
    get_disk_usage_stats_async,
    get_temp_dir_size_bytes_async,
    purge_stale_temp_files,
)
from utils.http_client import (
    download_carousel_batch,
    http_client,
    stream_url_to_file,
)
from utils.metrics import metrics
from utils.queue_manager import DownloadQueueManager
from utils.vip_manager import PRIORITY_FREE, PRIORITY_PAID, vip_manager


@pytest.mark.asyncio
async def test_carousel_concurrency_bounded_and_http_reused(tmp_path):
    """Carousel fan-out must respect semaphore concurrency and max item count while reusing one ClientSession."""
    await http_client.close_all()
    metrics.reset()

    state = {"active": 0, "max_seen": 0, "gets": 0, "ports": set()}
    payload = b"X" * 2048

    async def handle_media(request: web.Request) -> web.StreamResponse:
        peer = request.transport.get_extra_info("peername") if request.transport else None
        if peer:
            state["ports"].add(peer[1])
        if request.method == "HEAD":
            return web.Response(headers={"Content-Length": str(len(payload))})

        state["gets"] += 1
        state["active"] += 1
        state["max_seen"] = max(state["max_seen"], state["active"])
        try:
            await asyncio.sleep(0.05)
            return web.Response(body=payload, headers={"Content-Length": str(len(payload))})
        finally:
            state["active"] -= 1

    app = web.Application()
    app.router.add_route("*", "/media/{idx}", handle_media)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        items = [{"type": "photo", "url": f"http://127.0.0.1:{port}/media/{i}"} for i in range(10)]
        downloaded = await download_carousel_batch(
            items,
            tmp_path,
            max_items=7,
            max_total_bytes=1024 * 1024,
            concurrency=3,
            timeout_seconds=5.0,
        )
        assert len(downloaded) == 7
        assert state["gets"] == 7
        assert 1 <= state["max_seen"] <= 3
        assert len(state["ports"]) < 7  # keep-alive pooled TCP connections reused
        for entry in downloaded:
            assert entry["file_path"].exists()
            assert entry["file_path"].stat().st_size == len(payload)
    finally:
        await http_client.close_all()
        await runner.cleanup()

    assert len(http_client._sessions) == 0


@pytest.mark.asyncio
async def test_carousel_aggregate_size_limit(tmp_path):
    """Carousel download must enforce max_total_bytes across streamed items."""
    await http_client.close_all()
    chunk_payload = b"A" * 4096

    async def handle_chunked(request: web.Request) -> web.StreamResponse:
        # Omit Content-Length on purpose to test runtime streaming budget tracking too
        if request.method == "HEAD":
            return web.Response(status=200)
        resp = web.StreamResponse(status=200)
        await resp.prepare(request)
        await resp.write(chunk_payload)
        await asyncio.sleep(0.01)
        await resp.write(chunk_payload)  # 8192 bytes total per item
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_route("*", "/big/{idx}", handle_chunked)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        items = [{"type": "photo", "url": f"http://127.0.0.1:{port}/big/{i}"} for i in range(5)]
        downloaded = await download_carousel_batch(
            items,
            tmp_path,
            max_items=5,
            max_total_bytes=18000,  # allows at most 2 items of 8192B cleanly
            concurrency=1,
            timeout_seconds=5.0,
        )
        total_written = sum(item["file_path"].stat().st_size for item in downloaded)
        assert len(downloaded) <= 2
        assert total_written <= 18000
    finally:
        await http_client.close_all()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_http_timeout_and_session_cleanup(tmp_path):
    """HTTP timeout aborts cleanly and close_all closes pooled sessions without leaks."""
    await http_client.close_all()

    async def slow_handler(request: web.Request) -> web.Response:
        await asyncio.sleep(0.4)
        return web.Response(body=b"late")

    app = web.Application()
    app.router.add_get("/slow", slow_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        dest = tmp_path / "slow.bin"
        ok, written = await stream_url_to_file(
            f"http://127.0.0.1:{port}/slow",
            dest,
            timeout_seconds=0.05,
        )
        assert ok is False
        assert written == 0
        assert len(http_client._sessions) == 1
    finally:
        await http_client.close_all()
        await runner.cleanup()

    assert len(http_client._sessions) == 0


@pytest.mark.asyncio
async def test_nested_temp_cleanup_and_async_disk_helpers(tmp_path):
    """Abandoned nested instagram/twitter job directories are purged while active ones remain."""
    ig_bucket = tmp_path / "instagram"
    tw_bucket = tmp_path / "twitter"
    ig_bucket.mkdir(parents=True)
    tw_bucket.mkdir(parents=True)

    stale_job = ig_bucket / "job_old123"
    stale_job.mkdir()
    stale_file = stale_job / "media_1.mp4"
    stale_file.write_bytes(b"staledata")

    fresh_job = tw_bucket / "job_fresh456"
    fresh_job.mkdir()
    fresh_file = fresh_job / "media_1.jpg"
    fresh_file.write_bytes(b"fresh")

    stale_ts = time.time() - 7200
    os.utime(stale_file, (stale_ts, stale_ts))
    os.utime(stale_job, (stale_ts, stale_ts))

    pruned = purge_stale_temp_files(temp_dir=tmp_path, max_age=3600)
    assert pruned >= 1
    assert not stale_job.exists()
    assert fresh_job.exists()
    assert fresh_file.exists()

    size_bytes = await get_temp_dir_size_bytes_async(tmp_path)
    assert size_bytes == len(b"fresh")

    stats = await get_disk_usage_stats_async(tmp_path)
    assert stats["total_gb"] > 0
    has_space, free_mb = await check_disk_space_available_async(min_free_mb=1, target_dir=tmp_path)
    assert isinstance(has_space, bool)
    assert free_mb >= 0

    await cleanup_dir_async(fresh_job)
    assert not fresh_job.exists()


@pytest.mark.asyncio
async def test_queue_priority_cancellation_slot_release_and_edit_throttle():
    """Cancelled waiting items must not corrupt slot counters, VIP outranks standard FIFO, and edits are throttled."""
    qm = DownloadQueueManager(max_downloads=1, max_uploads=1)

    msg1 = MagicMock()
    msg1.edit_text = AsyncMock()
    msg2 = MagicMock()
    msg2.edit_text = AsyncMock()
    msg3 = MagicMock()
    msg3.edit_text = AsyncMock()

    async def _fake_priority(uid: int) -> int:
        return PRIORITY_PAID if uid == 300 else PRIORITY_FREE

    with patch.object(vip_manager, "get_user_priority", side_effect=_fake_priority):
        # Fill the single active download slot with user 100
        assert await qm.acquire_download_slot(user_id=100, media_title="Active Job", status_msg=msg1)
        assert qm._active_downloads == 1

        # Start standard waiter (200) and VIP waiter (300)
        t_std = asyncio.create_task(
            qm.acquire_download_slot(user_id=200, media_title="Std Waiter", status_msg=msg2)
        )
        t_vip = asyncio.create_task(
            qm.acquire_download_slot(user_id=300, media_title="VIP Waiter", status_msg=msg3)
        )
        await asyncio.sleep(0.03)
        assert len(qm._waiting_queue) == 2

        # Edit throttling guard: immediate repeated render should not trigger multiple Telegram edits
        std_item = next(q for q in qm._waiting_queue if q.user_id == 200)
        first_edits = msg2.edit_text.await_count
        await qm._render_queue_message(std_item, 1, 1)
        assert msg2.edit_text.await_count == first_edits  # throttled inside 2.0s window

        # Cancel standard waiting user BEFORE promotion -> slot counter must remain 1 (never 0 or negative!)
        assert qm.cancel_queue_item(200) is True
        assert await t_std is False
        assert qm._active_downloads == 1

        # Release user 100 -> VIP waiter should be promoted cleanly
        await qm._release_download_async(100)
        assert await t_vip is True
        assert qm._active_downloads == 1

        await qm._release_download_async(300)
        assert qm._active_downloads == 0

        # Test Condition-based global upload slot handoff
        await qm.acquire_upload_slot(user_id=1)
        up_waiter = asyncio.create_task(qm.acquire_upload_slot(user_id=2))
        await asyncio.sleep(0.02)
        assert not up_waiter.done()
        await qm._release_upload_async()
        await asyncio.wait_for(up_waiter, timeout=1.0)
        await qm._release_upload_async()
        assert qm._active_uploads == 0


@pytest.mark.asyncio
async def test_database_pool_concurrent_writes_indexes_pagination_and_stats_cache(tmp_path):
    """Pooled SQLite handles concurrent writes safely, uses expression index, paginates, and caches dashboard stats."""
    metrics.reset()
    test_db = Database(db_path=tmp_path / "perf.db", pool_size=4)
    await test_db.init_db()
    await test_db.connect()

    try:
        async def _worker(i: int) -> None:
            uid = 10000 + i
            await test_db.add_or_update_user(uid, f"user_{i}", f"User {i}")
            await test_db.record_search(uid, f"Query Song {i}")
            await test_db.record_download(
                telegram_id=uid,
                media_title=f"Track {i}",
                source=f"https://example.com/{i}",
                media_type="audio",
                selected_quality="320k",
                selected_format="mp3",
                status="completed",
            )

        await asyncio.gather(*[_worker(i) for i in range(20)])

        page1 = await test_db.get_recent_searches(limit=5, offset=0)
        page2 = await test_db.get_recent_searches(limit=5, offset=5)
        assert len(page1) == 5
        assert len(page2) == 5
        assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})

        dl_page2 = await test_db.get_recent_downloads(limit=4, offset=4)
        assert len(dl_page2) == 4

        # Verify expression index idx_search_history_lower_query is used by SQLite query planner
        async with test_db._get_connection() as conn:
            async with conn.execute(
                "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM search_history WHERE LOWER(search_query) = LOWER(?)",
                ("Query Song 1",),
            ) as cursor:
                plan_rows = [dict(r)["detail"] for r in await cursor.fetchall()]
        assert any("idx_search_history_lower_query" in detail for detail in plan_rows)

        # Stats cache benchmark: cold query vs warm cached hit + invalidation on write
        t0 = time.perf_counter()
        stats_cold = await test_db.get_statistics()
        cold_ms = (time.perf_counter() - t0) * 1000.0
        assert stats_cold["total_users"] == 20
        assert stats_cold["successful_downloads"] == 20
        assert cold_ms < 250.0

        t1 = time.perf_counter()
        stats_warm = await test_db.get_statistics()
        warm_ms = (time.perf_counter() - t1) * 1000.0
        assert stats_warm == stats_cold
        assert warm_ms < 20.0

        # Mutation invalidates cached statistics
        await test_db.record_search(10001, "Brand New Query")
        stats_refreshed = await test_db.get_statistics()
        assert stats_refreshed["total_searches"] == 21
    finally:
        await test_db.close()


@pytest.mark.asyncio
async def test_bounded_cache_ttl_and_eviction(tmp_path):
    """Metadata caches must enforce max entry count and expire entries after TTL."""
    ig = InstagramService(temp_base=tmp_path / "ig")
    ig._cache_max_entries = 3
    ig._cache_ttl_seconds = 0.05

    ig._set_cached_media("p:1", [{"type": "photo", "url": "https://example.com/1.jpg"}])
    ig._set_cached_media("p:2", [{"type": "photo", "url": "https://example.com/2.jpg"}])
    ig._set_cached_media("p:3", [{"type": "photo", "url": "https://example.com/3.jpg"}])
    ig._set_cached_media("p:4", [{"type": "photo", "url": "https://example.com/4.jpg"}])

    assert len(ig._media_cache) == 3
    assert ig._get_cached_media("p:1") is None  # oldest evicted
    assert ig._get_cached_media("p:4") is not None

    await asyncio.sleep(0.06)
    assert ig._get_cached_media("p:4") is None  # TTL expired

    tw = TwitterService(temp_base=tmp_path / "tw")
    assert tw.max_items_limit == 15
    tw._cache_max_entries = 2
    tw._set_cached_tweet("a:1", [{"type": "photo", "url": "https://x.com/1"}])
    tw._set_cached_tweet("a:2", [{"type": "photo", "url": "https://x.com/2"}])
    tw._set_cached_tweet("a:3", [{"type": "photo", "url": "https://x.com/3"}])
    assert len(tw._tweet_cache) == 2

    ms = MediaService()
    ms._raw_info_cache.clear()
    ms._INFO_CACHE_MAX_ENTRIES = 2
    ms._INFO_CACHE_TTL_SECONDS = 0.05
    ms._set_cached_raw_info("v1", {"id": "v1"})
    ms._set_cached_raw_info("v2", {"id": "v2"})
    ms._set_cached_raw_info("v3", {"id": "v3"})
    assert len(ms._raw_info_cache) == 2
    await asyncio.sleep(0.06)
    assert ms._get_cached_raw_info("v3") is None


def test_metrics_registry_counters_and_timings():
    """Metrics registry accurately aggregates counters and latency histograms."""
    metrics.reset()
    metrics.incr("downloads", 2)
    metrics.incr("http_requests", 5)
    with metrics.track_time("ffmpeg"):
        time.sleep(0.005)
    metrics.record_timing("db", 12.5)
    metrics.record_timing("db", 27.5)

    snap = metrics.snapshot()
    assert snap["counters"]["downloads"] == 2
    assert snap["counters"]["http_requests"] == 5
    assert snap["timings"]["db"]["count"] == 2
    assert snap["timings"]["db"]["avg_ms"] == pytest.approx(20.0)
    assert snap["timings"]["db"]["max_ms"] == pytest.approx(27.5)
    assert snap["timings"]["ffmpeg"]["avg_ms"] >= 1.0
