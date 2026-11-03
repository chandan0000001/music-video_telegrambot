"""
Main Menu Keyboards
Inline navigation buttons for start and help commands.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Generate main navigation inline keyboard with user preferred layout matching sample UI."""
    buttons = [
        [
            InlineKeyboardButton(text="🔍 Search Music", callback_data="menu:search"),
            InlineKeyboardButton(text="▶️ YouTube Music", callback_data="menu:ytmusic"),
        ],
        [
            InlineKeyboardButton(text="𝕏 X (Twitter) Downloader", callback_data="menu:twitter"),
            InlineKeyboardButton(text="📸 Instagram Downloader", callback_data="menu:instagram"),
        ],
        [
            InlineKeyboardButton(text="▶️ YouTube Video Downloader", callback_data="menu:video"),
        ],
        [
            InlineKeyboardButton(text="🎁 Invite & Earn VIP", callback_data="menu:referral"),
            InlineKeyboardButton(text="🎟️ Redeem Coupon", callback_data="coupon:redeem"),
        ],
        [
            InlineKeyboardButton(text="📜 My History", callback_data="menu:history"),
            InlineKeyboardButton(text="⚡ Features & Help", callback_data="menu:help"),
        ]
    ]

    if is_admin:
        buttons.append([
            InlineKeyboardButton(text="👤 Developer ↗", url="https://t.me/coder_chandan"),
            InlineKeyboardButton(text="⚙️ Admin Dashboard 🛡️", callback_data="admin:dashboard")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="👤 Developer ↗", url="https://t.me/coder_chandan")
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_ytmusic_menu_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for dedicated YouTube Music Downloader hub."""
    return get_back_to_main_keyboard()


def get_twitter_menu_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for dedicated X (Twitter) Downloader hub."""
    return get_back_to_main_keyboard()


def get_instagram_menu_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for dedicated Instagram Downloader hub."""
    return get_back_to_main_keyboard()


def get_back_to_main_keyboard() -> InlineKeyboardMarkup:
    """Generate universal clean 'Back to Main Menu' keyboard for all sub-sections."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="menu:main")
            ]
        ]
    )


def get_session_expired_keyboard() -> InlineKeyboardMarkup:
    """Generate clean 'Start Again' keyboard for expired windows and sessions."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Start Again (/start)", callback_data="menu:main")
            ]
        ]
    )

