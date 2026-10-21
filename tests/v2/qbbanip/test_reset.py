from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_size_filter import plugin


@pytest.mark.parametrize("kind", ["file", "directory", "missing"])
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
        plugin._reset_torrent_data.assert_called_once_with(client, torrent)
    client.torrents_delete.assert_not_called()
    client.torrents_add.assert_not_called()
