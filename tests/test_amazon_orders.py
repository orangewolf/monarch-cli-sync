"""Tests for amazon/orders.py."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from monarch_cli_sync.amazon.orders import AmazonOrder, _normalize_order, fetch_orders


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_order(
    order_number="112-1234567-8901234",
    grand_total="24.99",
    order_placed_date=date(2024, 3, 15),
    item_titles=("USB Cable", "Phone Stand"),
):
    """Build a mock Order object as returned by the amazonorders library."""
    order = MagicMock()
    order.order_number = order_number
    order.grand_total = grand_total
    order.order_placed_date = order_placed_date

    items = []
    for title in item_titles:
        item = MagicMock()
        item.title = title
        items.append(item)
    order.items = items

    return order


# ---------------------------------------------------------------------------
# _normalize_order
# ---------------------------------------------------------------------------

def test_normalize_order_basic():
    raw = _make_raw_order()
    result = _normalize_order(raw)
    assert result is not None
    assert result.order_number == "112-1234567-8901234"
    assert result.amount == 24.99
    assert result.date == date(2024, 3, 15)
    assert "USB Cable" in result.items_desc
    assert "Phone Stand" in result.items_desc


def test_normalize_order_missing_date_returns_none():
    raw = _make_raw_order()
    raw.order_placed_date = None
    assert _normalize_order(raw) is None


def test_normalize_order_bad_amount_defaults_to_zero():
    raw = _make_raw_order(grand_total="not-a-number")
    result = _normalize_order(raw)
    assert result is not None
    assert result.amount == 0.0


def test_normalize_order_amount_with_commas():
    raw = _make_raw_order(grand_total="1,234.56")
    result = _normalize_order(raw)
    assert result is not None
    assert result.amount == 1234.56


def test_normalize_order_no_items():
    raw = _make_raw_order(item_titles=())
    result = _normalize_order(raw)
    assert result is not None
    assert result.items_desc == ""


def test_normalize_order_empty_grand_total():
    raw = _make_raw_order(grand_total="")
    result = _normalize_order(raw)
    assert result is not None
    assert result.amount == 0.0


# ---------------------------------------------------------------------------
# fetch_orders
# ---------------------------------------------------------------------------

def _make_session():
    session = MagicMock()
    session.is_authenticated = True
    return session


def test_fetch_orders_returns_filtered_results():
    raw1 = _make_raw_order(order_number="A", order_placed_date=date(2024, 3, 10))
    raw2 = _make_raw_order(order_number="B", order_placed_date=date(2024, 3, 20))
    raw3 = _make_raw_order(order_number="C", order_placed_date=date(2024, 2, 1))

    with patch("monarch_cli_sync.amazon.orders.AmazonOrders") as MockAmazonOrders:
        MockAmazonOrders.return_value.get_order_history.return_value = [raw1, raw2, raw3]
        results = fetch_orders(
            _make_session(),
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
        )

    assert len(results) == 2
    order_numbers = {o.order_number for o in results}
    assert order_numbers == {"A", "B"}


def test_fetch_orders_by_year():
    raw = _make_raw_order(order_placed_date=date(2024, 6, 15))

    with patch("monarch_cli_sync.amazon.orders.AmazonOrders") as MockAmazonOrders:
        MockAmazonOrders.return_value.get_order_history.return_value = [raw]
        results = fetch_orders(_make_session(), year=2024)

    assert len(results) == 1
    MockAmazonOrders.return_value.get_order_history.assert_called_once_with(year=2024)


def test_fetch_orders_empty():
    with patch("monarch_cli_sync.amazon.orders.AmazonOrders") as MockAmazonOrders:
        MockAmazonOrders.return_value.get_order_history.return_value = []
        results = fetch_orders(_make_session(), days=30)

    assert results == []


def test_fetch_orders_default_days_uses_30():
    """When no date args given, defaults to last 30 days."""
    with patch("monarch_cli_sync.amazon.orders.AmazonOrders") as MockAmazonOrders:
        MockAmazonOrders.return_value.get_order_history.return_value = []
        # No error means date range was computed without raising
        fetch_orders(_make_session())
        assert MockAmazonOrders.return_value.get_order_history.called


def test_fetch_orders_crosses_year_boundary():
    """If start/end span two years, both years are queried."""
    raw2023 = _make_raw_order(order_number="X", order_placed_date=date(2023, 12, 28))
    raw2024 = _make_raw_order(order_number="Y", order_placed_date=date(2024, 1, 5))

    call_log = []

    def fake_get_order_history(year):
        call_log.append(year)
        if year == 2023:
            return [raw2023]
        return [raw2024]

    with patch("monarch_cli_sync.amazon.orders.AmazonOrders") as MockAmazonOrders:
        MockAmazonOrders.return_value.get_order_history.side_effect = fake_get_order_history
        results = fetch_orders(
            _make_session(),
            start_date=date(2023, 12, 25),
            end_date=date(2024, 1, 10),
        )

    assert sorted(call_log) == [2023, 2024]
    assert len(results) == 2


def test_fetch_orders_propagates_amazon_error():
    from amazonorders.exception import AmazonOrdersError

    with patch("monarch_cli_sync.amazon.orders.AmazonOrders") as MockAmazonOrders:
        MockAmazonOrders.return_value.get_order_history.side_effect = AmazonOrdersError("network error")
        with pytest.raises(AmazonOrdersError):
            fetch_orders(_make_session(), days=30)


def test_fetch_orders_skips_unparseable_orders():
    raw_good = _make_raw_order(order_number="OK", order_placed_date=date(2024, 3, 15))
    raw_bad = _make_raw_order(order_number="BAD")
    raw_bad.order_placed_date = None  # will be skipped

    with patch("monarch_cli_sync.amazon.orders.AmazonOrders") as MockAmazonOrders:
        MockAmazonOrders.return_value.get_order_history.return_value = [raw_good, raw_bad]
        results = fetch_orders(
            _make_session(),
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
        )

    assert len(results) == 1
    assert results[0].order_number == "OK"


# ---------------------------------------------------------------------------
# AmazonOrder.__str__
# ---------------------------------------------------------------------------

def test_amazon_order_str():
    order = AmazonOrder(
        order_number="123-4567890-1234567",
        amount=42.00,
        date=date(2024, 3, 15),
        items_desc="USB Cable",
    )
    s = str(order)
    assert "2024-03-15" in s
    assert "42.00" in s
