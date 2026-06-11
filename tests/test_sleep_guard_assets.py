from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sleep_guard_service_uses_sleep_target_lifecycle():
    body = (ROOT / "systemd" / "agentic-cloud-sleep-guard.service").read_text()

    assert "Before=sleep.target" in body
    assert "WantedBy=sleep.target" in body
    assert "Type=oneshot" in body
    assert "RemainAfterExit=yes" in body
    assert "ExecStart=/usr/local/sbin/agentic-cloud-sleep-guard pre" in body
    assert "ExecStop=/usr/local/sbin/agentic-cloud-sleep-guard post" in body


def test_sleep_guard_script_stops_mounts_before_network_teardown():
    body = (ROOT / "scripts" / "agentic-cloud-sleep-guard").read_text()

    assert "/usr/bin/systemctl --user" in body
    assert "stop $AGENTIC_CLOUD_UNITS" in body
    assert "fusermount3 -uz" in body
    assert "umount -l" in body
    assert "start --no-block $AGENTIC_CLOUD_UNITS" in body
    assert "XDG_RUNTIME_DIR=\"/run/user/$uid\"" in body


def test_sleep_guard_installer_installs_root_files():
    body = (ROOT / "scripts" / "install-sleep-guard").read_text()

    assert "sudo install -m 0755" in body
    assert "/usr/local/sbin/agentic-cloud-sleep-guard" in body
    assert "/etc/systemd/system/agentic-cloud-sleep-guard.service" in body
    assert "/etc/default/agentic-cloud-sleep-guard" in body
    assert "sudo systemctl enable agentic-cloud-sleep-guard.service" in body
    assert "sudo systemctl restart agentic-cloud-sleep-guard.service" not in body
    assert "sudo systemctl start agentic-cloud-sleep-guard.service" not in body
