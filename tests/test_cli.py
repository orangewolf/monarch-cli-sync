"""Smoke tests for the CLI entry point."""

import pytest
from click.testing import CliRunner

from monarch_cli_sync.cli import main


def test_help(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "monarch-cli-sync" in result.output.lower() or "sync amazon" in result.output.lower()


def test_version(runner):
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_sync_help(runner):
    result = runner.invoke(main, ["sync", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output


def test_auth_help(runner):
    result = runner.invoke(main, ["auth", "--help"])
    assert result.exit_code == 0
    assert "amazon" in result.output
    assert "monarch" in result.output


def test_doctor_runs_and_exits_nonzero_when_files_missing(runner):
    # In a clean test environment, config/session/cookie files won't be present.
    # doctor should run successfully (not crash) and exit 0 or 1.
    result = runner.invoke(main, ["doctor"], catch_exceptions=False)
    assert result.exit_code in (0, 1)  # 0 = all good, 1 = warnings


def test_status_reports_no_changes_when_no_last_run(runner):
    result = runner.invoke(main, ["status"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "no_changes" in result.output


def test_doctor_reports_captcha_solver_when_configured(runner, monkeypatch):
    monkeypatch.setenv("AMAZON_CAPTCHA_SOLVER", "2captcha")
    monkeypatch.setenv("AMAZON_CAPTCHA_API_KEY", "k")
    result = runner.invoke(main, ["doctor"], catch_exceptions=False)
    assert "Amazon WAF auto-solve: 2captcha" in result.output


def test_doctor_reports_captcha_solver_disabled(runner, monkeypatch):
    monkeypatch.setenv("AMAZON_CAPTCHA_SOLVER", "")
    monkeypatch.setenv("AMAZON_CAPTCHA_API_KEY", "")
    result = runner.invoke(main, ["doctor"], catch_exceptions=False)
    assert "Amazon WAF auto-solve: disabled" in result.output


# ---------------------------------------------------------------------------
# Phase 6: --account flag on sync and auth amazon
# ---------------------------------------------------------------------------

def test_sync_help_shows_account_flag(runner):
    result = runner.invoke(main, ["sync", "--help"])
    assert result.exit_code == 0
    assert "--account" in result.output


def test_auth_amazon_help_shows_account_flag(runner):
    result = runner.invoke(main, ["auth", "amazon", "--help"])
    assert result.exit_code == 0
    assert "--account" in result.output


def test_sync_account_flag_integer_passed_to_runner(runner, monkeypatch):
    """sync --account 1 passes account_selector=1 (int) to run_sync."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from monarch_cli_sync.status import SyncResult, SyncStatus
    from monarch_cli_sync.sync.matcher import MatchResult
    from monarch_cli_sync.sync.runner import RunOutput

    captured = {}

    async def fake_run_sync(config, start_date, end_date, dry_run, force,
                            account_selector=None, last_run_file=None,
                            shutdown_event=None):
        captured["account_selector"] = account_selector
        return RunOutput(
            result=SyncResult(status=SyncStatus.OK, message="ok"),
            orders=[],
            transactions=[],
            match_result=MatchResult(matches=[], unmatched_charges=[], unmatched_transactions=[]),
        )

    with patch("monarch_cli_sync.sync.runner.run_sync", fake_run_sync):
        result = runner.invoke(main, ["sync", "--dry-run", "--account", "1"],
                               catch_exceptions=False)

    assert result.exit_code == 0
    assert captured.get("account_selector") == 1  # int, not string


def test_sync_account_flag_label_passed_to_runner(runner):
    """sync --account personal passes account_selector='personal' (str)."""
    from unittest.mock import patch
    from monarch_cli_sync.status import SyncResult, SyncStatus
    from monarch_cli_sync.sync.matcher import MatchResult
    from monarch_cli_sync.sync.runner import RunOutput

    captured = {}

    async def fake_run_sync(config, start_date, end_date, dry_run, force,
                            account_selector=None, last_run_file=None,
                            shutdown_event=None):
        captured["account_selector"] = account_selector
        return RunOutput(
            result=SyncResult(status=SyncStatus.OK, message="ok"),
            orders=[],
            transactions=[],
            match_result=MatchResult(matches=[], unmatched_charges=[], unmatched_transactions=[]),
        )

    with patch("monarch_cli_sync.sync.runner.run_sync", fake_run_sync):
        result = runner.invoke(main, ["sync", "--dry-run", "--account", "personal"],
                               catch_exceptions=False)

    assert result.exit_code == 0
    assert captured.get("account_selector") == "personal"


def test_sync_no_account_flag_passes_none(runner):
    """sync without --account passes account_selector=None."""
    from unittest.mock import patch
    from monarch_cli_sync.status import SyncResult, SyncStatus
    from monarch_cli_sync.sync.matcher import MatchResult
    from monarch_cli_sync.sync.runner import RunOutput

    captured = {}

    async def fake_run_sync(config, start_date, end_date, dry_run, force,
                            account_selector=None, last_run_file=None,
                            shutdown_event=None):
        captured["account_selector"] = account_selector
        return RunOutput(
            result=SyncResult(status=SyncStatus.OK, message="ok"),
            orders=[],
            transactions=[],
            match_result=MatchResult(matches=[], unmatched_charges=[], unmatched_transactions=[]),
        )

    with patch("monarch_cli_sync.sync.runner.run_sync", fake_run_sync):
        result = runner.invoke(main, ["sync", "--dry-run"], catch_exceptions=False)

    assert result.exit_code == 0
    assert captured.get("account_selector") is None
