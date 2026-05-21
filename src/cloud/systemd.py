"""Generate, install, enable, and remove user-scope systemd units for cloud mounts."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from cloud import config, mount, rclone


def units_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "systemd" / "user"


def unit_name(name: str) -> str:
    return f"cloud-{name}.service"


def unit_path(name: str) -> Path:
    return units_dir() / unit_name(name)


def render_unit(
    name: str,
    *,
    mount_path: Path,
    mode: str,
    cache_size: str,
    cache_age: str,
    rclone_bin: str | None = None,
) -> str:
    """Return the systemd unit body for *name*.

    ExecStart uses foreground `rclone mount` so systemd manages the lifecycle.
    """
    rclone_bin = rclone_bin or rclone.which() or "/usr/bin/rclone"
    args = rclone.mount_args(name, mount_path, mode, cache_size, cache_age)
    # First arg is "mount"; drop it because rclone_bin already named.
    cmdline = " ".join([rclone_bin, *args])
    return f"""[Unit]
Description=cloud {name} (rclone WebDAV mount)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={cmdline}
ExecStop=/bin/fusermount3 -u {mount_path}
Restart=on-failure
RestartSec=5
KillMode=process

[Install]
WantedBy=default.target
"""


def install(name: str) -> Path:
    """Write the unit file. Requires the remote to be configured with mount/mode/etc."""
    remote = config.get_remote(name)
    if remote is None:
        raise LookupError(f"no such remote '{name}'")
    if "mount" not in remote:
        raise ValueError(f"remote '{name}' has no mount path — run `cloud mount {name}` first")
    body = render_unit(
        name,
        mount_path=Path(remote["mount"]).expanduser(),
        mode=remote.get("mode", mount.DEFAULT_MODE),
        cache_size=remote.get("vfs_cache", mount.DEFAULT_CACHE_SIZE),
        cache_age=remote.get("vfs_max_age", mount.DEFAULT_CACHE_AGE),
    )
    target = unit_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    return target


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    bin_ = shutil.which("systemctl")
    if bin_ is None:
        raise RuntimeError("systemctl not on PATH")
    return subprocess.run([bin_, "--user", *args], capture_output=True, text=True, check=False)


def enable(name: str) -> None:
    proc = _systemctl("daemon-reload")
    proc = _systemctl("enable", unit_name(name))
    if proc.returncode != 0:
        raise RuntimeError(f"systemctl enable failed: {proc.stderr.strip()}")


def disable(name: str) -> None:
    _systemctl("disable", unit_name(name))


def uninstall(name: str) -> bool:
    """Disable + delete unit file. Returns True if a unit existed."""
    p = unit_path(name)
    if not p.exists():
        return False
    disable(name)
    p.unlink()
    _systemctl("daemon-reload")
    return True


def install_and_enable(name: str) -> Path:
    target = install(name)
    enable(name)
    return target


def is_enabled(name: str) -> bool:
    proc = _systemctl("is-enabled", unit_name(name))
    return proc.returncode == 0 and proc.stdout.strip() == "enabled"


def lingering_enabled() -> bool:
    """`loginctl show-user $USER -p Linger` returns `Linger=yes` if linger is on."""
    bin_ = shutil.which("loginctl")
    if bin_ is None:
        return False
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not user:
        return False
    proc = subprocess.run(
        [bin_, "show-user", user, "-p", "Linger"], capture_output=True, text=True, check=False
    )
    return "Linger=yes" in proc.stdout
