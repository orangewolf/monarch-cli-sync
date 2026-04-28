"""CLI tests for auth monarch and sync --dry-run."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from monarch_cli_sync.cli import main
from monarch_cli_sync.monarch.transactions import MonarchTransaction

# runner fixture is provided by conftest.py


def _make_transaction(i: int = 0) -> MonarchTransaction:
    return MonarchTransaction(
        id=f"tx{i}",
        amount=-float(10 + i),
        date=date(2024, 3, 15 + i),
        merchant_name=f"Amazon Store {i}",
        account_name="Chase Checking",
        notes="",
        pending=False,
    )


@pytest.fixture
def runner():
    return CliRunner()


def test_auth_monarch_success(runner):
    mm_mock = MagicMock()

    async def fake_load_or_login(config, force=False, session_file=None):
        return mm_mock

    with patch("monarch_cli_sync.monarch.session.load_or_login", fake_load_or_login):
        result = runner.invoke(main, ["auth", "monarch"])

    assert result.exit_code == 0
    assert "ok" in result.output


def test_auth_monarch_auth_required_on_sys_exit_2(runner):
    from monarchmoney import LoginFailedException

    async def fail_login(config, force=False, session_file=None):
        raise SystemExit(2)

    with patch("monarch_cli_sync.monarch.session.load_or_login", fail_login):
        result = runner.invoke(main, ["auth", "monarch"])

    assert result.exit_code == 2


def test_sync_dry_run_prints_table(runner):
    transactions = [_make_transaction(i) for i in range(3)]

    async def fake_load_or_login(config, **kwargs):
        return MagicMock()

    async def fake_fetch(mm, start_date, end_date, **kwargs):
        return transactions

    def fake_amazon_login(config, **kwargs):
        return MagicMock()

    with patch("monarch_cli_sync.monarch.session.load_or_login", fake_load_or_login), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", fake_fetch), \
         patch("monarch_cli_sync.amazon.session.load_or_login", fake_amazon_login), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=[]):
        result = runner.invoke(main, ["sync", "--dry-run"])

    # No orders → no matches → no_changes (exit 0)
    assert result.exit_code == 0
    assert "monarch-cli-sync:" in result.output


def test_sync_dry_run_empty_transactions(runner):
    async def fake_load_or_login(config, **kwargs):
        return MagicMock()

    async def fake_fetch(mm, start_date, end_date, **kwargs):
        return []

    def fake_amazon_login(config, **kwargs):
        return MagicMock()

    with patch("monarch_cli_sync.monarch.session.load_or_login", fake_load_or_login), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", fake_fetch), \
         patch("monarch_cli_sync.amazon.session.load_or_login", fake_amazon_login), \
         patch("monarch_cli_sync.amazon.orders.fetch_orders", return_value=[]):
        result = runner.invoke(main, ["sync", "--dry-run"])

    assert result.exit_code == 0
    assert "monarch-cli-sync:" in result.output


def test_sync_without_dry_run_performs_write(runner):
    """Non-dry-run sync should call the write path and succeed."""
    from monarch_cli_sync.sync.runner import RunOutput
    from monarch_cli_sync.status import SyncResult, SyncStatus
    from monarch_cli_sync.sync.matcher import MatchResult

    fake_output = RunOutput(
        result=SyncResult(status=SyncStatus.OK, matched=1, updated=1),
        orders=[],
        transactions=[],
        match_result=MatchResult(matches=[], unmatched_charges=[], unmatched_transactions=[]),
    )

    async def fake_run_sync(*args, **kwargs):
        return fake_output

    with patch("monarch_cli_sync.sync.runner.run_sync", fake_run_sync):
        result = runner.invoke(main, ["sync"])

    assert result.exit_code == 0
    assert "ok" in result.output


def test_sync_dry_run_error_from_api(runner):
    async def fake_load_or_login(config, **kwargs):
        raise RuntimeError("network error")

    with patch("monarch_cli_sync.monarch.session.load_or_login", fake_load_or_login):
        result = runner.invoke(main, ["sync", "--dry-run"])

    assert result.exit_code == 4
    assert "error" in result.output


def test_sync_json_output(runner):
    """--json flag emits a JSON object to stdout."""
    from monarch_cli_sync.sync.runner import RunOutput
    from monarch_cli_sync.status import SyncResult, SyncStatus
    from monarch_cli_sync.sync.matcher import MatchResult
    import json

    fake_output = RunOutput(
        result=SyncResult(status=SyncStatus.OK, matched=2, updated=2),
        orders=[],
        transactions=[],
        match_result=MatchResult(matches=[], unmatched_charges=[], unmatched_transactions=[]),
    )

    async def fake_run_sync(*args, **kwargs):
        return fake_output

    with patch("monarch_cli_sync.sync.runner.run_sync", fake_run_sync):
        result = runner.invoke(main, ["sync", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "ok"
    assert data["matched"] == 2


# ---------------------------------------------------------------------------
# status subcommand
# ---------------------------------------------------------------------------

def test_status_no_last_run(runner, tmp_path):
    """status with no last_run.json reports no_changes (exit 0)."""
    missing = tmp_path / "last_run.json"
    with patch("monarch_cli_sync.cli.LAST_RUN_FILE", missing):
        result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "no_changes" in result.output


def test_status_reads_last_run(runner, tmp_path):
    """status reads last_run.json and echoes summary."""
    import json
    last_run = tmp_path / "last_run.json"
    last_run.write_text(json.dumps({
        "status": "ok",
        "matched": 3,
        "updated": 3,
        "skipped": 0,
        "orders_inspected": 5,
        "transactions_fetched": 4,
        "errors": [],
        "warnings": [],
        "message": "sync complete",
        "timestamp": "2024-03-15T12:00:00Z",
    }))

    with patch("monarch_cli_sync.cli.LAST_RUN_FILE", last_run):
        result = runner.invoke(main, ["status"])

    assert result.exit_code == 0
    assert "ok" in result.output
    assert "matched=3" in result.output


def test_status_corrupt_last_run(runner, tmp_path):
    """status with corrupt last_run.json exits with error."""
    last_run = tmp_path / "last_run.json"
    last_run.write_text("not valid json {{{")

    with patch("monarch_cli_sync.cli.LAST_RUN_FILE", last_run):
        result = runner.invoke(main, ["status"])

    assert result.exit_code == 4
    assert "error" in result.output


def test_status_partial_last_run(runner, tmp_path):
    """status for partial run exits 1."""
    import json
    last_run = tmp_path / "last_run.json"
    last_run.write_text(json.dumps({
        "status": "partial",
        "matched": 2,
        "updated": 1,
        "skipped": 0,
        "errors": ["tx99 failed"],
        "warnings": [],
        "message": "partial sync",
    }))

    with patch("monarch_cli_sync.cli.LAST_RUN_FILE", last_run):
        result = runner.invoke(main, ["status"])

    assert result.exit_code == 1
    assert "partial" in result.output
