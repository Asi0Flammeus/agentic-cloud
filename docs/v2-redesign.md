# v2 redesign — sync strategies per data class

Decisions from the 2026-06-11 grill session with asi0, grounded in a live incident.

## The incident that motivated this

`cloud-sync.service` (60s timer, `TimeoutStartSec=300`) livelocked for >24h on the
`videos-raw` pair:

- 4 recordings from April (~19.7 GB total) existed locally but had never reached
  `crqpt:Videos/raw`. Every bisync cycle correctly tried to upload them.
- 19.7 GB cannot upload in 300s. systemd SIGTERM'd every run mid-transfer,
  leaving rclone's bisync `.lck` behind; transfers restarted from byte 0 every
  5 minutes, forever.
- Side effects: uplink permanently saturated → WebDAV mounts starved
  (`http2: timeout awaiting response headers`, TLS handshake timeouts), cgroup
  memory peaks of 13–14 GB (page cache from re-reading the mp4s — not a leak;
  rclone RSS was ~310 MB), and a fully silent failure: nothing notified, the
  pair just stopped syncing.
- The inotify watcher made it worse: `rec` has ffmpeg write directly to the
  final filename in `raw/`, so the watcher saw a *growing* file at recording
  start and triggered full bisync cycles against a half-written multi-GB file.

Root cause is **not** a bug in one component: the heuristics (60s cadence,
300s hard kill, bisync-everything, instant watch) were designed for a vault of
small markdown files and applied uniformly to multi-GB video recordings.

## Use cases (agreed)

| Pair | Content | Real flow | Strategy |
|---|---|---|---|
| `nondual-mind` | ~8.8k md files | Laptop is main editor; server envoys write occasionally (meeting syntheses). Remote→local latency of 1–5 min is fine; local→remote stays near-instant via watch. | `bisync` (interval relaxed to 2–5 min) |
| `videos-raw` | few multi-GB recordings | Processing **queue**: laptop records → NC → server agents consume, then move `Videos/raw` → `Videos/archive` remote-side. Local `raw/` must mirror the remote queue: archived remotely ⇒ removed locally. | `queue` |
| `pictures` | ~8.2k photos | Produced on laptop, NC is backup, must stay offline-available. Local deletions propagate (NC trash is the safety net). Nothing lands remote-side. | `mirror` |
| `downloads` | ~1.9k files | Same as pictures. | `mirror` |

Cross-cutting decisions:

- **Bandwidth: full speed, no cap.** Priority is the server agents receiving
  raws ASAP. Accepted tradeoff: connection loaded for minutes after a
  recording. (Fiber uplink, measured ~50 MiB/s — uploads are short.)
- **Failures: self-heal first, escalate late.** Auto-repair whatever is
  repairable (orphaned `.lck` purge when no rclone process holds it, retry with
  backoff, auto-resync on corrupted listings). If a pair is still broken after
  **24h**, send a Matrix saved message (`mx` CLI) and surface the state in the
  eww cloud pill. No noise for transient incidents; never silently broken >24h.
- **Mounts: maximum snappiness.** `~/clouds/*` should feel "as if the files
  were already on the PC". Keep `--vfs-cache-mode full` + big cache; add
  metadata pre-warming (`rclone rc vfs/refresh recursive=true` after mount and
  periodically) so directory listings are always hot. Staleness of a few
  minutes is acceptable; cold-listing latency is not.

## Strategy semantics

### `queue` (videos-raw)

Two ordered one-way passes, no bisync state, no `.lck`:

1. **push**: `rclone copy local→remote --min-age 2m` — uploads new recordings;
   `--min-age` skips files still being written (belt-and-braces with the rec
   staging fix below).
2. **reconcile**: `rclone sync remote→local --min-age 2m --max-delete-guard` —
   pulls remote state, which propagates the agents' raw→archive moves as local
   deletions. `--min-age` excludes fresh local files from deletion (a recording
   created between the two passes is untouchable). After the push pass,
   remote ⊇ local-stable, so the only local deletions are files the agents
   archived.

Guards: abort reconcile if it would delete more than N% (reuse `--max-delete 25`
spirit); never delete a local file younger than min-age.

### `mirror` (pictures, downloads)

One-way `rclone sync local→remote --min-age 2m --max-delete 25`. Local is the
single source of truth; remote follows, NC trash catches mistakes. No
remote→local pass at all (offline availability is inherent: files are local).
Seeding a new machine reuses the existing `initialize()` path.

### `bisync` (nondual-mind)

Current engine, kept, with:

- per-pair `interval` honored (vault: 120–300s instead of global 60s),
- self-heal extended to orphaned rclone `.lck` files (purge iff no rclone
  process references the pair paths),
- conflict policy stays `--conflict-resolve none` (conflict copies are visible
  in the vault, asi0 arbitrates).

## Scheduler changes

- The sync service must **never be killed mid-transfer**: drop the 300s
  `TimeoutStartSec` (generous value, e.g. 4h) — slow cycles are already
  serialized per-pair by flock; an overlapping timer tick skips, not queues.
- Watch (inotify) only triggers cheap passes: `queue` push pass and `mirror`
  push for small deltas; it never triggers the heavy reconcile.
- Pair ordering in a run: cheap pairs first (vault, downloads, pictures), heavy
  queue last, so a long upload never delays the vault pull.

## Health & escalation

- `cloud sync` writes `~/.cache/agentic-cloud/health.json` after every pair
  run: `{pair: {last_ok, last_err, consecutive_failures, since}}`.
- The eww pill script (`~/.config/eww/scripts/cloudsync`) gains per-pair detail
  from that file (today it only reads unit-level systemd state).
- Escalation: any pair with `now - last_ok > 24h` *and* failures in the window
  → one `mx` saved message (deduplicated; one per breakage episode).

## Bug fixes rolled into v2

1. **Config clobber** (`mount.py:109-117`, already in ROADMAP): `cloud mount`
   rewrites `vfs_cache`/`vfs_max_age` defaults over user-edited values.
2. **Hardcoded `--vfs-cache-min-free-space 80G`** (`rclone.py:154`): make it
   configurable per remote.
3. **rec writes in place** (centaur-infra `packages/rec`): ffmpeg must write to
   a staging name excluded from the watcher (`*.partial` is already in
   `INOTIFY_EXCLUDE`) and atomically rename into `raw/` on stop.

## Migration

1. (done live, 2026-06-11) `TimeoutStartSec=2h` drop-in let the wedged 19.7 GB
   upload finally complete; `--max-lock 2m` expired the poisoned `.lck`.
2. Ship v2 engine + tests.
3. Update `config.toml`: add `strategy` per pair (default `bisync` keeps
   backcompat), relax vault interval.
4. Regenerate systemd units (folds the drop-in into the unit proper).
5. Verify each pair one full cycle; remove `videos-raw` bisync state
   (markers/listings) once `queue` is live.
