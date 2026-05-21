"""TOML config — single source of truth for cloud remotes.

Lives at ``$XDG_CONFIG_HOME/agentic-cloud/config.toml`` (default ``~/.config``).
Credentials are NEVER stored here — only ``url``, ``mount``, ``mode`` and flags.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) if base else Path.home() / ".config"


def config_path() -> Path:
    return config_dir() / "agentic-cloud" / "config.toml"


def rclone_config_path() -> Path:
    # rclone respects RCLONE_CONFIG env var; default is XDG location.
    env = os.environ.get("RCLONE_CONFIG")
    if env:
        return Path(env)
    return config_dir() / "rclone" / "rclone.conf"


def load() -> dict:
    path = config_path()
    if not path.exists():
        return {"remotes": {}}
    with path.open("rb") as f:
        data = tomllib.load(f)
    data.setdefault("remotes", {})
    return data


def save(data: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_dump(data))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def get_remote(name: str) -> dict | None:
    return load().get("remotes", {}).get(name)


def set_remote(name: str, **fields: str | bool) -> None:
    data = load()
    data.setdefault("remotes", {})[name] = {k: v for k, v in fields.items() if v is not None}
    save(data)


def remove_remote(name: str) -> bool:
    data = load()
    remotes = data.get("remotes", {})
    if name not in remotes:
        return False
    del remotes[name]
    save(data)
    return True


def list_remotes() -> dict[str, dict]:
    return load().get("remotes", {})


def parse_remote_path(arg: str) -> tuple[str, str]:
    """Parse ``name:remote/path`` into ``(name, "remote/path")``.

    Raises ``ValueError`` if the colon is missing or the remote half is empty.
    A bare path before the colon is allowed to be empty (root): ``alysis:``.
    """
    if ":" not in arg:
        raise ValueError(f"expected 'name:remote/path', got: {arg!r}")
    name, _, path = arg.partition(":")
    if not name:
        raise ValueError(f"empty remote name in: {arg!r}")
    return name, path.lstrip("/")


def _dump(data: dict) -> str:
    """Minimal TOML emitter for our flat schema: top-level scalars + [remotes.<name>] tables."""
    lines: list[str] = []
    remotes = data.get("remotes", {})
    for name, fields in remotes.items():
        lines.append(f"[remotes.{_key(name)}]")
        for key, value in fields.items():
            lines.append(f"{key} = {_value(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _key(name: str) -> str:
    # bare keys allow letters/digits/_/- ; quote if anything else.
    if name and all(c.isalnum() or c in "_-" for c in name):
        return name
    return _value(name)


def _value(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"unsupported TOML value type: {type(v).__name__}")
