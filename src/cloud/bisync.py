"""Orchestrate sync pairs — strategy dispatch over rclone (see docs/v2-redesign.md).

A *pair* binds a real local directory to a remote path so the folder is always present
offline. Its ``strategy`` picks the engine:

- ``bisync`` — bidirectional ``rclone bisync`` (vault-style working sets)
- ``mirror`` — one-way local→remote ``rclone sync``; local is the source of truth
- ``queue``  — push local→remote, then reconcile remote→local so files consumed
  (archived) remote-side disappear locally

Lifecycle guarantees, shared by all strategies:

- **flock per pair** — a slow cycle on a flaky server never collides with the next timer
  tick; an overlapping run is skipped, not queued.
- **first-run safety** (bisync) — bisync needs a ``--resync`` baseline. We only ever
  resync a pair that has been *initialized* (seeded from the remote first), so an empty
  local side can never trigger a destructive resync.
- **self-heal** — orphaned rclone ``.lck`` files (left by a killed run) are purged when
  no live bisync references the pair; corrupted/missing listings retry once with
  ``--resync`` (safe: local already holds the content).
- **health** — every outcome lands in ``health.json``; a pair broken for >24h
  escalates ONCE per episode via ``mx`` (Matrix saved messages).
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import select
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from cloud import config, rclone

# Files younger than this are invisible to queue/mirror passes: neither uploaded
# (half-written recording) nor deleted by the queue reconcile.
MIN_AGE = "2m"


def state_dir() -> Path:
    """Our own bisync bookkeeping dir (markers + locks), distinct from rclone's workdir."""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    d = root / "agentic-cloud" / "bisync"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _marker(label: str) -> Path:
    return state_dir() / f"{label}.synced"


def _lock_path(label: str) -> Path:
    return state_dir() / f"{label}.lock"


def needs_resync(label: str) -> bool:
    """True until the first successful baseline resync has completed for *label*."""
    return not _marker(label).exists()


def _expand(p: str) -> str:
    return str(Path(p).expanduser())


