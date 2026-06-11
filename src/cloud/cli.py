"""Typer CLI entry — ``cloud <subcommand>``."""

from __future__ import annotations

from typing import Annotated

import typer

from pathlib import Path

from cloud import __version__, bisync as bisync_mod, config, doctor, mount as mount_mod, rclone


app = typer.Typer(no_args_is_help=True, add_completion=False, help="Multi-Nextcloud CLI on top of rclone.")
account_app = typer.Typer(no_args_is_help=True, help="Manage Nextcloud account remotes.")
app.add_typer(account_app, name="account")
sync_app = typer.Typer(no_args_is_help=True, help="Bidirectional rclone bisync of always-local folders.")
app.add_typer(sync_app, name="sync")


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
    cache_size: Annotated[str | None, typer.Option("--cache-size", help="VFS cache size cap (default: config value, else 5G).")] = None,
    cache_age: Annotated[str | None, typer.Option("--cache-age", help="VFS cache max age (default: config value, else 168h).")] = None,
    min_free: Annotated[str | None, typer.Option("--min-free", help="Min free disk space the VFS cache must leave (default: config value, else 80G).")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", help="Anchored rclone pattern (e.g. /Downloads/) to hide a path owned by `cloud sync`. Repeatable. Requires unmount+remount to change.")] = None,
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
            min_free=min_free,
            exclude=exclude or None,
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


def _default_label(remote_path: str) -> str:
    """crqpt:Videos/raw -> 'videos-raw'; Downloads -> 'downloads'."""
    return remote_path.strip("/").replace("/", "-").lower() or "root"


@sync_app.command("add")
def sync_add(
    remote: Annotated[str, typer.Argument(help="<name>:<remote-path>, e.g. crqpt:Downloads")],
    local: Annotated[str, typer.Argument(help="Local directory, e.g. ~/local/Downloads")],
    label: Annotated[str | None, typer.Option("--label", help="Pair name (default: derived from remote path).")] = None,
    interval: Annotated[int, typer.Option("--interval", help="Timer cadence in seconds.")] = 60,
    strategy: Annotated[str, typer.Option("--strategy", help="Sync engine: bisync (bidirectional), mirror (one-way local→remote), queue (push up, mirror the remote queue back).")] = "bisync",
    seed: Annotated[bool, typer.Option("--seed/--no-seed", help="Copy remote->local before the baseline resync. Use --no-seed if local is already populated.")] = True,
) -> None:
    """Register a sync pair and establish the baseline (seed from remote, then resync).

    Seeding before the resync guarantees local ⊇ remote, so the baseline can never
    delete remote content.
    """
    try:
        name, remote_path = config.parse_remote_path(remote)
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1)
    if config.get_remote(name) is None:
        typer.echo(f"error: no such remote '{name}'", err=True)
        raise typer.Exit(code=1)
    if not remote_path:
        typer.echo("error: a remote sub-path is required, e.g. crqpt:Downloads", err=True)
        raise typer.Exit(code=1)
    if strategy not in config.SYNC_STRATEGIES:
        typer.echo(f"error: --strategy must be one of {', '.join(config.SYNC_STRATEGIES)}", err=True)
        raise typer.Exit(code=1)

    lbl = label or _default_label(remote_path)
    pair = {"label": lbl, "local": local, "remote": f"{name}:{remote_path}", "interval": interval, "strategy": strategy}
    config.set_sync_pair(lbl, local=local, remote=f"{name}:{remote_path}", interval=interval, strategy=strategy)

    typer.echo(f"→ initializing '{lbl}'  {name}:{remote_path}  ↔  {local}")
    if seed:
        typer.echo("  seeding local from remote (one-way copy, may take a while)…")
    try:
        result = bisync_mod.initialize(pair) if seed else bisync_mod.run_pair(pair, force_resync=True)
    except rclone.RcloneNotInstalled as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=3)
    if not result["ok"]:
        typer.echo(f"✗ {lbl}: {result['error']}", err=True)
        typer.echo("  (pair registered; fix the cause and re-run: cloud sync add … or cloud sync run)", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"✓ '{lbl}' synced and registered (interval {interval}s)")
    typer.echo("  enable the background timer with: cloud sync auto")


