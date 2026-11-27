"""Regression tests for reliability, proxy failover, quota refunds, and FSM persistence."""
import asyncio
import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import CallbackQuery, Document, Message, User

from database import Database
from handlers.admin import handle_admin_config_files
from handlers.video import cb_start_video_download
from services.media_service import MediaService
from services.proxy_manager import YouTubeProxyManager, mask_proxy_url
from utils.fsm_storage import SQLiteStorage
from utils.task_registry import TaskRegistry


@pytest.mark.asyncio
async def test_quota_refund_on_failed_vip_video_download(tmp_path: Path):
    """Failed VIP 1080p video download must refund the deducted daily video quota."""
    test_db = Database(tmp_path / "quota.sqlite3")
    await test_db.init_db()
    uid = 515151
    await test_db.add_or_update_user(uid, "vipuser", "VIP")
    await test_db.activate_subscription(uid, "go")

    sub_before = await test_db.get_user_subscription(uid)
    assert sub_before["today_remaining"] == 20

    msg = MagicMock(spec=Message)
    msg.chat = MagicMock(id=uid)
    msg.message_id = 10
    msg.text = "Title: Great Song\nDuration: 03:00"
    msg.date = None
    msg.edit_text = AsyncMock()
    msg.bot = MagicMock()

    cb = MagicMock(spec=CallbackQuery)
    cb.data = "vdl:dQw4w9WgXcQ:1080p"
    cb.from_user = MagicMock(spec=User, id=uid)
    cb.message = msg
    cb.bot = msg.bot
    cb.answer = AsyncMock()

    with (
        patch("handlers.video.db", test_db),
        patch("handlers.video.send_download_animation", AsyncMock(return_value=None)),
        patch("handlers.video.vanish_download_animation", AsyncMock()),
        patch("handlers.video.queue_manager.can_user_start_job", AsyncMock(return_value=(True, ""))),
        patch("handlers.video.queue_manager.acquire_download_slot", AsyncMock(return_value=True)),
        patch("handlers.video.queue_manager.release_download_slot", MagicMock()),
        patch("utils.disk_cleaner.check_disk_space_available", return_value=(True, 10000.0)),
        patch("handlers.video.media_service.process_video", AsyncMock(return_value=(False, None, "Temporary error", None))),
        patch("handlers.video.telegram_service.update_status_with_keyboard", AsyncMock()),
    ):
        await cb_start_video_download(cb)

    sub_after = await test_db.get_user_subscription(uid)
    assert sub_after["today_used"] == 0
    assert sub_after["today_remaining"] == 20


@pytest.mark.asyncio
async def test_sqlite_fsm_storage_persists_across_restarts(tmp_path: Path):
    """SQLiteStorage must retain FSM state and data across Database instances."""
    db_path = tmp_path / "fsm.sqlite3"
    db1 = Database(db_path)
    await db1.init_db()
    storage1 = SQLiteStorage(db1)

    key = StorageKey(bot_id=1, chat_id=200, user_id=200)
    await storage1.set_state(key, "UserStates:waiting_for_utr")
    await storage1.set_data(key, {"order_id": "ORD_123", "amount": 100})

    # Simulate restart with fresh Database + SQLiteStorage
    db2 = Database(db_path)
    storage2 = SQLiteStorage(db2)
    assert await storage2.get_state(key) == "UserStates:waiting_for_utr"
    assert await storage2.get_data(key) == {"order_id": "ORD_123", "amount": 100}


@pytest.mark.asyncio
async def test_task_registry_supports_multiple_concurrent_tasks():
    """Unregistering one job for a user must not clobber another running job."""
    reg = TaskRegistry()

    async def waiter():
        await asyncio.sleep(5)

    t1 = asyncio.create_task(waiter())
    t2 = asyncio.create_task(waiter())
    await asyncio.sleep(0.01)

    reg.register(42, t1)
    reg.register(42, t2)
    reg.unregister(42, task=t1)
    assert reg.is_active(42) is True

    assert reg.cancel(42) is True
    await asyncio.sleep(0.02)
    assert t2.cancelled()
    t1.cancel()
    try:
        await t1
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_media_service_caches_metadata_within_ttl():
    """Repeated get_media_info calls for the same video_id reuse cached raw info."""
    svc = MediaService()
    sample_raw = {
        "title": "Cached Song",
        "uploader": "Artist",
        "duration": 120,
        "formats": [{"vcodec": "avc1", "height": 720, "width": 1280}],
        "thumbnails": [{"url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"}],
    }
    with patch("services.media_service.yt_dlp.YoutubeDL") as ydl_cls:
        ydl_cls.return_value.__enter__.return_value.extract_info.return_value = sample_raw
        ok1, info1, _ = await svc.get_media_info("dQw4w9WgXcQ")
        ok2, info2, _ = await svc.get_media_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert ok1 and ok2
    assert info1["title"] == "Cached Song"
    assert info2["title"] == "Cached Song"
    ydl_cls.return_value.__enter__.return_value.extract_info.assert_called_once()


def test_proxy_manager_google_ip_flag_detection_and_masking(tmp_path: Path):
    """Detects Google IP blocks, masks credentials, and hot-reloads proxies.txt."""
    mgr = YouTubeProxyManager([])
    assert mgr.is_retryable_error("We're sorry... automated queries") is True
    assert mgr.is_retryable_error("unusual traffic from your computer network") is True
    assert mgr.is_retryable_error("playability status: LOGIN_REQUIRED") is True
    assert mgr.is_retryable_error("The provided YouTube account cookies are no longer valid") is True
    assert mask_proxy_url("http://user123:secretpass@1.2.3.4:8080") == "http://***@1.2.3.4:8080"

    proxies_file = tmp_path / "proxies.txt"
    proxies_file.write_text("http://u:p@10.0.0.1:8080\n# comment\nhttp://u:p@10.0.0.2:8080\n", encoding="utf-8")
    auto_mgr = YouTubeProxyManager()
    with patch.object(auto_mgr, "_candidate_proxy_files", return_value=[proxies_file]):
        loaded = auto_mgr.get_proxies()
    assert loaded == ["http://u:p@10.0.0.1:8080", "http://u:p@10.0.0.2:8080"]


@pytest.mark.asyncio
async def test_admin_document_accepts_chrome_cookie_export_name(tmp_path: Path):
    """Admin upload accepts www.youtube.com_cookies (1).txt exports directly."""
    cookie_dest = tmp_path / "cookies.txt"
    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock(spec=User, id=999)
    msg.document = MagicMock(spec=Document, file_name="www.youtube.com_cookies (1).txt", file_id="doc1")
    msg.bot = MagicMock()
    msg.bot.download = AsyncMock(return_value=io.BytesIO(b".youtube.com\tTRUE\t/\tTRUE\t1828103380\tSID\tabc\n"))
    msg.answer = AsyncMock()

    with (
        patch("handlers.admin.is_admin", return_value=True),
        patch("config.COOKIE_FILE", cookie_dest),
    ):
        await handle_admin_config_files(msg)

    assert cookie_dest.exists()
    assert b".youtube.com" in cookie_dest.read_bytes()
    msg.answer.assert_awaited_once()
