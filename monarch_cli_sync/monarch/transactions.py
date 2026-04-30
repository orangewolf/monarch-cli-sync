"""Fetch and normalize Monarch Money transactions."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime

from monarchmoney import MonarchMoney

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 1.0


@dataclass
class MonarchTransaction:
    id: str
    amount: float        # negative for debits (Monarch convention)
    date: date
    merchant_name: str
    account_name: str
    notes: str
    pending: bool

    def __str__(self) -> str:
        return (
            f"{self.date}  {self.merchant_name:<30}  "
            f"${abs(self.amount):>8.2f}  {'(pending)' if self.pending else ''}"
        )


def _parse_transaction(raw: dict) -> MonarchTransaction:
    tx_date = datetime.strptime(raw["date"], "%Y-%m-%d").date()
    return MonarchTransaction(
        id=raw["id"],
        amount=float(raw["amount"]),
        date=tx_date,
        merchant_name=(raw.get("merchant") or {}).get("name") or raw.get("plaidName") or "",
        account_name=(raw.get("account") or {}).get("displayName") or "",
        notes=raw.get("notes") or "",
        pending=bool(raw.get("pending", False)),
    )


async def update_transaction(
    mm: MonarchMoney,
    tx_id: str,
    notes: str,
    max_retries: int = _MAX_RETRIES,
    backoff_base: float = _BACKOFF_BASE_SECONDS,
) -> bool:
    """Set the notes field on a Monarch transaction. Returns True on success.

    Retries up to ``max_retries`` times on transient errors using exponential
    backoff (1 s, 2 s, 4 s, …).
    """
    for attempt in range(max_retries + 1):
        try:
            await mm.update_transaction(transaction_id=tx_id, notes=notes)
            logger.debug("Updated transaction %s notes=%r", tx_id, notes)
            return True
        except Exception:
            if attempt == max_retries:
                logger.exception(
                    "Failed to update transaction %s after %d attempt(s)",
                    tx_id,
                    attempt + 1,
                )
                return False
            wait = backoff_base * (2 ** attempt)
            logger.warning(
                "Transient error updating transaction %s; retrying in %.1fs (attempt %d/%d)",
                tx_id,
                wait,
                attempt + 1,
                max_retries,
            )
            await asyncio.sleep(wait)
    return False  # unreachable, satisfies type checkers


async def fetch_amazon_transactions(
    mm: MonarchMoney,
    start_date: date,
    end_date: date,
    limit: int = 500,
) -> list[MonarchTransaction]:
    """Fetch Monarch transactions matching 'Amazon' in the given date range."""
    logger.debug(
        "Fetching Monarch transactions from %s to %s (search='Amazon')",
        start_date,
        end_date,
    )
    response = await mm.get_transactions(
        limit=limit,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        search="Amazon",
    )

    results = response.get("allTransactions", {}).get("results", [])
    total = response.get("allTransactions", {}).get("totalCount", 0)
    logger.debug("Fetched %d / %d transactions.", len(results), total)

    transactions = [_parse_transaction(r) for r in results]
    return transactions
