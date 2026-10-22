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
               and node.name in ("limit_per_downloader", "readable_file_size", "init_plugin", "get_form", "__update_config", "_reset_torrent_data", "_map_cleanup_path", "limit")]
    namespace = {"logger": Mock(), "os": __import__("os"),
                 "shutil": __import__("shutil"), "time": Mock(), "Path": Path,
                 "TorrentState": SimpleNamespace(DOWNLOADING="downloading")}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), namespace)
    test_class = type("IsolatedQBBanIp", (), {name: namespace[name] for name in
                      ("limit_per_downloader", "readable_file_size", "init_plugin", "get_form", "__update_config", "_reset_torrent_data", "_map_cleanup_path", "limit")})
    instance = test_class()
    instance._nolabels = ""
    instance._tracker_domain = ""
    instance._tracker_ports = "63222"
    instance._ip_whitelist = "159.195.49.141"
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


@pytest.mark.parametrize("value, expected", [
    ({}, "159.195.49.141"),
    ({"ip_whitelist": ""}, ""),
    ({"ip_whitelist": "192.0.2.1,192.0.2.2"}, "192.0.2.1,192.0.2.2"),
])
def test_ip_whitelist_init_and_persistence(plugin, value, expected):
    plugin.stop_service = Mock()
    plugin.init_plugin({"enabled": False, **value})
    plugin.update_config = Mock()
    plugin.__update_config()
    assert plugin._ip_whitelist == expected
    assert plugin.update_config.call_args.args[0]["ip_whitelist"] == expected
    plugin.init_plugin({"enabled": False})
    assert plugin._ip_whitelist == "159.195.49.141"


def test_ip_whitelist_form_order_and_default(plugin):
    plugin.get_form.__globals__["DownloaderHelper"] = Mock()
    plugin.get_form.__globals__["DownloaderHelper"].return_value.get_configs.return_value = {}
    form, defaults = plugin.get_form()
    models = []

    def walk(nodes):
        for node in nodes:
            if "model" in node.get("props", {}):
                models.append(node["props"]["model"])
            walk(node.get("content", []))

    walk(form)
    index = models.index("tracker_ports")
    assert models[index:index + 3] == ["tracker_ports", "ip_whitelist", "nopaths"]
    assert defaults["ip_whitelist"] == "159.195.49.141"


@pytest.mark.parametrize("whitelist, expected", [
    ("159.195.49.141", {"192.0.2.2", "192.0.2.3"}),
    (" , 159.195.49.141, ,192.0.2.2,", {"192.0.2.3"}),
    ("", {"159.195.49.141", "192.0.2.2", "192.0.2.3"}),
    ("159.195.49.14", {"159.195.49.141", "192.0.2.2", "192.0.2.3"}),
    ("159.195.49.141,192.0.2.2,192.0.2.3", set()),
])
def test_peer_whitelists_before_blocklist_update(plugin, whitelist, expected):
    plugin._ip_whitelist = whitelist
    plugin._no_torrent_size = 0
    client = Mock()

    class Torrent(dict):
        total_size = 1
        tags = ""
        hash = "mock-torrent"

    client.torrents_info.return_value = [Torrent(name="mock")]
    client.sync.torrent_peers.return_value.peers = {
        str(i): {"ip": ip, "port": port}
        for i, (ip, port) in enumerate([
            ("159.195.49.141", 1000), ("192.0.2.1", 63222),
            ("192.0.2.2", 1000), ("192.0.2.3", 1000),
        ])
    }
    client.app_preferences.return_value = {"banned_IPs": "192.0.2.100"}
    plugin.limit_per_downloader(client, "mock")
    if expected:
        client.app.set_preferences.assert_called_once()
        actual = client.app.set_preferences.call_args.args[0]["banned_IPs"]
        assert set(actual.splitlines()) == expected | {"192.0.2.100"}
    else:
        client.app_preferences.assert_not_called()
        client.app.set_preferences.assert_not_called()
    client.torrents_set_download_limit.assert_called_once()
    client.torrents_delete.assert_not_called()
