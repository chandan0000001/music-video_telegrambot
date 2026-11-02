"""
Comprehensive Unit Tests for Coupons, Referrals, and Inline Mode Features
"""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from database import Database
from handlers.states import UserStates, AdminStates


@pytest.fixture
async def test_db(tmp_path: Path):
    """Fixture providing a fresh isolated database for coupon and referral testing."""
    db_file = tmp_path / "test_coupons_refs.sqlite3"
    db_inst = Database(db_path=db_file)
    await db_inst.init_db()
    return db_inst


# ==============================================================================
# COUPON CODE TESTS & EDGE CASES
# ==============================================================================

@pytest.mark.asyncio
async def test_coupon_creation_validation(test_db: Database):
    """Test coupon creation input validation and edge cases."""
    # 1. Invalid characters or length
    ok, err = await test_db.create_coupon(code="A", days=7)
    assert not ok
    assert "3-30" in str(err)

    # 2. Invalid days or max_uses
    ok, err = await test_db.create_coupon(code="TESTCODE", days=0)
    assert not ok
    assert "at least 1" in str(err)

    ok, err = await test_db.create_coupon(code="TESTCODE", days=7, max_uses=0)
    assert not ok
    assert "at least 1" in str(err)

    # 3. Successful creation
    ok, coupon = await test_db.create_coupon(
        code="vip-week_2026",
        days=7,
        max_uses=5,
        plan_tier="go",
        expires_in_hours=24,
        created_by=111
    )
    assert ok
    assert isinstance(coupon, dict)
    assert coupon["code"] == "VIP-WEEK_2026"
    assert coupon["days"] == 7
    assert coupon["max_uses"] == 5
    assert coupon["used_count"] == 0
    assert coupon["is_active"] == 1
    assert coupon["expires_at"] is not None

    # 4. Duplicate coupon code
    ok_dup, err_dup = await test_db.create_coupon(code="VIP-WEEK_2026")
    assert not ok_dup
    assert "already exists" in str(err_dup)


@pytest.mark.asyncio
async def test_coupon_redemption_and_subscription_stacking(test_db: Database):
    """Test normal coupon redemption and stacking on existing active subscription."""
    user_id = 1001
    await test_db.add_or_update_user(user_id, username="testuser", first_name="Test")

    # Create coupon
    ok, _ = await test_db.create_coupon(code="VIP7DAYS", days=7, max_uses=2)
    assert ok

    # 1. First redemption
    ok, info, err = await test_db.redeem_coupon(telegram_id=user_id, code="vip7days")
    assert ok
    assert err is None
    assert info["code"] == "VIP7DAYS"
    assert info["days"] == 7

    sub = await test_db.get_user_subscription(user_id)
    assert sub is not None
    assert sub["is_active"] == 1
    first_exp = datetime.fromisoformat(sub["expiry_date"].replace(" ", "T"))

    # 2. Try redeeming same coupon again -> should fail with duplicate redemption error
    ok_dup, _, err_dup = await test_db.redeem_coupon(telegram_id=user_id, code="VIP7DAYS")
    assert not ok_dup
    assert "already redeemed" in err_dup

    # 3. Create another coupon for 14 days and redeem -> should STACK and extend expiry
    ok2, _ = await test_db.create_coupon(code="VIP14DAYS", days=14, max_uses=1)
    assert ok2

    ok_stack, info_stack, _ = await test_db.redeem_coupon(telegram_id=user_id, code="VIP14DAYS")
    assert ok_stack
    sub_stacked = await test_db.get_user_subscription(user_id)
    second_exp = datetime.fromisoformat(sub_stacked["expiry_date"].replace(" ", "T"))

    # The new expiry should be approximately 14 days AFTER first_exp
    assert (second_exp - first_exp).days >= 13


@pytest.mark.asyncio
async def test_coupon_max_uses_limit(test_db: Database):
    """Test coupon reaching maximum allowed uses."""
    user1, user2, user3 = 2001, 2002, 2003
    for uid in (user1, user2, user3):
        await test_db.add_or_update_user(uid, username=f"user_{uid}", first_name="U")

    # Create coupon with max_uses = 2
    ok, _ = await test_db.create_coupon(code="LIMIT2", days=3, max_uses=2)
    assert ok

    # User 1 redeems
    ok1, _, _ = await test_db.redeem_coupon(user1, "LIMIT2")
    assert ok1

    # User 2 redeems
    ok2, _, _ = await test_db.redeem_coupon(user2, "LIMIT2")
    assert ok2

    # User 3 tries to redeem -> fails because limit reached
    ok3, _, err3 = await test_db.redeem_coupon(user3, "LIMIT2")
    assert not ok3
    assert "maximum usage limit" in err3


