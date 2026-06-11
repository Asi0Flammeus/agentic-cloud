from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from cloud import bisync, config, rclone


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    yield tmp_path


PAIR = {"label": "downloads", "local": "~/local/Downloads", "remote": "crqpt:Downloads", "interval": 60}


def _record_bisync(monkeypatch):
    calls = []
    monkeypatch.setattr(rclone, "bisync", lambda local, remote, *, resync=False: calls.append((local, remote, resync)) or "")
    return calls


def test_needs_resync_tracks_marker():
    assert bisync.needs_resync("downloads") is True
    bisync._marker("downloads").touch()
    assert bisync.needs_resync("downloads") is False


def test_run_pair_refuses_uninitialized(monkeypatch):
    bcalls = []
    monkeypatch.setattr(rclone, "bisync", lambda *a, **k: bcalls.append((a, k)))
    res = bisync.run_pair(PAIR)
    assert res["ok"] is False
    assert "not initialized" in res["error"]
    assert bcalls == []  # never touched rclone (no destructive resync on an unseeded pair)


def test_force_resync_runs_and_marks(monkeypatch):
    calls = _record_bisync(monkeypatch)
    res = bisync.run_pair(PAIR, force_resync=True)
    assert res == {"label": "downloads", "ok": True, "skipped": False, "resynced": True, "error": None}
    assert calls == [(bisync._expand("~/local/Downloads"), "crqpt:Downloads", True)]
    assert bisync.needs_resync("downloads") is False


def test_normal_run_when_initialized(monkeypatch):
    bisync._marker("downloads").touch()
    calls = _record_bisync(monkeypatch)
    res = bisync.run_pair(PAIR)
    assert res["ok"] is True and res["resynced"] is False
    assert calls == [(bisync._expand("~/local/Downloads"), "crqpt:Downloads", False)]


def test_self_heal_on_resync_needed(monkeypatch):
    bisync._marker("downloads").touch()  # initialized
    seq = []

    def fake_bisync(local, remote, *, resync=False):
        seq.append(resync)
        if not resync:
            raise rclone.RcloneError(["bisync"], 2, "Bisync critical error: cannot find prior listing, must run --resync")
        return ""

    monkeypatch.setattr(rclone, "bisync", fake_bisync)
    res = bisync.run_pair(PAIR)
    assert res["ok"] is True and res["resynced"] is True
    assert seq == [False, True]  # tried normal, then self-healed with resync


def test_non_resync_error_propagates(monkeypatch):
    bisync._marker("downloads").touch()

    def fake_bisync(local, remote, *, resync=False):
        raise rclone.RcloneError(["bisync"], 1, "502 Bad Gateway")

    monkeypatch.setattr(rclone, "bisync", fake_bisync)
    res = bisync.run_pair(PAIR)
    assert res["ok"] is False
    assert "502" in res["error"]


def test_flock_skips_when_held(monkeypatch):
    calls = _record_bisync(monkeypatch)
    # hold the lock externally
    fh = bisync._lock_path("downloads").open("w")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        res = bisync.run_pair(PAIR, force_resync=True)
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
    assert res["skipped"] is True
    assert calls == []  # never ran while another holds the lock


def test_initialize_seeds_then_resyncs(monkeypatch):
    copies = []
    monkeypatch.setattr(rclone, "copy", lambda src, dst: copies.append((src, dst)) or "")
    calls = _record_bisync(monkeypatch)
    res = bisync.initialize(PAIR)
    assert copies == [("crqpt:Downloads", bisync._expand("~/local/Downloads"))]
    assert calls == [(bisync._expand("~/local/Downloads"), "crqpt:Downloads", True)]
    assert res["ok"] is True and res["resynced"] is True


def test_pair_for_path_longest_prefix(monkeypatch):
    monkeypatch.setenv("HOME", "/home/asi0")
    pairs = [
        {"label": "videos-raw", "local": "~/local/Videos/raw"},
        {"label": "downloads", "local": "~/local/Downloads"},
    ]
    assert bisync._pair_for_path("/home/asi0/local/Downloads/a/b.txt", pairs) == "downloads"
    assert bisync._pair_for_path("/home/asi0/local/Videos/raw/clip.mp4", pairs) == "videos-raw"
    assert bisync._pair_for_path("/home/asi0/local/Videos/raw", pairs) == "videos-raw"  # the dir itself
    assert bisync._pair_for_path("/home/asi0/elsewhere/x", pairs) is None


def test_pair_for_path_no_false_prefix(monkeypatch):
    monkeypatch.setenv("HOME", "/home/asi0")
    pairs = [{"label": "downloads", "local": "~/local/Downloads"}]
    # must not match a sibling dir that merely shares a string prefix
    assert bisync._pair_for_path("/home/asi0/local/Downloads-old/x", pairs) is None


