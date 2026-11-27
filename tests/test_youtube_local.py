"""Keep local YouTube extraction direct and preserve useful error responses."""
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from services.media_service import MediaService
from services.proxy_manager import YouTubeProxyManager


def test_common_options_do_not_read_chrome_or_force_stale_clients():
    with patch('config.get_cookie_file', return_value=None), patch('config.YTDLP_PROXY', None):
        opts = MediaService()._get_common_ydl_opts(with_cookies=True)
    assert 'cookiesfrombrowser' not in opts
    assert 'extractor_args' not in opts
    assert opts['proxy'] == ''
    assert opts['noplaylist'] is True


def test_explicit_empty_pool_does_not_discover_downloads():
    with patch.object(YouTubeProxyManager, '_discover_proxies') as discover:
        manager = YouTubeProxyManager([])
    discover.assert_not_called()
    assert manager.get_proxies() == []


def test_failed_download_preserves_cause_without_proxies():
    error = RuntimeError('HTTP Error 403: Forbidden')
    with patch('services.media_service.yt_dlp.YoutubeDL') as ydl, patch('services.media_service.youtube_proxy_manager.get_proxies', return_value=[]):
        ydl.return_value.__enter__.return_value.download.side_effect = error
        with pytest.raises(RuntimeError, match='403'):
            MediaService()._sync_download({}, 'https://www.youtube.com/watch?v=m_pdyZUOrJE')


@pytest.mark.asyncio
async def test_timeout_message_is_plain_text_for_html_escaping():
    with patch.object(MediaService, '_sync_extract_info', side_effect=TimeoutError):
        ok, info, error = await MediaService().get_media_info('m_pdyZUOrJE')
    assert not ok and info is None
    assert '<b>' not in error
    assert '45 seconds' in error


@pytest.mark.asyncio
async def test_private_video_classification_is_reachable():
    with patch.object(MediaService, '_sync_extract_info', side_effect=RuntimeError('Private video')):
        ok, info, error = await MediaService().get_media_info('m_pdyZUOrJE')
    assert not ok and info is None
    assert 'Access Restricted' in error
