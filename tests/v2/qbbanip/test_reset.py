from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_size_filter import plugin


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_reset_keeps_original_task(plugin, tmp_path, kind):
    target = tmp_path / "payload"
    if kind == "file":
        target.write_bytes(b"test")
    elif kind == "directory":
        target.mkdir()
        (target / "part").write_bytes(b"test")
    class Torrent(dict):
        hash = "target"
        save_path = str(tmp_path)
        name = "payload"
    torrent = Torrent(name="payload")
    client = Mock()
    timeline = []
    def pause(**kwargs):
        timeline.append("stop")
    def recheck(**kwargs):
        assert not target.exists()
        timeline.append("recheck")
    client.torrents_pause.side_effect = pause
    client.torrents_recheck.side_effect = recheck
    client.torrents_resume.side_effect = lambda **kwargs: timeline.append("resume")
    plugin._reset_torrent_data.__globals__["time"].sleep.side_effect = lambda n: timeline.append(n)
    plugin._reset_torrent_data(client, torrent)
    assert timeline == ["stop", 2, 5, "recheck", 5, "resume", 2]
    client.torrents_pause.assert_called_once_with(torrent_hashes="target")
    client.torrents_recheck.assert_called_once_with(torrent_hashes="target")
    client.torrents_resume.assert_called_once_with(torrent_hashes="target")
    client.torrents_delete.assert_not_called()
    client.torrents_add.assert_not_called()
    client.torrents_reannounce.assert_not_called()


def test_cleanup_failure_does_not_crash_or_resume(plugin, tmp_path):
    client = Mock()
    torrent = SimpleNamespace(hash="target", save_path=str(tmp_path), name="payload")
    client.torrents_pause.side_effect = RuntimeError("offline")
    plugin._reset_torrent_data(client, torrent)
    client.torrents_recheck.assert_not_called()
    client.torrents_resume.assert_not_called()


@pytest.mark.parametrize("selected, progress, calls", [(True, .81, 1), (True, .8, 0), (False, .9, 0)])
def test_scan_dispatches_selected_hash_above_threshold(plugin, selected, progress, calls):
    plugin._no_torrent_size = 0
    plugin._nopaths = "target" if selected else "other"
    plugin._reset_torrent_data = Mock()
    class Torrent(dict):
        hash = "target"
        tags = ""
        total_size = 1
    torrent = Torrent(name="mock")
    torrent.progress = progress
    client = Mock()
    client.torrents_info.return_value = [torrent]
    client.sync.torrent_peers.return_value.peers = {}
    plugin.limit_per_downloader(client, "mock")
    assert plugin._reset_torrent_data.call_count == calls
    if calls:
        plugin._reset_torrent_data.assert_called_once_with(client, torrent, path_mapper=None)
    client.torrents_delete.assert_not_called()
    client.torrents_add.assert_not_called()


def test_missing_path_falls_back_to_qb_delete_and_readd(plugin, tmp_path):
    class Torrent(dict):
        hash = "target"
        save_path = str(tmp_path)
        name = "missing"
        category = "mock-category"
        tags = "mock-tag"
    torrent = Torrent(name="missing")
    client = Mock()
    client.torrents_export.return_value = b"mock-torrent"
    plugin._reset_torrent_data(client, torrent)
    names = [call[0] for call in client.mock_calls]
    assert names == ["torrents_pause", "torrents_export",
                     "torrents_delete", "torrents_add"]
    client.torrents_reannounce.assert_not_called()
    client.torrents_delete.assert_called_once_with(torrent_hashes="target", delete_files=True)
    client.torrents_add.assert_called_once_with(
        torrent_files=b"mock-torrent", save_path=str(tmp_path), category="mock-category", tags="mock-tag")
    client.torrents_recheck.assert_not_called()
    client.torrents_resume.assert_not_called()


@pytest.mark.parametrize("export_result", [None, b""])
def test_empty_export_never_deletes_task(plugin, tmp_path, export_result):
    torrent = SimpleNamespace(hash="target", save_path=str(tmp_path), name="missing")
    client = Mock()
    client.torrents_export.return_value = export_result
    plugin._reset_torrent_data(client, torrent)
    client.torrents_delete.assert_not_called()
    client.torrents_add.assert_not_called()
    client.torrents_recheck.assert_not_called()


