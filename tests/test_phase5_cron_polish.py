"""Phase 5 cron-polish tests.

Covers:
- request_delay_seconds used between year-level Amazon fetches
- Retry logic (3 retries, exponential backoff) in update_transaction
- SIGTERM / shutdown_event stops the write loop early and saves partial results
- --days and --year CLI flags produce correct date ranges
"""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from monarch_cli_sync.amazon.orders import AmazonOrder, fetch_orders
from monarch_cli_sync.monarch.transactions import MonarchTransaction, update_transaction
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
    cfg = MagicMock()
    cfg.amazon.request_delay_seconds = 1.0
    return cfg


# ---------------------------------------------------------------------------
# request_delay_seconds — fetch_orders
# ---------------------------------------------------------------------------

def test_fetch_orders_no_delay_for_single_year():
    """No sleep when fetching a single year."""
    raw_order = MagicMock()
    raw_order.order_placed_date = date(2024, 3, 10)
    raw_order.grand_total = "25.99"
    raw_order.order_number = "111-A"
    raw_order.items = []

    session = MagicMock()

    with patch("monarch_cli_sync.amazon.orders.AmazonOrders") as MockOrders, \
         patch("monarch_cli_sync.amazon.orders.time.sleep") as mock_sleep:
        MockOrders.return_value.get_order_history.return_value = [raw_order]
        orders = fetch_orders(
            session,
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
            request_delay_seconds=1.0,
        )

    # Only one year fetched — sleep should not be called.
    mock_sleep.assert_not_called()
    assert len(orders) == 1


def test_fetch_orders_sleeps_between_years():
    """Sleep is called once when fetching two consecutive years."""
    raw_order_2024 = MagicMock()
    raw_order_2024.order_placed_date = date(2024, 12, 20)
    raw_order_2024.grand_total = "10.00"
    raw_order_2024.order_number = "111-A"
    raw_order_2024.items = []

    raw_order_2025 = MagicMock()
    raw_order_2025.order_placed_date = date(2025, 1, 5)
    raw_order_2025.grand_total = "20.00"
    raw_order_2025.order_number = "111-B"
    raw_order_2025.items = []

    session = MagicMock()

    def _get_order_history(year):
        if year == 2024:
            return [raw_order_2024]
        return [raw_order_2025]

    with patch("monarch_cli_sync.amazon.orders.AmazonOrders") as MockOrders, \
         patch("monarch_cli_sync.amazon.orders.time.sleep") as mock_sleep:
        MockOrders.return_value.get_order_history.side_effect = _get_order_history
        orders = fetch_orders(
            session,
            start_date=date(2024, 12, 1),
            end_date=date(2025, 1, 31),
            request_delay_seconds=2.5,
        )

    # Two years → one inter-year sleep.
    mock_sleep.assert_called_once_with(2.5)
    assert len(orders) == 2


