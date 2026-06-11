"""Mount lifecycle — uses rclone+FUSE; tracks state via /proc/self/mounts."""

from __future__ import annotations

import os
from pathlib import Path

from cloud import config, rclone


MOUNTS_FILE = "/proc/self/mounts"

DEFAULT_CACHE_SIZE = "5G"
DEFAULT_CACHE_AGE = "168h"
DEFAULT_MIN_FREE = "80G"
DEFAULT_MODE = "vfs"


def default_mount_path(name: str) -> Path:
    return Path.home() / "clouds" / name


def cache_dir(name: str) -> Path:
    """rclone's default VFS cache root: $XDG_CACHE_HOME/rclone/vfs/<name>."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "rclone" / "vfs" / name


def _parse_proc_mounts(text: str) -> list[tuple[str, Path, str]]:
    """Return list of (device, mountpoint, fstype) for each line."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mountpoint, fstype = parts[0], parts[1], parts[2]
        # /proc/mounts encodes spaces as \040 etc.
        mountpoint = mountpoint.encode().decode("unicode_escape")
        out.append((device, Path(mountpoint), fstype))
    return out


def current_rclone_mounts() -> dict[Path, str]:
    """Map resolved mountpoint → fstype for all live fuse.rclone mounts."""
    try:
        text = Path(MOUNTS_FILE).read_text()
    except FileNotFoundError:
        return {}
    return {mp: fs for _, mp, fs in _parse_proc_mounts(text) if fs.startswith("fuse.rclone")}


def is_mounted(path: Path) -> bool:
    target = path.expanduser().resolve()
    return any(mp.resolve() == target for mp in current_rclone_mounts())


def is_stale(path: Path) -> bool:
    """A mount is stale if /proc/mounts lists it but stat() fails (transport disconnected)."""
    if not is_mounted(path):
        return False
    try:
        path.expanduser().stat()
        return False
    except OSError:
        return True


def cache_size_bytes(path: Path) -> int:
    """Logical file size in bytes. Sparse VFS cache files can make this much larger than disk use."""
    if not path.exists():
        return 0
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            continue
    return total


def cache_disk_bytes(path: Path) -> int:
    """Actual disk blocks consumed by the cache."""
    if not path.exists():
        return 0
    total = 0
    for f in path.rglob("*"):
        try:
            total += f.stat().st_blocks * 512
        except OSError:
            continue
    return total


def mount(
    name: str,
    *,
    mode: str = DEFAULT_MODE,
    mount_path: Path | None = None,
    cache_size: str | None = None,
    cache_age: str | None = None,
    min_free: str | None = None,
    exclude: list[str] | None = None,
) -> tuple[Path, bool]:
    """Mount a configured remote. Returns (resolved mount path, was_already_mounted).

    Tuning params resolve explicit argument > value persisted in config.toml >
    built-in default — so a plain remount never clobbers user-edited config values.

    *exclude* hides remote paths owned by a separate sync engine (``cloud sync``) so
    they are not served twice. When omitted, any ``mount_exclude`` already in the
    remote's config is reused (so a remount preserves it); changing it requires an
    unmount+remount (a live FUSE mount's argv is fixed).
    """
    remote = config.get_remote(name)
    if remote is None:
        raise LookupError(f"no such remote '{name}' (run: cloud account add {name} <url>)")

    if mount_path is None:
        existing = remote.get("mount")
        target = Path(existing).expanduser() if existing else default_mount_path(name)
    else:
        target = Path(mount_path).expanduser()

    if is_mounted(target):
        return target, True

    effective_exclude = exclude if exclude is not None else remote.get("mount_exclude")
    cache_size = cache_size if cache_size is not None else remote.get("vfs_cache", DEFAULT_CACHE_SIZE)
    cache_age = cache_age if cache_age is not None else remote.get("vfs_max_age", DEFAULT_CACHE_AGE)
    min_free = min_free if min_free is not None else remote.get("vfs_min_free", DEFAULT_MIN_FREE)

    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise FileExistsError(f"refusing to mount over non-empty directory: {target}")

    rclone.mount_daemon(name, target, mode, cache_size, cache_age, effective_exclude, min_free=min_free)

    fields: dict[str, str | bool | list] = {
        **remote,
        "mount": str(target).replace(str(Path.home()), "~", 1) if str(target).startswith(str(Path.home())) else str(target),
        "mode": mode,
    }
    if mode == "vfs":
        fields["vfs_cache"] = cache_size
        fields["vfs_max_age"] = cache_age
        fields["vfs_min_free"] = min_free
    if effective_exclude:
        fields["mount_exclude"] = effective_exclude
    config.set_remote(name, **fields)
    return target, False


def unmount(name: str) -> tuple[Path | None, bool]:
    """Release the mount for *name*. Returns (resolved path or None, was_mounted)."""
    remote = config.get_remote(name)
    if remote is None or "mount" not in remote:
        return None, False
    target = Path(remote["mount"]).expanduser()
    if not is_mounted(target):
        return target, False
    rclone.unmount(target)
    return target, True
