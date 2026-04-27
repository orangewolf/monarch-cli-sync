"""Tests for sync/matcher.py — Phase 3 matching engine.

Exhaustive unit tests covering all edge cases described in plans/application.md.
Written before the implementation (TDD).
"""

from __future__ import annotations

from datetime import date

from monarch_cli_sync.amazon.orders import AmazonOrder
from monarch_cli_sync.monarch.transactions import MonarchTransaction
from monarch_cli_sync.sync.matcher import (
    AmazonCharge,
    Match,
    MatchResult,
    flatten_to_charges,
    match,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _order(
    order_number: str = "111-0000000-0000001",
    amount: float = 25.99,
    order_date: date = date(2024, 3, 10),
    items_desc: str = "Widget A",
) -> AmazonOrder:
    return AmazonOrder(
        order_number=order_number,
        amount=amount,
        date=order_date,
        items_desc=items_desc,
    )


def _tx(
    tx_id: str = "tx1",
    amount: float = -25.99,
    tx_date: date = date(2024, 3, 10),
    notes: str = "",
) -> MonarchTransaction:
    return MonarchTransaction(
        id=tx_id,
        amount=amount,
        date=tx_date,
        merchant_name="Amazon",
        account_name="Chase",
        notes=notes,
        pending=False,
    )


# ---------------------------------------------------------------------------
# flatten_to_charges
# ---------------------------------------------------------------------------


def test_flatten_empty_orders():
    assert flatten_to_charges([]) == []


def test_flatten_single_order_produces_one_charge():
    orders = [_order()]
    charges = flatten_to_charges(orders)
    assert len(charges) == 1
    c = charges[0]
    assert c.order_number == orders[0].order_number
    assert c.amount == orders[0].amount
    assert c.date == orders[0].date
    assert c.items_desc == orders[0].items_desc


def test_flatten_multiple_orders_preserves_all():
    orders = [
        _order("111-0000000-0000001", 10.00, date(2024, 3, 1)),
        _order("111-0000000-0000002", 20.00, date(2024, 3, 5)),
        _order("111-0000000-0000003", 30.00, date(2024, 3, 8)),
    ]
    charges = flatten_to_charges(orders)
    assert len(charges) == 3
    amounts = {c.amount for c in charges}
    assert amounts == {10.00, 20.00, 30.00}


# ---------------------------------------------------------------------------
# AmazonCharge dataclass
# ---------------------------------------------------------------------------


def test_amazon_charge_fields():
    charge = AmazonCharge(
        order_number="111-0000000-0000001",
        amount=42.00,
        date=date(2024, 3, 15),
        items_desc="Test item",
    )
    assert charge.order_number == "111-0000000-0000001"
    assert charge.amount == 42.00
    assert charge.date == date(2024, 3, 15)
    assert charge.items_desc == "Test item"


# ---------------------------------------------------------------------------
# match — empty inputs
# ---------------------------------------------------------------------------


def test_match_empty_charges_and_transactions():
    result = match([], [])
    assert isinstance(result, MatchResult)
    assert result.matches == []
    assert result.unmatched_charges == []
    assert result.unmatched_transactions == []


def test_match_empty_charges_with_transactions():
    txs = [_tx()]
    result = match([], txs)
    assert result.matches == []
    assert result.unmatched_charges == []
    assert len(result.unmatched_transactions) == 1


def test_match_charges_with_empty_transactions():
    charges = flatten_to_charges([_order()])
    result = match(charges, [])
    assert result.matches == []
    assert len(result.unmatched_charges) == 1
    assert result.unmatched_transactions == []


# ---------------------------------------------------------------------------
# match — exact match
# ---------------------------------------------------------------------------


def test_exact_match_same_date():
    charges = flatten_to_charges([_order(amount=25.99, order_date=date(2024, 3, 10))])
    txs = [_tx(amount=-25.99, tx_date=date(2024, 3, 10))]

    result = match(charges, txs)

    assert len(result.matches) == 1
    assert result.unmatched_charges == []
    assert result.unmatched_transactions == []

    m = result.matches[0]
    assert isinstance(m, Match)
    assert m.charge.amount == 25.99
    assert m.transaction.id == "tx1"


# ---------------------------------------------------------------------------
# match — date window
# ---------------------------------------------------------------------------


def test_match_within_window_positive_offset():
    """Monarch date is 5 days after Amazon date — within default 7-day window."""
    charges = flatten_to_charges([_order(amount=30.00, order_date=date(2024, 3, 10))])
    txs = [_tx(amount=-30.00, tx_date=date(2024, 3, 15))]

    result = match(charges, txs)
    assert len(result.matches) == 1


def test_match_within_window_negative_offset():
    """Monarch date is 5 days before Amazon date — within default 7-day window."""
    charges = flatten_to_charges([_order(amount=30.00, order_date=date(2024, 3, 10))])
    txs = [_tx(amount=-30.00, tx_date=date(2024, 3, 5))]

    result = match(charges, txs)
    assert len(result.matches) == 1


def test_match_at_window_boundary():
    """Exactly at the 7-day limit — should still match."""
    charges = flatten_to_charges([_order(amount=30.00, order_date=date(2024, 3, 10))])
    txs = [_tx(amount=-30.00, tx_date=date(2024, 3, 17))]

    result = match(charges, txs)
    assert len(result.matches) == 1


def test_no_match_outside_window():
    """8 days apart — outside default 7-day window."""
    charges = flatten_to_charges([_order(amount=30.00, order_date=date(2024, 3, 10))])
    txs = [_tx(amount=-30.00, tx_date=date(2024, 3, 18))]

    result = match(charges, txs)
    assert result.matches == []
    assert len(result.unmatched_charges) == 1
    assert len(result.unmatched_transactions) == 1


def test_custom_date_window_respected():
    """date_window=3 — 5-day gap should not match."""
    charges = flatten_to_charges([_order(amount=30.00, order_date=date(2024, 3, 10))])
    txs = [_tx(amount=-30.00, tx_date=date(2024, 3, 15))]

    result = match(charges, txs, date_window=3)
    assert result.matches == []


def test_custom_date_window_allows_match():
    """date_window=10 — 8-day gap should match."""
    charges = flatten_to_charges([_order(amount=30.00, order_date=date(2024, 3, 10))])
    txs = [_tx(amount=-30.00, tx_date=date(2024, 3, 18))]

    result = match(charges, txs, date_window=10)
    assert len(result.matches) == 1


# ---------------------------------------------------------------------------
# match — amount mismatch
# ---------------------------------------------------------------------------


def test_no_match_wrong_amount():
    charges = flatten_to_charges([_order(amount=25.99)])
    txs = [_tx(amount=-26.00)]

    result = match(charges, txs)
    assert result.matches == []
    assert len(result.unmatched_charges) == 1
    assert len(result.unmatched_transactions) == 1


# ---------------------------------------------------------------------------
# match — tie-breaking (closest date wins)
# ---------------------------------------------------------------------------


def test_tiebreak_closest_date_wins():
    """Two charges with same amount, different dates — pick the closer one."""
    charge_near = AmazonCharge(
        order_number="111-near",
        amount=50.00,
        date=date(2024, 3, 8),   # 2 days from tx date 2024-03-10
        items_desc="Near",
    )
    charge_far = AmazonCharge(
        order_number="111-far",
        amount=50.00,
        date=date(2024, 3, 4),   # 6 days from tx date 2024-03-10
        items_desc="Far",
    )
    txs = [_tx(amount=-50.00, tx_date=date(2024, 3, 10))]

    result = match([charge_near, charge_far], txs)
    assert len(result.matches) == 1
    assert result.matches[0].charge.order_number == "111-near"
    assert len(result.unmatched_charges) == 1
    assert result.unmatched_charges[0].order_number == "111-far"


def test_tiebreak_two_transactions_same_charge_amount():
    """Two transactions, one charge — first matched tx wins; other tx is unmatched."""
    charge = AmazonCharge(
        order_number="111-only",
        amount=40.00,
        date=date(2024, 3, 10),
        items_desc="Item",
    )
    tx_close = _tx(tx_id="tx-close", amount=-40.00, tx_date=date(2024, 3, 10))
    tx_far = _tx(tx_id="tx-far", amount=-40.00, tx_date=date(2024, 3, 14))

    # charge matches both; tx_close is closer so it should win
    result = match([charge], [tx_close, tx_far])
    assert len(result.matches) == 1
    assert result.matches[0].transaction.id == "tx-close"
    assert len(result.unmatched_transactions) == 1
    assert result.unmatched_transactions[0].id == "tx-far"


# ---------------------------------------------------------------------------
# match — duplicate prevention (charge used only once)
# ---------------------------------------------------------------------------


def test_charge_used_only_once():
    """Single charge should not be matched to two transactions."""
    charges = [
        AmazonCharge(
            order_number="111-single",
            amount=20.00,
            date=date(2024, 3, 10),
            items_desc="Thing",
        )
    ]
    txs = [
        _tx(tx_id="tx-a", amount=-20.00, tx_date=date(2024, 3, 10)),
        _tx(tx_id="tx-b", amount=-20.00, tx_date=date(2024, 3, 11)),
    ]

    result = match(charges, txs)
    assert len(result.matches) == 1
    assert len(result.unmatched_transactions) == 1


def test_two_charges_two_transactions_each_used_once():
    charges = [
        AmazonCharge("111-a", 15.00, date(2024, 3, 5), "A"),
        AmazonCharge("111-b", 25.00, date(2024, 3, 6), "B"),
    ]
    txs = [
        _tx(tx_id="tx-a", amount=-15.00, tx_date=date(2024, 3, 5)),
        _tx(tx_id="tx-b", amount=-25.00, tx_date=date(2024, 3, 6)),
    ]

    result = match(charges, txs)
    assert len(result.matches) == 2
    assert result.unmatched_charges == []
    assert result.unmatched_transactions == []


# ---------------------------------------------------------------------------
# match — notes handling (skip vs. force)
# ---------------------------------------------------------------------------


def test_skip_transaction_with_existing_notes_by_default():
    """Transactions that already have notes are skipped when force=False."""
    charges = flatten_to_charges([_order(amount=60.00, order_date=date(2024, 3, 10))])
    txs = [_tx(amount=-60.00, tx_date=date(2024, 3, 10), notes="Already tagged")]

    result = match(charges, txs)
    assert result.matches == []
    # The charge is unmatched because the transaction was skipped
    assert len(result.unmatched_charges) == 1
    # The skipped transaction appears in unmatched_transactions
    assert len(result.unmatched_transactions) == 1


def test_force_overrides_existing_notes():
    """When force=True, transactions with existing notes are eligible for matching."""
    charges = flatten_to_charges([_order(amount=60.00, order_date=date(2024, 3, 10))])
    txs = [_tx(amount=-60.00, tx_date=date(2024, 3, 10), notes="Already tagged")]

    result = match(charges, txs, force=True)
    assert len(result.matches) == 1


def test_empty_notes_not_skipped():
    """Transactions with empty string notes are not skipped."""
    charges = flatten_to_charges([_order(amount=70.00, order_date=date(2024, 3, 10))])
    txs = [_tx(amount=-70.00, tx_date=date(2024, 3, 10), notes="")]

    result = match(charges, txs)
    assert len(result.matches) == 1


# ---------------------------------------------------------------------------
# match — refund handling (positive Monarch amounts / negative Amazon amounts)
# ---------------------------------------------------------------------------


def test_refund_positive_monarch_amount():
    """Amazon refund (negative) matches Monarch credit (positive)."""
    refund_charge = AmazonCharge(
        order_number="111-refund",
        amount=-15.00,   # negative = refund on Amazon side
        date=date(2024, 3, 10),
        items_desc="Returned widget",
    )
    # Monarch refund: positive amount
    tx = MonarchTransaction(
        id="tx-refund",
        amount=15.00,
        date=date(2024, 3, 10),
        merchant_name="Amazon",
        account_name="Chase",
        notes="",
        pending=False,
    )
    result = match([refund_charge], [tx])
    assert len(result.matches) == 1
    assert result.matches[0].transaction.id == "tx-refund"


def test_purchase_does_not_match_monarch_credit():
    """Amazon purchase (positive +15) must NOT match a Monarch credit (+15).
    Both positive — wrong-sign combination; old abs logic would incorrectly match."""
    purchase_charge = AmazonCharge(
        order_number="111-purchase",
        amount=15.00,   # positive = purchase
        date=date(2024, 3, 10),
        items_desc="Widget",
    )
    # Monarch credit (positive) — same absolute value but wrong direction
    tx = MonarchTransaction(
        id="tx-credit",
        amount=15.00,
        date=date(2024, 3, 10),
        merchant_name="Amazon",
        account_name="Chase",
        notes="",
        pending=False,
    )
    result = match([purchase_charge], [tx])
    assert result.matches == []
    assert len(result.unmatched_charges) == 1
    assert len(result.unmatched_transactions) == 1


def test_refund_does_not_match_monarch_debit():
    """Amazon refund (negative -15) must NOT match a Monarch debit (-15).
    Both negative — wrong-sign combination; old abs logic would incorrectly match."""
    refund_charge = AmazonCharge(
        order_number="111-refund-wrong",
        amount=-15.00,   # negative = refund
        date=date(2024, 3, 10),
        items_desc="Returned widget",
    )
    # Monarch debit (negative) — same absolute value but wrong direction
    tx = MonarchTransaction(
        id="tx-debit",
        amount=-15.00,
        date=date(2024, 3, 10),
        merchant_name="Amazon",
        account_name="Chase",
        notes="",
        pending=False,
    )
    result = match([refund_charge], [tx])
    assert result.matches == []
    assert len(result.unmatched_charges) == 1
    assert len(result.unmatched_transactions) == 1


# ---------------------------------------------------------------------------
# MatchResult structure
# ---------------------------------------------------------------------------


def test_match_result_fields():
    result = match([], [])
    assert hasattr(result, "matches")
    assert hasattr(result, "unmatched_charges")
    assert hasattr(result, "unmatched_transactions")


def test_match_object_fields():
    charges = flatten_to_charges([_order(amount=10.00)])
    txs = [_tx(amount=-10.00)]
    result = match(charges, txs)
    m = result.matches[0]
    assert hasattr(m, "charge")
    assert hasattr(m, "transaction")
    assert isinstance(m.charge, AmazonCharge)
    assert isinstance(m.transaction, MonarchTransaction)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_multiple_charges_only_some_match():
    charges = [
        AmazonCharge("111-a", 10.00, date(2024, 3, 5), "A"),
        AmazonCharge("111-b", 99.99, date(2024, 3, 6), "B — no matching tx"),
    ]
    txs = [_tx(tx_id="tx-a", amount=-10.00, tx_date=date(2024, 3, 5))]

    result = match(charges, txs)
    assert len(result.matches) == 1
    assert result.matches[0].charge.order_number == "111-a"
    assert len(result.unmatched_charges) == 1
    assert result.unmatched_charges[0].order_number == "111-b"
    assert result.unmatched_transactions == []


def test_pending_transaction_still_matched():
    """Pending transactions are still eligible for matching."""
    charges = flatten_to_charges([_order(amount=80.00)])
    tx = MonarchTransaction(
        id="tx-pending",
        amount=-80.00,
        date=date(2024, 3, 10),
        merchant_name="Amazon",
        account_name="Chase",
        notes="",
        pending=True,
    )
    result = match(charges, [tx])
    assert len(result.matches) == 1
