"""ZYT媒体库服务器通知插件的TMDB ID解析测试。"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


PLUGIN_FILE = Path(__file__).parents[3] / "plugins.v2" / "zytmediaservermsg" / "__init__.py"
SPEC = importlib.util.spec_from_file_location("zytmediaservermsg_test_plugin", PLUGIN_FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ZYTMediaServerMsg = MODULE.ZYTMediaServerMsg


def _emby_episode():
    """构造携带单集TMDB ID的Emby事件。"""
    return SimpleNamespace(
        channel="emby",
        server_name="emby",
        media_type="Episode",
        item_id="111876",
    )


def test_emby_episode_uses_series_tmdb_id():
    """Emby单集事件应通过SeriesId取得电视剧TMDB ID。"""
    plugin = object.__new__(ZYTMediaServerMsg)
    service = MagicMock()
    service.get_iteminfo.return_value = SimpleNamespace(tmdbid=261676)
    plugin.service_info = MagicMock(return_value=SimpleNamespace(instance=service))

    result = plugin._resolve_tv_tmdb_id_by_zyt(_emby_episode(), "5532863")

    assert result == "261676"
    service.get_iteminfo.assert_called_once_with("111876")


def test_emby_episode_does_not_reuse_episode_tmdb_id_when_series_lookup_fails():
    """电视剧条目查询失败时不得继续把单集TMDB ID当作电视剧ID。"""
    plugin = object.__new__(ZYTMediaServerMsg)
    plugin.service_info = MagicMock(return_value=None)

    result = plugin._resolve_tv_tmdb_id_by_zyt(_emby_episode(), "5532863")

    assert result is None


def test_non_emby_event_keeps_original_tmdb_id():
    """非Emby事件应保持原有TMDB ID处理逻辑。"""
    plugin = object.__new__(ZYTMediaServerMsg)
    event_info = SimpleNamespace(channel="jellyfin", media_type="Episode")

    result = plugin._resolve_tv_tmdb_id_by_zyt(event_info, "12345")

    assert result == "12345"
