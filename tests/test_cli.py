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


def test_doctor_exits_with_error_when_not_implemented(runner):
    result = runner.invoke(main, ["doctor"], catch_exceptions=False)
    assert result.exit_code == 4  # SyncStatus.ERROR


def test_status_exits_with_error_when_not_implemented(runner):
    result = runner.invoke(main, ["status"], catch_exceptions=False)
    assert result.exit_code == 4
