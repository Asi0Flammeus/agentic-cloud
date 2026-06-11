# Install & reference

## Prerequisites

| dep | min version | install |
|---|---|---|
| `rclone` | 1.69 | <https://rclone.org/install/> (the upstream `.deb` is signed; `apt install rclone` is a year-ish behind) |
| Python | 3.11 | (3.12 ships on Ubuntu 24.04) |
| `uv` | recent | <https://docs.astral.sh/uv/> |
| `fusermount3` | — | `apt install fuse3` |

## Install

```bash
git clone https://github.com/Asi0Flammeus/agentic-cloud.git
cd agentic-cloud
uv sync
./bin/cloud doctor       # all greens? you're set
```

Optional: put `cloud` on your PATH.

```bash
ln -s "$(pwd)/bin/cloud" ~/.local/bin/cloud
```

## Quickstart

```bash
# 1. Register the account (use a Nextcloud app password, not your login password — settings → Security)
cloud account add work https://nc.example.com/remote.php/dav/files/me

# 2. Verify it
cloud account test work

# 3. Mount it
cloud mount work --mode vfs --auto

# 4. Use it
ls ~/clouds/work/
```

## Command reference

| command | purpose |
|---|---|
| `cloud account add <name> <url>` | register an account (prompts for user + app password) |
| `cloud account list` | tabular: name, url, mode |
| `cloud account test <name>` | auth + connectivity probe (`rclone lsd`) |
| `cloud account remove <name>` | unregister (cleans both config files) |
| `cloud mount <name> [--mode vfs\|full] [--mount-path PATH] [--cache-size SIZE] [--cache-age DURATION] [--auto]` | FUSE-mount the remote |
| `cloud unmount <name>` | release the mount |
| `cloud status` | tabular state of every configured remote |
| `cloud doctor` | rclone + FUSE + mounts + cache + per-remote health |
| `cloud push <local> <name>:<remote-path> [--share]` | upload one file (optional public link) |
| `cloud share <name>:<path>` | create a public link on an already-uploaded file |
| `cloud share <name>:<path> --revoke` | remove all public links on a path |
| `cloud share-list <name>` | list active public links |

## Mount modes

- **`--mode vfs`** (default) — files appear lazily; `ls`/`stat` use only metadata; `cat`/`cp` cache content locally. Cache eviction by age (`--cache-age`, default `168h`) and size (`--cache-size`, default `5G`).
- **`--mode full`** — every read/write streams through `rclone` with no eager local cache. Lower disk cost, higher per-access latency.

`--vfs-cache-mode full` is used under the hood for VFS — see the `rclone` docs.

## Config schema

Single source of truth: `~/.config/agentic-cloud/config.toml`. `cloud` generates `~/.config/rclone/rclone.conf` from it. **Credentials live only in `rclone.conf`** (mode `0600`, obscured via `rclone obscure`), never in TOML.

```toml
[remotes.work]
url = "https://nc.example.com/remote.php/dav/files/me"
mount = "~/clouds/work"
mode = "vfs"
vfs_cache = "5G"
vfs_max_age = "168h"
auto = true
```

## Systemd auto-mount

`cloud mount <name> --auto` writes a user unit at `~/.config/systemd/user/cloud-<name>.service` and enables it. For **boot survival** (the mount comes up even when you are not logged in), enable user-linger:

```bash
sudo loginctl enable-linger $USER
```

Without linger, the unit only starts when you log in.

### Sleep guard for FUSE mounts

Network-backed FUSE mounts should be detached before suspend. Otherwise a
process that is walking the mount (`git status`, an editor, an indexer, `mv`)
can wait in the kernel for rclone while rclone waits on WebDAV/DNS that is being
torn down for sleep. That can make userspace freeze fail and create a repeated
lid-close suspend loop.

Install the sleep guard on machines using auto-mounted cloud remotes:

```bash
scripts/install-sleep-guard
```

The installer performs the explicit root-side steps:

```bash
sudo install -m 0755 scripts/agentic-cloud-sleep-guard /usr/local/sbin/agentic-cloud-sleep-guard
sudo install -m 0644 systemd/agentic-cloud-sleep-guard.service /etc/systemd/system/agentic-cloud-sleep-guard.service
sudo install -m 0644 systemd/agentic-cloud-sleep-guard.env.example /etc/default/agentic-cloud-sleep-guard
sudo systemctl daemon-reload
sudo systemctl enable agentic-cloud-sleep-guard.service
```

Edit `/etc/default/agentic-cloud-sleep-guard` when the local account names or
mount paths differ from the example. On suspend, the guard stops the configured
user units and lazily detaches their FUSE mountpoints. On resume, it starts the
same user units with `--no-block`, so resume is not held hostage by Wi-Fi or DNS
settling.

## Tip: keep system indexers out of `~/clouds/`

GNOME's `tracker-miner-fs-3`, KDE's `baloo`, and similar will happily walk a new mountpoint and force-download every file via `stat`. Drop a `.nomedia` file at the parent of your mounts to keep them out:

```bash
touch ~/clouds/.nomedia
```

This file is local (sits alongside the mount dirs, not inside them) so it does not sync to any cloud.

## Exit codes

| code | meaning |
|---|---|
| 0 | success |
| 1 | config error (bad TOML, unknown remote, bad args) |
| 2 | mount error (rclone failure, FUSE busy, path conflict) |
| 3 | rclone subprocess error (network, auth, etc.) |
| 4 | share / OCS API error |

## Cache management (manual, until v1.1)

Eviction happens automatically by age and size caps, but you can free space yourself:

```bash
rm -rf ~/.cache/rclone/vfs/<name>/path/to/file       # one file
rm -rf ~/.cache/rclone/vfs/<name>/some-dir/          # subtree
rm -rf ~/.cache/rclone/vfs/<name>/*                  # whole account
```

The mount stays mounted; next access re-downloads. Cloud content is never touched.
