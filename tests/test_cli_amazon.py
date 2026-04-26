"""CLI tests for auth amazon, sync --dry-run (with Amazon orders), and doctor."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from monarch_cli_sync.amazon.orders import AmazonOrder
from monarch_cli_sync.cli import main
from monarch_cli_sync.monarch.transactions import MonarchTransaction


def _make_order(i: int = 0) -> AmazonOrder:
    return AmazonOrder(
        order_number=f"112-000000{i}-0000000",
        amount=float(20 + i),
        date=date(2024, 3, 10 + i),
        items_desc=f"Item {i}",
    )


def _make_transaction(i: int = 0) -> MonarchTransaction:
    return MonarchTransaction(
        id=f"tx{i}",
        amount=-float(20 + i),
        date=date(2024, 3, 10 + i),
        merchant_name="Amazon",
        account_name="Chase",
        notes="",
        pending=False,
    )


# ---------------------------------------------------------------------------
# auth amazon
# ---------------------------------------------------------------------------

def test_auth_amazon_success(runner):
    def fake_load_or_login(config, force=False, cookie_file=None, **kwargs):
        session = MagicMock()
        session.is_authenticated = True
        return session

    with patch("monarch_cli_sync.amazon.session.load_or_login", fake_load_or_login):
        result = runner.invoke(main, ["auth", "amazon"])

    assert result.exit_code == 0
    assert "ok" in result.output


def test_auth_amazon_auth_required_on_sys_exit_2(runner):
    def fail_login(config, force=False, cookie_file=None, **kwargs):
        raise SystemExit(2)

    with patch("monarch_cli_sync.amazon.session.load_or_login", fail_login):
        result = runner.invoke(main, ["auth", "amazon"])

    assert result.exit_code == 2


def test_auth_amazon_unexpected_error(runner):
    def boom(config, force=False, cookie_file=None, **kwargs):
        raise RuntimeError("unexpected")

    with patch("monarch_cli_sync.amazon.session.load_or_login", boom):
        result = runner.invoke(main, ["auth", "amazon"])

    assert result.exit_code == 4
    assert "error" in result.output


# ---------------------------------------------------------------------------
# sync --dry-run (Phase 2: shows both Amazon orders and Monarch transactions)
# ---------------------------------------------------------------------------

def test_sync_dry_run_shows_both(runner):
    orders = [_make_order(i) for i in range(2)]
    transactions = [_make_transaction(i) for i in range(2)]

    async def fake_monarch_login(config, **kwargs):
        return MagicMock()

    async def fake_fetch_transactions(mm, start_date, end_date, **kwargs):
        return transactions

    def fake_amazon_login(config, **kwargs):
        return MagicMock()

    def fake_fetch_orders(session, **kwargs):
        return orders

    with (
        patch("monarch_cli_sync.monarch.session.load_or_login", fake_monarch_login),
        patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", fake_fetch_transactions),
        patch("monarch_cli_sync.amazon.session.load_or_login", fake_amazon_login),
        patch("monarch_cli_sync.amazon.orders.fetch_orders", fake_fetch_orders),
    ):
        result = runner.invoke(main, ["sync", "--dry-run"])

    assert result.exit_code == 0
    assert "ok" in result.output


def test_sync_dry_run_amazon_auth_failure_exits_2(runner):
    """If Amazon session returns exit(2), sync should propagate it."""

    async def fake_monarch_login(config, **kwargs):
        return MagicMock()

    async def fake_fetch_transactions(mm, start_date, end_date, **kwargs):
        return []

    def fake_amazon_login(config, **kwargs):
        raise SystemExit(2)

    with (
        patch("monarch_cli_sync.monarch.session.load_or_login", fake_monarch_login),
        patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", fake_fetch_transactions),
        patch("monarch_cli_sync.amazon.session.load_or_login", fake_amazon_login),
    ):
        result = runner.invoke(main, ["sync", "--dry-run"])

    assert result.exit_code == 2


def test_sync_dry_run_amazon_error_exits_4(runner):
    async def fake_monarch_login(config, **kwargs):
        return MagicMock()

    async def fake_fetch_transactions(mm, start_date, end_date, **kwargs):
        return []

    def fake_amazon_login(config, **kwargs):
        raise RuntimeError("scraping failed")

    with (
        patch("monarch_cli_sync.monarch.session.load_or_login", fake_monarch_login),
        patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", fake_fetch_transactions),
        patch("monarch_cli_sync.amazon.session.load_or_login", fake_amazon_login),
    ):
        result = runner.invoke(main, ["sync", "--dry-run"])

    assert result.exit_code == 4
    assert "error" in result.output


def test_sync_dry_run_quiet_suppresses_tables(runner):
    async def fake_monarch_login(config, **kwargs):
        return MagicMock()

    async def fake_fetch_transactions(mm, start_date, end_date, **kwargs):
        return [_make_transaction()]

    def fake_amazon_login(config, **kwargs):
        return MagicMock()

    def fake_fetch_orders(session, **kwargs):
        return [_make_order()]

    with (
        patch("monarch_cli_sync.monarch.session.load_or_login", fake_monarch_login),
        patch("monarch_cli_sync.monarch.transactions.fetch_amazon_transactions", fake_fetch_transactions),
        patch("monarch_cli_sync.amazon.session.load_or_login", fake_amazon_login),
        patch("monarch_cli_sync.amazon.orders.fetch_orders", fake_fetch_orders),
    ):
        result = runner.invoke(main, ["--quiet", "sync", "--dry-run"])

    assert result.exit_code == 0
    # Only the summary line should appear; no table headers
    assert "Amazon orders" not in result.output
    assert "Monarch" not in result.output


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def test_doctor_all_present(runner, tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("[monarch]\nemail='a@b.com'\n")
    monarch_session = tmp_path / "monarch_session.pkl"
    monarch_session.write_bytes(b"")
    amazon_cookies = tmp_path / "amazon_cookies.json"
    amazon_cookies.write_text("{}")

    with (
        patch("monarch_cli_sync.config.CONFIG_FILE", config_file),
        patch("monarch_cli_sync.monarch.session.get_session_file", return_value=monarch_session),
        patch("monarch_cli_sync.amazon.session.get_cookie_file", return_value=amazon_cookies),
    ):
        result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "ok" in result.output


def test_doctor_missing_monarch_session(runner, tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("")
    amazon_cookies = tmp_path / "amazon_cookies.json"
    amazon_cookies.write_text("{}")
    # No monarch session file created

    with (
        patch("monarch_cli_sync.config.CONFIG_FILE", config_file),
        patch("monarch_cli_sync.monarch.session.get_session_file", return_value=tmp_path / "missing.pkl"),
        patch("monarch_cli_sync.amazon.session.get_cookie_file", return_value=amazon_cookies),
    ):
        result = runner.invoke(main, ["doctor"])

    # Warnings → partial (exit 1)
    assert result.exit_code == 1
    assert "partial" in result.output


def test_doctor_missing_everything(runner, tmp_path):
    with (
        patch("monarch_cli_sync.config.CONFIG_FILE", tmp_path / "no_config.toml"),
        patch("monarch_cli_sync.monarch.session.get_session_file", return_value=tmp_path / "no_session.pkl"),
        patch("monarch_cli_sync.amazon.session.get_cookie_file", return_value=tmp_path / "no_cookies.json"),
    ):
        result = runner.invoke(main, ["doctor"])

    # All warnings → partial
    assert result.exit_code == 1


def test_doctor_missing_config_file_is_warning_not_error(runner, tmp_path):
    monarch_session = tmp_path / "monarch_session.pkl"
    monarch_session.write_bytes(b"")
    amazon_cookies = tmp_path / "amazon_cookies.json"
    amazon_cookies.write_text("{}")

    with (
        patch("monarch_cli_sync.config.CONFIG_FILE", tmp_path / "no_config.toml"),
        patch("monarch_cli_sync.monarch.session.get_session_file", return_value=monarch_session),
        patch("monarch_cli_sync.amazon.session.get_cookie_file", return_value=amazon_cookies),
    ):
        result = runner.invoke(main, ["doctor"])

    # Missing config is a warning (partial), not a hard error
    assert result.exit_code == 1
    assert "partial" in result.output
