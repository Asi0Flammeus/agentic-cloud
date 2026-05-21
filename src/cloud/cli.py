"""Typer CLI entry — ``cloud <subcommand>``."""

from __future__ import annotations

from typing import Annotated

import typer

from pathlib import Path

from cloud import __version__, config, doctor, mount as mount_mod, rclone


app = typer.Typer(no_args_is_help=True, add_completion=False, help="Multi-Nextcloud CLI on top of rclone.")
account_app = typer.Typer(no_args_is_help=True, help="Manage Nextcloud account remotes.")
app.add_typer(account_app, name="account")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cloud {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    pass


@account_app.command("add")
def account_add(
    name: Annotated[str, typer.Argument(help="Short name for the remote, e.g. 'crqpt'.")],
    url: Annotated[str, typer.Argument(help="WebDAV URL, e.g. https://nextcloud.example.com/remote.php/dav/files/<user>")],
    user: Annotated[str, typer.Option(prompt="WebDAV username")],
    password: Annotated[
        str,
        typer.Option(
            prompt="WebDAV password (Nextcloud app password recommended)",
            hide_input=True,
            confirmation_prompt=False,
        ),
    ],
) -> None:
    """Register a Nextcloud account. Idempotent — re-running updates in place."""
    try:
        obscured = rclone.obscure(password)
        rclone.write_remote(name, url=url, user=user, obscured_pass=obscured)
    except rclone.RcloneNotInstalled as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=3)
    except rclone.RcloneError as e:
        typer.echo(f"rclone error: {e.stderr}", err=True)
        raise typer.Exit(code=3)

    config.set_remote(name, url=url)
    typer.echo(f"✓ Added remote '{name}'")
    typer.echo(f"  config:  {config.config_path()}")
    typer.echo(f"  rclone:  {config.rclone_config_path()}")


@account_app.command("list")
def account_list() -> None:
    """List configured remotes."""
    remotes = config.list_remotes()
    if not remotes:
        typer.echo("No remotes configured. Try: cloud account add <name> <webdav-url>")
        return
    width = max(len(n) for n in remotes)
    for name, fields in remotes.items():
        url = fields.get("url", "?")
        mode = fields.get("mode", "—")
        typer.echo(f"  {name.ljust(width)}  {mode:10}  {url}")


@account_app.command("test")
def account_test(
    name: Annotated[str, typer.Argument(help="Remote name as configured.")],
) -> None:
    """Probe auth + connectivity (rclone lsd)."""
    if config.get_remote(name) is None:
        typer.echo(f"error: no such remote '{name}'", err=True)
        raise typer.Exit(code=1)
    if not rclone.has_remote(name):
        typer.echo(f"error: '{name}' is in config.toml but missing from rclone.conf — re-run `account add`", err=True)
        raise typer.Exit(code=1)
    try:
        rclone.lsd(name)
    except rclone.RcloneNotInstalled as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=3)
    except rclone.RcloneError as e:
        typer.echo(f"✗ {name}: {e.stderr}", err=True)
        raise typer.Exit(code=3)
    typer.echo(f"✓ {name}: reachable")


@account_app.command("remove")
def account_remove(
    name: Annotated[str, typer.Argument(help="Remote name to remove.")],
) -> None:
    """Remove a remote from both config.toml and rclone.conf."""
    removed_toml = config.remove_remote(name)
    removed_rclone = rclone.delete_remote(name)
    if not (removed_toml or removed_rclone):
        typer.echo(f"No such remote '{name}'.")
        return
    typer.echo(f"✓ Removed remote '{name}'")


@app.command("doctor")
def doctor_cmd() -> None:
    """Run diagnostics across rclone, FUSE, config files, and remotes."""
    raise typer.Exit(code=doctor.run())


