"""
Animation & Sticker Companion for Download/Upload Pipelines
Sends entertaining animated GIF/stickers during processing and cleanly vanishes them
when media delivery completes.
"""
import random
import logging
from pathlib import Path
from typing import Optional
from aiogram import Bot
from aiogram.types import Message, FSInputFile

from utils.progress import vanish_status_message

logger = logging.getLogger(__name__)

ANIMATIONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "animations"


def get_available_animations() -> list[Path]:
    """Retrieve all available animated files (.mp4 or .gif) from assets/animations/."""
    if not ANIMATIONS_DIR.exists():
        return []
    files = list(ANIMATIONS_DIR.glob("*.mp4")) + list(ANIMATIONS_DIR.glob("*.gif"))
    return [f for f in files if f.is_file() and f.stat().st_size > 0]


async def send_download_animation(bot: Bot, chat_id: int) -> Optional[Message]:
    """
    Send a random animated sticker/GIF to entertain the user while downloading/uploading.
    Fails safely without interrupting the download pipeline.
    """
    try:
        animations = get_available_animations()
        if not animations:
            return None

        chosen = random.choice(animations)
        input_file = FSInputFile(str(chosen))
        anim_msg = await bot.send_animation(
            chat_id=chat_id,
            animation=input_file,
            disable_notification=True
        )
        return anim_msg
    except Exception as e:
        logger.debug(f"Could not send download companion animation to {chat_id}: {e}")
        return None


async def vanish_download_animation(anim_msg: Optional[Message], delay: float = 0.2) -> bool:
    """
    Safely delete the animation message once the media has arrived or on abort.
    """
    if not anim_msg:
        return True
    try:
        return await vanish_status_message(anim_msg, delay=delay)
    except Exception as e:
        logger.debug(f"Error vanishing download animation: {e}")
        return False
