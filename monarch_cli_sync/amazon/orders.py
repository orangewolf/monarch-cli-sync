"""Fetch and normalize Amazon order history."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from amazonorders.exception import AmazonOrdersError
from amazonorders.orders import AmazonOrders
from amazonorders.session import AmazonSession

logger = logging.getLogger(__name__)


@dataclass
class AmazonOrder:
    order_number: str
    amount: float       # positive dollar amount, e.g. 24.99
    date: date
    items_desc: str     # short description of items in the order

    def __str__(self) -> str:
        return (
            f"{self.date}  #{self.order_number:<20}  "
            f"${self.amount:>8.2f}  {self.items_desc[:40]}"
        )


def _normalize_order(raw) -> AmazonOrder | None:
    """Convert an amazonorders.Order to AmazonOrder. Returns None if unparseable."""
    order_date = getattr(raw, "order_placed_date", None)
    if order_date is None:
        return None

    amount_str = (getattr(raw, "grand_total", None) or "").replace("$", "").replace(",", "").strip()
    try:
        amount = float(amount_str) if amount_str else 0.0
    except ValueError:
        amount = 0.0

    items = getattr(raw, "items", []) or []
    items_desc = ", ".join(
        str(getattr(item, "title", "") or "") for item in items
    )

    return AmazonOrder(
        order_number=str(raw.order_number or ""),
        amount=amount,
        date=order_date,
        items_desc=items_desc,
    )


def fetch_orders(
    session: AmazonSession,
    year: int | None = None,
    days: int = 30,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[AmazonOrder]:
    """Fetch Amazon orders and return normalized AmazonOrder objects.

    If year is given, fetches all orders for that year.
    Otherwise fetches within the last `days` days (or the explicit start/end range).
    Results are filtered to the computed date range.
    """
    if year is not None:
        start_date = date(year, 1, 1)
        end_date = date(year, 12, 31)
    elif start_date is None or end_date is None:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

    years_to_fetch = sorted({start_date.year, end_date.year})

    logger.debug(
        "Fetching Amazon orders for year(s) %s (filtering %s → %s)",
        years_to_fetch,
        start_date,
        end_date,
    )

    client = AmazonOrders(session)
    raw_orders: list = []
    for yr in years_to_fetch:
        try:
            yr_orders = client.get_order_history(year=yr)
            raw_orders.extend(yr_orders)
            logger.debug("Fetched %d raw orders for %d", len(yr_orders), yr)
        except AmazonOrdersError:
            raise

    orders = []
    for raw in raw_orders:
        normalized = _normalize_order(raw)
        if normalized is None:
            continue
        if start_date <= normalized.date <= end_date:
            orders.append(normalized)

    logger.debug("Returning %d orders in date range.", len(orders))
    return orders