@app.command("mount")
def mount_cmd(
    name: Annotated[str, typer.Argument(help="Remote name as configured.")],
    mode: Annotated[str, typer.Option("--mode", help="vfs | full")] = "vfs",
    mount_path: Annotated[str | None, typer.Option("--mount-path", help="Override mount path (default ~/clouds/<name>).")] = None,
    cache_size: Annotated[str, typer.Option("--cache-size", help="VFS cache size cap.")] = "5G",
    cache_age: Annotated[str, typer.Option("--cache-age", help="VFS cache max age.")] = "168h",
    auto: Annotated[bool, typer.Option("--auto/--no-auto", help="Install + enable systemd user unit for boot persistence.")] = False,
) -> None:
    """Mount a configured remote as a local FUSE filesystem."""
    if mode not in ("vfs", "full"):
        typer.echo(f"error: --mode must be vfs or full, got {mode!r}", err=True)
        raise typer.Exit(code=1)

    try:
        target, was_mounted = mount_mod.mount(
            name,
            mode=mode,
            mount_path=Path(mount_path) if mount_path else None,
            cache_size=cache_size,
            cache_age=cache_age,
        )
    except LookupError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)
    except FileExistsError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2)
    except rclone.RcloneNotInstalled as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=3)
    except rclone.RcloneError as e:
        typer.echo(f"mount error: {e.stderr or e}", err=True)
        raise typer.Exit(code=2)

    if was_mounted:
        typer.echo(f"✓ Already mounted at {target}")
    else:
        cache_note = f", cache: {mount_mod.cache_dir(name)}" if mode == "vfs" else ""
        typer.echo(f"✓ Mounted {name} at {target} ({mode}{cache_note})")

    if auto:
        from cloud import systemd
        try:
            systemd.install_and_enable(name)
            config.set_remote(name, **{**config.get_remote(name), "auto": True})
            typer.echo(f"✓ Systemd user unit cloud-{name}.service installed and enabled")
            if not systemd.lingering_enabled():
                typer.echo("⚠ user-linger is OFF — unit will only start when you log in.")
                typer.echo("  To survive reboots: sudo loginctl enable-linger $USER")
        except Exception as e:
            typer.echo(f"⚠ Mount succeeded but systemd install failed: {e}", err=True)


@app.command("unmount")
def unmount_cmd(
    name: Annotated[str, typer.Argument(help="Remote name to unmount.")],
) -> None:
    """Release a FUSE mount."""
    try:
        target, was_mounted = mount_mod.unmount(name)
    except rclone.RcloneError as e:
        msg = e.stderr or str(e)
        typer.echo(f"unmount error: {msg}", err=True)
        if "busy" in msg.lower() or "EBUSY" in msg:
            typer.echo("  hint: a process has a handle in the mount. Find it with: lsof +D <path>", err=True)
            typer.echo("        Or lazy-unmount with: fusermount3 -uz <path>", err=True)
        raise typer.Exit(code=2)
    if target is None:
        typer.echo(f"No mount configured for '{name}'.")
        return
    if not was_mounted:
        typer.echo(f"  '{name}' was not mounted (path: {target})")
        return
    typer.echo(f"✓ Unmounted {name} ({target})")


@app.command("status")
def status_cmd() -> None:
    """Show mount state and cache size per configured remote."""
    remotes = config.list_remotes()
    if not remotes:
        typer.echo("No remotes configured.")
        return
    rows = []
    width_name = max(len(n) for n in remotes)
    for name, fields in remotes.items():
        mode = fields.get("mode", "—")
        mount_str = fields.get("mount", "—")
        target = Path(mount_str).expanduser() if mount_str != "—" else None
        if target is None:
            state = "—"
            cache = "—"
        elif mount_mod.is_stale(target):
            state = "stale"
            cache = "—"
        elif mount_mod.is_mounted(target):
            state = "mounted"
            cache = _humanize_bytes(mount_mod.cache_size_bytes(mount_mod.cache_dir(name))) if mode == "vfs" else "—"
        else:
            state = "unmounted"
            cache = "—"
        rows.append((name, mode, state, mount_str, cache))
    typer.echo(f"  {'NAME'.ljust(width_name)}  MODE  STATE      MOUNT                          CACHE")
    for name, mode, state, mnt, cache in rows:
        typer.echo(f"  {name.ljust(width_name)}  {mode:4}  {state:9}  {mnt:30}  {cache}")


