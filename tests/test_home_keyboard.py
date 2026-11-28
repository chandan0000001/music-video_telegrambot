from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiogram import Bot
from aiogram.types import Message, Chat, User, ReplyKeyboardRemove, InlineKeyboardMarkup
from handlers.home import handle_home_button
from keyboards.main_menu import get_home_reply_keyboard, get_main_menu_keyboard, HOME_ACTIONS


def test_persistent_keyboard_layout():
    keyboard = get_home_reply_keyboard()
    assert [b.text for b in keyboard.keyboard[0]] == ["▶️ YOUTUBE", "📸 INSTAGRAM", "𝕏 X/Twitter"]
    assert not keyboard.is_persistent and not keyboard.one_time_keyboard
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
    for label, expected_rows in [('☰ MORE', [2, 2, 2]), ('🏠 MAIN MENU', [3, 2])]:
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
                assert keyboard.keyboard[-1][1].text == '🙈 HIDE BAR'
            else:
                assert keyboard.keyboard[1][1].text == '☰ MORE'
    assert state.clear.await_count == 2
    await bot.session.close()


@pytest.mark.asyncio
async def test_start_hides_bottom_bar_and_show_hide_toggle(tmp_path):
    from database import Database
    from handlers.start import cmd_start, cmd_hide, cb_show_quick_bar, cb_main_menu

    test_db = Database(db_path=tmp_path / "start_ui.db")
    await test_db.init_db()
    bot = Bot(token='123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi')
    state = AsyncMock()

    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=321, type='private'),
        from_user=User(id=321, first_name='Tester', is_bot=False),
        text='/start',
    ).as_(bot)

    with patch("handlers.start.db", test_db), patch.object(
        Message, "answer", new_callable=AsyncMock
    ) as answer:
        await cmd_start(message, state)
        assert answer.await_count == 2
        assert isinstance(answer.await_args_list[0].kwargs["reply_markup"], ReplyKeyboardRemove)
        final_markup = answer.await_args_list[1].kwargs["reply_markup"]
        assert isinstance(final_markup, InlineKeyboardMarkup)
        callbacks = [b.callback_data for row in final_markup.inline_keyboard for b in row if b.callback_data]
        assert "menu:show_bar" in callbacks

    # Tapping Show Quick Bar opens the bottom reply keyboard
    cb = MagicMock()
    cb.from_user = MagicMock(id=321)
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.answer = AsyncMock()
    await cb_show_quick_bar(cb)
    assert [b.text for b in cb.message.answer.await_args.kwargs["reply_markup"].keyboard[0]] == [
        "▶️ YOUTUBE", "📸 INSTAGRAM", "𝕏 X/Twitter"
    ]

    # /hide removes the bottom bar and shows clean inline menu
    with patch.object(Message, "answer", new_callable=AsyncMock) as hide_answer:
        await cmd_hide(message)
        assert hide_answer.await_count == 2
        assert isinstance(hide_answer.await_args_list[0].kwargs["reply_markup"], ReplyKeyboardRemove)
        assert isinstance(hide_answer.await_args_list[1].kwargs["reply_markup"], InlineKeyboardMarkup)

    # menu:main keeps clean inline menu without bottom bar
    cb_main = MagicMock()
    cb_main.from_user = MagicMock(id=321)
    cb_main.answer = AsyncMock()
    cb_main.message = MagicMock()
    cb_main.message.photo = None
    cb_main.message.edit_text = AsyncMock()
    await cb_main_menu(cb_main, state)
    assert isinstance(cb_main.message.edit_text.await_args.kwargs["reply_markup"], InlineKeyboardMarkup)

    await bot.session.close()


def test_admin_tools_only_on_more_page():
    from keyboards.main_menu import get_more_reply_keyboard
    assert len(get_home_reply_keyboard(True).keyboard) == 2
    user_labels = [b.text for row in get_more_reply_keyboard().keyboard for b in row]
    admin_labels = [b.text for row in get_more_reply_keyboard(True).keyboard for b in row]
    assert '⚙️ ADMIN' not in user_labels
    assert '⚙️ ADMIN' in admin_labels
    assert admin_labels[-2:] == ['🏠 MAIN MENU', '🙈 HIDE BAR']
