"""Regression coverage for runtime failures missed by syntax checks."""
import asyncio
import importlib
import inspect
import typing
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery
from database import Database
from handlers.audio import cb_choose_audio_bitrate, cb_start_audio_download
from handlers.video import cb_start_video_download
from services.ffmpeg_service import FFmpegService
from utils.callback_data import parse_callback


@pytest.mark.parametrize('module_name', [
    'config', 'database', 'handlers.search', 'handlers.video',
    'handlers.instagram', 'utils.progress',
])
def test_annotations_resolve_without_missing_imports(module_name):
    module = importlib.import_module(module_name)
    typing.get_type_hints(module)
    for obj in vars(module).values():
        if inspect.isfunction(obj) and obj.__module__ == module_name:
            typing.get_type_hints(obj)
    if module_name == 'database':
        typing.get_type_hints(Database.atomic_claim_and_mark_paid)


@pytest.mark.asyncio
@pytest.mark.parametrize('data', [None, '', 'adl', 'adl:video', 'adl:video:mp3', 'adl::mp3:128', 'adl:video:mp3:128:extra'])
async def test_invalid_callback_returns_notice(data):
    callback = MagicMock(spec=CallbackQuery)
    callback.data = data
    callback.answer = AsyncMock()
    assert await parse_callback(callback, 'adl', 4) is None
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('handler,data', [
    (cb_choose_audio_bitrate, 'afmt:video'),
    (cb_choose_audio_bitrate, 'afmt:video:exe'),
    (cb_start_audio_download, 'adl:video:mp3'),
    (cb_start_audio_download, 'adl:video:mp3:garbage'),
    (cb_start_video_download, 'vdl:video'),
])
async def test_handlers_reject_bad_callbacks_before_work(handler, data):
    callback = MagicMock(spec=CallbackQuery)
    callback.data = data
    callback.answer = AsyncMock()
    await handler(callback)
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_ffmpeg_timeout_reaps_process():
    process = MagicMock()
    process.communicate = AsyncMock(side_effect=[asyncio.TimeoutError, (b'', b'')])
    with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=process)):
        ok, error = await FFmpegService()._run_command(['ffmpeg', '-version'], timeout=1)
    assert not ok and 'timed out' in error
    process.kill.assert_called_once()
    assert process.communicate.await_count == 2


@pytest.mark.asyncio
async def test_ffmpeg_cancellation_reaps_process_and_propagates():
    process = MagicMock()
    process.communicate = AsyncMock(side_effect=[asyncio.CancelledError, (b'', b'')])
    with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=process)):
        with pytest.raises(asyncio.CancelledError):
            await FFmpegService()._run_command(['ffmpeg', '-version'])
    process.kill.assert_called_once()
    assert process.communicate.await_count == 2


@pytest.mark.asyncio
async def test_missing_binary_is_reported():
    with patch('asyncio.create_subprocess_exec', AsyncMock(side_effect=FileNotFoundError('missing binary'))):
        ok, error = await FFmpegService()._run_command(['missing-binary'])
    assert not ok and 'missing binary' in error


@pytest.mark.asyncio
async def test_invalid_subscription_date_does_not_crash_or_grant_access(tmp_path):
    db = Database(tmp_path / 'subscriptions.sqlite3')
    await db.init_db()
    await db.add_or_update_user(123, 'test', 'Test')
    await db.activate_subscription(123, 'go')
    async with db._get_connection() as conn:
        await conn.execute("UPDATE user_subscriptions SET expiry_date = 'broken' WHERE telegram_id = 123")
        await conn.commit()
    sub = await db.get_user_subscription(123)
    assert not sub['is_active']
    assert sub['invalid_expiry']


@pytest.mark.asyncio
async def test_shutdown_closes_session_if_uploader_stop_fails():
    from bot import on_shutdown
    bot = MagicMock()
    bot.session.close = AsyncMock()
    with patch('bot.mtproto_service.stop', AsyncMock(side_effect=RuntimeError('stop failed'))):
        with pytest.raises(RuntimeError, match='stop failed'):
            await on_shutdown(bot)
    bot.session.close.assert_awaited_once()


def test_music_search_reaches_extractor_without_missing_path_import():
    from services.music_service import music_service
    with patch('config.get_cookie_file', return_value=None), patch('services.music_service.yt_dlp.YoutubeDL') as ydl:
        ydl.return_value.__enter__.return_value.extract_info.return_value = {"entries": []}
        assert music_service._sync_search("test song") == []
        ydl.return_value.__enter__.return_value.extract_info.assert_called_once()


def test_numeric_settings_use_fallbacks():
    from config import _safe_int
    assert _safe_int("not-a-number", 3) == 3
    assert _safe_int(" 12 ", 3) == 12
    assert _safe_int(None, 3) == 3
