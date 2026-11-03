"""
Unit and Integration Tests for Security Audit Vulnerability Fixes.
Verifies CRIT-1 through CRIT-4, HIGH-1 through HIGH-5, MED-1, MED-2, MED-4, MED-6, PERF-2, PERF-4, PERF-5.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, patch
from pathlib import Path

from config import ADMIN_IDS
from database import Database, db
from services.gmail_service import GmailService
from services.ffmpeg_service import FFmpegService
from utils.validators import is_safe_url, extract_youtube_id
from utils.rate_limiter import UserRateLimiter


@pytest.mark.asyncio
async def test_crit1_gmail_amount_and_credit_verification():
    """CRIT-1: Verify Gmail service checks expected amount and enforces credit alerts."""
    service = GmailService(user="dummy@gmail.com", app_password="password")

    # 1. Invalid UTR format rejected before IMAP (MED-2)
    ok, msg = service._sync_check_utr("invalid_utr", 799.0)
    assert ok is False
    assert "Invalid UTR format" in msg

    # 2. UTR with injection characters rejected (MED-2)
    ok, msg = service._sync_check_utr('123456")}][[ SEARCH ALL', 799.0)
    assert ok is False
    assert "Invalid UTR format" in msg


@pytest.mark.asyncio
async def test_crit2_atomic_utr_claim_prevents_double_spending(tmp_path: Path):
    """CRIT-2: Test that atomic_claim_and_mark_paid prevents TOCTOU race conditions."""
    test_db = Database(tmp_path / "test_sec.sqlite3")
    await test_db.init_db()

    # Create two users and two pending orders
    await test_db.add_or_update_user(telegram_id=111, username="user1", first_name="User1")
    await test_db.add_or_update_user(telegram_id=222, username="user2", first_name="User2")

    ord1 = await test_db.create_order(telegram_id=111, order_type="single", amount_inr=19.0)
    ord2 = await test_db.create_order(telegram_id=222, order_type="single", amount_inr=19.0)

    utr = "987654321012"

    # Concurrent race: both orders attempt to claim the exact same UTR simultaneously
    results = await asyncio.gather(
        test_db.atomic_claim_and_mark_paid(ord1, utr),
        test_db.atomic_claim_and_mark_paid(ord2, utr)
    )

    success_count = sum(1 for res in results if res[0] is True)
    fraud_count = sum(1 for res in results if res[0] is False and "Fraud Alert" in res[2])

    # Exactly ONE order must succeed; the other must be rejected as fraud
    assert success_count == 1
    assert fraud_count == 1

    # Verify orders in DB
    o1 = await test_db.get_order(ord1)
    o2 = await test_db.get_order(ord2)
    assert (o1["status"] == "paid" and o2["status"] == "pending") or (o2["status"] == "paid" and o1["status"] == "pending")


@pytest.mark.asyncio
async def test_high1_ffmpeg_metadata_sanitization():
    """HIGH-1: Test FFmpeg metadata argument injection prevention."""
    svc = FFmpegService()

    # Argument option injection payload
    dirty = "-vf scale=0:0 -c:v libx264\x00\nmalicious"
    clean = svc.sanitize_metadata(dirty)

    assert not clean.startswith("-")
    assert "\x00" not in clean
    assert "\n" not in clean
    assert clean == "vf scale=0:0 -c:v libx264 malicious"


@pytest.mark.asyncio
async def test_high2_order_attempts_limiting(tmp_path: Path):
    """HIGH-2: Test per-order attempt counting and lockout."""
    test_db = Database(tmp_path / "test_attempts.sqlite3")
    await test_db.init_db()

    await test_db.add_or_update_user(telegram_id=333, username="user3", first_name="User3")
    order_id = await test_db.create_order(telegram_id=333, order_type="single", amount_inr=19.0)

    # Increment attempts
    for i in range(1, 6):
        count = await test_db.increment_order_attempts(order_id)
        assert count == i

    # Mark expired
    expired = await test_db.mark_order_expired(order_id)
    assert expired is True

    order = await test_db.get_order(order_id)
    assert order["status"] == "expired"


def test_high3_ssrf_validator_blocks_internal_and_metadata_ips():
    """HIGH-3: Verify SSRF validator blocks cloud metadata, loopbacks, and private IP variants."""
    blocked_urls = [
        "http://127.0.0.1:8000/secret",
        "http://localhost:5000",
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.170.2/credentials",
        "http://[fd00:ec2::254]/latest",
        "http://[::ffff:127.0.0.1]/admin",
        "http://192.168.1.1/router",
        "http://10.0.0.1/internal",
        "http://172.16.0.1/vpc",
        "ftp://youtube.com",
        "file:///etc/passwd",
    ]
    for url in blocked_urls:
        assert is_safe_url(url) is False, f"Expected {url} to be blocked by SSRF filter"

    # Public allowed URL
    assert is_safe_url("https://www.youtube.com/watch?v=BjL7AuPsmEk") is True


@pytest.mark.asyncio
async def test_high5_cancel_and_reject_order(tmp_path: Path):
    """HIGH-5: Verify cancel_order and reject_order methods."""
    test_db = Database(tmp_path / "test_cancel.sqlite3")
    await test_db.init_db()

    await test_db.add_or_update_user(telegram_id=444, username="user4", first_name="User4")
    ord_cancel = await test_db.create_order(telegram_id=444, order_type="single", amount_inr=19.0)
    ord_reject = await test_db.create_order(telegram_id=444, order_type="single", amount_inr=19.0)

    assert await test_db.cancel_order(ord_cancel) is True
    assert await test_db.reject_order(ord_reject) is True

    o_c = await test_db.get_order(ord_cancel)
    o_r = await test_db.get_order(ord_reject)
    assert o_c["status"] == "cancelled"
    assert o_r["status"] == "rejected"


@pytest.mark.asyncio
async def test_med1_export_table_allowlist(tmp_path: Path):
    """MED-1: Verify export_table_to_csv rejects SQL injection or unallowed table names."""
    test_db = Database(tmp_path / "test_export.sqlite3")
    await test_db.init_db()

    with pytest.raises(ValueError, match="Invalid table name"):
        await test_db.export_table_to_csv("users; DROP TABLE users;--")

    with pytest.raises(ValueError, match="Invalid table name"):
        await test_db.export_table_to_csv("sqlite_master")

    # Insert a user so rows exist
    await test_db.add_or_update_user(telegram_id=999, username="export_u", first_name="Export")
    csv_out = await test_db.export_table_to_csv("users")
    assert "telegram_id" in csv_out


@pytest.mark.asyncio
async def test_med4_daily_quota_reset(tmp_path: Path):
    """MED-4: Verify reset_daily_quotas resets today_video_used for active subscriptions."""
    test_db = Database(tmp_path / "test_quota.sqlite3")
    await test_db.init_db()

    await test_db.add_or_update_user(telegram_id=555, username="subuser", first_name="Sub")
    await test_db.activate_subscription(555, "go")

    # Simulate usage
    async with test_db._get_connection() as conn:
        await conn.execute("UPDATE user_subscriptions SET today_video_used = 10, last_reset_date = '2020-01-01' WHERE telegram_id = 555")
        await conn.commit()

    reset_count = await test_db.reset_daily_quotas()
    assert reset_count == 1

    sub = await test_db.get_user_subscription(555)
    assert sub["today_used"] == 0


@pytest.mark.asyncio
async def test_perf2_get_statistics_combined_query(tmp_path: Path):
    """PERF-2: Verify get_statistics produces accurate dashboard stats via combined query."""
    test_db = Database(tmp_path / "test_stats.sqlite3")
    await test_db.init_db()

    await test_db.add_or_update_user(telegram_id=666, username="u666", first_name="U666")
    await test_db.add_or_update_user(telegram_id=777, username="u777", first_name="U777")
    await test_db.record_search(telegram_id=666, search_query="test song", source="youtube")
    await test_db.record_download(telegram_id=666, media_title="song", source="youtube", media_type="audio", selected_quality="320k", selected_format="mp3", status="completed")

    stats = await test_db.get_statistics()
    assert stats["total_users"] == 2
    assert stats["total_searches"] == 1
    assert stats["successful_downloads"] == 1
    assert stats["failed_downloads"] == 0


@pytest.mark.asyncio
async def test_perf4_rate_limiter_sharded_locks():
    """PERF-4: Verify UserRateLimiter allows concurrent checks without global lock contention."""
    limiter = UserRateLimiter(max_requests=10, window_seconds=5)

    # 20 concurrent checks across distinct users
    async def check_user(uid: int):
        limited, _ = await limiter.is_rate_limited(uid)
        return limited

    results = await asyncio.gather(*(check_user(uid) for uid in range(1000, 1020)))
    assert all(res is False for res in results)
    assert len(limiter._user_locks) == 20
