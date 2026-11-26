from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
from aiogram import Bot
from aiogram.types import Message, Chat, User
from handlers.home import handle_home_button
from keyboards.main_menu import get_home_reply_keyboard, HOME_ACTIONS


def test_persistent_keyboard_layout():
    keyboard = get_home_reply_keyboard()
    assert [b.text for b in keyboard.keyboard[0]] == ["▶️ YOUTUBE", "📸 INSTAGRAM", "𝕏 X/Twitter"]
    assert keyboard.is_persistent and not keyboard.one_time_keyboard
    assert [len(row) for row in keyboard.keyboard] == [3, 2]
    assert all(button.text in HOME_ACTIONS for row in keyboard.keyboard for button in row)
    assert all(button.text != '⚙️ ADMIN' for row in keyboard.keyboard for button in row)


@pytest.mark.asyncio
@pytest.mark.parametrize('label,target,command', [
    ('🎵 SEARCH SONG', 'handlers.search.cmd_search', '/search'),
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


@pytest.mark.asyncio
async def test_more_and_main_menu_replace_keyboard_and_clear_input_state():
    bot = Bot(token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
    state = AsyncMock()
    for label, expected_rows in [('☰ MORE', [2, 2, 1]), ('🏠 MAIN MENU', [3, 2])]:
        message = Message(message_id=1, date=datetime.now(timezone.utc),
                          chat=Chat(id=123, type='private'),
                          from_user=User(id=123, first_name='Test', is_bot=False),
                          text=label).as_(bot)
        with patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
            await handle_home_button(message, state)
            answer.assert_awaited_once()
            keyboard = answer.call_args.kwargs['reply_markup']
            assert [len(row) for row in keyboard.keyboard] == expected_rows
            if label == '☰ MORE':
                assert keyboard.keyboard[-1][0].text == '🏠 MAIN MENU'
            else:
                assert keyboard.keyboard[1][1].text == '☰ MORE'
    assert state.clear.await_count == 2
    await bot.session.close()


def test_admin_tools_only_on_more_page():
    from keyboards.main_menu import get_more_reply_keyboard
    assert len(get_home_reply_keyboard(True).keyboard) == 2
    user_labels = [b.text for row in get_more_reply_keyboard().keyboard for b in row]
    admin_labels = [b.text for row in get_more_reply_keyboard(True).keyboard for b in row]
    assert '⚙️ ADMIN' not in user_labels
    assert '⚙️ ADMIN' in admin_labels
    assert admin_labels[-1] == '🏠 MAIN MENU'
