from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch

import pytest

from cloud import config, share


@pytest.fixture(autouse=True)
def isolated_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    yield tmp_path


def _seed_rclone_conf(name: str, url: str, user: str, obscured_pass: str):
    """Write a minimal rclone.conf with one [name] section."""
    path = config.rclone_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"[{name}]\ntype=webdav\nvendor=nextcloud\nurl={url}\nuser={user}\npass={obscured_pass}\n"
    )


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_ocs_base_url():
    assert share._ocs_base_url("https://drive.alysis.cat/remote.php/dav/files/asi0") == "https://drive.alysis.cat"
    assert share._ocs_base_url("https://nc.example.com:8443/remote.php/dav/files/x") == "https://nc.example.com:8443"


def test_create_link_parses_url(monkeypatch):
    _seed_rclone_conf("alysis", "https://drive.alysis.cat/remote.php/dav/files/asi0", "asi0", "OBSCURED")
    monkeypatch.setattr("cloud.rclone.reveal", lambda s: "cleartext")

    fake_payload = {
        "ocs": {
            "meta": {"status": "ok", "statuscode": 200},
            "data": {"id": 42, "url": "https://drive.alysis.cat/s/abc123", "token": "abc123", "path": "/test.pdf"},
        }
    }

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data.decode() if req.data else None
        return FakeResponse(fake_payload)

    with patch("urllib.request.urlopen", fake_urlopen):
        link = share.OcsClient("alysis").create_link("test.pdf")

    assert link.url == "https://drive.alysis.cat/s/abc123"
    assert link.id == "42"
    assert "/ocs/v2.php/apps/files_sharing/api/v1/shares" in captured["url"]
    assert captured["method"] == "POST"
    assert "Ocs-apirequest" in captured["headers"]  # urllib lower-cases after the first letter
    assert "shareType=3" in captured["body"]
    assert "permissions=1" in captured["body"]


def test_list_links_filters_share_type_3(monkeypatch):
    _seed_rclone_conf("alysis", "https://drive.alysis.cat/remote.php/dav/files/asi0", "asi0", "X")
    monkeypatch.setattr("cloud.rclone.reveal", lambda s: "cleartext")
    fake_payload = {
        "ocs": {
            "meta": {"status": "ok", "statuscode": 200},
            "data": [
                {"id": 1, "share_type": 0, "url": "", "token": "u1", "path": "/internal"},
                {"id": 2, "share_type": 3, "url": "https://drive.alysis.cat/s/aaa", "token": "aaa", "path": "/pub1.pdf"},
                {"id": 3, "share_type": 3, "url": "https://drive.alysis.cat/s/bbb", "token": "bbb", "path": "/pub2.pdf"},
            ],
        }
    }
    with patch("urllib.request.urlopen", lambda req, timeout=None: FakeResponse(fake_payload)):
        links = share.OcsClient("alysis").list_links()
    assert len(links) == 2
    assert {l.id for l in links} == {"2", "3"}


def test_no_rclone_remote_raises(monkeypatch):
    monkeypatch.setattr("cloud.rclone.reveal", lambda s: "cleartext")
    with pytest.raises(share.ShareError, match="no rclone remote"):
        share.OcsClient("ghost")


def test_meta_status_failure_raises(monkeypatch):
    _seed_rclone_conf("alysis", "https://drive.alysis.cat/remote.php/dav/files/asi0", "asi0", "X")
    monkeypatch.setattr("cloud.rclone.reveal", lambda s: "cleartext")
    bad_payload = {"ocs": {"meta": {"status": "failure", "statuscode": 404, "message": "Not found"}, "data": []}}
    with patch("urllib.request.urlopen", lambda req, timeout=None: FakeResponse(bad_payload, 200)):
        with pytest.raises(share.ShareError, match="Not found"):
            share.OcsClient("alysis").create_link("missing.pdf")


def test_parse_remote_path():
    assert config.parse_remote_path("alysis:foo/bar.pdf") == ("alysis", "foo/bar.pdf")
    assert config.parse_remote_path("alysis:/foo/bar.pdf") == ("alysis", "foo/bar.pdf")
    assert config.parse_remote_path("alysis:") == ("alysis", "")
    with pytest.raises(ValueError):
        config.parse_remote_path("alysis")
    with pytest.raises(ValueError):
        config.parse_remote_path(":foo")
