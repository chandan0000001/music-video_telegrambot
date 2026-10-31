"""
Telegram Music & Video Downloader Bot
Main entrypoint executing long polling on local Linux environment.
"""
import sys
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, ADMIN_IDS, TELEGRAM_API_SERVER
from database import db
from utils.logger import setup_logger
from utils.rate_limiter import ThrottlingMiddleware
from handlers import setup_routers
from services.ffmpeg_service import ffmpeg_service

# Initialize logger
logger = setup_logger("music_bot")


async def on_startup(bot: Bot) -> None:
    """Startup initialization: verify environment, DB, and FFmpeg."""
    logger.info("Initializing Telegram Music & Video Downloader Bot...")

    # 1. Initialize SQLite Database Schema
    try:
        await db.init_db()
        logger.info("Database schema initialized and verified.")
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}")
        raise

    # 2. Check FFmpeg availability
    probe_test = await ffmpeg_service._run_command(["ffmpeg", "-version"], timeout=5)
    if not probe_test[0]:
        logger.warning("FFmpeg binary test failed or FFmpeg is not in system PATH! Please install FFmpeg.")
    else:
        logger.info("FFmpeg engine verified successfully.")

    bot_info = await bot.get_me()
    logger.info(f"Bot connected: @{bot_info.username} (ID: {bot_info.id})")
    logger.info(f"Loaded {len(ADMIN_IDS)} administrator ID(s).")


async def on_shutdown(bot: Bot) -> None:
    """Graceful shutdown: close bot session and cleanup."""
    logger.info("Shutting down bot and closing network sessions...")
    await bot.session.close()
    logger.info("Bot stopped cleanly.")


async def main() -> None:
    """Main application loop."""
    # Check if placeholder token is still present
    if not BOT_TOKEN or BOT_TOKEN in ("PASTE_NEW_REGENERATED_BOT_TOKEN_HERE", "YOUR_BOT_TOKEN_HERE"):
        print("\n" + "=" * 65)
        print("⚠️  BOT TOKEN NOT CONFIGURED")
        print("=" * 65)
        print("Please edit 'config.py' and replace:")
        print("  BOT_TOKEN = \"YOUR_BOT_TOKEN_HERE\"")
        print("with your actual bot token from @BotFather.")
        print("=" * 65 + "\n")
        sys.exit(1)

    # Initialize Bot & Dispatcher with high-throughput network session
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer

    session = None
    if TELEGRAM_API_SERVER:
        server = TelegramAPIServer.from_base(TELEGRAM_API_SERVER)
        session = AiohttpSession(api=server)
        logger.info(f"Using custom Telegram Bot API Server: {TELEGRAM_API_SERVER} (up to 2000 MB uploads)")

    bot = Bot(token=BOT_TOKEN, session=session)
    dp = Dispatcher(storage=MemoryStorage())

    # Register Middlewares
    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())

    # Register Handlers
    root_router = setup_routers()
    dp.include_router(root_router)

    # Register Lifecycle Hooks
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Start Long Polling
    try:
        logger.info("Starting Telegram long polling...")
        # Drop pending updates to avoid processing stale backlog
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )
    except (KeyboardInterrupt, SystemExit):
        logger.info("Execution interrupted by user.")
    except Exception as e:
        logger.critical(f"Critical execution error: {e}")
    finally:
        await on_shutdown(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nProcess terminated.")
