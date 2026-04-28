"""Full sync runner: fetch → match → write → persist last_run.json."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from monarch_cli_sync.config import AppConfig
from monarch_cli_sync.status import SyncResult, SyncStatus

logger = logging.getLogger(__name__)

LAST_RUN_FILE = Path("~/.config/monarch-cli-sync/last_run.json").expanduser()


@dataclass
class RunOutput:
    result: SyncResult
    orders: list
    transactions: list
    match_result: object  # MatchResult from sync.matcher


async def run_sync(
    config: AppConfig,
    start_date: date,
    end_date: date,
    dry_run: bool = False,
    force: bool = False,
    last_run_file: Path | None = None,
) -> RunOutput:
    """Orchestrate full sync: fetch → match → write → persist last_run.json."""
    from monarch_cli_sync.monarch.session import load_or_login
    from monarch_cli_sync.monarch.transactions import (
        fetch_amazon_transactions,
        update_transaction,
    )
    from monarch_cli_sync.amazon.session import load_or_login as amazon_load_or_login
    from monarch_cli_sync.amazon.orders import fetch_orders
    from monarch_cli_sync.sync.matcher import flatten_to_charges, match as run_match

    mm = await load_or_login(config)
    transactions = await fetch_amazon_transactions(mm, start_date, end_date)

    session = amazon_load_or_login(config)
    orders = fetch_orders(session, start_date=start_date, end_date=end_date)

    charges = flatten_to_charges(orders)
    match_result = run_match(charges, transactions, force=force)

    updated = 0
    errors: list[str] = []

    if not dry_run:
        for m in match_result.matches:
            ok = await update_transaction(mm, m.transaction.id, m.charge.order_number)
            if ok:
                updated += 1
            else:
                errors.append(f"Failed to update tx {m.transaction.id}")

    matched_count = len(match_result.matches)

    if errors and updated == 0:
        status = SyncStatus.ERROR
    elif errors:
        status = SyncStatus.PARTIAL
    elif matched_count == 0:
        status = SyncStatus.NO_CHANGES
    else:
        status = SyncStatus.OK

    result = SyncResult(
        status=status,
        orders_inspected=len(orders),
        transactions_fetched=len(transactions),
        matched=matched_count,
        updated=updated,
        skipped=matched_count if dry_run else 0,
        errors=errors,
        message="dry-run complete" if dry_run else "sync complete",
    )

    _write_last_run(result, last_run_file or LAST_RUN_FILE)
    return RunOutput(
        result=result,
        orders=orders,
        transactions=transactions,
        match_result=match_result,
    )


def _write_last_run(result: SyncResult, path: Path) -> None:
    """Persist last_run.json for the status subcommand."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = result.to_dict()
        data["timestamp"] = datetime.utcnow().isoformat() + "Z"
        path.write_text(json.dumps(data, indent=2))
        logger.debug("Wrote last_run.json → %s", path)
    except Exception:
        logger.exception("Could not write last_run.json to %s", path)
