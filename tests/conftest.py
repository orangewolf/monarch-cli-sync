"""Shared pytest fixtures."""

import pytest
from click.testing import CliRunner

from monarch_cli_sync.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli(runner: CliRunner):
    """Convenience wrapper: invoke(args) -> Result."""
    def invoke(*args: str, **kwargs):
        return runner.invoke(main, list(args), catch_exceptions=False, **kwargs)
    return invoke
