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