@contextmanager
def _flock(label: str):
    """Non-blocking exclusive lock. Yields True if acquired, False if another run holds it."""
    fh = _lock_path(label).open("w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EAGAIN):
                yield False
                return
            raise
        yield True
    finally:
        fh.close()


def _result(label: str, **kw) -> dict:
    base = {"label": label, "ok": False, "skipped": False, "resynced": False, "error": None}
    base.update(kw)
    return base


def run_pair(pair: dict, *, force_resync: bool = False, push_only: bool = False) -> dict:
    """Run one cycle for *pair* under its strategy. Never raises — failures land in ``error``.

    *push_only* is honored by the ``queue`` strategy (skip the reconcile pass) so the
    inotify watcher only ever triggers the cheap local→remote upload.
    """
    label = pair["label"]
    local = _expand(pair["local"])
    remote = pair["remote"]
    strategy = pair.get("strategy", "bisync")

    with _flock(label) as acquired:
        if not acquired:
            return _result(label, skipped=True)
        if strategy == "mirror":
            return _run_mirror(label, local, remote)
        if strategy == "queue":
            return _run_queue(label, local, remote, push_only=push_only)
        return _run_bisync(label, local, remote, force_resync=force_resync)


def _run_bisync(label: str, local: str, remote: str, *, force_resync: bool) -> dict:
    purge_orphan_lock(local, remote)
    initialized = not needs_resync(label)

    if force_resync:
        try:
            rclone.bisync(local, remote, resync=True)
        except rclone.RcloneError as e:
            return _result(label, error=e.stderr or str(e))
        _marker(label).touch()
        return _result(label, ok=True, resynced=True)

    if not initialized:
        return _result(label, error="not initialized — run: cloud sync add <remote> <local>")

    try:
        rclone.bisync(local, remote, resync=False)
    except rclone.RcloneError as e:
        # Self-heal corrupted/missing listings on an already-seeded pair.
        if "resync" in (e.stderr or "").lower():
            try:
                rclone.bisync(local, remote, resync=True)
            except rclone.RcloneError as e2:
                return _result(label, error=e2.stderr or str(e2))
            _marker(label).touch()
            return _result(label, ok=True, resynced=True)
        return _result(label, error=e.stderr or str(e))

    _marker(label).touch()
    return _result(label, ok=True)


def _run_mirror(label: str, local: str, remote: str) -> dict:
    """One-way local→remote. The empty-source guard is the catastrophic-case fuse:
    a missing/empty local dir would otherwise wipe the remote copy."""
    p = Path(local)
    if not p.is_dir() or not any(p.iterdir()):
        return _result(label, error=f"local dir missing or empty: {local} — refusing to mirror")
    try:
        rclone.sync_oneway(local, remote, min_age=MIN_AGE)
    except rclone.RcloneError as e:
        return _result(label, error=e.stderr or str(e))
    _marker(label).touch()
    return _result(label, ok=True)


def _run_queue(label: str, local: str, remote: str, *, push_only: bool = False) -> dict:
    """Push new local files up, then make local mirror the remote queue.

    The reconcile pass only runs after a successful push: every stable local file is
    then known to exist remotely, so the only local deletions it can perform are files
    the remote consumer archived. ``MIN_AGE`` shields an in-progress recording on both
    passes.
    """
    Path(local).mkdir(parents=True, exist_ok=True)
    try:
        rclone.copy(local, remote, min_age=MIN_AGE)
    except rclone.RcloneError as e:
        return _result(label, error=f"push failed: {e.stderr or e}")
    if not push_only:
        try:
            rclone.sync_oneway(remote, local, min_age=MIN_AGE)
        except rclone.RcloneError as e:
            return _result(label, error=f"reconcile failed: {e.stderr or e}")
    _marker(label).touch()
    return _result(label, ok=True)


def run_all(labels: list[str] | None = None) -> list[dict]:
    """Run every configured pair (or only *labels*). Used by the systemd timer.

    Cheap pairs run before ``queue`` pairs so a long upload never delays the vault
    pull. Pairs whose ``interval`` has not elapsed since their last attempt are
    skipped (the global timer fires at the smallest cadence; per-pair intervals
    are enforced here). Outcomes are recorded in health.json and >24h breakages
    escalate via mx.
    """
    pairs = config.list_sync_pairs()
    if labels:
        wanted = set(labels)
        pairs = [p for p in pairs if p["label"] in wanted]
    pairs = sorted(pairs, key=lambda p: p.get("strategy", "bisync") == "queue")
    explicit = labels is not None
    results = []
    for p in pairs:
        if not explicit and not _interval_elapsed(p):
            results.append(_result(p["label"], skipped=True))
            continue
        r = run_pair(p)
        record_result(r)
        results.append(r)
    check_escalations()
    return results


# --- self-heal: orphaned rclone bisync locks -----------------------------------
# rclone bisync writes <workdir>/<path1>..<path2>.lck and removes it on clean exit.
# A SIGKILL'd run (e.g. systemd timeout) leaves it behind; every later cycle then
# fails fast with "prior lock file found" until --max-lock expires it. We purge it
# proactively iff no live rclone bisync process references the pair's local path.


def _rclone_workdir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "rclone" / "bisync"


def _lck_path(local: str, remote: str) -> Path:
    munge = lambda s: s.strip("/").replace("/", "_").replace(":", "_")  # noqa: E731
    return _rclone_workdir() / f"{munge(local)}..{munge(remote)}.lck"


def _bisync_running(local: str) -> bool:
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = cmdline.read_bytes().split(b"\0")
        except OSError:
            continue
        if b"bisync" in args and local.encode() in args:
            return True
    return False


def purge_orphan_lock(local: str, remote: str) -> bool:
    """Remove a stale rclone .lck. Returns True if one was purged."""
    lck = _lck_path(local, remote)
    if not lck.exists() or _bisync_running(local):
        return False
    lck.unlink(missing_ok=True)
    return True


# --- health: per-pair outcome tracking + late escalation ------------------------
# health.json is the ambient-state interface (read by the eww pill); escalation is
# the loud path: one Matrix saved-message per breakage episode, only after 24h.

ESCALATE_AFTER_S = 24 * 3600


def health_path() -> Path:
    return state_dir().parent / "health.json"


def load_health() -> dict:
    try:
        return json.loads(health_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_health(data: dict) -> None:
    p = health_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(p)


def record_result(result: dict, *, now: float | None = None) -> None:
    """Fold one run_pair outcome into health.json. Skips are not a signal."""
    if result.get("skipped"):
        return
    now = now if now is not None else time.time()
    health = load_health()
    entry = health.setdefault(result["label"], {})
    entry["last_attempt"] = now
    if result["ok"]:
        entry.update(last_ok=now, consecutive_failures=0, last_error=None,
                     failing_since=None, escalated=False)
    else:
        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        entry["last_error"] = result["error"]
        if not entry.get("failing_since"):
            entry["failing_since"] = now
    _save_health(health)


def _interval_elapsed(pair: dict, *, now: float | None = None) -> bool:
    now = now if now is not None else time.time()
    entry = load_health().get(pair["label"], {})
    last = entry.get("last_attempt")
    return last is None or (now - last) >= pair.get("interval", 60)


def _mx_notify(message: str) -> bool:
    """Best-effort Matrix saved-message via the `mx` CLI. Never raises."""
    mx = shutil.which("mx")
    if mx is None:
        return False
    room = os.environ.get("CLOUD_NOTIFY_ROOM", "saved")
    try:
        proc = subprocess.run([mx, "send", room, message],
                              capture_output=True, text=True, timeout=30, check=False)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def check_escalations(*, now: float | None = None, notify=None) -> list[str]:
    """Notify (once per episode) for every pair broken longer than 24h."""
    now = now if now is not None else time.time()
    notify = notify or _mx_notify
    health = load_health()
    escalated = []
    for label, entry in health.items():
        since = entry.get("failing_since")
        if not since or entry.get("escalated") or (now - since) < ESCALATE_AFTER_S:
            continue
        hours = int((now - since) // 3600)
        if notify(f"⚠ cloud sync: pair '{label}' failing for {hours}h — last error: "
                  f"{(entry.get('last_error') or 'unknown')[:300]}"):
            entry["escalated"] = True
            escalated.append(label)
    if escalated:
        _save_health(health)
    return escalated


# --- inotify watcher: instant local→remote push -------------------------------
# The timer (every 60s) handles remote→local and is the backstop. The watcher adds
# near-instant local→remote by running a bisync cycle shortly after a local change.
# inotifywait's recursive watch does not auto-watch dirs created after start, but the
# timer guarantees those are still picked up within its interval.

INOTIFY_EXCLUDE = r"(/\.git/|\.sync-conflict|~\$|\.tmp$|\.partial$|/4913$|\.swp$)"


def _pair_for_path(path: str, pairs: list[dict]) -> str | None:
    """Return the label whose expanded local dir contains *path* (longest prefix wins)."""
    best, best_len = None, -1
    for p in pairs:
        root = _expand(p["local"]).rstrip("/")
        if (path == root or path.startswith(root + "/")) and len(root) > best_len:
            best, best_len = p["label"], len(root)
    return best


def _inotify_cmd(dirs: list[str], inotifywait: str = "inotifywait") -> list[str]:
    return [
        inotifywait, "-m", "-r", "-q", "--format", "%w%f",
        "-e", "close_write", "-e", "create", "-e", "delete",
        "-e", "moved_to", "-e", "moved_from",
        "--exclude", INOTIFY_EXCLUDE,
        *dirs,
    ]


def watch(
    *,
    debounce: float = 2.0,
    max_wait: float = 10.0,
    inotifywait: str = "inotifywait",
    on_sync=None,
) -> int:
    """Watch all pairs' local dirs and push to remote shortly after a change.

    A pair syncs once it has been quiet for *debounce* seconds, or once it has been
    dirty for *max_wait* seconds (so continuous churn still flushes). Returns when the
    inotifywait subprocess exits.
    """
    pairs = config.list_sync_pairs()
    dirs = [d for d in (_expand(p["local"]) for p in pairs) if Path(d).is_dir()]
    if not dirs:
        return 0

    proc = subprocess.Popen(
        _inotify_cmd(dirs, inotifywait),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    dirty: set[str] = set()
    dirty_since: dict[str, float] = {}
    last_event = 0.0

    try:
        while True:
            ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            now = time.monotonic()
            if ready:
                line = proc.stdout.readline()
                if not line:  # inotifywait exited
                    break
                label = _pair_for_path(line.strip(), pairs)
                if label:
                    dirty.add(label)
                    dirty_since.setdefault(label, now)
                    last_event = now
                continue
            # idle tick — flush pairs that are debounced or have waited long enough
            for label in sorted(dirty):
                if (now - last_event) >= debounce or (now - dirty_since[label]) >= max_wait:
                    pair = config.get_sync_pair(label)
                    if pair:
                        # push_only: the watcher only ever does the cheap upload pass;
                        # reconcile (queue) stays on the timer.
                        record_result(run_pair(pair, push_only=True))
                        if on_sync:
                            on_sync(label)
                    dirty.discard(label)
                    dirty_since.pop(label, None)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


def initialize(pair: dict) -> dict:
    """One-time setup: ensure local dir, seed from remote (additive copy), baseline resync.

    Seeding from the remote BEFORE the resync guarantees local ⊇ remote, so the resync
    can never delete remote content — ``--resync`` path1-priority only governs conflicts.
    """
    local = _expand(pair["local"])
    Path(local).mkdir(parents=True, exist_ok=True)
    try:
        rclone.copy(pair["remote"], local)
    except rclone.RcloneError as e:
        return _result(pair["label"], error=f"seed copy failed: {e.stderr or e}")
    return run_pair(pair, force_resync=True)
