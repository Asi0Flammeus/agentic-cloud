"""Nextcloud OCS share API client — public links for files already on the remote.

We use the OCS API (not `rclone link`) because we also need listing and revocation,
which rclone doesn't expose. Auth = WebDAV user + revealed app password.
"""

from __future__ import annotations

import base64
import configparser
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from cloud import config, rclone


class ShareError(Exception):
    pass


@dataclass(frozen=True)
class Share:
    id: str
    url: str
    token: str
    path: str  # path within Files root, e.g. "Documents/x.pdf"


def _rclone_remote_section(name: str) -> dict[str, str]:
    """Read the [name] section from rclone.conf."""
    path = config.rclone_config_path()
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path)
    if name not in parser:
        raise ShareError(f"no rclone remote '{name}' (run: cloud account add {name} <url>)")
    return dict(parser[name])


def _ocs_base_url(webdav_url: str) -> str:
    """Strip the WebDAV path tail to recover the Nextcloud root URL.

    >>> _ocs_base_url("https://drive.alysis.cat/remote.php/dav/files/asi0")
    'https://drive.alysis.cat'
    """
    parsed = urlparse(webdav_url)
    return f"{parsed.scheme}://{parsed.netloc}"


class OcsClient:
    def __init__(self, name: str):
        section = _rclone_remote_section(name)
        self.name = name
        self.base_url = _ocs_base_url(section["url"])
        self.user = section["user"]
        self._password = rclone.reveal(section["pass"])

    def _auth_header(self) -> str:
        token = base64.b64encode(f"{self.user}:{self._password}".encode()).decode()
        return f"Basic {token}"

    def _request(self, method: str, path: str, *, data: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = urllib.parse.urlencode(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", self._auth_header())
        req.add_header("OCS-APIRequest", "true")
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode())
                msg = payload.get("ocs", {}).get("meta", {}).get("message", str(e))
            except Exception:
                msg = str(e)
            raise ShareError(f"OCS {method} {path} failed: {msg}") from e
        except urllib.error.URLError as e:
            raise ShareError(f"OCS network error: {e}") from e
        meta = payload.get("ocs", {}).get("meta", {})
        if meta.get("status") != "ok":
            raise ShareError(f"OCS error: {meta.get('message', payload)}")
        return payload["ocs"]["data"]

    def create_link(self, remote_path: str) -> Share:
        """Create a public read-only share on *remote_path* (Files-root-relative)."""
        data = self._request(
            "POST",
            "/ocs/v2.php/apps/files_sharing/api/v1/shares",
            data={"path": "/" + remote_path.lstrip("/"), "shareType": "3", "permissions": "1"},
        )
        return Share(id=str(data["id"]), url=data["url"], token=data["token"], path=data.get("path", remote_path))

    def list_links(self, remote_path: str | None = None) -> list[Share]:
        """List public shares. If *remote_path* given, scope to that path."""
        path = "/ocs/v2.php/apps/files_sharing/api/v1/shares"
        if remote_path is not None:
            path += "?" + urllib.parse.urlencode({"path": "/" + remote_path.lstrip("/")})
        data = self._request("GET", path)
        # /shares returns a list under data
        shares = data if isinstance(data, list) else data.get("element", [])
        out: list[Share] = []
        for s in shares:
            if str(s.get("share_type")) != "3":
                continue
            out.append(
                Share(
                    id=str(s["id"]),
                    url=s.get("url", ""),
                    token=s.get("token", ""),
                    path=s.get("path", ""),
                )
            )
        return out

    def revoke(self, share_id: str) -> None:
        self._request("DELETE", f"/ocs/v2.php/apps/files_sharing/api/v1/shares/{share_id}")
