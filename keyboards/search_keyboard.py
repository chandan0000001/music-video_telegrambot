"""
Search Keyboards
Generates result listings and media type selection keyboards.
"""
from typing import List, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_search_results_keyboard(results: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Generate keyboard with up to 5 search result buttons."""
    buttons = []
    for idx, item in enumerate(results, start=1):
        title = item["title"]
        if len(title) > 34:
            title = title[:31] + "..."
        duration_str = item.get("duration_str", "")
        label = f"{idx}. {title} [{duration_str}]"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"sel:{item['id']}")
        ])

    buttons.append([
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_media_type_keyboard(video_id: str) -> InlineKeyboardMarkup:
    """Ask user whether they want audio or video for this selection."""
    buttons = [
        [
            InlineKeyboardButton(text="🎧 Download Audio", callback_data=f"opt_audio:{video_id}"),
            InlineKeyboardButton(text="🎬 Download Video", callback_data=f"opt_video:{video_id}")
        ],
        [
            InlineKeyboardButton(text="🔙 Main Menu", callback_data="menu:main")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
