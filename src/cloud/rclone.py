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