@sync_app.command("run")
def sync_run(
    label: Annotated[str | None, typer.Argument(help="Run only this pair (default: all).")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Run every pair (explicit; same as passing no label).")] = False,
) -> None:
    """Run one bidirectional cycle for all pairs (or just one). Used by the systemd timer."""
    results = bisync_mod.run_all(labels=[label] if label else None)
    if not results:
        typer.echo("No sync pairs configured." if not label else f"No such pair '{label}'.")
        return
    failed = 0
    for r in results:
        if r["skipped"]:
            typer.echo(f"  ↷ {r['label']}: already running, skipped")
        elif r["ok"]:
            tag = " (resync)" if r["resynced"] else ""
            typer.echo(f"  ✓ {r['label']}{tag}")
        else:
            failed += 1
            typer.echo(f"  ✗ {r['label']}: {r['error']}", err=True)
    if failed:
        raise typer.Exit(code=2)


@sync_app.command("watch")
def sync_watch() -> None:
    """Watch local folders and push to remote on change (foreground; used by the watch service)."""
    if not config.list_sync_pairs():
        typer.echo("No sync pairs configured.")
        return
    typer.echo("watching local folders for changes (Ctrl-C to stop)…")
    try:
        bisync_mod.watch(on_sync=lambda label: typer.echo(f"  ↑ pushed {label}"))
    except FileNotFoundError:
        typer.echo("error: inotifywait not found — install inotify-tools", err=True)
        raise typer.Exit(code=3)
    except KeyboardInterrupt:
        raise typer.Exit(code=0)


@sync_app.command("status")
def sync_status() -> None:
    """Show configured sync pairs and their baseline state."""
    pairs = config.list_sync_pairs()
    if not pairs:
        typer.echo("No sync pairs configured. Try: cloud sync add crqpt:Downloads ~/local/Downloads")
        return
    from cloud import systemd
    timer = "on" if systemd.sync_timer_enabled() else "off"
    watch = "on" if systemd.sync_watch_enabled() else "off"
    width = max(len(p["label"]) for p in pairs)
    typer.echo(f"  timer: {timer}   instant-push (watch): {watch}")
    typer.echo(f"  {'LABEL'.ljust(width)}  STRATEGY  STATE          REMOTE → LOCAL")
    for p in pairs:
        state = "uninitialized" if bisync_mod.needs_resync(p["label"]) else "synced"
        typer.echo(f"  {p['label'].ljust(width)}  {p.get('strategy', 'bisync'):8}  {state:13}  {p['remote']} → {p['local']}")


@sync_app.command("health")
def sync_health() -> None:
    """Per-pair sync health as JSON (consumed by the eww pill; humans welcome too)."""
    import json as json_mod

    typer.echo(json_mod.dumps(bisync_mod.load_health(), indent=1))


@sync_app.command("auto")
def sync_auto(
    on: Annotated[bool, typer.Option("--on/--off", help="Enable or disable the background sync timer.")] = True,
    interval: Annotated[int, typer.Option("--interval", help="Timer cadence in seconds (on).")] = 60,
) -> None:
    """Install/enable (or remove) the systemd user timer that runs `cloud sync run --all`."""
    from cloud import systemd
    if not on:
        removed = systemd.uninstall_sync()
        typer.echo("✓ Sync timer disabled and removed." if removed else "No sync timer was installed.")
        return
    try:
        _svc, tmr = systemd.enable_sync(interval)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2)
    try:
        systemd.enable_watch()
        watch_note = "✓ Instant push (inotify watcher) enabled"
    except Exception as e:
        watch_note = f"⚠ Instant-push watcher not enabled: {e}"
    typer.echo(f"✓ Sync timer enabled (every {interval}s)")
    typer.echo(f"  {tmr}")
    typer.echo(f"  {watch_note}")
    if not systemd.lingering_enabled():
        typer.echo("⚠ user-linger is OFF — the timer only runs while you're logged in.")
        typer.echo("  To run across logouts/reboots: sudo loginctl enable-linger $USER")


@sync_app.command("remove")
def sync_remove(
    label: Annotated[str, typer.Argument(help="Pair label to remove.")],
) -> None:
    """Unregister a sync pair (does NOT delete local files or remote data)."""
    if config.remove_sync_pair(label):
        typer.echo(f"✓ Removed sync pair '{label}' (local files and remote untouched).")
    else:
        typer.echo(f"No such sync pair '{label}'.")


def _humanize_bytes(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"
