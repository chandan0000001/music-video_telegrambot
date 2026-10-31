"""
Quality and Format Selection Keyboards
Interactive inline buttons for choosing audio formats/bitrates and video resolutions.
"""
from typing import List, Dict, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_audio_format_keyboard(video_id: str) -> InlineKeyboardMarkup:
    """Generate audio format selection buttons (MP3, M4A, WAV)."""
    buttons = [
        [
            InlineKeyboardButton(text="🎵 MP3", callback_data=f"afmt:{video_id}:mp3"),
            InlineKeyboardButton(text="🎶 M4A", callback_data=f"afmt:{video_id}:m4a"),
            InlineKeyboardButton(text="🎼 WAV", callback_data=f"afmt:{video_id}:wav"),
        ],
        [
            InlineKeyboardButton(text="🔙 Back", callback_data=f"sel:{video_id}"),
            InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_audio_quality_keyboard(
    video_id: str,
    selected_format: str,
    approx_sizes: Optional[Dict[str, str]] = None
) -> InlineKeyboardMarkup:
    """Generate audio quality / bitrate selection buttons (128k, 192k, 320k)."""
    approx_sizes = approx_sizes or {}
    buttons = []

    bitrates = ["128", "192", "320"]
    for br in bitrates:
        size_label = f" (~{approx_sizes.get(br)})" if br in approx_sizes else ""
        text = f"⚡ {br} kbps{size_label}"
        buttons.append([
            InlineKeyboardButton(
                text=text,
                callback_data=f"adl:{video_id}:{selected_format}:{br}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="🔙 Change Format", callback_data=f"opt_audio:{video_id}"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_video_quality_keyboard(
    video_id: str,
    available_resolutions: List[str]
) -> InlineKeyboardMarkup:
    """
    Generate video quality buttons sorted strictly from low to high.
    Only genuinely available resolutions are displayed.
    """
    buttons = []
    # Two buttons per row for neat presentation
    row = []
    for res in available_resolutions:
        btn_text = f"🎬 {res} (MP4)"
        row.append(InlineKeyboardButton(text=btn_text, callback_data=f"vdl:{video_id}:{res}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(text="🔙 Back", callback_data=f"sel:{video_id}"),
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_retry_keyboard(
    retry_callback: str,
    back_callback: str
) -> InlineKeyboardMarkup:
    """Generate inline retry keyboard when download or processing fails."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Retry Download", callback_data=retry_callback)
        ],
        [
            InlineKeyboardButton(text="🔙 Change Quality", callback_data=back_callback),
            InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")
        ]
    ])
