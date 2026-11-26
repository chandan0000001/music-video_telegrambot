from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, User
from handlers.payment import cb_cancel_order, cb_recheck_utr
from handlers.start import get_welcome_text
from services.gmail_service import GmailService
from utils.rate_limiter import ThrottlingMiddleware


def test_welcome_escapes_name():
    text = get_welcome_text('<a href="https://evil.test">Admin</a>')
    assert '<a href=' not in text
    assert '&lt;a href=' in text


def test_email_text_cannot_approve_payment():
    with patch('services.gmail_service.imaplib.IMAP4_SSL') as connect:
        ok, reason = GmailService('test@example.com', 'password')._sync_check_utr('123456789012', 19)
    assert not ok
    assert 'Manual' in reason
    connect.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('handler,data', [(cb_cancel_order, 'cancel_order:other'), (cb_recheck_utr, 'recheck_utr:other:123456789012')])
async def test_other_users_cannot_act_on_order(handler, data):
    callback = MagicMock(spec=CallbackQuery)
    callback.message = None
    callback.data = data
    callback.from_user = User(id=123, is_bot=False, first_name='Test')
    callback.answer = AsyncMock()
    state = AsyncMock()
    with patch('handlers.payment.db.get_order', AsyncMock(return_value={'telegram_id': 456, 'status': 'paid'})), patch('handlers.payment.db.cancel_order', AsyncMock()) as cancel, patch('handlers.payment.fulfill_paid_order', AsyncMock()) as fulfill:
        await handler(callback, state)
    cancel.assert_not_called()
    fulfill.assert_not_called()
    state.clear.assert_not_called()
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('data', ['vdl:video:720p', 'adl:video:mp3:320', 'dosearch:test', 'coupon:redeem'])
async def test_expensive_callbacks_are_throttled(data):
    callback = MagicMock(spec=CallbackQuery)
    callback.message = None
    callback.data = data
    callback.from_user = User(id=123, is_bot=False, first_name='Test')
    callback.answer = AsyncMock()
    handler = AsyncMock()
    with patch('database.db.is_user_blocked', AsyncMock(return_value=False)), patch('utils.rate_limiter.rate_limiter.is_rate_limited', AsyncMock(return_value=(True, 2))):
        await ThrottlingMiddleware()(handler, callback, {})
    handler.assert_not_called()
    callback.answer.assert_awaited_once()
