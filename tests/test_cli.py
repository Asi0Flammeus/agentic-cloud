from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cloud import bisync as bisync_mod
from cloud.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    yield tmp_path


_OK = {"label": "x", "ok": True, "skipped": False, "resynced": False, "error": None}


def test_sync_run_all_flag_parses(monkeypatch):
    # regression guard: the systemd unit runs `cloud sync run --all`
    monkeypatch.setattr(bisync_mod, "run_all", lambda labels=None: [_OK])
    result = runner.invoke(app, ["sync", "run", "--all"])
    assert result.exit_code == 0, result.stdout
    assert "x" in result.stdout


def test_sync_run_no_arg_means_all(monkeypatch):
    seen = {}

    def fake(labels=None):
        seen["labels"] = labels
        return []

    monkeypatch.setattr(bisync_mod, "run_all", fake)
    result = runner.invoke(app, ["sync", "run"])
    assert result.exit_code == 0
    assert seen["labels"] is None


def test_sync_run_single_label(monkeypatch):
    seen = {}

    def fake(labels=None):
        seen["labels"] = labels
        return [_OK]

    monkeypatch.setattr(bisync_mod, "run_all", fake)
    result = runner.invoke(app, ["sync", "run", "downloads"])
    assert result.exit_code == 0
    assert seen["labels"] == ["downloads"]
