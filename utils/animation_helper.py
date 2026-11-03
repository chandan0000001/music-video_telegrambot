"""
Animation & Sticker Companion for Download/Upload Pipelines
Sends entertaining animated GIF/stickers during processing and cleanly vanishes them
when media delivery completes.
"""
import random
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict
from aiogram import Bot
from aiogram.types import Message, FSInputFile
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest

from utils.progress import vanish_status_message

logger = logging.getLogger(__name__)

ANIMATIONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "animations"

# In-memory cache for Telegram file_ids to avoid redundant uploads and network timeouts
_FILE_ID_CACHE: Dict[str, str] = {}


def get_available_animations() -> list[Path]:
    """Retrieve all available animated files (.mp4 or .gif) from assets/animations/."""
    if not ANIMATIONS_DIR.exists():
        return []
    files = list(ANIMATIONS_DIR.glob("*.mp4")) + list(ANIMATIONS_DIR.glob("*.gif"))
    return [f for f in files if f.is_file() and f.stat().st_size > 0]


async def send_download_animation(bot: Bot, chat_id: int) -> Optional[Message]:
    """
    Send a random animated sticker/GIF to entertain the user while downloading/uploading.
    Uses file_id caching for instant delivery and handles rate-limiting / retries safely.
    """
    try:
        animations = get_available_animations()
        if not animations:
            return None

        chosen = random.choice(animations)
        file_key = chosen.name

        # 1. If file_id is cached, deliver immediately (0.05s response time)
        if file_key in _FILE_ID_CACHE:
            try:
                anim_msg = await bot.send_animation(
                    chat_id=chat_id,
                    animation=_FILE_ID_CACHE[file_key],
                    disable_notification=True
                )
                return anim_msg
            except TelegramBadRequest:
                # If cached file_id expired or became invalid, evict from cache
                _FILE_ID_CACHE.pop(file_key, None)
            except TelegramRetryAfter as e:
                wait = float(getattr(e, "retry_after", 1.0))
                if wait <= 2:
                    await asyncio.sleep(wait + 0.2)
                    return await bot.send_animation(
                        chat_id=chat_id,
                        animation=_FILE_ID_CACHE.get(file_key, FSInputFile(str(chosen))),
                        disable_notification=True
                    )
                return None
            except Exception as e:
                logger.debug(f"Failed sending cached animation {file_key}: {e}")

        # 2. Upload file directly
        input_file = FSInputFile(str(chosen))
        try:
            anim_msg = await bot.send_animation(
                chat_id=chat_id,
                animation=input_file,
                disable_notification=True
            )
        except TelegramRetryAfter as e:
            wait = float(getattr(e, "retry_after", 1.0))
            if wait <= 2:
                await asyncio.sleep(wait + 0.2)
                anim_msg = await bot.send_animation(
                    chat_id=chat_id,
                    animation=input_file,
                    disable_notification=True
                )
            else:
                return None

        # 3. Cache the newly generated file_id
        if anim_msg and getattr(anim_msg, "animation", None) and getattr(anim_msg.animation, "file_id", None):
            _FILE_ID_CACHE[file_key] = anim_msg.animation.file_id

        return anim_msg
    except Exception as e:
        logger.warning(f"Could not send download companion animation to {chat_id}: {e}")
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