def test_inotify_cmd_shape():
    cmd = bisync._inotify_cmd(["/home/asi0/local/Downloads"])
    assert cmd[0] == "inotifywait"
    assert "-m" in cmd and "-r" in cmd
    assert "--exclude" in cmd
    assert cmd[-1] == "/home/asi0/local/Downloads"


def test_run_all_filters_labels(monkeypatch):
    config.set_sync_pair("downloads", local="~/local/Downloads", remote="crqpt:Downloads")
    config.set_sync_pair("vault", local="~/local/nondual-mind", remote="crqpt:nondual-mind")
    bisync._marker("downloads").touch()
    bisync._marker("vault").touch()
    ran = []
    monkeypatch.setattr(rclone, "bisync", lambda local, remote, *, resync=False: ran.append(remote) or "")
    results = bisync.run_all(labels=["vault"])
    assert [r["label"] for r in results] == ["vault"]
    assert ran == ["crqpt:nondual-mind"]


# --- v2: strategies (queue | mirror), self-heal, health -------------------------

MIRROR_PAIR = {"label": "pictures", "local": "~/local/Pictures", "remote": "crqpt:Pictures",
               "interval": 60, "strategy": "mirror"}
QUEUE_PAIR = {"label": "videos-raw", "local": "~/local/Videos/raw", "remote": "crqpt:Videos/raw",
              "interval": 60, "strategy": "queue"}


def _mklocal(pair, *files):
    d = Path(bisync._expand(pair["local"]))
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        (d / f).write_text("x")
    return d


def test_mirror_runs_oneway_local_to_remote(monkeypatch):
    _mklocal(MIRROR_PAIR, "a.jpg")
    calls = []
    monkeypatch.setattr(rclone, "sync_oneway", lambda src, dst, *, min_age=None: calls.append((src, dst, min_age)) or "")
    res = bisync.run_pair(MIRROR_PAIR)
    assert res["ok"] is True
    assert calls == [(bisync._expand("~/local/Pictures"), "crqpt:Pictures", bisync.MIN_AGE)]


def test_mirror_refuses_missing_or_empty_local(monkeypatch):
    calls = []
    monkeypatch.setattr(rclone, "sync_oneway", lambda *a, **k: calls.append(a))
    res = bisync.run_pair(MIRROR_PAIR)  # local dir doesn't exist
    assert res["ok"] is False and "refusing to mirror" in res["error"]
    _mklocal(MIRROR_PAIR)  # exists but empty
    res = bisync.run_pair(MIRROR_PAIR)
    assert res["ok"] is False and "refusing to mirror" in res["error"]
    assert calls == []  # rclone never touched — remote copy is safe


def test_queue_pushes_then_reconciles(monkeypatch):
    _mklocal(QUEUE_PAIR, "rec.mp4")
    seq = []
    monkeypatch.setattr(rclone, "copy", lambda src, dst, *, min_age=None: seq.append(("push", src, dst, min_age)) or "")
    monkeypatch.setattr(rclone, "sync_oneway", lambda src, dst, *, min_age=None, backup_dir=None: seq.append(("reconcile", src, dst, min_age, backup_dir)) or "")
    res = bisync.run_pair(QUEUE_PAIR)
    assert res["ok"] is True
    local = bisync._expand("~/local/Videos/raw")
    assert seq == [
        ("push", local, "crqpt:Videos/raw", bisync.MIN_AGE),
        ("reconcile", "crqpt:Videos/raw", local, bisync.MIN_AGE,
         str(bisync.state_dir().parent / "queue-trash" / "videos-raw")),
    ]


def test_queue_push_only_skips_reconcile(monkeypatch):
    _mklocal(QUEUE_PAIR)
    seq = []
    monkeypatch.setattr(rclone, "copy", lambda *a, **k: seq.append("push") or "")
    monkeypatch.setattr(rclone, "sync_oneway", lambda *a, **k: seq.append("reconcile") or "")
    res = bisync.run_pair(QUEUE_PAIR, push_only=True)
    assert res["ok"] is True
    assert seq == ["push"]  # the watcher path never deletes anything locally


def test_queue_failed_push_gates_reconcile(monkeypatch):
    _mklocal(QUEUE_PAIR)
    seq = []

    def failing_copy(*a, **k):
        raise rclone.RcloneError(["copy"], 1, "network down")

    monkeypatch.setattr(rclone, "copy", failing_copy)
    monkeypatch.setattr(rclone, "sync_oneway", lambda *a, **k: seq.append("reconcile"))
    res = bisync.run_pair(QUEUE_PAIR)
    assert res["ok"] is False and "push failed" in res["error"]
    # reconcile MUST NOT run: a local file that failed to upload would otherwise
    # be deleted as "absent from remote"
    assert seq == []


