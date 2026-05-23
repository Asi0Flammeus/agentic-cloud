# Roadmap

## Shipped

- Account management — `cloud account add / list / test / remove`
- Diagnostics — `cloud doctor` (rclone, FUSE, stale mounts, cache fullness, per-remote reachability)
- Mount lifecycle — `cloud mount / unmount / status` with VFS and full modes
- Systemd user-unit auto-mount (`--auto`)
- Public link share via Nextcloud OCS — `cloud push --share`, `cloud share`, `cloud share-list`, `cloud share --revoke`

## Planned

- **`cloud pull`, `cloud ls`, `cloud cat`** — one-shot operations without mounting
- **`cloud sync`** — bidirectional sync via `rclone bisync`
- **Subpath mounts** — `cloud mount work:Documents` mounts just the subfolder
- **Selective sync mode** — `mode = "selective"` with an explicit include-list per remote
- **Share password + expiry** — `--password`, `--expires` flags on `push --share` and `share` (OCS already supports this; CLI wiring deferred)
- **`cloud cache evict` / `cloud cache size`** — explicit cache management commands
- **`--json` output everywhere** — for agentic pipelines
- **Encrypted rclone config password** — wrap the rclone-side config encryption

## Ideas worth considering

- Health-check hook for systemd units (auto-restart on `lsd` failure)
- Per-mount bandwidth caps
- Conflict-resolution policy for bisync
- Plugin hook for other WebDAV backends (Nextcloud-specific bits are confined to `share.py`)
