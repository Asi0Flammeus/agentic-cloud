from __future__ import annotations

from pathlib import Path

import pytest

from cloud import config, mount


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    yield tmp_path


def test_parse_proc_mounts():
    fixture = (
        "/dev/sda1 / ext4 rw,relatime 0 0\n"
        "crqpt: /home/asi0/clouds/crqpt fuse.rclone rw,nosuid,nodev 0 0\n"
        "alysis: /home/asi0/clouds/alysis fuse.rclone rw,nosuid,nodev 0 0\n"
        "tmpfs /tmp tmpfs rw,nosuid 0 0\n"
    )
    parsed = mount._parse_proc_mounts(fixture)
    fuse = [(d, p, f) for d, p, f in parsed if f.startswith("fuse.rclone")]
    assert len(fuse) == 2
    assert fuse[0][1] == Path("/home/asi0/clouds/crqpt")
    assert fuse[1][1] == Path("/home/asi0/clouds/alysis")


def test_current_rclone_mounts_reads_file(tmp_path, monkeypatch):
    fixture = tmp_path / "fake-mounts"
    fixture.write_text(
        "crqpt: /home/asi0/clouds/crqpt fuse.rclone rw 0 0\n"
        "/dev/sda1 / ext4 rw 0 0\n"
    )
    monkeypatch.setattr(mount, "MOUNTS_FILE", str(fixture))
    mounts = mount.current_rclone_mounts()
    assert Path("/home/asi0/clouds/crqpt") in mounts
    assert all("fuse.rclone" in v for v in mounts.values())


def test_cache_size_bytes(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 250)
    assert mount.cache_size_bytes(tmp_path) == 350


def test_cache_size_bytes_missing_dir(tmp_path):
    assert mount.cache_size_bytes(tmp_path / "does-not-exist") == 0


def test_cache_disk_bytes_missing_dir(tmp_path):
    assert mount.cache_disk_bytes(tmp_path / "does-not-exist") == 0


def test_cache_disk_bytes_counts_allocated_blocks(tmp_path):
    file = tmp_path / "sparse.bin"
    with file.open("wb") as f:
        f.truncate(10 * 1024 * 1024)
    assert mount.cache_size_bytes(tmp_path) == 10 * 1024 * 1024
    assert mount.cache_disk_bytes(tmp_path) < mount.cache_size_bytes(tmp_path)


def test_mount_unknown_remote_raises():
    with pytest.raises(LookupError):
        mount.mount("ghost")


def test_unmount_unknown_remote_returns_false():
    target, was = mount.unmount("ghost")
    assert (target, was) == (None, False)


def test_mount_writes_toml_fields(tmp_path, monkeypatch):
    config.set_remote("crqpt", url="https://nc.example.com")
    # Force is_mounted to False so we proceed to mount_daemon
    monkeypatch.setattr(mount, "is_mounted", lambda p: False)
    calls = []
    monkeypatch.setattr(
        "cloud.rclone.mount_daemon",
        lambda name, mp, mode, cs, ca, exclude=None, min_free=None: calls.append((name, mp, mode, cs, ca, exclude)),
    )
    target, already = mount.mount("crqpt", mode="vfs", cache_size="2G", cache_age="48h")
    assert already is False
    assert calls == [("crqpt", target, "vfs", "2G", "48h", None)]
    remote = config.get_remote("crqpt")
    assert remote["mode"] == "vfs"
    assert remote["vfs_cache"] == "2G"
    assert remote["vfs_max_age"] == "48h"
    assert "mount" in remote


def test_mount_threads_exclude_and_persists(monkeypatch):
    config.set_remote("crqpt", url="https://nc.example.com")
    monkeypatch.setattr(mount, "is_mounted", lambda p: False)
    calls = []
    monkeypatch.setattr(
        "cloud.rclone.mount_daemon",
        lambda name, mp, mode, cs, ca, exclude=None, min_free=None: calls.append(exclude),
    )
    mount.mount("crqpt", mode="vfs", exclude=["/Downloads/", "/Videos/raw/"])
    assert calls == [["/Downloads/", "/Videos/raw/"]]
    # persisted so a later remount reuses it
    assert config.get_remote("crqpt")["mount_exclude"] == ["/Downloads/", "/Videos/raw/"]


def test_mount_reuses_persisted_exclude_when_not_passed(monkeypatch):
    config.set_remote("crqpt", url="https://nc.example.com", mount_exclude=["/Pictures/"])
    monkeypatch.setattr(mount, "is_mounted", lambda p: False)
    calls = []
    monkeypatch.setattr(
        "cloud.rclone.mount_daemon",
        lambda name, mp, mode, cs, ca, exclude=None, min_free=None: calls.append(exclude),
    )
    mount.mount("crqpt", mode="vfs")  # no exclude passed
    assert calls == [["/Pictures/"]]


def test_mount_idempotent_when_already_mounted(monkeypatch):
    config.set_remote("crqpt", url="https://nc.example.com", mount="~/clouds/crqpt", mode="vfs")
    monkeypatch.setattr(mount, "is_mounted", lambda p: True)
    called = []
    monkeypatch.setattr("cloud.rclone.mount_daemon", lambda *a, **k: called.append(a))
    target, already = mount.mount("crqpt")
    assert already is True
    assert called == []  # rclone NOT invoked


def test_default_mount_path():
    assert mount.default_mount_path("alysis").name == "alysis"
    assert mount.default_mount_path("alysis").parent.name == "clouds"


def test_mount_preserves_config_tuning_when_not_passed(monkeypatch):
    # The 2026-05-23 clobber bug: a remount without explicit flags must reuse
    # the user-edited config values, not silently revert to defaults.
    config.set_remote("crqpt", url="https://nc.example.com", vfs_cache="50G", vfs_max_age="120h")
    monkeypatch.setattr(mount, "is_mounted", lambda p: False)
    calls = []
    monkeypatch.setattr(
        "cloud.rclone.mount_daemon",
        lambda name, mp, mode, cs, ca, exclude=None, min_free=None: calls.append((cs, ca, min_free)),
    )
    mount.mount("crqpt", mode="vfs")  # no tuning flags passed
    assert calls == [("50G", "120h", mount.DEFAULT_MIN_FREE)]
    remote = config.get_remote("crqpt")
    assert remote["vfs_cache"] == "50G"
    assert remote["vfs_max_age"] == "120h"


def test_mount_explicit_flags_override_config(monkeypatch):
    config.set_remote("crqpt", url="https://nc.example.com", vfs_cache="50G", vfs_min_free="10G")
    monkeypatch.setattr(mount, "is_mounted", lambda p: False)
    calls = []
    monkeypatch.setattr(
        "cloud.rclone.mount_daemon",
        lambda name, mp, mode, cs, ca, exclude=None, min_free=None: calls.append((cs, min_free)),
    )
    mount.mount("crqpt", mode="vfs", cache_size="2G")
    assert calls == [("2G", "10G")]  # explicit beats config; config beats default
    assert config.get_remote("crqpt")["vfs_cache"] == "2G"
