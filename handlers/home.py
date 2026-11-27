"""Route persistent keyboard buttons before text and state-specific handlers."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from keyboards.main_menu import HOME_ACTIONS, get_back_to_main_keyboard, get_youtube_hub_keyboard
from handlers.states import UserStates
from config import ADMIN_IDS
from keyboards.main_menu import get_home_reply_keyboard, get_more_reply_keyboard

router = Router(name="home_keyboard_router")


@router.message(F.text.in_(HOME_ACTIONS))
async def handle_home_button(message: Message, state: FSMContext) -> None:
    from handlers.start import cmd_start, cmd_help, MAIN_MENU_TEXT
    from handlers.search import cmd_search
    from handlers.ytmusic import cmd_ytmusic
    from handlers.instagram import cmd_instagram
    from handlers.twitter import cmd_twitter
    from handlers.referral import cmd_referral
    from handlers.coupons import cmd_redeem_coupon
    from handlers.history import cmd_history
    from handlers.admin import cmd_admin
    from utils.selection_session import selection_session_manager

    action = HOME_ACTIONS[message.text]
    await state.clear()
    if message.from_user:
        selection_session_manager.clear_session(message.from_user.id)
    # Command handlers parse message.text; pass the intended command, not the label.
    command = message.model_copy(update={"text": "/" + action}).as_(message.bot)
    state_handlers = {
        "start": cmd_start, "search": cmd_search, "ytmusic": cmd_ytmusic,
        "instagram": cmd_instagram, "twitter": cmd_twitter,
        "coupon": cmd_redeem_coupon, "admin": cmd_admin,
    }
    plain_handlers = {"referral": cmd_referral, "history": cmd_history, "help": cmd_help}
    if action == "more":
        is_admin = bool(message.from_user and message.from_user.id in ADMIN_IDS)
        await message.answer(
            "☰ <b>MORE</b>\n────────────────────\n"
            "Rewards, history, and help below.\n"
            "💡 Also available: /plan · /privacy · /about",
            parse_mode="HTML", reply_markup=get_more_reply_keyboard(is_admin),
        )
    elif action == "main":
        is_admin = bool(message.from_user and message.from_user.id in ADMIN_IDS)
        await message.answer(
            MAIN_MENU_TEXT, parse_mode="HTML", reply_markup=get_home_reply_keyboard(is_admin),
        )
    elif action == "youtube":
        await message.answer(
            "▶️ <b>YOUTUBE</b>\n\nChoose music and audio, or video and Shorts.\nYou can also paste a YouTube link directly.",
            parse_mode="HTML", reply_markup=get_youtube_hub_keyboard(),
        )
    elif action in state_handlers:
        await state_handlers[action](command, state)
    elif action in plain_handlers:
        await plain_handlers[action](command)
    elif action == "video":
        await state.set_state(UserStates.waiting_for_video_link)
        sent = await message.answer(
            "🎬 <b>YOUTUBE VIDEO</b>\n\nPaste a YouTube video or Shorts link below.\nChoose your quality on the next screen.",
            parse_mode="HTML", reply_markup=get_back_to_main_keyboard(),
        )
        if message.from_user:
            await selection_session_manager.register_session(
                bot=message.bot, user_id=message.from_user.id,
                chat_id=message.chat.id, message_id=sent.message_id,
            )
