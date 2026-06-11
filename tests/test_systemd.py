from __future__ import annotations

from pathlib import Path

import pytest

from cloud import config, systemd


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    yield tmp_path


def test_unit_path_under_xdg(isolated_xdg):
    p = systemd.unit_path("crqpt")
    assert p.name == "cloud-crqpt.service"
    assert "systemd/user" in str(p)


def test_render_unit_vfs():
    body = systemd.render_unit(
        "crqpt",
        mount_path=Path("/home/asi0/clouds/crqpt"),
        mode="vfs",
        cache_size="5G",
        cache_age="168h",
        rclone_bin="/usr/bin/rclone",
    )
    assert "[Unit]" in body
    assert "Description=cloud crqpt" in body
    assert "ExecStartPre=/bin/sh -c '/usr/bin/timeout 30s /usr/bin/rclone lsd crqpt: >/dev/null'" in body
    assert "ExecStart=/usr/bin/rclone mount crqpt: /home/asi0/clouds/crqpt --vfs-cache-mode full --vfs-cache-max-size 5G --vfs-cache-max-age 168h" in body
    assert "--vfs-cache-min-free-space 80G" in body
    assert "--daemon-timeout 20s" in body
    assert "--log-file" in body
    assert "StartLimitIntervalSec=0" in body
    assert "ExecStop=/bin/fusermount3 -uz /home/asi0/clouds/crqpt" in body
    assert "TimeoutStopSec=15" in body
    assert "KillMode=control-group" in body
    assert "WantedBy=default.target" in body
    assert "Type=simple" in body
    assert "Restart=always" in body


def test_render_unit_threads_exclude():
    body = systemd.render_unit(
        "crqpt",
        mount_path=Path("/home/asi0/clouds/crqpt"),
        mode="vfs",
        cache_size="5G",
        cache_age="168h",
        exclude=["/Downloads/", "/Videos/raw/"],
        rclone_bin="/usr/bin/rclone",
    )
    assert "--exclude /Downloads/" in body
    assert "--exclude /Videos/raw/" in body


def test_install_threads_mount_exclude(isolated_xdg, monkeypatch):
    config.set_remote(
        "crqpt", url="https://nc.example.com", mount="~/clouds/crqpt", mode="vfs",
        mount_exclude=["/Downloads/"],
    )
    monkeypatch.setattr("cloud.rclone.which", lambda: "/usr/bin/rclone")
    body = systemd.install("crqpt").read_text()
    assert "--exclude /Downloads/" in body


def test_render_unit_full_mode_omits_vfs_flags():
    body = systemd.render_unit(
        "crqpt",
        mount_path=Path("/tmp/x"),
        mode="full",
        cache_size="5G",
        cache_age="168h",
        rclone_bin="/usr/bin/rclone",
    )
    assert "--vfs-cache-mode" not in body
    assert "--dir-cache-time 30m" in body
    assert "ExecStart=/usr/bin/rclone mount crqpt: /tmp/x" in body


def test_install_requires_mount_field():
    config.set_remote("crqpt", url="https://nc.example.com")
    with pytest.raises(ValueError, match="no mount path"):
        systemd.install("crqpt")


def test_install_writes_unit(isolated_xdg, monkeypatch):
    config.set_remote("crqpt", url="https://nc.example.com", mount="~/clouds/crqpt", mode="vfs")
    monkeypatch.setattr("cloud.rclone.which", lambda: "/usr/bin/rclone")
    target = systemd.install("crqpt")
    assert target.exists()
    body = target.read_text()
    assert "ExecStart=/usr/bin/rclone mount crqpt:" in body


def test_install_unknown_remote():
    with pytest.raises(LookupError):
        systemd.install("ghost")


def test_render_sync_service():
    body = systemd.render_sync_service("/home/asi0/.local/bin/cloud")
    assert "Type=oneshot" in body
    assert "ExecStart=/home/asi0/.local/bin/cloud sync run --all" in body
    # /snap/bin must be on PATH so the wrapper's `uv` resolves under systemd
    assert "/snap/bin" in body
    assert "Environment=PATH=" in body


def test_render_sync_timer():
    body = systemd.render_sync_timer(60)
    assert "[Timer]" in body
    assert "OnUnitActiveSec=60s" in body
    assert "Persistent=true" in body
    assert "WantedBy=timers.target" in body


def test_install_sync_writes_both_units(isolated_xdg):
    svc, tmr = systemd.install_sync(90)
    assert svc.name == "cloud-sync.service" and svc.exists()
    assert tmr.name == "cloud-sync.timer" and tmr.exists()
    assert "OnUnitActiveSec=90s" in tmr.read_text()


def test_render_watch_service():
    body = systemd.render_watch_service("/home/asi0/.local/bin/cloud")
    assert "Type=simple" in body
    assert "ExecStart=/home/asi0/.local/bin/cloud sync watch" in body
    assert "Restart=always" in body
    assert "/snap/bin" in body


def test_install_watch_writes_unit(isolated_xdg):
    p = systemd.install_watch()
    assert p.name == "cloud-sync-watch.service" and p.exists()
    assert "sync watch" in p.read_text()


def test_sync_service_survives_long_transfers():
    body = systemd.render_sync_service("/home/asi0/.local/bin/cloud")
    # the 2026-06-11 livelock: a 300s kill mid-upload poisons bisync locks
    assert "TimeoutStartSec=4h" in body
    assert "TimeoutStartSec=300" not in body
