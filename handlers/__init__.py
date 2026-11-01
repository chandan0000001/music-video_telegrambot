"""Handlers package combining all feature routers."""
from aiogram import Router

from handlers.start import router as start_router
from handlers.ytmusic import router as ytmusic_router
from handlers.search import router as search_router
from handlers.audio import router as audio_router
from handlers.video import router as video_router
from handlers.history import router as history_router
from handlers.admin import router as admin_router


def setup_routers() -> Router:
    """Combine and order all feature routers under main root router."""
    root_router = Router(name="root_router")
    root_router.include_router(admin_router)
    root_router.include_router(start_router)
    root_router.include_router(ytmusic_router)
    root_router.include_router(history_router)
    root_router.include_router(audio_router)
    root_router.include_router(video_router)
    root_router.include_router(search_router)  # search_router handles fallback text, so include last
    return root_router


__all__ = ["setup_routers"]