@app.command("push")
def push_cmd(
    local: Annotated[str, typer.Argument(help="Local file path.")],
    remote: Annotated[str, typer.Argument(help="<name>:<remote-path> destination, e.g. alysis:devis.pdf")],
    share: Annotated[bool, typer.Option("--share", help="After upload, create public link and print URL.")] = False,
) -> None:
    """Upload a single file. Optionally create a public share URL (gogcli parity)."""
    try:
        name, remote_path = config.parse_remote_path(remote)
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)
    if config.get_remote(name) is None:
        typer.echo(f"error: no such remote '{name}'", err=True)
        raise typer.Exit(code=1)
    if not remote_path:
        typer.echo("error: destination path is required, e.g. alysis:dest.pdf", err=True)
        raise typer.Exit(code=1)

    try:
        rclone.copyto(local, f"{name}:{remote_path}")
    except rclone.RcloneNotInstalled as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=3)
    except rclone.RcloneError as e:
        typer.echo(f"upload error: {e.stderr or e}", err=True)
        raise typer.Exit(code=3)
    typer.echo(f"✓ Uploaded {local} → {name}:{remote_path}")

    if share:
        from cloud import share as share_mod
        try:
            link = share_mod.OcsClient(name).create_link(remote_path)
        except share_mod.ShareError as e:
            typer.echo(f"share error: {e}", err=True)
            raise typer.Exit(code=4)
        typer.echo(f"✓ Public link:")
        typer.echo(f"  {link.url}")


@app.command("share")
def share_cmd(
    remote: Annotated[str, typer.Argument(help="<name>:<remote-path>")],
    revoke: Annotated[bool, typer.Option("--revoke", help="Remove all public links on this path.")] = False,
) -> None:
    """Create or revoke a public share link on a file already on the remote."""
    try:
        name, remote_path = config.parse_remote_path(remote)
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)
    if config.get_remote(name) is None:
        typer.echo(f"error: no such remote '{name}'", err=True)
        raise typer.Exit(code=1)
    if not remote_path:
        typer.echo("error: path is required, e.g. alysis:Documents/file.pdf", err=True)
        raise typer.Exit(code=1)

    from cloud import share as share_mod
    try:
        client = share_mod.OcsClient(name)
    except share_mod.ShareError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)

    if revoke:
        try:
            links = client.list_links(remote_path)
            for link in links:
                client.revoke(link.id)
        except share_mod.ShareError as e:
            typer.echo(f"share error: {e}", err=True)
            raise typer.Exit(code=4)
        typer.echo(f"✓ Revoked {len(links)} link(s) on {name}:{remote_path}")
        return

    try:
        link = client.create_link(remote_path)
    except share_mod.ShareError as e:
        typer.echo(f"share error: {e}", err=True)
        raise typer.Exit(code=4)
    typer.echo(f"✓ Public link on {name}:{remote_path}:")
    typer.echo(f"  {link.url}")


@app.command("share-list")
def share_list_cmd(
    name: Annotated[str, typer.Argument(help="Remote name.")],
) -> None:
    """List all public links on a remote (account-wide)."""
    if config.get_remote(name) is None:
        typer.echo(f"error: no such remote '{name}'", err=True)
        raise typer.Exit(code=1)
    from cloud import share as share_mod
    try:
        client = share_mod.OcsClient(name)
        links = client.list_links()
    except share_mod.ShareError as e:
        typer.echo(f"share error: {e}", err=True)
        raise typer.Exit(code=4)
    if not links:
        typer.echo(f"No public links on '{name}'.")
        return
    for link in links:
        typer.echo(f"  [{link.id}] {link.path}  →  {link.url}")


def _humanize_bytes(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"
