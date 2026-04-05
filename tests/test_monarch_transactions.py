"""Tests for monarch/transactions.py."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from monarch_cli_sync.monarch.transactions import (
    MonarchTransaction,
    _parse_transaction,
    fetch_amazon_transactions,
)


RAW_TRANSACTION = {
    "id": "tx123",
    "amount": -45.99,
    "date": "2024-03-15",
    "pending": False,
    "notes": "some note",
    "plaidName": "AMAZON.COM",
    "merchant": {"name": "Amazon", "id": "m1", "transactionsCount": 5},
    "account": {"id": "a1", "displayName": "Chase Checking"},
    "tags": [],
}


def test_parse_transaction_basic():
    tx = _parse_transaction(RAW_TRANSACTION)
    assert tx.id == "tx123"
    assert tx.amount == -45.99
    assert tx.date == date(2024, 3, 15)
    assert tx.merchant_name == "Amazon"
    assert tx.account_name == "Chase Checking"
    assert tx.notes == "some note"
    assert tx.pending is False


def test_parse_transaction_no_merchant():
    raw = {**RAW_TRANSACTION, "merchant": None, "plaidName": "AMAZON MARKETPLACE"}
    tx = _parse_transaction(raw)
    assert tx.merchant_name == "AMAZON MARKETPLACE"


def test_parse_transaction_no_merchant_or_plaid():
    raw = {**RAW_TRANSACTION, "merchant": None, "plaidName": None}
    tx = _parse_transaction(raw)
    assert tx.merchant_name == ""


def test_parse_transaction_pending():
    raw = {**RAW_TRANSACTION, "pending": True}
    tx = _parse_transaction(raw)
    assert tx.pending is True


def test_parse_transaction_no_notes():
    raw = {**RAW_TRANSACTION, "notes": None}
    tx = _parse_transaction(raw)
    assert tx.notes == ""


@pytest.mark.asyncio
async def test_fetch_amazon_transactions_returns_list():
    mm = MagicMock()
    mm.get_transactions = AsyncMock(return_value={
        "allTransactions": {
            "totalCount": 2,
            "results": [
                RAW_TRANSACTION,
                {**RAW_TRANSACTION, "id": "tx456", "amount": -12.00, "date": "2024-03-20"},
            ],
        }
    })

    txs = await fetch_amazon_transactions(
        mm,
        start_date=date(2024, 3, 1),
        end_date=date(2024, 3, 31),
    )

    assert len(txs) == 2
    assert txs[0].id == "tx123"
    assert txs[1].id == "tx456"

    mm.get_transactions.assert_called_once_with(
        limit=500,
        start_date="2024-03-01",
        end_date="2024-03-31",
        search="Amazon",
    )


@pytest.mark.asyncio
async def test_fetch_amazon_transactions_empty():
    mm = MagicMock()
    mm.get_transactions = AsyncMock(return_value={
        "allTransactions": {"totalCount": 0, "results": []}
    })

    txs = await fetch_amazon_transactions(
        mm,
        start_date=date(2024, 3, 1),
        end_date=date(2024, 3, 31),
    )

    assert txs == []


def test_monarch_transaction_str():
    tx = MonarchTransaction(
        id="tx1",
        amount=-55.00,
        date=date(2024, 3, 15),
        merchant_name="Amazon",
        account_name="Chase",
        notes="",
        pending=False,
    )
    s = str(tx)
    assert "2024-03-15" in s
    assert "Amazon" in s
    assert "55.00" in s
