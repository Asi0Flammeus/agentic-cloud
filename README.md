# agentic-cloud

Multi-Nextcloud CLI on top of `rclone`. Pilots several Nextcloud accounts with per-folder modes (full sync, selective, VFS) and gogcli-parity share URLs.

Status: **slice 1** — account management + diagnostics. Mount, sync, and share land in following slices.

## Prerequisites

- `rclone` ≥ 1.69 — install from <https://rclone.org/install/>
- `uv` — <https://docs.astral.sh/uv/>
- Python ≥ 3.11
- `fusermount3` (only for slice 2+ mount features)

## Install

```bash
cd ~/repos/Asi0Flammeus/agentic-cloud
uv sync
./bin/cloud doctor
```

Optional: symlink `./bin/cloud` into your PATH.

## Quickstart

```bash
cloud account add crqpt https://cloud.crqpt.com/remote.php/dav/files/asi0
# prompts for WebDAV user + password (use a Nextcloud app password)

cloud account list
cloud account test crqpt
cloud doctor
```

The TOML source of truth lives at `~/.config/agentic-cloud/config.toml`. Credentials live in `~/.config/rclone/rclone.conf` (mode 0600, password obscured via `rclone obscure`). The TOML never contains secrets.

## Exit codes

| code | meaning              |
|------|----------------------|
| 0    | success              |
| 1    | config error         |
| 2    | mount error (slice 2)|
| 3    | rclone subprocess error |

## Roadmap

- **Slice 2** — `mount` / `unmount` / `status` (full + VFS modes), systemd user units.
- **Slice 3** — `push` / `pull` / `share` family (OCS API: password + expiry), `ls`, `cat`.
- **Slice 4** — `sync` (bisync), selective mode, cache management, `--json` output, encrypted rclone config password.

Long-form spec: see [`startup.md`](startup.md).
