"""Tests for config loading."""

import os
import tomllib
from pathlib import Path

import pytest

from monarch_cli_sync.config import AppConfig, load_config


def test_load_config_missing_file(tmp_path):
    """Loading a non-existent file returns defaults."""
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert isinstance(cfg, AppConfig)
    assert cfg.sync.default_days == 30
    assert cfg.sync.date_window_days == 7


def test_load_config_from_toml(tmp_path):
    """Values in TOML override defaults."""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        "[amazon]\nusername = 'test@example.com'\n\n[sync]\ndefault_days = 14\n"
    )
    cfg = load_config(toml_file)
    assert cfg.amazon.username == "test@example.com"
    assert cfg.sync.default_days == 14


def test_env_vars_override(monkeypatch, tmp_path):
    """Env vars override TOML values."""
    monkeypatch.setenv("AMAZON_USERNAME", "env_user")
    monkeypatch.setenv("MONARCH_EMAIL", "monarch@example.com")
    cfg = load_config(tmp_path / "nonexistent.toml")
    assert cfg.amazon.username == "env_user"
    assert cfg.monarch.email == "monarch@example.com"
