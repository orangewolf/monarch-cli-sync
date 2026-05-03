"""Tests for sync/runner.py — Phase 4 write-path orchestration."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monarch_cli_sync.amazon.orders import AmazonOrder
from monarch_cli_sync.monarch.transactions import MonarchTransaction
from monarch_cli_sync.status import SyncStatus
from monarch_cli_sync.sync.runner import run_sync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order(
    order_number: str = "111-0000001-0000001",
    amount: float = 25.99,
    order_date: date = date(2024, 3, 10),
) -> AmazonOrder:
    return AmazonOrder(
        order_number=order_number,
        amount=amount,
        date=order_date,
        items_desc="Widget A",
    )


def _make_tx(
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


@pytest.fixture
def mock_config():
    return MagicMock()


# ---------------------------------------------------------------------------
# Dry-run tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_sync_dry_run_no_writes(mock_config, tmp_path):
    """Dry run: matches found but no update calls made."""
    orders = [_make_order()]
    transactions = [_make_tx()]
    mm = MagicMock()
    mm.update_transaction = AsyncMock()

    last_run = tmp_path / "last_run.json"

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_or_login", return_value=MagicMock()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders):
        output = await run_sync(
            mock_config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=True, last_run_file=last_run,
        )

    assert output.result.matched == 1
    assert output.result.updated == 0
    assert output.result.status == SyncStatus.OK
    mm.update_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_run_sync_dry_run_skipped_count(mock_config, tmp_path):
    orders = [_make_order("111-A"), _make_order("111-B", amount=10.00, order_date=date(2024, 3, 12))]
    transactions = [
        _make_tx("tx1", amount=-25.99),
        _make_tx("tx2", amount=-10.00, tx_date=date(2024, 3, 12)),
    ]
    mm = MagicMock()
    mm.update_transaction = AsyncMock()

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_or_login", return_value=MagicMock()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders):
        output = await run_sync(
            mock_config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=True, last_run_file=tmp_path / "last_run.json",
        )

    assert output.result.matched == 2
    assert output.result.skipped == 2
    assert output.result.updated == 0


# ---------------------------------------------------------------------------
# Non-dry-run write path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_sync_writes_match_notes(mock_config, tmp_path):
    """Non-dry-run: update_transaction called for each match with order number."""
    orders = [_make_order("111-0000001-0000001")]
    transactions = [_make_tx("tx1", amount=-25.99)]
    mm = MagicMock()
    mm.update_transaction = AsyncMock(return_value={})

    last_run = tmp_path / "last_run.json"

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_or_login", return_value=MagicMock()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders):
        output = await run_sync(
            mock_config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=False, last_run_file=last_run,
        )

    assert output.result.matched == 1
    assert output.result.updated == 1
    assert output.result.status == SyncStatus.OK
    mm.update_transaction.assert_called_once_with(
        transaction_id="tx1", notes="111-0000001-0000001"
    )


@pytest.mark.asyncio
async def test_run_sync_write_failure_recorded_in_errors(mock_config, tmp_path):
    """If update_transaction raises on all retries, the error is captured in SyncResult.errors."""
    orders = [_make_order()]
    transactions = [_make_tx()]
    mm = MagicMock()
    mm.update_transaction = AsyncMock(side_effect=RuntimeError("API down"))

    # Patch asyncio.sleep so retry backoff doesn't slow the test.
    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_or_login", return_value=MagicMock()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders), \
         patch("monarch_cli_sync.monarch.transactions.asyncio.sleep", new_callable=AsyncMock):
        output = await run_sync(
            mock_config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=False, last_run_file=tmp_path / "last_run.json",
        )

    assert output.result.updated == 0
    assert len(output.result.errors) == 1
    assert output.result.status == SyncStatus.ERROR


@pytest.mark.asyncio
async def test_run_sync_partial_write_errors(mock_config, tmp_path):
    """Some succeed, some fail → PARTIAL status.

    tx2 fails on every attempt (including all retries) so that the retry logic
    in update_transaction exhausts itself and returns False, leaving us with a
    genuine partial result.
    """
    orders = [_make_order("111-A"), _make_order("111-B", amount=10.00, order_date=date(2024, 3, 12))]
    transactions = [
        _make_tx("tx1", amount=-25.99),
        _make_tx("tx2", amount=-10.00, tx_date=date(2024, 3, 12)),
    ]
    mm = MagicMock()

    async def _side_effect(**kwargs):
        # tx1 always succeeds; tx2 always fails (all retry attempts).
        if kwargs.get("transaction_id") == "tx2":
            raise RuntimeError("tx2 always fails")
        return {}

    mm.update_transaction = AsyncMock(side_effect=_side_effect)

    # Suppress asyncio.sleep so the test runs instantly despite retry backoff.
    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_or_login", return_value=MagicMock()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders), \
         patch("monarch_cli_sync.monarch.transactions.asyncio.sleep", new_callable=AsyncMock):
        output = await run_sync(
            mock_config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=False, last_run_file=tmp_path / "last_run.json",
        )

    assert output.result.updated == 1
    assert len(output.result.errors) == 1
    assert output.result.status == SyncStatus.PARTIAL


@pytest.mark.asyncio
async def test_run_sync_no_matches_is_no_changes(mock_config, tmp_path):
    orders = [_make_order(amount=25.99)]
    transactions = [_make_tx(amount=-99.00)]  # amounts differ → no match

    mm = MagicMock()
    mm.update_transaction = AsyncMock()

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_or_login", return_value=MagicMock()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders):
        output = await run_sync(
            mock_config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=False, last_run_file=tmp_path / "last_run.json",
        )

    assert output.result.matched == 0
    assert output.result.status == SyncStatus.NO_CHANGES
    mm.update_transaction.assert_not_called()


# ---------------------------------------------------------------------------
# last_run.json persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_sync_writes_last_run_json(mock_config, tmp_path):
    orders = [_make_order()]
    transactions = [_make_tx()]
    mm = MagicMock()
    mm.update_transaction = AsyncMock(return_value={})

    last_run = tmp_path / "last_run.json"

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_or_login", return_value=MagicMock()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders):
        await run_sync(
            mock_config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=False, last_run_file=last_run,
        )

    assert last_run.exists()
    data = json.loads(last_run.read_text())
    assert data["status"] == "ok"
    assert data["updated"] == 1
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_run_sync_returns_orders_and_transactions(mock_config, tmp_path):
    """RunOutput exposes orders, transactions, match_result for display."""
    orders = [_make_order()]
    transactions = [_make_tx()]
    mm = MagicMock()
    mm.update_transaction = AsyncMock(return_value={})

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_or_login", return_value=MagicMock()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders):
        output = await run_sync(
            mock_config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=False, last_run_file=tmp_path / "last_run.json",
        )

    assert output.orders == orders
    assert output.transactions == transactions
    assert len(output.match_result.matches) == 1
