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


def test_auth_amazon_logs_solver_status_when_configured(runner, monkeypatch):
    """When captcha solver is configured, auth amazon emits an info log line."""
    monkeypatch.setenv("AMAZON_CAPTCHA_SOLVER", "2captcha")
    monkeypatch.setenv("AMAZON_CAPTCHA_API_KEY", "k")

    def fake_load_or_login(config, force=False, cookie_file=None, **kwargs):
        session = MagicMock()
        session.is_authenticated = True
        return session

    with patch("monarch_cli_sync.amazon.session.load_or_login", fake_load_or_login):
        result = runner.invoke(main, ["-v", "auth", "amazon"])

    assert result.exit_code == 0
    assert "WAF auto-solve enabled" in result.output
    assert "2captcha" in result.output


def test_auth_amazon_no_solver_log_when_unconfigured(runner, monkeypatch):
    """No solver log line when captcha is not configured."""
    monkeypatch.setenv("AMAZON_CAPTCHA_SOLVER", "")
    monkeypatch.setenv("AMAZON_CAPTCHA_API_KEY", "")

    def fake_load_or_login(config, force=False, cookie_file=None, **kwargs):
        session = MagicMock()
        session.is_authenticated = True
        return session

    with patch("monarch_cli_sync.amazon.session.load_or_login", fake_load_or_login):
        result = runner.invoke(main, ["-v", "auth", "amazon"])

    assert result.exit_code == 0
    assert "WAF auto-solve enabled" not in result.output


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

def _clear_amazon_env(monkeypatch) -> None:
    """Ensure no AMAZON_* env vars are set so doctor gets a clean slate."""
    for var in [
        "AMAZON_USERNAME", "AMAZON_PASSWORD", "AMAZON_OTP_SECRET_KEY",
        "AMAZON_USERNAME_1", "AMAZON_PASSWORD_1", "AMAZON_OTP_SECRET_1", "AMAZON_LABEL_1",
        "AMAZON_USERNAME_2", "AMAZON_PASSWORD_2", "AMAZON_LABEL_2",
        "AMAZON_CAPTCHA_SOLVER", "AMAZON_CAPTCHA_API_KEY",
    ]:
        monkeypatch.delenv(var, raising=False)


def test_doctor_all_present(runner, monkeypatch, tmp_path):
    """doctor exits 0 when config, Monarch session, and Amazon cookies are all found."""
    import monarch_cli_sync.amazon.session as session_mod

    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME_1", "a@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_1", "pw1")
    monkeypatch.setenv("AMAZON_LABEL_1", "personal")

    config_file = tmp_path / "config.toml"
    config_file.write_text("[monarch]\nemail='a@b.com'\n")
    monarch_session = tmp_path / "monarch_session.pkl"
    monarch_session.write_bytes(b"")
    # Cookie file for the 'personal' account (non-compat label → amazon_cookies_personal.json)
    amazon_cookies = tmp_path / "amazon_cookies_personal.json"
    amazon_cookies.write_text("{}")

    with (
        patch("monarch_cli_sync.config.CONFIG_FILE", config_file),
        patch("monarch_cli_sync.monarch.session.get_session_file", return_value=monarch_session),
        patch.object(session_mod, "CONFIG_DIR", tmp_path),
    ):
        result = runner.invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert "ok" in result.output


def test_doctor_reports_captcha_solver_when_configured(runner, monkeypatch, tmp_path):
    """doctor reports configured Amazon WAF auto-solve status."""
    import monarch_cli_sync.amazon.session as session_mod

    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_CAPTCHA_SOLVER", "2captcha")
    monkeypatch.setenv("AMAZON_CAPTCHA_API_KEY", "k")

    config_file = tmp_path / "config.toml"
    config_file.write_text("")
    monarch_session = tmp_path / "monarch_session.pkl"
    monarch_session.write_bytes(b"")

    with (
        patch("monarch_cli_sync.config.CONFIG_FILE", config_file),
        patch("monarch_cli_sync.monarch.session.get_session_file", return_value=monarch_session),
        patch.object(session_mod, "CONFIG_DIR", tmp_path),
    ):
        result = runner.invoke(main, ["doctor"])

    assert "Amazon WAF auto-solve: 2captcha" in result.output


def test_doctor_reports_captcha_solver_disabled(runner, monkeypatch, tmp_path):
    """doctor reports disabled when no Amazon WAF solver is configured."""
    import monarch_cli_sync.amazon.session as session_mod

    _clear_amazon_env(monkeypatch)

    config_file = tmp_path / "config.toml"
    config_file.write_text("")
    monarch_session = tmp_path / "monarch_session.pkl"
    monarch_session.write_bytes(b"")

    with (
        patch("monarch_cli_sync.config.CONFIG_FILE", config_file),
        patch("monarch_cli_sync.monarch.session.get_session_file", return_value=monarch_session),
        patch.object(session_mod, "CONFIG_DIR", tmp_path),
    ):
        result = runner.invoke(main, ["doctor"])

    assert "Amazon WAF auto-solve: disabled" in result.output


def test_doctor_missing_monarch_session(runner, monkeypatch, tmp_path):
    """doctor warns when Monarch session file is absent."""
    import monarch_cli_sync.amazon.session as session_mod

    _clear_amazon_env(monkeypatch)

    config_file = tmp_path / "config.toml"
    config_file.write_text("")

    with (
        patch("monarch_cli_sync.config.CONFIG_FILE", config_file),
        patch("monarch_cli_sync.monarch.session.get_session_file", return_value=tmp_path / "missing.pkl"),
        patch.object(session_mod, "CONFIG_DIR", tmp_path),
    ):
        result = runner.invoke(main, ["doctor"])

    # Warnings → partial (exit 1)
    assert result.exit_code == 1
    assert "partial" in result.output


def test_doctor_missing_everything(runner, monkeypatch, tmp_path):
    """doctor warns about all missing items and exits 1."""
    import monarch_cli_sync.amazon.session as session_mod

    _clear_amazon_env(monkeypatch)

    with (
        patch("monarch_cli_sync.config.CONFIG_FILE", tmp_path / "no_config.toml"),
        patch("monarch_cli_sync.monarch.session.get_session_file", return_value=tmp_path / "no_session.pkl"),
        patch.object(session_mod, "CONFIG_DIR", tmp_path),
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