@pytest.mark.asyncio
async def test_coupon_expired_and_revoked(test_db: Database):
    """Test expired coupon rejection and admin revocation."""
    user = 3001
    await test_db.add_or_update_user(user, username="user3", first_name="U")

    # 1. Create already expired coupon by inserting directly with past expires_at
    past_date = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    async with test_db._get_connection() as conn:
        await conn.execute(
            """
            INSERT INTO coupons (code, plan_tier, days, max_uses, used_count, is_active, expires_at, created_by)
            VALUES ('EXPIRED1', 'go', 7, 10, 0, 1, ?, 999)
            """,
            (past_date,)
        )
        await conn.commit()

    ok_exp, _, err_exp = await test_db.redeem_coupon(user, "EXPIRED1")
    assert not ok_exp
    assert "expired" in err_exp

    # 2. Create active coupon then revoke it
    await test_db.create_coupon(code="REVOKEME", days=5, max_uses=10)
    rev_ok, rev_msg = await test_db.revoke_coupon("REVOKEME")
    assert rev_ok
    assert "terminated" in rev_msg

    # Attempt to redeem revoked coupon
    ok_rev, _, err_rev = await test_db.redeem_coupon(user, "REVOKEME")
    assert not ok_rev
    assert "terminated or revoked" in err_rev


# ==============================================================================
# REFERRAL SYSTEM TESTS & EDGE CASES
# ==============================================================================

@pytest.mark.asyncio
async def test_referral_milestones_and_anti_fraud(test_db: Database):
    """Test referral recording, anti-self-referral, duplicate block, and milestone awards."""
    inviter = 5001
    await test_db.add_or_update_user(inviter, username="inviter", first_name="Inviter")

    # 1. Self-referral prevention
    ok_self, total_self, mile_self = await test_db.record_referral(inviter, inviter)
    assert not ok_self
    assert total_self == 0
    assert not mile_self

    # 2. First referral
    friend1 = 6001
    await test_db.add_or_update_user(friend1, username="friend1", first_name="Friend 1")
    ok1, total1, mile1 = await test_db.record_referral(inviter, friend1)
    assert ok1
    assert total1 == 1
    assert not mile1  # Need 3 for milestone

    # 3. Duplicate referral from friend1 should be ignored
    ok_dup, total_dup, mile_dup = await test_db.record_referral(inviter, friend1)
    assert not ok_dup
    assert total_dup == 1

    # 4. Second referral
    friend2 = 6002
    await test_db.add_or_update_user(friend2, username="friend2", first_name="Friend 2")
    ok2, total2, mile2 = await test_db.record_referral(inviter, friend2)
    assert ok2
    assert total2 == 2
    assert not mile2

    # Verify inviter does NOT have VIP yet
    sub_before = await test_db.get_user_subscription(inviter)
    assert sub_before is None

    # 5. Third referral -> MILESTONE TRIGGERED!
    friend3 = 6003
    await test_db.add_or_update_user(friend3, username="friend3", first_name="Friend 3")
    ok3, total3, mile3 = await test_db.record_referral(inviter, friend3)
    assert ok3
    assert total3 == 3
    assert mile3  # Milestone unlocked!

    # Inviter should now have an active 7-day VIP subscription
    sub_after = await test_db.get_user_subscription(inviter)
    assert sub_after is not None
    assert sub_after["is_active"] == 1
    assert sub_after["is_active"] == 1
    assert sub_after["plan_tier"] == "go"

    # Verify referral stats
    stats = await test_db.get_referral_stats(inviter)
    assert stats["total_invited"] == 3
    assert stats["total_days_earned"] == 7
    assert stats["needed_for_next"] == 3
    assert stats["current_cycle_progress"] == 0


# ==============================================================================
# INLINE QUERY HANDLER TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_inline_search_short_query():
    """Inline query with < 2 characters returns helpful prompt."""
    from handlers.inline import inline_search_handler

    mock_iq = MagicMock()
    mock_iq.query = "a"
    mock_iq.answer = AsyncMock()

    await inline_search_handler(mock_iq)

    mock_iq.answer.assert_called_once()
    args, kwargs = mock_iq.answer.call_args
    results = args[0]
    assert len(results) == 1
    assert results[0].id == "prompt_query"
    assert "Search Music" in results[0].title


