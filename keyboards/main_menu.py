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
            InlineKeyboardButton(text="𝕏 X / Twitter", callback_data="menu:twitter"),
            InlineKeyboardButton(text="📸 Instagram", callback_data="menu:instagram"),
        ],
        [
            InlineKeyboardButton(text="🎬 YouTube Video", callback_data="menu:video"),
        ],
        [
            InlineKeyboardButton(text="🎁 Invite & Earn VIP", callback_data="menu:referral"),
            InlineKeyboardButton(text="🎟️ Redeem Coupon", callback_data="coupon:redeem"),
        ],
        [
            InlineKeyboardButton(text="💎 My Plan", callback_data="menu:plan"),
            InlineKeyboardButton(text="📜 My History", callback_data="menu:history"),
        ],
        [
            InlineKeyboardButton(text="❔ Help", callback_data="menu:help"),
            InlineKeyboardButton(text="🔒 Privacy", callback_data="menu:privacy"),
        ],
    ]

    if is_admin:
        buttons.append([
            InlineKeyboardButton(text="💬 Support", url="https://t.me/coder_chandan"),
            InlineKeyboardButton(text="⚙️ Admin", callback_data="admin:dashboard")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="ℹ️ About", callback_data="menu:about"),
            InlineKeyboardButton(text="💬 Support", url="https://t.me/coder_chandan"),
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
                InlineKeyboardButton(text="‹ Main menu", callback_data="menu:main")
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


# Telegram reply keyboards live below the composer, like a native control panel.
HOME_ACTIONS = {
    "▶️ YOUTUBE": "youtube",
    "𝕏 X": "twitter",
    "𝕏 X/Twitter": "twitter",
    "🎵 SEARCH MUSIC": "search",
    "🎵 SEARCH SONG": "search",
    "☰ MORE": "more",
    "▶️ YOUTUBE MUSIC": "ytmusic",
    "📸 INSTAGRAM": "instagram",
    "𝕏 X / TWITTER": "twitter",
    "🎬 YOUTUBE VIDEO": "video",
    "🎁 INVITE & EARN": "referral",
    "🎟 REDEEM COUPON": "coupon",
    "📜 HISTORY": "history",
    "❔ HELP": "help",
    "🏠 MAIN MENU": "main",
    "🙈 HIDE BAR": "hide",
    "⚙️ ADMIN": "admin",
}


def _reply_keyboard(rows, placeholder):
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
        is_persistent=False,
        one_time_keyboard=False,
        input_field_placeholder=placeholder,
    )


def get_home_reply_keyboard(is_admin: bool = False):
    """Keep the primary menu to two rows for every user."""
    return _reply_keyboard([
        ["▶️ YOUTUBE", "📸 INSTAGRAM", "𝕏 X/Twitter"],
        ["🎵 SEARCH SONG", "☰ MORE"],
    ], "Choose a platform or search for a song…")


def get_more_reply_keyboard(is_admin: bool = False):
    """Secondary actions with an always-visible route back home or to hide the bar."""
    rows = [
        ["🎁 INVITE & EARN", "🎟 REDEEM COUPON"],
        ["📜 HISTORY", "❔ HELP"],
    ]
    if is_admin:
        rows.append(["⚙️ ADMIN"])
    rows.append(["🏠 MAIN MENU", "🙈 HIDE BAR"])
    return _reply_keyboard(rows, "Choose an option, return home, or hide the bar…")


def get_youtube_hub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎧 Music & Audio", callback_data="menu:ytmusic"),
         InlineKeyboardButton(text="🎬 Video & Shorts", callback_data="menu:video")],
        [InlineKeyboardButton(text="‹ Main menu", callback_data="menu:main")],
    ])
