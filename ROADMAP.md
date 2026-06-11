# Roadmap

## Shipped

- Account management — `cloud account add / list / test / remove`
- Diagnostics — `cloud doctor` (rclone, FUSE, stale mounts, cache fullness, per-remote reachability)
- Mount lifecycle — `cloud mount / unmount / status` with VFS and full modes
- Systemd user-unit auto-mount (`--auto`)
- Public link share via Nextcloud OCS — `cloud push --share`, `cloud share`, `cloud share-list`, `cloud share --revoke`

## Planned

- **`cloud pull`, `cloud ls`, `cloud cat`** — one-shot operations without mounting
- **`cloud move <src> <remote>:<dest>`** — bulk directory migration with source-delete-on-success (wraps `rclone move --delete-empty-src-dirs --transfers=N --progress`). Use case: migrating $HOME stragglers to cloud without thrashing the FUSE cache. Should accept `--archive`, `--transfers`, `--bandwidth` flags and default to sane values (transfers=2, progress every 60s, log to `~/.cache/agentic-cloud/move-<ts>.log`).
- **`cloud sync`** — bidirectional sync via `rclone bisync`
- **Subpath mounts** — `cloud mount work:Documents` mounts just the subfolder
- **Selective sync mode** — `mode = "selective"` with an explicit include-list per remote
- **Share password + expiry** — `--password`, `--expires` flags on `push --share` and `share` (OCS already supports this; CLI wiring deferred)
- **`cloud cache evict` / `cloud cache size`** — explicit cache management commands
- **`--json` output everywhere** — for agentic pipelines
- **Encrypted rclone config password** — wrap the rclone-side config encryption

## Known bugs

- **`cloud mount` clobbers existing `vfs_cache` / `vfs_max_age` in config** — `mount.py:109-117` always writes the function-parameter defaults (`"5G"` / `"168h"`) back to `config.toml` instead of reading existing values. Reproduces by editing config to `vfs_cache = "50G"`, running `cloud unmount <name> && cloud mount <name>` (without `--cache-size`/`--cache-age` flags) — the saved values revert to defaults. Discovered 2026-05-23 during $HOME→cloud bulk migration. Fix: read remote config first, fall back to CLI args only if explicitly passed.
- **Sync livelock on large uploads** — `cloud-sync.service` had `TimeoutStartSec=300`; a multi-GB upload (videos-raw) exceeds it, systemd kills rclone mid-transfer, the bisync `.lck` is left behind, and the next runs fail fast on the lock until it expires and the cycle repeats — silently, for days. Root cause: bisync-everything heuristics applied to multi-GB recordings. Fixed by the v2 redesign (per-pair strategies + no kill timeout); see `docs/v2-redesign.md`. Discovered live 2026-06-11.
- **Hardcoded `--vfs-cache-min-free-space 80G`** — `rclone.py:154`; mounts refuse to start on machines with <80 GB free. Should be per-remote config.

## Ideas worth considering

- Health-check hook for systemd units (auto-restart on `lsd` failure)
- Per-mount bandwidth caps
- Conflict-resolution policy for bisync
- Plugin hook for other WebDAV backends (Nextcloud-specific bits are confined to `share.py`)
