"""
User History Handler
Displays recent searches and download activity for the current user.
Uses HTML parse mode with escaping to avoid entity parsing issues.
"""
import html
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

from database import db
from keyboards.main_menu import get_back_to_main_keyboard
from utils.time_utils import format_ist

logger = logging.getLogger(__name__)
router = Router(name="history_router")


async def render_user_history(user_id: int) -> str:
    """Format user's recent search and download records."""
    searches = await db.get_user_search_history(user_id, limit=5)
    downloads = await db.get_user_downloads(user_id, limit=5)

    lines = ["📜 <b>Your Activity History</b>\n"]

    # Search history section
    lines.append("🔍 <b>Recent Searches:</b>")
    if searches:
        for idx, s in enumerate(searches, start=1):
            date_part = format_ist(s.get("searched_at"))
            q_clean = html.escape(s.get("search_query", ""))
            lines.append(f"{idx}. <code>{q_clean}</code> — <i>{date_part}</i>")
    else:
        lines.append("<i>No search history recorded yet.</i>")

    lines.append("\n📦 <b>Recent Downloads:</b>")
    if downloads:
        for idx, d in enumerate(downloads, start=1):
            date_part = format_ist(d.get("created_at"))
            status_icon = "✅" if d["status"] == "completed" else ("❌" if d["status"] == "failed" else "⏳")
            media_type = d.get("media_type", "media").capitalize()
            quality = d.get("selected_quality", "")
            format_ = d.get("selected_format", "").upper()
            title = html.escape(d.get("media_title", "Unknown Title"))
            lines.append(
                f"{idx}. {status_icon} <b>{title}</b>\n"
                f"   └ {media_type} ({format_} {quality}) — <i>{date_part}</i>"
            )
    else:
        lines.append("<i>No download records found.</i>")

    return "\n".join(lines)


@router.message(Command("history"))
async def cmd_history(message: Message) -> None:
    """Handle /history command without exposing personal activity in group chats."""
    user = message.from_user
    if not user:
        return
    chat = getattr(message, "chat", None)
    chat_type = getattr(chat, "type", "private")
    text = await render_user_history(user.id)

    if isinstance(chat_type, str) and chat_type != "private":
        try:
            sent = await message.bot.send_message(
                chat_id=user.id,
                text=text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML"
            )
            from utils.selection_session import selection_session_manager
            await selection_session_manager.register_session(
                bot=message.bot,
                user_id=user.id,
                chat_id=user.id,
                message_id=getattr(sent, "message_id", None)
            )
            await message.answer("🔒 Sent your personal history to your private DM.")
        except Exception:
            await message.answer("🔒 Personal history is private — please open our private chat and run /history.")
        return

    sent = await message.answer(
        text=text,
        reply_markup=get_back_to_main_keyboard(),
        parse_mode="HTML"
    )
    from utils.selection_session import selection_session_manager
    chat_id = getattr(chat, "id", getattr(user, "id", 0)) if user else 0
    await selection_session_manager.register_session(
        bot=message.bot,
        user_id=user.id,
        chat_id=chat_id,
        message_id=getattr(sent, "message_id", None)
    )


@router.callback_query(F.data == "menu:history")
async def cb_history(callback: CallbackQuery) -> None:
    """Handle My History callback from main menu without leaking history into group chats."""
    user = callback.from_user
    if not user:
        return
    chat = getattr(callback.message, "chat", None) if callback.message else None
    chat_type = getattr(chat, "type", "private")

    text = await render_user_history(user.id)
    if isinstance(chat_type, str) and chat_type != "private":
        try:
            await callback.bot.send_message(
                chat_id=user.id,
                text=text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML"
            )
            await callback.answer("Sent your personal history to your private DM.", show_alert=True)
        except Exception:
            await callback.answer("Open my private chat to view your personal history.", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        sent_msg = None
        try:
            sent_msg = await callback.message.edit_text(
                text=text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML"
            )
        except Exception:
            sent_msg = await callback.message.answer(
                text=text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML"
            )
        from utils.selection_session import selection_session_manager
        await selection_session_manager.register_session(
            bot=callback.bot,
            user_id=user.id,
            chat_id=callback.message.chat.id,
            message_id=sent_msg.message_id if sent_msg else callback.message.message_id
        )
