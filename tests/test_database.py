"""
Unit & Integration Tests for SQLite Database
Tests Scenarios:
1. New user registration.
2. Existing user update.
14. User search history persistence.
15. Bot restart persistence without losing records.
"""
import pytest
import aiosqlite
from pathlib import Path
from database import Database


@pytest.mark.asyncio
async def test_database_init_and_restart(tmp_path: Path):
    """Test scenario 15: Bot restarts without losing database records."""
    db_file = tmp_path / "test_bot.sqlite3"
    db = Database(db_path=db_file)
    await db.init_db()

    # Insert user
    user = await db.add_or_update_user(telegram_id=12345, username="alice", first_name="Alice")
    assert user["telegram_id"] == 12345

    # Simulate bot restart by creating a new Database instance pointing to the same file
    db_restarted = Database(db_path=db_file)
    saved_user = await db_restarted.get_user(12345)
    assert saved_user is not None
    assert saved_user["username"] == "alice"
    assert saved_user["first_name"] == "Alice"


@pytest.mark.asyncio
async def test_new_and_existing_user(tmp_path: Path):
    """Test scenarios 1 & 2: New user and existing user handling."""
    db_file = tmp_path / "test_user.sqlite3"
    db = Database(db_path=db_file)
    await db.init_db()

    # 1. New user
    res1 = await db.add_or_update_user(telegram_id=98765, username="bob", first_name="Bob")
    assert res1["telegram_id"] == 98765

    # Verify user count in stats
    stats1 = await db.get_statistics()
    assert stats1["total_users"] == 1

    # 2. Existing user updates name
    res2 = await db.add_or_update_user(telegram_id=98765, username="bob_updated", first_name="Bobby")
    user = await db.get_user(98765)
    assert user["username"] == "bob_updated"
    assert user["first_name"] == "Bobby"

    # User count should still be 1 (no duplicate)
    stats2 = await db.get_statistics()
    assert stats2["total_users"] == 1


@pytest.mark.asyncio
async def test_search_and_download_history(tmp_path: Path):
    """Test scenario 14: User search history is saved and retrievable."""
    db_file = tmp_path / "test_history.sqlite3"
    db = Database(db_path=db_file)
    await db.init_db()

    user_id = 55555
    await db.add_or_update_user(telegram_id=user_id, username="charlie", first_name="Charlie")

    # Record multiple searches
    await db.record_search(user_id, "Tum Hi Ho Arijit Singh")
    await db.record_search(user_id, "Shape of You Ed Sheeran")

    history = await db.get_user_search_history(user_id, limit=5)
    assert len(history) == 2
    assert history[0]["search_query"] == "Shape of You Ed Sheeran"
    assert history[1]["search_query"] == "Tum Hi Ho Arijit Singh"

    # Record download
    dl_id = await db.record_download(
        telegram_id=user_id,
        media_title="Shape of You",
        source="https://youtube.com/watch?v=JGwWNGJdvx8",
        media_type="audio",
        selected_quality="320kbps",
        selected_format="mp3",
        status="pending"
    )
    assert dl_id > 0

    await db.update_download_status(dl_id, "completed")
    downloads = await db.get_user_downloads(user_id, limit=5)
    assert len(downloads) == 1
    assert downloads[0]["status"] == "completed"
    assert downloads[0]["selected_quality"] == "320kbps"

    # Verify statistics
    stats = await db.get_statistics()
    assert stats["total_searches"] == 2
    assert stats["successful_downloads"] == 1


@pytest.mark.asyncio
async def test_block_unblock_and_csv_export(tmp_path: Path):
    """Test blocking/unblocking users and CSV export capabilities."""
    db_file = tmp_path / "test_admin.sqlite3"
    db = Database(db_path=db_file)
    await db.init_db()

    target_id = 77777
    await db.add_or_update_user(telegram_id=target_id, username="david", first_name="David")

    assert await db.is_user_blocked(target_id) is False

    # Block user
    await db.set_user_blocked(target_id, is_blocked=True)
    assert await db.is_user_blocked(target_id) is True

    # Unblock user
    await db.set_user_blocked(target_id, is_blocked=False)
    assert await db.is_user_blocked(target_id) is False

    # CSV Export
    csv_text = await db.export_table_to_csv("users")
    assert "telegram_id" in csv_text
    assert "77777" in csv_text
    assert "david" in csv_text
