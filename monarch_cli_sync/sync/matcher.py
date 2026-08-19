"""Amount + date window matching logic for syncing Amazon orders to Monarch."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from monarch_cli_sync.amazon.orders import AmazonOrder
from monarch_cli_sync.monarch.transactions import MonarchTransaction

logger = logging.getLogger(__name__)


@dataclass
class AmazonCharge:
    """A single chargeable unit from an Amazon order."""

    order_number: str
    amount: float   # positive; negative = refund
    date: date
    items_desc: str
    account_label: str = ""  # which Amazon account this came from (logging only)


@dataclass
class Match:
    """A confirmed pairing of an Amazon charge to a Monarch transaction."""

    charge: AmazonCharge
    transaction: MonarchTransaction


@dataclass
class MatchResult:
    """Output of the matching pass."""

    matches: list[Match]
    unmatched_charges: list[AmazonCharge]
    unmatched_transactions: list[MonarchTransaction]


def flatten_to_charges(orders: list[AmazonOrder]) -> list[AmazonCharge]:
    """Convert a list of AmazonOrder objects to flat AmazonCharge records.

    Currently 1-to-1 (each order is one charge). Future phases may split by
    shipment once full_details are available.
    """
    return [
        AmazonCharge(
            order_number=o.order_number,
            amount=o.amount,
            date=o.date,
            items_desc=o.items_desc,
            account_label=o.account_label,
        )
        for o in orders
    ]


def match(
    charges: list[AmazonCharge],
    transactions: list[MonarchTransaction],
    date_window: int = 7,
    force: bool = False,
) -> MatchResult:
    """Match Amazon charges to Monarch transactions.

    Algorithm:
    - For each Monarch transaction (skip those with notes unless force=True),
      find candidate charges where amounts have opposite signs (charge = -tx) and date distance ≤ date_window.
    - Pick the candidate with the smallest date distance (ties: first in list).
    - Mark that charge as used so it cannot be re-used.
    - Produce a MatchResult with confirmed matches, unmatched charges, and
      unmatched transactions.
    """
    used: set[int] = set()   # indices into `charges` that have been claimed
    matches: list[Match] = []
    unmatched_transactions: list[MonarchTransaction] = []

    for tx in transactions:
        # Skip transactions that already have notes (unless --force)
        if tx.notes and not force:
            logger.debug(
                "Skipping tx %s (%s) — notes already present (use --force to override)",
                tx.id,
                tx.merchant_name,
            )
            unmatched_transactions.append(tx)
            continue

        # Gather candidates: sign-aware amount match (purchase +X → debit −X;
        # refund −X → credit +X) and within date window, not used
        candidates: list[tuple[int, int, AmazonCharge]] = []  # (index, distance, charge)
        for idx, charge in enumerate(charges):
            if idx in used:
                continue
            if charge.amount != -tx.amount:
                continue
            distance = abs((tx.date - charge.date).days)
            if distance <= date_window:
                candidates.append((idx, distance, charge))

        if not candidates:
            logger.debug(
                "No match found for tx %s (%.2f on %s)",
                tx.id,
                tx.amount,
                tx.date,
            )
            unmatched_transactions.append(tx)
            continue

        # Tie-break: smallest date distance; stable (preserves list order for equal distances)
        best_idx, best_dist, best_charge = min(candidates, key=lambda t: t[1])

        logger.debug(
            "Matched tx %s (%.2f on %s) → order %s (distance %d day(s))",
            tx.id,
            tx.amount,
            tx.date,
            best_charge.order_number,
            best_dist,
        )
        used.add(best_idx)
        matches.append(Match(charge=best_charge, transaction=tx))

    unmatched_charges = [c for idx, c in enumerate(charges) if idx not in used]

    return MatchResult(
        matches=matches,
        unmatched_charges=unmatched_charges,
        unmatched_transactions=unmatched_transactions,
    )
