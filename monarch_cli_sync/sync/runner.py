"""Full sync runner: fetch → match → write → persist last_run.json."""

from __future__ import annotations

import asyncio
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
    account_selector: str | int | None = None,
    last_run_file: Path | None = None,
    shutdown_event: asyncio.Event | None = None,
) -> RunOutput:
    """Orchestrate full sync across all (or selected) Amazon accounts.

    1. Auth all configured Amazon accounts via load_all_sessions().
    2. Fetch orders from each authenticated account sequentially.
    3. Pool all orders, flatten to charges, match against Monarch transactions.
    4. Write matched order numbers as notes on Monarch transactions.
    5. Persist last_run.json.
    """
    from monarch_cli_sync.monarch.session import load_or_login
    from monarch_cli_sync.monarch.transactions import (
        fetch_amazon_transactions,
        update_transaction,
    )
    from monarch_cli_sync.amazon.session import load_all_sessions
    from monarch_cli_sync.amazon.orders import fetch_orders
    from monarch_cli_sync.sync.matcher import flatten_to_charges, match as run_match

    # --- Monarch auth & transaction fetch (single session, unchanged) --------
    mm = await load_or_login(config)
    transactions = await fetch_amazon_transactions(mm, start_date, end_date)

    # --- Amazon: authenticate all selected accounts --------------------------
    account_sessions = load_all_sessions(config, account_selector=account_selector)

    if not account_sessions:
        logger.error(
            "No Amazon accounts authenticated. "
            "Run 'monarch-cli-sync auth amazon' first."
        )
        from monarch_cli_sync.sync.matcher import MatchResult
        result = SyncResult(
            status=SyncStatus.AUTH_REQUIRED,
            transactions_fetched=len(transactions),
            errors=["No Amazon accounts could be authenticated."],
            message="auth required",
        )
        _write_last_run(result, last_run_file or LAST_RUN_FILE)
        return RunOutput(
            result=result,
            orders=[],
            transactions=transactions,
            match_result=MatchResult(matches=[], unmatched_charges=[], unmatched_transactions=list(transactions)),
        )

    # --- Fetch orders from all authenticated accounts sequentially -----------
    all_orders: list = []
    account_errors: list[str] = []
    account_results: list[dict] = []

    for acct, session in account_sessions:
        logger.info(
            "[amazon:%s] Fetching orders %s → %s", acct.label, start_date, end_date
        )
        try:
            orders = fetch_orders(
                session,
                start_date=start_date,
                end_date=end_date,
                request_delay_seconds=acct.request_delay_seconds,
                account_label=acct.label,
            )
            all_orders.extend(orders)
            account_results.append({"label": acct.label, "orders_fetched": len(orders)})
            logger.info("[amazon:%s] Fetched %d orders.", acct.label, len(orders))
        except Exception as exc:
            msg = f"Amazon account '{acct.label}': fetch failed: {exc}"
            logger.warning("[amazon:%s] Fetch failed: %s", acct.label, exc)
            account_errors.append(msg)
            account_results.append({"label": acct.label, "orders_fetched": 0, "error": str(exc)})

    # --- Match ---------------------------------------------------------------
    charges = flatten_to_charges(all_orders)
    match_result = run_match(charges, transactions, force=force)

    # --- Write path ----------------------------------------------------------
    updated = 0
    errors: list[str] = list(account_errors)

    if not dry_run:
        for m in match_result.matches:
            if shutdown_event is not None and shutdown_event.is_set():
                logger.warning("SIGTERM received; stopping write loop early.")
                errors.append(
                    "Interrupted by SIGTERM; some transactions may not have been updated."
                )
                break
            ok = await update_transaction(mm, m.transaction.id, m.charge.order_number)
            if ok:
                updated += 1
            else:
                errors.append(f"Failed to update tx {m.transaction.id}")

    matched_count = len(match_result.matches)

    # --- Status determination ------------------------------------------------
    if errors and updated == 0 and matched_count == 0:
        status = SyncStatus.ERROR
    elif errors and updated == 0 and matched_count > 0:
        # matches found but none written and errors present
        status = SyncStatus.ERROR
    elif errors:
        status = SyncStatus.PARTIAL
    elif matched_count == 0:
        status = SyncStatus.NO_CHANGES
    else:
        status = SyncStatus.OK

    result = SyncResult(
        status=status,
        orders_inspected=len(all_orders),
        transactions_fetched=len(transactions),
        matched=matched_count,
        updated=updated,
        skipped=matched_count if dry_run else 0,
        errors=errors,
        message="dry-run complete" if dry_run else "sync complete",
        account_results=account_results,
    )

    _write_last_run(result, last_run_file or LAST_RUN_FILE)
    return RunOutput(
        result=result,
        orders=all_orders,
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
