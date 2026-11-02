"""
Telegram Inline Mode Router
Allows users to search and send songs and videos directly in any chat or group.
"""
import html
import logging
from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from services.music_service import music_service

logger = logging.getLogger(__name__)
router = Router(name="inline_router")


@router.inline_query()
async def inline_search_handler(inline_query: InlineQuery) -> None:
    """Handle @songsuperzxbot inline searches in private chats and groups."""
    query = (inline_query.query or "").strip()

    # If query is empty or 1 character, show helpful search prompt
    if len(query) < 2:
        prompt_item = InlineQueryResultArticle(
            id="prompt_query",
            title="🔍 Search Music & Videos",
            description="Type a song title, artist, or YouTube query to share with friends...",
            input_message_content=InputTextMessageContent(
                message_text=(
                    "🎧 <b>Fast Music & Video Downloader</b>\n\n"
                    "Search and download original 320kbps MP3s and 1080p videos: @songsuperzxbot"
                ),
                parse_mode="HTML"
            ),
            thumbnail_url="https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=120&q=80"
        )
        await inline_query.answer([prompt_item], cache_time=60, is_personal=True)
        return

    # Search music (cached by Telegram for 5 minutes via cache_time=300)
    try:
        results = await music_service.search_music(query, limit=10)
    except Exception as e:
        logger.warning(f"Inline music search failed for '{query}': {e}")
        results = []

    if not results:
        no_res = InlineQueryResultArticle(
            id="no_results",
            title=f"❌ No tracks found for '{query[:30]}'",
            description="Try searching with a different song title or artist name.",
            input_message_content=InputTextMessageContent(
                message_text=f"🔍 No tracks found matching <i>{html.escape(query)}</i>.",
                parse_mode="HTML"
            )
        )
        await inline_query.answer([no_res], cache_time=30, is_personal=True)
        return

    articles = []
    for item in results:
        vid_id = item.get("id")
        if not vid_id:
            continue
        title = item.get("title", "Unknown Title")
        artist = item.get("artist", "Unknown Artist")
        duration = item.get("duration_str", "N/A")
        thumb = item.get("thumbnail") or f"https://img.youtube.com/vi/{vid_id}/hqdefault.jpg"

        share_card_text = (
            f"🎵 <b>{html.escape(title)}</b>\n"
            f"👤 <b>Artist:</b> {html.escape(artist)} | ⏱ <code>{duration}</code>\n\n"
            f"⚡ <i>Download 320kbps MP3 Audio or 1080p Video via @songsuperzxbot</i>"
        )

        dl_url = f"https://t.me/songsuperzxbot?start=dl_{vid_id}"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🎧 Download MP3 / 🎬 Video", url=dl_url)
                ]
            ]
        )

        articles.append(
            InlineQueryResultArticle(
                id=f"track_{vid_id}",
                title=title[:60],
                description=f"👤 {artist} | ⏱ {duration}",
                thumbnail_url=thumb,
                input_message_content=InputTextMessageContent(
                    message_text=share_card_text,
                    parse_mode="HTML"
                ),
                reply_markup=kb
            )
        )

    await inline_query.answer(articles, cache_time=300, is_personal=False)
