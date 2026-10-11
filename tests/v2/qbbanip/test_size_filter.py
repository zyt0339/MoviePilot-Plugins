import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def plugin():
    # 仅加载待测方法，避免宿主初始化和真实外部服务访问。
    path = Path(__file__).resolve().parents[3] / "plugins.v2/qbbanip/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "QBBanIp")
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef)
               and node.name in ("limit_per_downloader", "readable_file_size", "init_plugin")]
    namespace = {"logger": Mock(),
                 "TorrentState": SimpleNamespace(DOWNLOADING="downloading")}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), namespace)
    test_class = type("IsolatedQBBanIp", (), {name: namespace[name] for name in
                      ("limit_per_downloader", "readable_file_size", "init_plugin")})
    instance = test_class()
    instance._nolabels = ""
    instance._tracker_domain = ""
    instance._tracker_ports = "63222"
    instance._nopaths = ""
    return instance


@pytest.mark.parametrize("value", ["0.5", "bad"])
def test_non_integer_threshold_stops_before_downloader_calls(plugin, value):
    plugin._no_torrent_size = value
    client = Mock()
    with pytest.raises(ValueError):
        plugin.limit_per_downloader(client, "mock")
    assert client.mock_calls == []


@pytest.mark.parametrize("value", ["10", 10, "0", 0])
def test_integer_threshold_remains_supported(plugin, value):
    plugin._no_torrent_size = value
    client = Mock()
    client.torrents_info.return_value = []
    plugin.limit_per_downloader(client, "mock")
    client.torrents_info.assert_called_once_with(status_filter="downloading")


def test_init_preserves_numeric_zero(plugin):
    plugin.stop_service = Mock()
    plugin.init_plugin({"no_torrent_size": 0})
    assert plugin._no_torrent_size == 0


@pytest.mark.parametrize("config", [{}, {"no_torrent_size": None},
                                  {"no_torrent_size": ""}, {"no_torrent_size": " \t "}])
def test_empty_threshold_is_zero(plugin, config):
    plugin.stop_service = Mock()
    plugin.init_plugin({"enabled": False, **config})
    client = Mock()
    class Torrent(dict):
        total_size = 1
        tags = ""
        hash = "small-torrent"

    client.torrents_info.return_value = [Torrent(name="mock")]
    client.sync.torrent_peers.return_value.peers = {}
    plugin.limit_per_downloader(client, "mock")
    client.torrents_set_download_limit.assert_called_once_with(
        11 * 1024 ** 2, ["small-torrent"])
