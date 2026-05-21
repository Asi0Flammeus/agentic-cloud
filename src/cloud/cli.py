"""Typer CLI entry — ``cloud <subcommand>``."""

from __future__ import annotations

from typing import Annotated

import typer

from cloud import __version__, config, doctor, rclone


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
