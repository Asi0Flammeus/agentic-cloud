"""End-to-end diagnostics. Run via ``cloud doctor``."""

from __future__ import annotations

import shutil
from pathlib import Path

from cloud import config, mount, rclone


OK = "✓"
FAIL = "✗"
WARN = "⚠"


def _parse_size_to_bytes(s: str) -> int | None:
    """Parse strings like '5G', '500M', '1024K' into bytes. Returns None on failure."""
    s = s.strip().upper()
    if not s:
        return None
    suffix = s[-1]
    multiplier = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}.get(suffix)
    if multiplier is None:
        try:
            return int(s)
        except ValueError:
            return None
    try:
        return int(float(s[:-1]) * multiplier)
    except ValueError:
        return None


def run() -> int:
    failed = False
    remotes = config.list_remotes()
    any_mount_configured = any("mount" in r for r in remotes.values())

    # rclone
    rclone_path = rclone.which()
    if rclone_path:
        try:
            ver = rclone.version()
            print(f"{OK} rclone present     {ver} ({rclone_path})")
        except rclone.RcloneError as e:
            print(f"{FAIL} rclone broken       {e.stderr}")
            failed = True
    else:
        print(f"{FAIL} rclone missing      install: https://rclone.org/install/")
        failed = True

    # fusermount — hard-fail iff any account has been mounted
    fuse = shutil.which("fusermount3") or shutil.which("fusermount")
    if fuse:
        print(f"{OK} fusermount         {fuse}")
    elif any_mount_configured:
        print(f"{FAIL} fusermount missing  needed for mounts; install: apt install fuse3")
        failed = True
    else:
        print(f"{WARN} fusermount missing  needed for VFS mounts (no mounts configured yet)")

    # config dir
    cfg_path = config.config_path()
    cfg_dir = cfg_path.parent
    if cfg_dir.exists():
        print(f"{OK} config dir         {cfg_dir}")
    else:
        print(f"{WARN} config dir         {cfg_dir} (will be created on first `account add`)")

    # rclone config
    rclone_cfg = config.rclone_config_path()
    if rclone_cfg.exists():
        print(f"{OK} rclone config      {rclone_cfg}")
    else:
        print(f"{WARN} rclone config      {rclone_cfg} (will be created on first `account add`)")

    # remotes
    if not remotes:
        print(f"{WARN} no remotes configured  run: cloud account add <name> <webdav-url>")
        return 1 if failed else 0

    print()
    print(f"Probing {len(remotes)} remote(s):")
    for name, fields in remotes.items():
        # Reachability
        if not rclone.has_remote(name):
            print(f"  {FAIL} {name}  missing from rclone.conf (re-run `account add`)")
            failed = True
            continue
        try:
            rclone.lsd(name)
            reach = f"{OK} reachable"
        except rclone.RcloneError as e:
            reach = f"{WARN} {(e.stderr.splitlines()[-1] if e.stderr else 'unreachable')[:80]}"

        # Mount status
        mount_path_str = fields.get("mount")
        mount_state = ""
        cache_note = ""
        if mount_path_str:
            target = Path(mount_path_str).expanduser()
            if mount.is_stale(target):
                mount_state = f"  {FAIL} stale mount at {target}"
                failed = True
            elif mount.is_mounted(target):
                mount_state = f"  mounted at {target}"
                if fields.get("mode") == "vfs":
                    cache_used = mount.cache_size_bytes(mount.cache_dir(name))
                    cap_bytes = _parse_size_to_bytes(fields.get("vfs_cache", "5G")) or 0
                    if cap_bytes > 0:
                        pct = (cache_used / cap_bytes) * 100
                        marker = WARN if pct >= 90 else "  "
                        cache_note = f"  {marker} cache {_humanize(cache_used)} / {fields.get('vfs_cache', '5G')} ({pct:.0f}%)"
                        if pct >= 90:
                            # Cache-full is a warning, not a fail.
                            pass
            else:
                mount_state = f"  unmounted ({target})"

        print(f"  {name}  {reach}{mount_state}{cache_note}")

    return 1 if failed else 0


def _humanize(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"
