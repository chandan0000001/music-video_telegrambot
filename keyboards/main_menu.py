"""
Main Menu Keyboards
Inline navigation buttons for start and help commands.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_main_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Generate main navigation inline keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="🎵 Search Music", callback_data="menu:search"),
            InlineKeyboardButton(text="🎧 Download Audio", callback_data="menu:audio"),
        ],
        [
            InlineKeyboardButton(text="🎬 Download Video", callback_data="menu:video"),
            InlineKeyboardButton(text="📜 My History", callback_data="menu:history"),
        ],
        [
            InlineKeyboardButton(text="❓ Help", callback_data="menu:help"),
            InlineKeyboardButton(text="👨‍💻 Contact Developer", url="https://t.me/coder_chandan"),
        ]
    ]

    if is_admin:
        buttons.append([
            InlineKeyboardButton(text="⚙️ Admin Dashboard", callback_data="admin:dashboard")
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_back_to_main_keyboard() -> InlineKeyboardMarkup:
    """Generate simple 'Back to Main Menu' keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔙 Main Menu", callback_data="menu:main"),
                InlineKeyboardButton(text="👨‍💻 Contact Developer", url="https://t.me/coder_chandan")
            ]
        ]
    )
