from __future__ import annotations

from pathlib import Path

from cloud import rclone


def test_mount_args_vfs_baseline():
    args = rclone.mount_args("crqpt", Path("/home/asi0/clouds/crqpt"), "vfs", "5G", "168h")
    assert args[:3] == ["mount", "crqpt:", "/home/asi0/clouds/crqpt"]
    assert "--vfs-cache-mode" in args and "full" in args
    assert "--exclude" not in args


def test_mount_args_appends_excludes():
    args = rclone.mount_args(
        "crqpt", Path("/x"), "vfs", "5G", "168h",
        exclude=["/Downloads/", "/Pictures/", "/Videos/raw/"],
    )
    # one --exclude per pattern, value follows immediately
    pairs = [(args[i], args[i + 1]) for i, a in enumerate(args) if a == "--exclude"]
    assert pairs == [
        ("--exclude", "/Downloads/"),
        ("--exclude", "/Pictures/"),
        ("--exclude", "/Videos/raw/"),
    ]
    # excludes precede the common flags (so --dir-cache-time still present)
    assert "--dir-cache-time" in args


def test_mount_args_full_mode_no_vfs_flags():
    args = rclone.mount_args("crqpt", Path("/x"), "full", "5G", "168h", exclude=["/a/"])
    assert "--vfs-cache-mode" not in args
    assert "--exclude" in args


def test_bisync_args_safety_and_conn_flags():
    args = rclone.bisync_args("/home/asi0/local/Downloads", "crqpt:Downloads")
    assert args[:3] == ["bisync", "/home/asi0/local/Downloads", "crqpt:Downloads"]
    # data-safety flags present
    assert "--conflict-resolve" in args and args[args.index("--conflict-resolve") + 1] == "none"
    assert "--max-delete" in args and args[args.index("--max-delete") + 1] == "25"
    assert "--resilient" in args
    assert "--recover" in args
    assert "--max-lock" in args and args[args.index("--max-lock") + 1] == "2m"
    # not a resync by default
    assert "--resync" not in args


def test_bisync_args_resync_flag():
    args = rclone.bisync_args("/l", "crqpt:r", resync=True)
    assert "--resync" in args


def test_bisync_invokes_run(monkeypatch):
    captured = {}

    class FakeProc:
        stdout = "ok"

    def fake_run(args, *, input_text=None):
        captured["args"] = args
        return FakeProc()

    monkeypatch.setattr(rclone, "_run", fake_run)
    out = rclone.bisync("/l", "crqpt:r", resync=True)
    assert out == "ok"
    assert captured["args"][0] == "bisync"
    assert "--resync" in captured["args"]


def test_copy_is_additive(monkeypatch):
    captured = {}

    class FakeProc:
        stdout = ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        return FakeProc()

    monkeypatch.setattr(rclone, "_run", fake_run)
    rclone.copy("crqpt:Downloads", "/home/asi0/local/Downloads")
    assert captured["args"][0] == "copy"
    assert captured["args"][1:3] == ["crqpt:Downloads", "/home/asi0/local/Downloads"]
    # copy never carries delete/sync semantics
    assert "--delete" not in " ".join(captured["args"])


def test_sync_oneway_and_copy_min_age(monkeypatch):
    captured = []

    class FakeProc:
        stdout = ""

    monkeypatch.setattr(rclone, "_run", lambda args, **k: captured.append(args) or FakeProc())
    rclone.sync_oneway("/l", "crqpt:r", min_age="2m")
    rclone.copy("/l", "crqpt:r", min_age="2m")
    rclone.copy("crqpt:r", "/l")  # seed path: no min-age
    assert captured[0][:3] == ["sync", "/l", "crqpt:r"]
    assert captured[0][-2:] == ["--min-age", "2m"]
    assert captured[1][:3] == ["copy", "/l", "crqpt:r"]
    assert captured[1][-2:] == ["--min-age", "2m"]
    assert "--min-age" not in captured[2]


def test_mount_args_min_free_configurable():
    args = rclone.mount_args("crqpt", Path("/x"), "vfs", "5G", "168h", min_free="10G")
    assert args[args.index("--vfs-cache-min-free-space") + 1] == "10G"
    default = rclone.mount_args("crqpt", Path("/x"), "vfs", "5G", "168h")
    assert default[default.index("--vfs-cache-min-free-space") + 1] == "80G"


def test_sync_oneway_backup_dir(monkeypatch):
    captured = []

    class FakeProc:
        stdout = ""

    monkeypatch.setattr(rclone, "_run", lambda args, **k: captured.append(args) or FakeProc())
    rclone.sync_oneway("crqpt:r", "/l", backup_dir="/trash")
    assert captured[0][-2:] == ["--backup-dir", "/trash"]
