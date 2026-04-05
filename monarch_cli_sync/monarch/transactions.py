"""Fetch and normalize Monarch Money transactions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from monarchmoney import MonarchMoney

logger = logging.getLogger(__name__)


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
