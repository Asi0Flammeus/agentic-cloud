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
    assert "ExecStart=/usr/bin/rclone mount crqpt: /home/asi0/clouds/crqpt --vfs-cache-mode full --vfs-cache-max-size 5G --vfs-cache-max-age 168h" in body
    assert "ExecStop=/bin/fusermount3 -u /home/asi0/clouds/crqpt" in body
    assert "WantedBy=default.target" in body
    assert "Type=simple" in body


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
