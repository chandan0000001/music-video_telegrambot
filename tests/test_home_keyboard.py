from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
from aiogram import Bot
from aiogram.types import Message, Chat, User
from handlers.home import handle_home_button
from keyboards.main_menu import get_home_reply_keyboard, HOME_ACTIONS


def test_persistent_keyboard_layout():
    keyboard = get_home_reply_keyboard()
    assert [b.text for b in keyboard.keyboard[0]] == ["▶️ YOUTUBE", "📸 INSTAGRAM", "𝕏 X"]
    assert keyboard.is_persistent and not keyboard.one_time_keyboard
    assert [len(row) for row in keyboard.keyboard] == [3, 1, 2, 2, 1]
    assert all(button.text in HOME_ACTIONS for row in keyboard.keyboard for button in row)
    assert all(button.text != '⚙️ ADMIN' for row in keyboard.keyboard for button in row)


@pytest.mark.asyncio
@pytest.mark.parametrize('label,target,command', [
    ('🎵 SEARCH MUSIC', 'handlers.search.cmd_search', '/search'),
    ('▶️ YOUTUBE MUSIC', 'handlers.ytmusic.cmd_ytmusic', '/ytmusic'),
    ('🎟 REDEEM COUPON', 'handlers.coupons.cmd_redeem_coupon', '/coupon'),
])
async def test_reply_button_routes_command_without_label_as_query(label, target, command):
    bot = Bot(token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
    message = Message(message_id=1, date=datetime.now(timezone.utc),
                      chat=Chat(id=123, type='private'),
                      from_user=User(id=123, first_name='Test', is_bot=False),
                      text=label).as_(bot)
    state = AsyncMock()
    with patch(target, new_callable=AsyncMock) as handler:
        await handle_home_button(message, state)
        passed = handler.call_args.args[0]
        assert passed.text == command
        assert passed.from_user.id == 123
        assert passed.bot is bot
        state.clear.assert_awaited_once()
    await bot.session.close()
