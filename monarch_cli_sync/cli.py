"""CLI entry point for monarch-cli-sync."""

from __future__ import annotations

import sys
import click
from rich.console import Console

from monarch_cli_sync import __version__
from monarch_cli_sync.status import SyncResult, SyncStatus

console = Console()
err_console = Console(stderr=True)


@click.group()
@click.version_option(__version__, prog_name="monarch-cli-sync")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging.")
@click.option("--quiet", "-q", is_flag=True, default=False, help="Suppress non-error output.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output JSON to stdout.")
@click.pass_context
def main(ctx: click.Context, verbose: bool, quiet: bool, output_json: bool) -> None:
    """Sync Amazon order history into Monarch Money."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet
    ctx.obj["output_json"] = output_json


@main.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Check config, auth, and connectivity. Exits 0 if all good."""
    result = SyncResult(status=SyncStatus.ERROR, message="doctor not yet implemented")
    click.echo(result.summary_line())
    sys.exit(result.exit_code)


@main.group()
def auth() -> None:
    """Manage authentication sessions."""


@auth.command("amazon")
@click.pass_context
def auth_amazon(ctx: click.Context) -> None:
    """Interactive Amazon login — persists cookies for future headless runs."""
    result = SyncResult(status=SyncStatus.ERROR, message="auth amazon not yet implemented")
    click.echo(result.summary_line())
    sys.exit(result.exit_code)


@auth.command("monarch")
@click.pass_context
def auth_monarch(ctx: click.Context) -> None:
    """Interactive Monarch login — persists session for future headless runs."""
    result = SyncResult(status=SyncStatus.ERROR, message="auth monarch not yet implemented")
    click.echo(result.summary_line())
    sys.exit(result.exit_code)


@main.command()
@click.option("--days", default=None, type=int, help="Days back to look (default: 30).")
@click.option("--year", default=None, type=int, help="Sync a full calendar year.")
@click.option("--dry-run", is_flag=True, default=False, help="Match but do not write to Monarch.")
@click.option("--force", is_flag=True, default=False, help="Overwrite existing notes.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output JSON SyncResult.")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Debug logging.")
@click.option("--quiet", "-q", is_flag=True, default=False, help="Suppress non-error output.")
@click.pass_context
def sync(
    ctx: click.Context,
    days: int | None,
    year: int | None,
    dry_run: bool,
    force: bool,
    output_json: bool,
    verbose: bool,
    quiet: bool,
) -> None:
    """Run full sync (Amazon → Monarch)."""
    result = SyncResult(status=SyncStatus.ERROR, message="sync not yet implemented")
    click.echo(result.summary_line())
    sys.exit(result.exit_code)


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show last run result."""
    result = SyncResult(status=SyncStatus.ERROR, message="status not yet implemented")
    click.echo(result.summary_line())
    sys.exit(result.exit_code)
