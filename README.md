# agentic-cloud — `cloud` CLI

Multi-Nextcloud CLI on top of `rclone`. Pilot several Nextcloud accounts with per-folder modes (full sync / VFS) and gogcli-parity share URLs.

## Why

Nextcloud Desktop on Linux has no real VFS (only `.nextcloud` placeholders), no CLI surface, and crashes on VFS enable. `rclone` covers all 3 modes (full / VFS-FUSE / selective), handles multi-account natively, and is fully CLI-driven. `cloud` is a thin layer that gives you one TOML, idempotent commands, a `doctor` check, systemd auto-mount, and `--share` URLs (Nextcloud OCS API).

## Prerequisites

| tool | install |
|---|---|
| `rclone` ≥ 1.69 | `https://rclone.org/install/` (or use the `.deb` from `downloads.rclone.org`) |
| `uv` | `https://docs.astral.sh/uv/` |
| Python ≥ 3.11 | (Ubuntu 24.04 ships 3.12) |
| `fusermount3` | `apt install fuse3` |

## Install

```bash
cd ~/repos/Asi0Flammeus/agentic-cloud
uv sync
./bin/cloud doctor
```

Optional: symlink `./bin/cloud` into your PATH (`ln -s "$(pwd)/bin/cloud" ~/.local/bin/cloud`).

## Quickstart — one account

```bash
# 1. Register the WebDAV endpoint (use a Nextcloud app password, not your login pw)
cloud account add crqpt https://cloud.crqpt.com/remote.php/dav/files/asi0

# 2. Verify auth + connectivity
cloud account test crqpt

# 3. Mount it (VFS by default; --auto installs systemd user unit for boot persistence)
cloud mount crqpt --mode vfs --auto

# 4. Use it
ls ~/clouds/crqpt/
echo "hello" > ~/clouds/crqpt/hello.txt   # writes propagate to Nextcloud

# 5. Diagnostics
cloud status
cloud doctor
```

## Share URLs (gogcli parity)

```bash
# Push + immediately get a public link
cloud push /tmp/devis.pdf alysis:clients/x/devis.pdf --share
# → ✓ Uploaded ...
# → ✓ Public link: https://drive.alysis.cat/s/aBcDeFgHiJkL

# Or create/revoke a link on an already-uploaded file
cloud share   alysis:clients/x/devis.pdf
cloud share   alysis:clients/x/devis.pdf --revoke
cloud share-list alysis
```

## Full command surface (v1)

| command | purpose |
|---|---|
| `cloud account add <name> <url>` | register a Nextcloud account (prompts for user + app password) |
| `cloud account list / test / remove` | manage accounts |
| `cloud mount <name> [--mode vfs\|full] [--mount-path PATH] [--auto]` | FUSE-mount a remote |
| `cloud unmount <name>` | release a mount |
| `cloud status` | tabular state of every configured remote |
| `cloud doctor` | rclone + FUSE + mounts + cache health |
| `cloud push <local> <name>:<remote-path> [--share]` | upload one file (optional public link) |
| `cloud share <name>:<path> [--revoke]` | create / remove public link |
| `cloud share-list <name>` | list active public links |

## Configuration

Single source of truth: `~/.config/agentic-cloud/config.toml`. `cloud` generates `~/.config/rclone/rclone.conf` from it. Credentials live only in `rclone.conf` (mode `0600`, obscured via `rclone obscure`), never in TOML.

```toml
[remotes.crqpt]
url = "https://cloud.crqpt.com/remote.php/dav/files/asi0"
mount = "~/clouds/crqpt"
mode = "vfs"
vfs_cache = "5G"
vfs_max_age = "168h"
auto = true
```

## Systemd auto-mount

`cloud mount <name> --auto` writes a user unit at `~/.config/systemd/user/cloud-<name>.service` and enables it. For **boot survival** (mount comes up even when you're not logged in), enable user-linger:

```bash
sudo loginctl enable-linger $USER
```

Without linger, the units only start when you log in.

## Exit codes

| code | meaning |
|---|---|
| 0 | success |
| 1 | config error (bad TOML, unknown remote, bad args) |
| 2 | mount error (rclone failure, FUSE busy, path conflict) |
| 3 | rclone subprocess error (network, auth, etc.) |
| 4 | share / OCS API error |

## Deferred to v1.1+

- `cloud pull`, `cloud ls`, `cloud cat`, `cloud sync` (bisync)
- subpath mounts (`name:sub`), `--selective <patterns>`
- share password + expiry
- `--json` output
- encrypted rclone config password
