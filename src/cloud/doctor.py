"""End-to-end diagnostics. Run via ``cloud doctor``."""

from __future__ import annotations

import shutil

from cloud import config, rclone


OK = "✓"
FAIL = "✗"
WARN = "⚠"


def run() -> int:
    """Print check report. Return 0 if no hard failures, else 1."""
    failed = False

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

    # fusermount3 (slice 2+ only — warn, don't fail)
    fuse = shutil.which("fusermount3") or shutil.which("fusermount")
    if fuse:
        print(f"{OK} fusermount         {fuse}")
    else:
        print(f"{WARN} fusermount missing  needed for VFS mounts (slice 2+)")

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
    remotes = config.list_remotes()
    if not remotes:
        print(f"{WARN} no remotes configured  run: cloud account add <name> <webdav-url>")
        return 1 if failed else 0

    print()
    print(f"Probing {len(remotes)} remote(s):")
    for name in remotes:
        if not rclone.has_remote(name):
            print(f"  {FAIL} {name}  missing from rclone.conf (re-run `account add`)")
            failed = True
            continue
        try:
            rclone.lsd(name)
            print(f"  {OK} {name}  reachable")
        except rclone.RcloneError as e:
            print(f"  {WARN} {name}  {e.stderr.splitlines()[-1] if e.stderr else 'failed'}")

    return 1 if failed else 0
