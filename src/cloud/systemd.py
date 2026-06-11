"""Generate, install, enable, and remove user-scope systemd units for cloud mounts."""

from __future__ import annotations

import os
import shlex
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
    min_free: str = mount.DEFAULT_MIN_FREE,
    exclude: list[str] | None = None,
    rclone_bin: str | None = None,
) -> str:
    """Return the systemd unit body for *name*.

    ExecStart uses foreground `rclone mount` so systemd manages the lifecycle.
    *exclude* is threaded into the mount argv so the boot-persistent unit hides the
    same paths as a live ``cloud mount`` (paths owned by ``cloud sync``).
    """
    rclone_bin = rclone_bin or rclone.which() or "/usr/bin/rclone"
    args = rclone.mount_args(name, mount_path, mode, cache_size, cache_age, exclude, min_free=min_free)
    log_dir = Path.home() / ".cache" / "agentic-cloud"
    log_file = log_dir / f"rclone-{name}.log"
    args += [
        "--log-level", "NOTICE",
        "--log-file", str(log_file),
        "--log-file-max-size", "10M",
        "--log-file-max-backups", "3",
    ]
    # First arg is "mount"; drop it because rclone_bin already named.
    cmdline = shlex.join([rclone_bin, *args])
    return f"""[Unit]
Description=cloud {name} (rclone WebDAV mount)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0


[Service]
Type=simple
ExecStartPre=/bin/mkdir -p {log_dir}
ExecStartPre=/bin/sh -c '{shlex.join(["/usr/bin/timeout", "30s", rclone_bin, "lsd", f"{name}:"])} >/dev/null'
ExecStart={cmdline}
ExecStop=/bin/fusermount3 -uz {mount_path}
TimeoutStartSec=60
TimeoutStopSec=15
Restart=always
RestartSec=20
KillMode=control-group

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
        min_free=remote.get("vfs_min_free", mount.DEFAULT_MIN_FREE),
        exclude=remote.get("mount_exclude"),
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


# --- cloud sync timer (rclone bisync of always-local folders) -------------------
# A oneshot service runs `cloud sync run --all`; a timer fires it on an interval so
# the always-local folders stay bidirectionally in sync. PATH must include /snap/bin
# because the `cloud` wrapper shells out to `uv` (installed as a snap on this host).

SYNC_SERVICE = "cloud-sync.service"
SYNC_TIMER = "cloud-sync.timer"
SYNC_WATCH_SERVICE = "cloud-sync-watch.service"
SYNC_PATH_ENV = "%h/.local/bin:/snap/bin:/usr/local/bin:/usr/bin:/bin"


def cloud_bin() -> str:
    found = shutil.which("cloud")
    return found or str(Path.home() / ".local" / "bin" / "cloud")


def sync_service_path() -> Path:
    return units_dir() / SYNC_SERVICE


def sync_timer_path() -> Path:
    return units_dir() / SYNC_TIMER


def render_sync_service(cloud_bin_path: str | None = None) -> str:
    cmd = cloud_bin_path or cloud_bin()
    log_dir = Path.home() / ".cache" / "agentic-cloud"
    return f"""[Unit]
Description=cloud sync (rclone bisync of always-local folders)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=PATH={SYNC_PATH_ENV}
ExecStartPre=/bin/mkdir -p {log_dir}
ExecStart={cmd} sync run --all
# A multi-GB queue upload legitimately runs for many minutes. Killing it
# mid-transfer poisons rclone bisync locks and restarts the transfer from
# byte 0 on the next tick (the 2026-06-11 livelock). Overlap is already
# prevented per-pair by flock, so a long cycle is safe.
TimeoutStartSec=4h
"""


def render_sync_timer(interval: int = 60) -> str:
    return f"""[Unit]
Description=cloud sync timer (bisync every {interval}s)

[Timer]
OnBootSec=2min
OnUnitActiveSec={interval}s
AccuracySec=10s
Persistent=true

[Install]
WantedBy=timers.target
"""


def sync_watch_path() -> Path:
    return units_dir() / SYNC_WATCH_SERVICE


def render_watch_service(cloud_bin_path: str | None = None) -> str:
    """Long-running unit: `cloud sync watch` pushes local changes to remote via inotify."""
    cmd = cloud_bin_path or cloud_bin()
    return f"""[Unit]
Description=cloud sync watch (instant local->remote push via inotify)
After=network-online.target

[Service]
Type=simple
Environment=PATH={SYNC_PATH_ENV}
Environment=PYTHONUNBUFFERED=1
ExecStart={cmd} sync watch
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_watch() -> Path:
    units_dir().mkdir(parents=True, exist_ok=True)
    p = sync_watch_path()
    p.write_text(render_watch_service())
    return p


def enable_watch() -> Path:
    p = install_watch()
    _systemctl("daemon-reload")
    proc = _systemctl("enable", "--now", SYNC_WATCH_SERVICE)
    if proc.returncode != 0:
        raise RuntimeError(f"systemctl enable {SYNC_WATCH_SERVICE} failed: {proc.stderr.strip()}")
    return p


def sync_watch_enabled() -> bool:
    proc = _systemctl("is-enabled", SYNC_WATCH_SERVICE)
    return proc.returncode == 0 and proc.stdout.strip() == "enabled"


def install_sync(interval: int = 60) -> tuple[Path, Path]:
    """Write the sync service + timer unit files. Returns their paths."""
    units_dir().mkdir(parents=True, exist_ok=True)
    svc = sync_service_path()
    tmr = sync_timer_path()
    svc.write_text(render_sync_service())
    tmr.write_text(render_sync_timer(interval))
    return svc, tmr


def enable_sync(interval: int = 60) -> tuple[Path, Path]:
    paths = install_sync(interval)
    _systemctl("daemon-reload")
    proc = _systemctl("enable", "--now", SYNC_TIMER)
    if proc.returncode != 0:
        raise RuntimeError(f"systemctl enable {SYNC_TIMER} failed: {proc.stderr.strip()}")
    return paths


def uninstall_sync() -> bool:
    """Disable + delete the sync timer/service/watch units. Returns True if anything existed."""
    existed = (
        sync_timer_path().exists()
        or sync_service_path().exists()
        or sync_watch_path().exists()
    )
    if not existed:
        return False
    _systemctl("disable", "--now", SYNC_TIMER)
    _systemctl("disable", "--now", SYNC_WATCH_SERVICE)
    sync_timer_path().unlink(missing_ok=True)
    sync_service_path().unlink(missing_ok=True)
    sync_watch_path().unlink(missing_ok=True)
    _systemctl("daemon-reload")
    return True


def sync_timer_enabled() -> bool:
    proc = _systemctl("is-enabled", SYNC_TIMER)
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
