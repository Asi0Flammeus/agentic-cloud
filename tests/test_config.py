from __future__ import annotations

import pytest

from cloud import config


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    yield tmp_path


def test_load_missing_returns_empty():
    data = config.load()
    assert data == {"remotes": {}}


def test_round_trip():
    config.set_remote("crqpt", url="https://nc.example.com/remote.php/dav/files/asi0", mode="vfs")
    config.set_remote("alysis", url="https://alysis.example.com/remote.php/dav/files/asi0", mode="full")

    data = config.load()
    assert set(data["remotes"]) == {"crqpt", "alysis"}
    assert data["remotes"]["crqpt"]["mode"] == "vfs"
    assert data["remotes"]["alysis"]["url"].startswith("https://alysis.")


def test_set_remote_overwrites():
    config.set_remote("crqpt", url="https://old.example.com", mode="full")
    config.set_remote("crqpt", url="https://new.example.com", mode="vfs")

    remote = config.get_remote("crqpt")
    assert remote == {"url": "https://new.example.com", "mode": "vfs"}


def test_remove_remote():
    config.set_remote("crqpt", url="https://nc.example.com", mode="vfs")
    assert config.remove_remote("crqpt") is True
    assert config.get_remote("crqpt") is None
    assert config.remove_remote("crqpt") is False


def test_config_path_respects_xdg(isolated_xdg):
    assert config.config_path() == isolated_xdg / "agentic-cloud" / "config.toml"


def test_file_mode_0600(isolated_xdg):
    config.set_remote("crqpt", url="https://nc.example.com", mode="vfs")
    mode = (isolated_xdg / "agentic-cloud" / "config.toml").stat().st_mode & 0o777
    assert mode == 0o600


def test_sync_pair_round_trip():
    config.set_sync_pair("downloads", local="~/local/Downloads", remote="crqpt:Downloads")
    config.set_sync_pair("vault", local="~/local/nondual-mind", remote="crqpt:nondual-mind", interval=30)

    pairs = config.list_sync_pairs()
    assert {p["label"] for p in pairs} == {"downloads", "vault"}
    vault = config.get_sync_pair("vault")
    assert vault == {"label": "vault", "local": "~/local/nondual-mind", "remote": "crqpt:nondual-mind", "interval": 30, "strategy": "bisync"}
    assert config.get_sync_pair("downloads")["interval"] == 60  # default


def test_sync_pair_upsert_by_label():
    config.set_sync_pair("downloads", local="~/a", remote="crqpt:A")
    config.set_sync_pair("downloads", local="~/b", remote="crqpt:B", interval=120)
    pairs = config.list_sync_pairs()
    assert len(pairs) == 1
    assert pairs[0]["local"] == "~/b" and pairs[0]["interval"] == 120


def test_remove_sync_pair():
    config.set_sync_pair("downloads", local="~/a", remote="crqpt:A")
    assert config.remove_sync_pair("downloads") is True
    assert config.get_sync_pair("downloads") is None
    assert config.remove_sync_pair("downloads") is False


def test_sync_and_remotes_coexist():
    config.set_remote("crqpt", url="https://nc.example.com", mode="vfs")
    config.set_sync_pair("downloads", local="~/local/Downloads", remote="crqpt:Downloads")
    # saving a remote must not drop sync pairs, and vice-versa
    config.set_remote("alysis", url="https://a.example.com", mode="vfs")
    assert config.get_sync_pair("downloads") is not None
    assert set(config.list_remotes()) == {"crqpt", "alysis"}


def test_load_missing_still_has_no_sync_key():
    # existing contract: a missing config returns exactly {"remotes": {}}
    assert config.load() == {"remotes": {}}
    assert config.list_sync_pairs() == []


def test_sync_pair_strategy_persisted_and_defaulted():
    config.set_sync_pair("videos-raw", local="~/local/Videos/raw", remote="crqpt:Videos/raw",
                         strategy="queue")
    pair = config.get_sync_pair("videos-raw")
    assert pair["strategy"] == "queue"
    config.set_sync_pair("downloads", local="~/local/Downloads", remote="crqpt:Downloads")
    assert config.get_sync_pair("downloads")["strategy"] == "bisync"


def test_sync_pair_rejects_unknown_strategy():
    import pytest

    with pytest.raises(ValueError):
        config.set_sync_pair("x", local="~/x", remote="crqpt:x", strategy="yolo")
