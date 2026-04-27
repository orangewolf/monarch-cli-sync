"""CLI entry point for monarch-cli-sync."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, timedelta

import click
from rich.console import Console
from rich.table import Table

from monarch_cli_sync import __version__
from monarch_cli_sync.config import load_config
from monarch_cli_sync.status import SyncResult, SyncStatus

console = Console()
err_console = Console(stderr=True)

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else (logging.ERROR if quiet else logging.INFO)
    logging.basicConfig(
        stream=sys.stderr,
        level=level,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )


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
@click.option("--verbose", "-v", is_flag=True, default=False, help="Debug logging.")
@click.pass_context
def doctor(ctx: click.Context, verbose: bool) -> None:
    """Check config, auth, and connectivity. Exits 0 if all good."""
    quiet = (ctx.obj or {}).get("quiet", False)
    _setup_logging(verbose or (ctx.obj or {}).get("verbose", False), quiet)

    from monarch_cli_sync.config import CONFIG_FILE, CONFIG_DIR
    from monarch_cli_sync.monarch.session import get_session_file
    from monarch_cli_sync.amazon.session import get_cookie_file

    warnings: list[str] = []
    errors: list[str] = []

    # 1. Config file
    if CONFIG_FILE.exists():
        if not quiet:
            console.print(f"[green]✓[/green] Config file found: {CONFIG_FILE}")
    else:
        warnings.append(f"Config file not found at {CONFIG_FILE}; using env vars / defaults.")
        if not quiet:
            console.print(f"[yellow]![/yellow] Config file not found: {CONFIG_FILE}")

    # 2. Monarch session
    monarch_session = get_session_file()
    if monarch_session.exists():
        if not quiet:
            console.print(f"[green]✓[/green] Monarch session found: {monarch_session}")
    else:
        warnings.append(f"Monarch session not found at {monarch_session}. Run 'auth monarch'.")
        if not quiet:
            console.print(f"[yellow]![/yellow] Monarch session not found: {monarch_session}")

    # 3. Amazon cookies
    amazon_cookies = get_cookie_file()
    if amazon_cookies.exists():
        if not quiet:
            console.print(f"[green]✓[/green] Amazon cookies found: {amazon_cookies}")
    else:
        warnings.append(f"Amazon cookies not found at {amazon_cookies}. Run 'auth amazon'.")
        if not quiet:
            console.print(f"[yellow]![/yellow] Amazon cookies not found: {amazon_cookies}")

    # 4. Optional Amazon WAF CAPTCHA solver
    config = load_config()
    solver_status = config.amazon.captcha_solver or "disabled"
    if not quiet:
        console.print(f"Amazon WAF auto-solve: {solver_status}")

    if errors:
        result = SyncResult(status=SyncStatus.ERROR, errors=errors, message="doctor found errors")
    elif warnings:
        result = SyncResult(status=SyncStatus.PARTIAL, warnings=warnings, message="doctor found warnings")
    else:
        result = SyncResult(status=SyncStatus.OK, message="all checks passed")

    click.echo(result.summary_line())
    sys.exit(result.exit_code)


@main.group()
def auth() -> None:
    """Manage authentication sessions."""


@auth.command("amazon")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Debug logging.")
@click.pass_context
def auth_amazon(ctx: click.Context, verbose: bool) -> None:
    """Interactive Amazon login — persists cookies for future headless runs."""
    quiet = (ctx.obj or {}).get("quiet", False)
    _setup_logging(verbose or (ctx.obj or {}).get("verbose", False), quiet)

    config = load_config()

    if config.amazon.captcha_solver:
        logger.info(
            "Amazon WAF auto-solve enabled (%s)", config.amazon.captcha_solver
        )

    try:
        from monarch_cli_sync.amazon.session import load_or_login as amazon_load_or_login
        amazon_load_or_login(config, force=True)
        if not quiet:
            console.print("[green]Amazon cookies saved successfully.[/green]")
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        result = SyncResult(status=SyncStatus.ERROR, message=str(exc))
        click.echo(result.summary_line())
        sys.exit(result.exit_code)

    result = SyncResult(status=SyncStatus.OK, message="amazon auth complete")
    click.echo(result.summary_line())
    sys.exit(0)


@auth.command("monarch")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Debug logging.")
@click.pass_context
def auth_monarch(ctx: click.Context, verbose: bool) -> None:
    """Interactive Monarch login — persists session for future headless runs."""
    quiet = (ctx.obj or {}).get("quiet", False)
    _setup_logging(verbose or (ctx.obj or {}).get("verbose", False), quiet)

    config = load_config()

    async def _run() -> None:
        from monarch_cli_sync.monarch.session import load_or_login
        mm = await load_or_login(config, force=True)
        if not quiet:
            console.print("[green]Monarch session saved successfully.[/green]")

    try:
        asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        result = SyncResult(status=SyncStatus.ERROR, message=str(exc))
        click.echo(result.summary_line())
        sys.exit(result.exit_code)

    result = SyncResult(status=SyncStatus.OK, message="monarch auth complete")
    click.echo(result.summary_line())
    sys.exit(0)


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
    verbose = verbose or (ctx.obj or {}).get("verbose", False)
    quiet = quiet or (ctx.obj or {}).get("quiet", False)
    _setup_logging(verbose, quiet)

    if not dry_run:
        result = SyncResult(status=SyncStatus.ERROR, message="sync without --dry-run not yet implemented")
        click.echo(result.summary_line())
        sys.exit(result.exit_code)

    config = load_config()

    # Determine date range
    if year is not None:
        start_date = date(year, 1, 1)
        end_date = date(year, 12, 31)
    else:
        num_days = days if days is not None else config.sync.default_days
        end_date = date.today()
        start_date = end_date - timedelta(days=num_days)

    async def _run_monarch() -> list:
        from monarch_cli_sync.monarch.session import load_or_login
        from monarch_cli_sync.monarch.transactions import fetch_amazon_transactions
        mm = await load_or_login(config)
        return await fetch_amazon_transactions(mm, start_date, end_date)

    def _run_amazon() -> list:
        from monarch_cli_sync.amazon.session import load_or_login as amazon_load_or_login
        from monarch_cli_sync.amazon.orders import fetch_orders
        session = amazon_load_or_login(config)
        return fetch_orders(session, start_date=start_date, end_date=end_date)

    try:
        transactions = asyncio.run(_run_monarch())
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        result = SyncResult(status=SyncStatus.ERROR, message=str(exc))
        click.echo(result.summary_line())
        sys.exit(result.exit_code)

    try:
        orders = _run_amazon()
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"[red]Error:[/red] {exc}")
        result = SyncResult(status=SyncStatus.ERROR, message=str(exc))
        click.echo(result.summary_line())
        sys.exit(result.exit_code)

    from monarch_cli_sync.sync.matcher import flatten_to_charges
    from monarch_cli_sync.sync.matcher import match as run_match

    charges = flatten_to_charges(orders)
    match_result = run_match(charges, transactions, force=force)

    if not quiet:
        _print_orders_table(orders, start_date, end_date)
        _print_transactions_table(transactions, start_date, end_date)
        _print_match_table(match_result)

    result = SyncResult(
        status=SyncStatus.OK,
        orders_inspected=len(orders),
        transactions_fetched=len(transactions),
        matched=len(match_result.matches),
        message="dry-run complete",
    )
    click.echo(result.summary_line())
    sys.exit(0)


def _print_orders_table(orders: list, start_date: date, end_date: date) -> None:
    table = Table(title=f"Amazon orders  {start_date} → {end_date}")
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Order #", style="white")
    table.add_column("Amount", justify="right", style="green")
    table.add_column("Items", style="dim")

    for order in orders:
        table.add_row(
            str(order.date),
            order.order_number,
            f"${order.amount:.2f}",
            order.items_desc[:60],
        )

    if orders:
        console.print(table)
    else:
        console.print(f"[yellow]No Amazon orders found between {start_date} and {end_date}.[/yellow]")


def _print_transactions_table(transactions: list, start_date: date, end_date: date) -> None:
    table = Table(title=f"Monarch 'Amazon' transactions  {start_date} → {end_date}")
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("Merchant", style="white")
    table.add_column("Account", style="dim")
    table.add_column("Amount", justify="right", style="green")
    table.add_column("Notes", style="dim")
    table.add_column("Pending", style="yellow")

    for tx in transactions:
        table.add_row(
            str(tx.date),
            tx.merchant_name,
            tx.account_name,
            f"${abs(tx.amount):.2f}",
            tx.notes[:40] if tx.notes else "",
            "yes" if tx.pending else "",
        )

    if transactions:
        console.print(table)
    else:
        console.print(f"[yellow]No Amazon transactions found between {start_date} and {end_date}.[/yellow]")


def _print_match_table(match_result) -> None:
    matched = match_result.matches
    unmatched_charges = match_result.unmatched_charges
    unmatched_txs = match_result.unmatched_transactions

    if matched:
        table = Table(title=f"Match preview ({len(matched)} matched)")
        table.add_column("Charge date", style="cyan", no_wrap=True)
        table.add_column("Order #", style="white")
        table.add_column("Amount", justify="right", style="green")
        table.add_column("Items", style="dim")
        table.add_column("Tx date", style="cyan", no_wrap=True)
        table.add_column("Merchant", style="white")
        table.add_column("Days Δ", justify="right", style="dim")

        for m in matched:
            delta = abs((m.transaction.date - m.charge.date).days)
            table.add_row(
                str(m.charge.date),
                m.charge.order_number,
                f"${m.charge.amount:.2f}",
                m.charge.items_desc[:40],
                str(m.transaction.date),
                m.transaction.merchant_name,
                str(delta),
            )
        console.print(table)
    else:
        console.print("[yellow]No matches found.[/yellow]")

    if unmatched_charges:
        console.print(f"[yellow]{len(unmatched_charges)} unmatched Amazon charge(s).[/yellow]")
    if unmatched_txs:
        console.print(f"[yellow]{len(unmatched_txs)} unmatched Monarch transaction(s).[/yellow]")


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show last run result."""
    result = SyncResult(status=SyncStatus.ERROR, message="status not yet implemented")
    click.echo(result.summary_line())
    sys.exit(result.exit_code)