def test_run_pair_defaults_to_bisync_strategy(monkeypatch):
    bisync._marker("downloads").touch()
    calls = _record_bisync(monkeypatch)
    res = bisync.run_pair(PAIR)  # PAIR has no "strategy" key
    assert res["ok"] is True
    assert len(calls) == 1


def test_purge_orphan_lock_removes_stale(monkeypatch, tmp_path):
    local, remote = "/home/asi0/local/Videos/raw", "crqpt:Videos/raw"
    lck = bisync._lck_path(local, remote)
    lck.parent.mkdir(parents=True, exist_ok=True)
    lck.touch()
    assert lck.name == "home_asi0_local_Videos_raw..crqpt_Videos_raw.lck"  # rclone's munging
    monkeypatch.setattr(bisync, "_bisync_running", lambda l: False)
    assert bisync.purge_orphan_lock(local, remote) is True
    assert not lck.exists()


def test_purge_orphan_lock_keeps_live(monkeypatch):
    local, remote = "/home/asi0/local/Videos/raw", "crqpt:Videos/raw"
    lck = bisync._lck_path(local, remote)
    lck.parent.mkdir(parents=True, exist_ok=True)
    lck.touch()
    monkeypatch.setattr(bisync, "_bisync_running", lambda l: True)
    assert bisync.purge_orphan_lock(local, remote) is False
    assert lck.exists()  # a live run owns it


def test_health_records_ok_and_failure_episodes():
    bisync.record_result({"label": "vault", "ok": False, "skipped": False, "error": "boom"}, now=1000.0)
    h = bisync.load_health()["vault"]
    assert h["consecutive_failures"] == 1 and h["failing_since"] == 1000.0
    bisync.record_result({"label": "vault", "ok": False, "skipped": False, "error": "boom2"}, now=1060.0)
    h = bisync.load_health()["vault"]
    assert h["consecutive_failures"] == 2 and h["failing_since"] == 1000.0  # episode start kept
    bisync.record_result({"label": "vault", "ok": True, "skipped": False, "error": None}, now=1120.0)
    h = bisync.load_health()["vault"]
    assert h["consecutive_failures"] == 0 and h["failing_since"] is None and h["last_ok"] == 1120.0


def test_health_skip_is_not_a_signal():
    bisync.record_result({"label": "vault", "ok": False, "skipped": True, "error": None}, now=1.0)
    assert bisync.load_health() == {}


def test_escalation_fires_once_after_24h():
    t0 = 1_000_000.0
    bisync.record_result({"label": "vault", "ok": False, "skipped": False, "error": "boom"}, now=t0)
    sent = []
    # before 24h: silence
    assert bisync.check_escalations(now=t0 + 23 * 3600, notify=lambda m: sent.append(m) or True) == []
    # after 24h: one message
    assert bisync.check_escalations(now=t0 + 25 * 3600, notify=lambda m: sent.append(m) or True) == ["vault"]
    assert len(sent) == 1 and "vault" in sent[0]
    # same episode: never again
    assert bisync.check_escalations(now=t0 + 30 * 3600, notify=lambda m: sent.append(m) or True) == []
    assert len(sent) == 1
    # recovery resets the episode
    bisync.record_result({"label": "vault", "ok": True, "skipped": False, "error": None}, now=t0 + 31 * 3600)
    assert bisync.load_health()["vault"]["escalated"] is False


def test_run_all_orders_queue_last_and_respects_interval(monkeypatch):
    config.set_sync_pair("vault", local="~/local/nondual-mind", remote="crqpt:nondual-mind",
                         interval=60, strategy="bisync")
    config.set_sync_pair("videos-raw", local="~/local/Videos/raw", remote="crqpt:Videos/raw",
                         interval=60, strategy="queue")
    bisync._marker("vault").touch()
    _mklocal(QUEUE_PAIR)
    ran = []
    monkeypatch.setattr(rclone, "bisync", lambda local, remote, *, resync=False: ran.append("vault") or "")
    monkeypatch.setattr(rclone, "copy", lambda *a, **k: ran.append("queue-push") or "")
    monkeypatch.setattr(rclone, "sync_oneway", lambda *a, **k: ran.append("queue-reconcile") or "")
    results = bisync.run_all()
    assert ran == ["vault", "queue-push", "queue-reconcile"]  # queue runs last
    assert all(r["ok"] for r in results)
    # immediately after, intervals have not elapsed: both pairs skip
    ran.clear()
    results = bisync.run_all()
    assert ran == []
    assert all(r["skipped"] for r in results)