def test_fetch_orders_zero_delay_no_sleep():
    """Sleep is skipped when request_delay_seconds=0."""
    raw_order_2024 = MagicMock()
    raw_order_2024.order_placed_date = date(2024, 12, 31)
    raw_order_2024.grand_total = "5.00"
    raw_order_2024.order_number = "111-A"
    raw_order_2024.items = []

    raw_order_2025 = MagicMock()
    raw_order_2025.order_placed_date = date(2025, 1, 1)
    raw_order_2025.grand_total = "6.00"
    raw_order_2025.order_number = "111-B"
    raw_order_2025.items = []

    session = MagicMock()

    def _get_order_history(year):
        return [raw_order_2024] if year == 2024 else [raw_order_2025]

    with patch("monarch_cli_sync.amazon.orders.AmazonOrders") as MockOrders, \
         patch("monarch_cli_sync.amazon.orders.time.sleep") as mock_sleep:
        MockOrders.return_value.get_order_history.side_effect = _get_order_history
        fetch_orders(
            session,
            start_date=date(2024, 12, 1),
            end_date=date(2025, 1, 31),
            request_delay_seconds=0,
        )

    mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# Retry logic — update_transaction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_transaction_retries_then_succeeds():
    """Fails twice, succeeds on the third attempt."""
    mm = MagicMock()
    call_count = 0

    async def _side_effect(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient error")
        return {}

    mm.update_transaction = AsyncMock(side_effect=_side_effect)

    with patch("monarch_cli_sync.monarch.transactions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await update_transaction(mm, "tx1", "111-A", max_retries=3, backoff_base=1.0)

    assert result is True
    assert call_count == 3
    # Should have slept twice (after attempt 1 and 2): 1s, 2s.
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(1.0)
    mock_sleep.assert_any_call(2.0)


@pytest.mark.asyncio
async def test_update_transaction_exhausts_retries_returns_false():
    """Returns False after exhausting all retries."""
    mm = MagicMock()
    mm.update_transaction = AsyncMock(side_effect=RuntimeError("always fails"))

    with patch("monarch_cli_sync.monarch.transactions.asyncio.sleep", new_callable=AsyncMock):
        result = await update_transaction(mm, "tx1", "111-A", max_retries=3, backoff_base=0.01)

    assert result is False
    assert mm.update_transaction.call_count == 4  # initial + 3 retries


@pytest.mark.asyncio
async def test_update_transaction_no_sleep_on_first_attempt_success():
    """No sleep when first attempt succeeds."""
    mm = MagicMock()
    mm.update_transaction = AsyncMock(return_value={})

    with patch("monarch_cli_sync.monarch.transactions.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await update_transaction(mm, "tx1", "111-A", max_retries=3, backoff_base=1.0)

    assert result is True
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_update_transaction_exponential_backoff_values():
    """Verify sleep durations follow 2^attempt * base pattern."""
    mm = MagicMock()
    # Fail all attempts so we can observe all sleep calls.
    mm.update_transaction = AsyncMock(side_effect=RuntimeError("always fails"))

    sleep_calls = []

    async def _fake_sleep(seconds):
        sleep_calls.append(seconds)

    with patch("monarch_cli_sync.monarch.transactions.asyncio.sleep", side_effect=_fake_sleep):
        await update_transaction(mm, "tx1", "111-A", max_retries=3, backoff_base=1.0)

    # 3 retries → 3 sleeps: 1.0, 2.0, 4.0
    assert sleep_calls == [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# SIGTERM / shutdown_event handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_sync_shutdown_event_stops_write_loop(tmp_path):
    """Shutdown event set before the loop starts → no updates, partial result."""
    orders = [
        _make_order("111-A", amount=25.99),
        _make_order("111-B", amount=10.00, order_date=date(2024, 3, 12)),
    ]
    transactions = [
        _make_tx("tx1", amount=-25.99),
        _make_tx("tx2", amount=-10.00, tx_date=date(2024, 3, 12)),
    ]
    mm = MagicMock()
    mm.update_transaction = AsyncMock(return_value={})

    config = MagicMock()
    config.amazon.request_delay_seconds = 0

    shutdown_event = asyncio.Event()
    shutdown_event.set()  # Signal before the run starts

    from tests.test_runner import _one_session_pair
    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders):
        output = await run_sync(
            config,
            date(2024, 3, 1),
            date(2024, 3, 31),
            dry_run=False,
            last_run_file=tmp_path / "last_run.json",
            shutdown_event=shutdown_event,
        )

    # No updates written because shutdown happened before the first iteration.
    mm.update_transaction.assert_not_called()
    assert output.result.updated == 0
    # Errors list should mention the interruption.
    assert any("SIGTERM" in e for e in output.result.errors)


@pytest.mark.asyncio
async def test_run_sync_shutdown_mid_loop_gives_partial(tmp_path):
    """Shutdown event set after first successful update → PARTIAL result."""
    orders = [
        _make_order("111-A", amount=25.99),
        _make_order("111-B", amount=10.00, order_date=date(2024, 3, 12)),
    ]
    transactions = [
        _make_tx("tx1", amount=-25.99),
        _make_tx("tx2", amount=-10.00, tx_date=date(2024, 3, 12)),
    ]
    mm = MagicMock()

    shutdown_event = asyncio.Event()

    async def _update_side_effect(**kwargs):
        # Set the shutdown event after the first successful update.
        shutdown_event.set()
        return {}

    mm.update_transaction = AsyncMock(side_effect=_update_side_effect)

    config = MagicMock()
    config.amazon.request_delay_seconds = 0

    from tests.test_runner import _one_session_pair
    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders):
        output = await run_sync(
            config,
            date(2024, 3, 1),
            date(2024, 3, 31),
            dry_run=False,
            last_run_file=tmp_path / "last_run.json",
            shutdown_event=shutdown_event,
        )

    # First update succeeded, second was skipped due to shutdown.
    assert output.result.updated == 1
    assert any("SIGTERM" in e for e in output.result.errors)
    assert output.result.status == SyncStatus.PARTIAL


@pytest.mark.asyncio
async def test_run_sync_no_shutdown_event_runs_fully(tmp_path):
    """Passing shutdown_event=None (default) runs the full write loop."""
    orders = [_make_order("111-A")]
    transactions = [_make_tx("tx1")]
    mm = MagicMock()
    mm.update_transaction = AsyncMock(return_value={})

    config = MagicMock()
    config.amazon.request_delay_seconds = 0

    from tests.test_runner import _one_session_pair
    with patch("monarch_cli_sync.monarch.session.load_or_login", AsyncMock(return_value=mm)), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", AsyncMock(return_value=transactions)), \
         patch("monarch_cli_sync.amazon.session.load_all_sessions", return_value=_one_session_pair()), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=orders):
        output = await run_sync(
            config,
            date(2024, 3, 1),
            date(2024, 3, 31),
            dry_run=False,
            last_run_file=tmp_path / "last_run.json",
            shutdown_event=None,
        )

    assert output.result.updated == 1
    assert output.result.status == SyncStatus.OK


# ---------------------------------------------------------------------------
# --days and --year CLI flags produce correct date ranges
# ---------------------------------------------------------------------------

def test_days_flag_produces_correct_date_range():
    """--days 7 should result in start_date = today - 7 days."""
    from datetime import timedelta
    captured = {}

    async def _fake_run_sync(config, start_date, end_date, **kwargs):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        from monarch_cli_sync.sync.runner import RunOutput
        result = MagicMock()
        result.exit_code = 0
        result.summary_line.return_value = "monarch-cli-sync: ok | matched=0 updated=0 skipped=0 errors=0"
        result.to_dict.return_value = {"status": "ok"}
        match_result = MagicMock()
        match_result.matches = []
        match_result.unmatched_charges = []
        match_result.unmatched_transactions = []
        return RunOutput(result=result, orders=[], transactions=[], match_result=match_result)

    from click.testing import CliRunner
    from monarch_cli_sync.cli import main

    runner = CliRunner()
    with patch("monarch_cli_sync.sync.runner.run_sync", side_effect=_fake_run_sync):
        result = runner.invoke(main, ["sync", "--days", "7", "--dry-run"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "start_date" in captured
    expected_start = date.today() - timedelta(days=7)
    assert captured["start_date"] == expected_start
    assert captured["end_date"] == date.today()


def test_year_flag_produces_full_calendar_year():
    """--year 2023 should result in start=2023-01-01, end=2023-12-31."""
    captured = {}

    async def _fake_run_sync(config, start_date, end_date, **kwargs):
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        from monarch_cli_sync.sync.runner import RunOutput
        result = MagicMock()
        result.exit_code = 0
        result.summary_line.return_value = "monarch-cli-sync: ok | matched=0 updated=0 skipped=0 errors=0"
        result.to_dict.return_value = {"status": "ok"}
        match_result = MagicMock()
        match_result.matches = []
        match_result.unmatched_charges = []
        match_result.unmatched_transactions = []
        return RunOutput(result=result, orders=[], transactions=[], match_result=match_result)

    from click.testing import CliRunner
    from monarch_cli_sync.cli import main

    runner = CliRunner()
    with patch("monarch_cli_sync.sync.runner.run_sync", side_effect=_fake_run_sync):
        result = runner.invoke(main, ["sync", "--year", "2023", "--dry-run"], catch_exceptions=False)

    assert result.exit_code == 0
    assert captured.get("start_date") == date(2023, 1, 1)
    assert captured.get("end_date") == date(2023, 12, 31)


# ---------------------------------------------------------------------------
# Partial failure + exit code
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_failure_exit_code_is_one(tmp_path):
    """PARTIAL status maps to exit code 1."""
    from monarch_cli_sync.status import SyncResult

    result = SyncResult(
        status=SyncStatus.PARTIAL,
        matched=2,
        updated=1,
        errors=["Failed to update tx tx2"],
    )
    assert result.exit_code == 1
