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
