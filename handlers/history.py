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
            date_part = s.get("searched_at", "").split(".")[0]
            q_clean = html.escape(s.get("search_query", ""))
            lines.append(f"{idx}. <code>{q_clean}</code> — <i>{date_part}</i>")
    else:
        lines.append("<i>No search history recorded yet.</i>")

    lines.append("\n📦 <b>Recent Downloads:</b>")
    if downloads:
        for idx, d in enumerate(downloads, start=1):
            date_part = d.get("created_at", "").split(".")[0]
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
    """Handle /history command."""
    user = message.from_user
    if not user:
        return
    text = await render_user_history(user.id)
    await message.answer(
        text=text,
        reply_markup=get_back_to_main_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "menu:history")
async def cb_history(callback: CallbackQuery) -> None:
    """Handle My History callback from main menu."""
    user = callback.from_user
    if not user:
        return
    text = await render_user_history(user.id)
    if callback.message:
        try:
            await callback.message.edit_text(
                text=text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML"
            )
        except Exception:
            await callback.message.answer(
                text=text,
                reply_markup=get_back_to_main_keyboard(),
                parse_mode="HTML"
            )
    await callback.answer()
