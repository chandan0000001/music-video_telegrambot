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
    144p - 480p marked Free.
    720p and higher marked with premium price badges.
    """
    buttons = []
    # Two buttons per row for neat presentation
    row = []
    for res in available_resolutions:
        if res in ("144p", "240p", "360p", "480p"):
            btn_text = f"🎬 {res} (Free)"
        elif res == "720p":
            btn_text = f"🔒 720p (₹29)"
        else:
            btn_text = f"🔒 {res} (₹49)"
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


def get_paywall_keyboard(video_id: str, quality: str) -> InlineKeyboardMarkup:
    """Keyboard for selecting plan when clicking premium 720p / 1080p."""
    price = 29 if quality == "720p" else 49
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⚡ Unlock This {quality} Video — ₹{price}",
                    callback_data=f"pay:single:{video_id}:{quality}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💎 Weekly Pass (20 HD/day) — ₹38",
                    callback_data=f"pay:weekly:{video_id}:{quality}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👑 Monthly VIP (45 HD/day) — ₹120",
                    callback_data=f"pay:monthly:{video_id}:{quality}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Free Qualities (144p - 480p)",
                    callback_data=f"opt_video:{video_id}"
                ),
                InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")
            ]
        ]
    )


def get_payment_qr_keyboard(order_id: str, upi_uri: str) -> InlineKeyboardMarkup:
    """Keyboard shown under Dynamic QR Code."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📱 Pay with Google Pay / UPI App", url=upi_uri)
            ],
            [
                InlineKeyboardButton(
                    text="✅ Submit 12-Digit UTR",
                    callback_data=f"submit_utr:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Contact Admin",
                    url="https://t.me/coder_chandan"
                ),
                InlineKeyboardButton(text="❌ Cancel Order", callback_data="cancel_action")
            ]
        ]
    )


def get_utr_check_again_keyboard(order_id: str, utr: str) -> InlineKeyboardMarkup:
    """Keyboard shown if UTR is not yet detected in Gmail (e.g. slight delay)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Check Again",
                    callback_data=f"recheck_utr:{order_id}:{utr}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Contact Admin (@coder_chandan)",
                    url="https://t.me/coder_chandan"
                ),
                InlineKeyboardButton(text="🔙 Main Menu", callback_data="menu:main")
            ]
        ]
    )


def get_admin_payment_alert_keyboard(order_id: str, utr: str) -> InlineKeyboardMarkup:
    """Allows Admin to 1-tap force approve an order if needed."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Force Approve & Deliver",
                    callback_data=f"admin_approve:{order_id}:{utr}"
                ),
                InlineKeyboardButton(
                    text="❌ Reject",
                    callback_data=f"admin_reject:{order_id}"
                )
            ]
        ]
    )


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


def get_cancel_download_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Generate inline cancellation button shown during active downloads."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛑 Cancel Download", callback_data=f"cancel_dl:{user_id}")
        ]
    ])


def get_youtube_music_keyboard(video_id: str) -> InlineKeyboardMarkup:
    """
    Generate optimized YouTube Music fast-download keyboard.
    Allows 1-tap instant download for top audio bitrates, plus format/video options.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚡ MP3 320 kbps (High)", callback_data=f"adl:{video_id}:mp3:320"),
            InlineKeyboardButton(text="⚡ MP3 192 kbps (Standard)", callback_data=f"adl:{video_id}:mp3:192"),
        ],
        [
            InlineKeyboardButton(text="⚡ MP3 128 kbps (Fast)", callback_data=f"adl:{video_id}:mp3:128"),
            InlineKeyboardButton(text="🎶 M4A (AAC)", callback_data=f"adl:{video_id}:m4a:192"),
        ],
        [
            InlineKeyboardButton(text="🎼 Lossless WAV", callback_data=f"adl:{video_id}:wav:320"),
            InlineKeyboardButton(text="🎬 Video (MP4)", callback_data=f"opt_video:{video_id}"),
        ],
        [
            InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_action")
        ]
    ])
