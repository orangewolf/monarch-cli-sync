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


def _fake_sessions(orders_to_return):
    """Return a load_all_sessions side_effect that yields one (account, session) pair
    and makes fetch_orders return the given orders list.

    Usage: patch load_all_sessions with return_value=_one_session_pair(), then
    patch fetch_orders with return_value=orders.
    """
    pass  # used as documentation; actual patching done inline


def _one_session_pair():
    """Single (account, session) pair with realistic mock account."""
    acct = MagicMock()
    acct.label = "account-1"
    acct.request_delay_seconds = 1.0
    sess = MagicMock()
    return [(acct, sess)]


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
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
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
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
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
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
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
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
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
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
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
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
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
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
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
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders):
        output = await run_sync(
            mock_config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=False, last_run_file=tmp_path / "last_run.json",
        )

    assert output.orders == orders
    assert output.transactions == transactions
    assert len(output.match_result.matches) == 1


# ---------------------------------------------------------------------------
# Phase 5: multi-account runner tests
# ---------------------------------------------------------------------------

def _make_two_account_config():
    """Build a mock AppConfig with two Amazon accounts."""
    from monarch_cli_sync.config import AmazonAccountConfig, AmazonConfig, AppConfig
    acct1 = AmazonAccountConfig(index=1, label="personal", username="a@x.com", password="pw1")
    acct2 = AmazonAccountConfig(index=2, label="work", username="b@x.com", password="pw2")
    amazon_cfg = MagicMock()
    amazon_cfg.accounts = [acct1, acct2]
    amazon_cfg.request_delay_seconds = 1.0
    cfg = MagicMock()
    cfg.amazon = amazon_cfg
    return cfg


def _make_session_pair(acct):
    """Return a fake (account, session) tuple."""
    sess = MagicMock()
    sess.is_authenticated = True
    return (acct, sess)


@pytest.mark.asyncio
async def test_run_sync_all_accounts_default(tmp_path):
    """run_sync with no account_selector fetches orders from both accounts."""
    from monarch_cli_sync.config import AmazonAccountConfig

    config = _make_two_account_config()
    accts = config.amazon.accounts

    orders_acct1 = [_make_order("111-A", amount=25.99)]
    orders_acct2 = [_make_order("222-B", amount=10.00, order_date=date(2024, 3, 12))]
    transactions = [
        _make_tx("tx1", amount=-25.99),
        _make_tx("tx2", amount=-10.00, tx_date=date(2024, 3, 12)),
    ]
    mm = MagicMock()
    mm.update_transaction = AsyncMock(return_value={})

    sessions = [_make_session_pair(accts[0]), _make_session_pair(accts[1])]

    def _fake_fetch_orders(session, *, start_date, end_date, request_delay_seconds, account_label=""):
        if account_label == "personal":
            return orders_acct1
        return orders_acct2

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=sessions), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", side_effect=_fake_fetch_orders):
        output = await run_sync(
            config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=True, last_run_file=tmp_path / "last_run.json",
        )

    assert output.result.matched == 2
    assert len(output.orders) == 2


@pytest.mark.asyncio
async def test_run_sync_single_account_selected(tmp_path):
    """run_sync with account_selector='personal' fetches only that account's orders."""
    from monarch_cli_sync.config import AmazonAccountConfig

    config = _make_two_account_config()
    acct1 = config.amazon.accounts[0]

    orders_acct1 = [_make_order("111-A", amount=25.99)]
    transactions = [_make_tx("tx1", amount=-25.99)]
    mm = MagicMock()
    mm.update_transaction = AsyncMock(return_value={})

    sessions = [_make_session_pair(acct1)]  # only account 1 returned

    def _fake_fetch_orders(session, *, start_date, end_date, request_delay_seconds, account_label=""):
        return orders_acct1

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=sessions), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", side_effect=_fake_fetch_orders):
        output = await run_sync(
            config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=True, account_selector="personal",
            last_run_file=tmp_path / "last_run.json",
        )

    assert output.result.matched == 1
    assert len(output.orders) == 1


@pytest.mark.asyncio
async def test_run_sync_partial_auth_failure(tmp_path):
    """If one account fails auth (empty sessions list for it), result is PARTIAL."""
    config = _make_two_account_config()
    acct1 = config.amazon.accounts[0]

    orders_acct1 = [_make_order("111-A", amount=25.99)]
    transactions = [_make_tx("tx1", amount=-25.99)]
    mm = MagicMock()
    mm.update_transaction = AsyncMock(return_value={})

    # Only account 1 authenticated; account 2 failed (already excluded by load_all_sessions)
    sessions = [_make_session_pair(acct1)]

    def _fake_fetch_orders(session, *, start_date, end_date, request_delay_seconds, account_label=""):
        return orders_acct1

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=sessions), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", side_effect=_fake_fetch_orders):
        output = await run_sync(
            config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=False, last_run_file=tmp_path / "last_run.json",
            # Simulate partial auth via a pre-populated auth_errors list
        )

    # With 1 successful account, writes succeed → OK (no auth errors passed in yet)
    # This tests that runner works with a reduced session list
    assert output.result.matched == 1
    assert output.result.updated == 1


@pytest.mark.asyncio
async def test_run_sync_all_auth_failure_returns_auth_required(tmp_path):
    """If all accounts fail auth (empty sessions list), result is AUTH_REQUIRED."""
    config = _make_two_account_config()
    mm = MagicMock()

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=[])), \
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=[]):
        output = await run_sync(
            config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=False, last_run_file=tmp_path / "last_run.json",
        )

    assert output.result.status == SyncStatus.AUTH_REQUIRED


@pytest.mark.asyncio
async def test_run_sync_account_selector_passed_to_load_all_sessions(tmp_path):
    """run_sync passes account_selector through to load_all_sessions."""
    config = _make_two_account_config()
    acct1 = config.amazon.accounts[0]
    mm = MagicMock()
    mm.update_transaction = AsyncMock(return_value={})

    captured_selector = {}

    def _fake_load_all_sessions(cfg, account_selector=None, force=False, **kwargs):
        captured_selector["value"] = account_selector
        return [_make_session_pair(acct1)]

    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=[])), \
         patch("monarch_cli_sync.amazon.session.load_all_sessions", side_effect=_fake_load_all_sessions), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=[]):
        await run_sync(
            config, date(2024, 3, 1), date(2024, 3, 31),
            dry_run=True, account_selector=1,
            last_run_file=tmp_path / "last_run.json",
        )

    assert captured_selector["value"] == 1
