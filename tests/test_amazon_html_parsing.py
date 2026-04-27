"""Parser tests that feed sanitized real-shape HTML through amazon-orders.

These tests cover ``monarch_cli_sync.amazon.orders._normalize_order`` against
the live ``amazonorders`` parser, using a hand-crafted fixture that mirrors
Amazon's order-history HTML structure (data-component attributes, .order-card
wrappers, .yohtmlc-* classes, etc.). They are the structural counterpart to
the VCR cassette in ``test_e2e_api_recording.py`` — VCR captures network
shape, this captures HTML shape.

Why a separate file: Amazon's HTML drifts often (CSRF tokens, anti-bot
challenges, query-param ordering) which makes whole-cassette replay brittle.
Parser tests stay green even when Amazon rotates their flow as long as the
selectors hold.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from amazonorders.conf import AmazonOrdersConfig
from amazonorders.entity.order import Order
from bs4 import BeautifulSoup

from monarch_cli_sync.amazon.orders import _normalize_order


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "amazon"
ORDER_HISTORY_2024_HTML = FIXTURE_DIR / "order_history_2024.html"


@pytest.fixture(scope="module")
def amazon_config() -> AmazonOrdersConfig:
    return AmazonOrdersConfig()


@pytest.fixture(scope="module")
def parsed_orders(amazon_config: AmazonOrdersConfig) -> list[Order]:
    """Parse the order-history fixture into amazon-orders Order objects.

    Uses the real ``Selectors`` from ``amazonorders`` so any selector drift
    in the upstream library or our fixture is caught here.
    """
    html = ORDER_HISTORY_2024_HTML.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, amazon_config.bs4_parser)
    selectors = amazon_config.selectors.ORDER_HISTORY_ENTITY_SELECTOR
    if isinstance(selectors, str):
        selectors = [selectors]
    tags = []
    for selector in selectors:
        tags = soup.select(selector)
        if tags:
            break
    return [Order(tag, amazon_config) for tag in tags]


# ---------------------------------------------------------------------------
# Sanity checks on the fixture itself
# ---------------------------------------------------------------------------


def test_fixture_file_exists():
    assert ORDER_HISTORY_2024_HTML.exists(), (
        f"Amazon HTML fixture missing at {ORDER_HISTORY_2024_HTML}"
    )


def test_fixture_parses_three_orders(parsed_orders: list[Order]):
    assert len(parsed_orders) == 3, (
        "Fixture should expose three order cards; "
        "if the upstream selector changed, update Selectors."
        "ORDER_HISTORY_ENTITY_SELECTOR or the fixture markup."
    )


# ---------------------------------------------------------------------------
# _normalize_order against real Amazon HTML
# ---------------------------------------------------------------------------


def test_normalize_first_order_two_items(parsed_orders: list[Order]):
    """A normal multi-item order: number, date, total, comma-joined item titles."""
    normalized = _normalize_order(parsed_orders[0])
    assert normalized is not None
    assert normalized.order_number == "112-0000001-0000001"
    assert normalized.date == date(2024, 1, 15)
    assert normalized.amount == pytest.approx(24.99)
    assert "Anker USB-C Charging Cable (3-Pack)" in normalized.items_desc
    assert "Adjustable Phone Stand" in normalized.items_desc


def test_normalize_handles_thousands_separator_in_total(parsed_orders: list[Order]):
    """A grand_total parsed by amazonorders as 1299.0 must come through unchanged.

    The upstream parser strips the dollar sign and comma already, so
    ``_normalize_order`` should accept the float as-is. This is a regression
    pin for: "'float' object has no attribute 'replace'".
    """
    normalized = _normalize_order(parsed_orders[1])
    assert normalized is not None
    assert normalized.amount == pytest.approx(1299.0)
    assert normalized.order_number == "112-0000002-0000002"


def test_normalize_cancelled_order_zeros_amount(parsed_orders: list[Order]):
    """Cancelled orders parse with grand_total=None; we want amount=0.0
    rather than dropping the order entirely so users can still see the entry."""
    cancelled = parsed_orders[2]
    assert cancelled.grand_total is None, (
        "Fixture invariant: cancelled order must parse to grand_total=None"
    )
    normalized = _normalize_order(cancelled)
    assert normalized is not None
    assert normalized.amount == 0.0
    assert normalized.order_number == "112-0000003-0000003"
    assert "Wireless Mouse" in normalized.items_desc


def test_normalize_returns_none_when_date_missing():
    """A card with no order-date selector match must yield None (not crash).

    Uses a config with ``warn_on_missing_required_field=True`` so the
    upstream Order parser logs (rather than raises) when grand_total is
    missing — letting us isolate the date-missing branch.
    """
    config = AmazonOrdersConfig(data={"warn_on_missing_required_field": True})
    html = (
        '<div class="order-card">'
        '<div data-component="orderId">112-0000099-0000099</div>'
        '</div>'
    )
    soup = BeautifulSoup(html, config.bs4_parser)
    tag = soup.select_one("div.order-card")
    order = Order(tag, config)
    assert order.order_placed_date is None
    assert _normalize_order(order) is None