@pytest.mark.asyncio
async def test_inline_search_results():
    """Inline query returns formatted track cards with deep download links."""
    from handlers.inline import inline_search_handler

    mock_iq = MagicMock()
    mock_iq.query = "The Weeknd"
    mock_iq.answer = AsyncMock()

    mock_tracks = [
        {
            "id": "dQw4w9WgXcQ",
            "title": "Blinding Lights",
            "artist": "The Weeknd",
            "duration_str": "3:20",
            "thumbnail": "https://img.youtube.com/thumb.jpg"
        }
    ]

    with patch("services.music_service.music_service.search_music", new=AsyncMock(return_value=mock_tracks)):
        await inline_search_handler(mock_iq)

    mock_iq.answer.assert_called_once()
    args, kwargs = mock_iq.answer.call_args
    results = args[0]
    assert len(results) == 1
    track = results[0]
    assert track.id == "track_dQw4w9WgXcQ"
    assert "Blinding Lights" in track.title
    # Check download button URL
    button = track.reply_markup.inline_keyboard[0][0]
    assert button.url == "https://t.me/songsuperzxbot?start=dl_dQw4w9WgXcQ"


# ==============================================================================
# COMMAND & HANDLER INTEGRATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_cmd_redeem_coupon(test_db: Database):
    """Test user redeeming a coupon via /coupon command."""
    from handlers.coupons import cmd_redeem_coupon
    from database import db

    # Patch global db singleton with test_db
    with patch("handlers.coupons.db", test_db):
        user_id = 7001
        await test_db.add_or_update_user(user_id, username="sam", first_name="Sam")
        await test_db.create_coupon("SAMVIP", days=7, max_uses=1)

        msg = MagicMock()
        msg.from_user.id = user_id
        msg.from_user.username = "sam"
        msg.from_user.first_name = "Sam"
        msg.text = "/coupon SAMVIP"
        msg.answer = AsyncMock()
        state = AsyncMock()

        await cmd_redeem_coupon(msg, state)

        msg.answer.assert_called_once()
        sent_text = msg.answer.call_args[0][0]
        assert "Coupon Redeemed Successfully" in sent_text
        assert "SAMVIP" in sent_text
        assert "+7 Days" in sent_text


@pytest.mark.asyncio
async def test_cmd_referral_panel(test_db: Database):
    """Test /referral command rendering statistics and personal invite link."""
    from handlers.referral import cmd_referral

    with patch("handlers.referral.db", test_db):
        user_id = 8001
        await test_db.add_or_update_user(user_id, username="rachel", first_name="Rachel")

        msg = MagicMock()
        msg.from_user.id = user_id
        msg.from_user.username = "rachel"
        msg.from_user.first_name = "Rachel"
        msg.text = "/referral"
        msg.answer = AsyncMock()

        await cmd_referral(msg)

        msg.answer.assert_called_once()
        text, kwargs = msg.answer.call_args[0][0], msg.answer.call_args[1]
        assert "Invite Friends & Earn Free VIP Access" in text
        assert f"ref_{user_id}" in text
        assert kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_start_handler_deep_links(test_db: Database):
    """Test /start deep-linking for inline downloads and referral tracking."""
    from handlers.start import cmd_start

    with patch("handlers.start.db", test_db):
        inviter_id = 9001
        new_user_id = 9002
        await test_db.add_or_update_user(inviter_id, username="inviter9", first_name="Boss")

        # 1. Referral deep link: /start ref_9001
        ref_msg = MagicMock()
        ref_msg.from_user.id = new_user_id
        ref_msg.from_user.username = "newbie"
        ref_msg.from_user.first_name = "Newbie"
        ref_msg.text = f"/start ref_{inviter_id}"
        ref_msg.answer = AsyncMock()
        ref_msg.bot.send_message = AsyncMock()
        state = AsyncMock()

        await cmd_start(ref_msg, state)

        # Inviter was notified
        ref_msg.bot.send_message.assert_called_once()
        inviter_note = ref_msg.bot.send_message.call_args[1]["text"]
        assert "New Friend Joined" in inviter_note

        # 2. Download deep link: /start dl_dQw4w9WgXcQ
        dl_msg = MagicMock()
        dl_msg.from_user.id = new_user_id
        dl_msg.from_user.username = "newbie"
        dl_msg.from_user.first_name = "Newbie"
        dl_msg.text = "/start dl_dQw4w9WgXcQ"
        dl_msg.answer = AsyncMock()

        mock_info = {"title": "Never Gonna Give You Up", "duration_string": "3:33"}
        with patch("services.media_service.media_service.get_media_info", new=AsyncMock(return_value=(True, mock_info, None))):
            await cmd_start(dl_msg, state)

        dl_msg.answer.assert_called_once()
        dl_text = dl_msg.answer.call_args[1]["text"]
        assert "Never Gonna Give You Up" in dl_text

