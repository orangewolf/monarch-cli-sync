"""CLI tests for auth monarch and sync --dry-run."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from monarch_cli_sync.cli import main
from monarch_cli_sync.monarch.transactions import MonarchTransaction


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

    with patch("monarch_cli_sync.monarch.session.load_or_login", fake_load_or_login), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", fake_fetch):
        result = runner.invoke(main, ["sync", "--dry-run"])

    assert result.exit_code == 0
    assert "ok" in result.output


def test_sync_dry_run_empty_transactions(runner):
    async def fake_load_or_login(config, **kwargs):
        return MagicMock()

    async def fake_fetch(mm, start_date, end_date, **kwargs):
        return []

    with patch("monarch_cli_sync.monarch.session.load_or_login", fake_load_or_login), \
         patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", fake_fetch):
        result = runner.invoke(main, ["sync", "--dry-run"])

    assert result.exit_code == 0
    assert "ok" in result.output


def test_sync_without_dry_run_exits_error(runner):
    result = runner.invoke(main, ["sync"])
    assert result.exit_code == 4
    assert "error" in result.output


def test_sync_dry_run_error_from_api(runner):
    async def fake_load_or_login(config, **kwargs):
        raise RuntimeError("network error")

    with patch("monarch_cli_sync.monarch.session.load_or_login", fake_load_or_login):
        result = runner.invoke(main, ["sync", "--dry-run"])

    assert result.exit_code == 4
    assert "error" in result.output
