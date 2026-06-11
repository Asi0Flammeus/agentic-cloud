"""Thin subprocess wrapper around the ``rclone`` binary."""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from pathlib import Path

from cloud import config


class RcloneError(Exception):
    """Non-zero rclone exit. ``stderr`` carries the message."""

    def __init__(self, args: list[str], returncode: int, stderr: str):
        self.args = args
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"rclone {' '.join(args)} exited {returncode}: {self.stderr}")


class RcloneNotInstalled(Exception):
    pass


def which() -> str | None:
    return shutil.which("rclone")


def _binary() -> str:
    path = which()
    if path is None:
        raise RcloneNotInstalled(
            "rclone is not on PATH. Install: https://rclone.org/install/"
        )
    return path


def _run(args: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [_binary(), *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RcloneError(args, proc.returncode, proc.stderr)
    return proc


def version() -> str:
    """First line of ``rclone version``, e.g. ``rclone v1.74.1``."""
    return _run(["version"]).stdout.splitlines()[0].strip()


def obscure(plain: str) -> str:
    """Return rclone's reversible-obfuscated form of *plain*.

    Uses ``--obscure`` via stdin would be ideal, but the public CLI is
    ``rclone obscure <password>`` (argv only). On a single-user laptop this is
    acceptable; documented in README.
    """
    return _run(["obscure", plain]).stdout.strip()


def write_remote(name: str, url: str, user: str, obscured_pass: str) -> Path:
    """Write/update a [name] section in rclone.conf for Nextcloud WebDAV."""
    path = config.rclone_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path)

    parser[name] = {
        "type": "webdav",
        "vendor": "nextcloud",
        "url": url,
        "user": user,
        "pass": obscured_pass,
    }

    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        parser.write(f, space_around_delimiters=False)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def delete_remote(name: str) -> bool:
    path = config.rclone_config_path()
    if not path.exists():
        return False
    parser = configparser.ConfigParser()
    parser.read(path)
    if name not in parser:
        return False
    parser.remove_section(name)
    with path.open("w") as f:
        parser.write(f, space_around_delimiters=False)
    os.chmod(path, 0o600)
    return True


def has_remote(name: str) -> bool:
    path = config.rclone_config_path()
    if not path.exists():
        return False
    parser = configparser.ConfigParser()
    parser.read(path)
    return name in parser


def lsd(remote: str) -> None:
    """Cheap auth+connectivity probe: list root directories on the remote."""
    _run(["lsd", f"{remote}:"])


def reveal(obscured: str) -> str:
    """Reverse of obscure: recover cleartext from rclone's obfuscated form."""
    return _run(["reveal", obscured]).stdout.strip()


def copyto(local: str, remote: str) -> None:
    """rclone copyto <local> <remote:path> — copy a single file, preserving its name."""
    _run(["copyto", local, remote])


def mount_args(
    name: str,
    mount_path: Path,
    mode: str,
    cache_size: str,
    cache_age: str,
    exclude: list[str] | None = None,
    *,
    min_free: str = "80G",
) -> list[str]:
    """Argv tail for `rclone mount` — shared between live-mount and systemd unit ExecStart.

    *exclude* entries are passed as ``--exclude <pattern>`` so a path served by a
    separate sync engine (e.g. ``cloud sync``) is not also served by the VFS mount —
    that would give two owners of the same remote path. Use anchored patterns like
    ``/Downloads/`` to hide a top-level directory entirely.
    """
    args = ["mount", f"{name}:", str(mount_path)]
    if mode == "vfs":
        args += [
            "--vfs-cache-mode", "full",
            "--vfs-cache-max-size", cache_size,
            "--vfs-cache-max-age", cache_age,
            "--vfs-cache-min-free-space", min_free,
            "--vfs-cache-poll-interval", "10m",
        ]
    for pattern in exclude or []:
        args += ["--exclude", pattern]
    args += [
        "--dir-cache-time", "30m",
        "--poll-interval", "0",
        "--daemon-timeout", "20s",
        "--timeout", "30s",
        "--contimeout", "10s",
        "--retries", "2",
        "--low-level-retries", "3",
        "--transfers", "2",
        "--checkers", "4",
    ]
    return args


def mount_daemon(
    name: str,
    mount_path: Path,
    mode: str,
    cache_size: str,
    cache_age: str,
    exclude: list[str] | None = None,
    *,
    min_free: str = "80G",
) -> None:
    """Detached mount via `rclone mount --daemon`. Returns once mount is ready or errors."""
    args = mount_args(name, mount_path, mode, cache_size, cache_age, exclude, min_free=min_free)
    args.insert(1, "--daemon")  # right after "mount"
    _run(args)


# bisync safety flags — shared by live runs and (implicitly) the systemd timer.
# --max-delete is fail-safe: it can only ABORT a cycle, never delete extra. Even if
# rclone interpreted it as a count rather than a percent, the worst case is a halt.
BISYNC_SAFETY = [
    "--resilient",            # retry after minor errors without demanding --resync
    "--recover",              # auto-recover from an interrupted run
    "--max-lock", "2m",       # expire stale locks (a slow flaky-server cycle won't wedge the next)
    "--conflict-resolve", "none",   # keep both versions as ..conflict copies — never silently lose data
    "--max-delete", "25",     # abort if >25% would be deleted (guards a transient empty side)
    "--create-empty-src-dirs",
]

# Connection flags mirror the mount: fail fast on a flaky server, retry next cycle.
BISYNC_CONN = [
    "--timeout", "30s",
    "--contimeout", "10s",
    "--retries", "2",
    "--low-level-retries", "3",
    "--transfers", "4",
    "--checkers", "8",
]


def bisync_args(local: str, remote: str, *, resync: bool = False) -> list[str]:
    """Argv for `rclone bisync <local> <remote>` with our safety + connection flags."""
    args = ["bisync", local, remote, *BISYNC_SAFETY, *BISYNC_CONN]
    if resync:
        args.append("--resync")  # first-run baseline; caller must have seeded local first
    return args


def bisync(local: str, remote: str, *, resync: bool = False) -> str:
    """Run one bidirectional sync cycle. Returns rclone stdout; raises RcloneError on failure."""
    return _run(bisync_args(local, remote, resync=resync)).stdout


def copy(src: str, dst: str, *, min_age: str | None = None) -> str:
    """rclone copy <src> <dst> — one-way, additive (never deletes).

    Used to seed a local copy and as the push pass of the *queue* strategy.
    *min_age* skips files younger than the given duration — a recording still
    being written is never uploaded half-baked.
    """
    args = ["copy", src, dst, *BISYNC_CONN]
    if min_age:
        args += ["--min-age", min_age]
    return _run(args).stdout


def sync_oneway(src: str, dst: str, *, min_age: str | None = None, backup_dir: str | None = None) -> str:
    """rclone sync <src> <dst> — one-way MIRROR: dst follows src, deletions included.

    *min_age* excludes fresh files on BOTH sides: they are neither transferred
    nor deleted (rclone leaves filtered-out destination files alone), which
    shields an in-progress recording from the queue reconcile pass.

    *backup_dir* turns deletions/overwrites on dst into moves into that dir —
    a local trash so a surprising src-side deletion never destroys data.
    """
    args = ["sync", src, dst, *BISYNC_CONN]
    if min_age:
        args += ["--min-age", min_age]
    if backup_dir:
        args += ["--backup-dir", backup_dir]
    return _run(args).stdout


def unmount(path: Path) -> None:
    """Release a FUSE mount via fusermount3 (fall back to fusermount). Raise on EBUSY."""
    bin_ = shutil.which("fusermount3") or shutil.which("fusermount")
    if bin_ is None:
        raise RcloneError(["fusermount3", "-u", str(path)], 127, "fusermount3 not on PATH")
    proc = subprocess.run([bin_, "-u", str(path)], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RcloneError([bin_, "-u", str(path)], proc.returncode, proc.stderr)