def test_export_error_never_deletes_task(plugin, tmp_path):
    torrent = SimpleNamespace(hash="target", save_path=str(tmp_path), name="missing")
    client = Mock()
    client.torrents_export.side_effect = RuntimeError("mock export failure")
    plugin._reset_torrent_data(client, torrent)
    client.torrents_delete.assert_not_called()
    client.torrents_add.assert_not_called()


@pytest.mark.parametrize("kind", ["file", "directory", "missing"])
def test_mapped_path_used_for_cleanup_and_original_path_for_readd(plugin, tmp_path, kind):
    local = tmp_path / "mapped-data"
    local.mkdir()
    target = local / "payload"
    if kind == "file":
        target.write_bytes(b"mock")
    elif kind == "directory":
        target.mkdir()
        (target / "part").write_bytes(b"mock")
    class Torrent(dict):
        hash = "target"
        save_path = "/downloads"
        name = "payload"
        category = "mock-category"
        tags = "mock-tag"
    torrent = Torrent(name="payload")
    client = Mock()
    client.torrents_export.return_value = b"mock-torrent"
    mapper = Mock(return_value=str(target))
    plugin._reset_torrent_data(client, torrent, path_mapper=mapper)
    mapper.assert_called_once_with("/downloads/payload")
    assert not target.exists()
    if kind == "missing":
        client.torrents_delete.assert_called_once_with(torrent_hashes="target", delete_files=True)
        assert client.torrents_add.call_args.kwargs["save_path"] == "/downloads"
        client.torrents_recheck.assert_not_called()
    else:
        client.torrents_delete.assert_not_called()
        client.torrents_add.assert_not_called()
        client.torrents_recheck.assert_called_once_with(torrent_hashes="target")


def test_current_downloader_mapping_reaches_scan(plugin):
    module = Mock()
    module.get_config.return_value = SimpleNamespace(path_mapping=[("/mock/storage", "/downloads")])
    module.normalize_return_path.return_value = "/mock/storage/payload"
    client = Mock()
    plugin._downloaders1 = ["selected-qb"]
    plugin.get_downloader_service_infos = Mock(return_value={"selected-qb": SimpleNamespace(
        name="selected-qb", type="qbittorrent", instance=SimpleNamespace(qbc=client), module=module)})
    plugin.limit_per_downloader = Mock()
    plugin.limit()
    mapper = plugin.limit_per_downloader.call_args.kwargs["path_mapper"]
    assert mapper("/downloads/payload") == "/mock/storage/payload"
    from pathlib import Path
    module.normalize_return_path.assert_called_once_with(Path("/downloads/payload"), "selected-qb")
    module.get_config.assert_called_once_with("selected-qb")


@pytest.mark.parametrize("source, expected", [
    ("/downloads/payload", "/mock/storage/payload"),
    ("/downloads/nested/payload", "/mock/storage/nested/payload"),
    ("/downloads2/payload", "/downloads2/payload"),
])
def test_host_return_path_mapping_contract(plugin, source, expected):
    # 直接验证本地宿主的映射方法，不启动宿主或读取真实运行配置。
    import ast
    from pathlib import Path
    from typing import Optional, Union
    host_path = Path(__file__).resolve().parents[4] / "MoviePilot/app/modules/__init__.py"
    tree = ast.parse(host_path.read_text())
    host_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_DownloaderBase")
    methods = [n for n in host_class.body if isinstance(n, ast.FunctionDef)
               and n.name in ("__replace_path_prefix", "normalize_return_path")]
    isolated = ast.ClassDef(name="IsolatedDownloader", bases=[], keywords=[], body=methods, decorator_list=[])
    namespace = {"Path": Path, "Optional": Optional, "Union": Union}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[isolated], type_ignores=[])), str(host_path), "exec"), namespace)
    module = namespace["IsolatedDownloader"]()
    config = SimpleNamespace(path_mapping=[("local:/mock/storage", "/downloads")])
    module.get_config = Mock(return_value=config)
    service = SimpleNamespace(module=module, name="mock-qb", config=config)
    assert plugin._map_cleanup_path(source, service) == expected
    module.get_config.assert_called_with("mock-qb")
