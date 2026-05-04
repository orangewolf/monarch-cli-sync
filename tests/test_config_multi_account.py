"""Tests for multi-account AmazonAccountConfig discovery in config.py."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from monarch_cli_sync.config import AmazonAccountConfig, AmazonConfig, AppConfig, load_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_amazon_env(monkeypatch):
    """Clear all single-account and numbered Amazon env vars."""
    for var in (
        "AMAZON_USERNAME", "AMAZON_PASSWORD", "AMAZON_OTP_SECRET_KEY",
        "AMAZON_CAPTCHA_SOLVER", "AMAZON_CAPTCHA_API_KEY",
    ):
        monkeypatch.setenv(var, "")
    for n in range(1, 6):
        for suffix in ("AMAZON_USERNAME", "AMAZON_PASSWORD", "AMAZON_OTP_SECRET",
                       "AMAZON_LABEL"):
            monkeypatch.setenv(f"{suffix}_{n}", "")


# ---------------------------------------------------------------------------
# AmazonAccountConfig.cookie_file_stem property
# ---------------------------------------------------------------------------

def test_cookie_file_stem_compat_path():
    """index=1 + label='account-1' → 'amazon_cookies' (backward-compat path)."""
    acct = AmazonAccountConfig(index=1, label="account-1", username="u", password="p")
    assert acct.cookie_file_stem == "amazon_cookies"


def test_cookie_file_stem_labeled_account():
    """Labeled account uses label in stem."""
    acct = AmazonAccountConfig(index=1, label="personal", username="u", password="p")
    assert acct.cookie_file_stem == "amazon_cookies_personal"


def test_cookie_file_stem_numbered_without_label():
    """Account N without custom label uses 'amazon_cookies_account-N'."""
    acct = AmazonAccountConfig(index=2, label="account-2", username="u", password="p")
    assert acct.cookie_file_stem == "amazon_cookies_account-2"


# ---------------------------------------------------------------------------
# Single-account backward-compat (unnumbered env vars)
# ---------------------------------------------------------------------------

def test_single_account_compat_unnumbered_env(monkeypatch):
    """Unnumbered AMAZON_USERNAME/PASSWORD → one account, label 'account-1', compat cookie stem."""
    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME", "me@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD", "secret")

    cfg = AppConfig.model_validate({})

    assert len(cfg.amazon.accounts) == 1
    acct = cfg.amazon.accounts[0]
    assert acct.index == 1
    assert acct.label == "account-1"
    assert acct.username == "me@example.com"
    assert acct.password == "secret"
    assert acct.cookie_file_stem == "amazon_cookies"


def test_single_account_compat_flat_fields_preserved(monkeypatch):
    """Legacy flat fields (username, password) still accessible on AmazonConfig."""
    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME", "me@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD", "secret")

    cfg = AppConfig.model_validate({})

    assert cfg.amazon.username == "me@example.com"
    assert cfg.amazon.password == "secret"


# ---------------------------------------------------------------------------
# Numbered env vars
# ---------------------------------------------------------------------------

def test_numbered_env_vars_two_accounts(monkeypatch):
    """AMAZON_USERNAME_1 + AMAZON_USERNAME_2 → two AmazonAccountConfig entries."""
    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME_1", "a@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_1", "pw1")
    monkeypatch.setenv("AMAZON_USERNAME_2", "b@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_2", "pw2")

    cfg = AppConfig.model_validate({})

    assert len(cfg.amazon.accounts) == 2
    assert cfg.amazon.accounts[0].username == "a@example.com"
    assert cfg.amazon.accounts[0].index == 1
    assert cfg.amazon.accounts[1].username == "b@example.com"
    assert cfg.amazon.accounts[1].index == 2


def test_numbered_env_vars_take_precedence_over_unnumbered(monkeypatch):
    """Numbered env vars override unnumbered when both are set."""
    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME", "old@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD", "oldpw")
    monkeypatch.setenv("AMAZON_USERNAME_1", "new@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_1", "newpw")

    cfg = AppConfig.model_validate({})

    assert len(cfg.amazon.accounts) == 1
    assert cfg.amazon.accounts[0].username == "new@example.com"


def test_label_from_env(monkeypatch):
    """AMAZON_LABEL_1=personal → account.label == 'personal'."""
    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME_1", "me@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_1", "pw")
    monkeypatch.setenv("AMAZON_LABEL_1", "personal")

    cfg = AppConfig.model_validate({})

    assert cfg.amazon.accounts[0].label == "personal"
    assert cfg.amazon.accounts[0].cookie_file_stem == "amazon_cookies_personal"


def test_otp_secret_from_numbered_env(monkeypatch):
    """AMAZON_OTP_SECRET_1 is picked up for account 1."""
    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME_1", "me@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_1", "pw")
    monkeypatch.setenv("AMAZON_OTP_SECRET_1", "MYSECRET")

    cfg = AppConfig.model_validate({})

    assert cfg.amazon.accounts[0].otp_secret_key == "MYSECRET"


def test_default_label_when_not_set(monkeypatch):
    """Without AMAZON_LABEL_N, label defaults to 'account-N'."""
    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME_2", "b@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_2", "pw2")
    # No _1 set so discovery starts at _1 (empty) → only _2 won't be discovered.
    # Let's set _1 as well:
    monkeypatch.setenv("AMAZON_USERNAME_1", "a@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_1", "pw1")

    cfg = AppConfig.model_validate({})

    assert cfg.amazon.accounts[1].label == "account-2"


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

def test_gap_in_numbering_warns_and_stops(monkeypatch, caplog):
    """AMAZON_USERNAME_1 + AMAZON_USERNAME_3 (no _2) → only account 1 discovered; warning logged."""
    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME_1", "a@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_1", "pw1")
    monkeypatch.setenv("AMAZON_USERNAME_3", "c@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_3", "pw3")

    with caplog.at_level(logging.WARNING, logger="monarch_cli_sync.config"):
        cfg = AppConfig.model_validate({})

    assert len(cfg.amazon.accounts) == 1
    assert cfg.amazon.accounts[0].username == "a@example.com"
    assert any("gap" in msg.lower() or "numbering" in msg.lower() for msg in caplog.messages)


# ---------------------------------------------------------------------------
# TOML [[amazon.accounts]] array
# ---------------------------------------------------------------------------

def test_toml_accounts_array(monkeypatch, tmp_path):
    """[[amazon.accounts]] in TOML creates list of AmazonAccountConfig."""
    _clear_amazon_env(monkeypatch)
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        '[[amazon.accounts]]\n'
        'label = "personal"\n'
        'username = "me@example.com"\n'
        'password = "secret"\n'
        'otp_secret_key = "BASE32"\n'
        '\n'
        '[[amazon.accounts]]\n'
        'label = "business"\n'
        'username = "work@example.com"\n'
        'password = "biz"\n'
    )

    cfg = load_config(toml_file)

    assert len(cfg.amazon.accounts) == 2
    assert cfg.amazon.accounts[0].label == "personal"
    assert cfg.amazon.accounts[0].username == "me@example.com"
    assert cfg.amazon.accounts[0].otp_secret_key == "BASE32"
    assert cfg.amazon.accounts[1].label == "business"
    assert cfg.amazon.accounts[1].username == "work@example.com"


def test_toml_accounts_get_auto_indexes(monkeypatch, tmp_path):
    """TOML accounts without explicit index get 1-based indexes assigned."""
    _clear_amazon_env(monkeypatch)
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        '[[amazon.accounts]]\nlabel = "a"\nusername = "a@x.com"\npassword = "p"\n'
        '[[amazon.accounts]]\nlabel = "b"\nusername = "b@x.com"\npassword = "p"\n'
    )

    cfg = load_config(toml_file)

    assert cfg.amazon.accounts[0].index == 1
    assert cfg.amazon.accounts[1].index == 2


def test_numbered_env_vars_override_toml(monkeypatch, tmp_path):
    """Numbered env vars take precedence over TOML [[amazon.accounts]]."""
    _clear_amazon_env(monkeypatch)
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        '[[amazon.accounts]]\nlabel = "from-toml"\nusername = "toml@x.com"\npassword = "p"\n'
    )
    monkeypatch.setenv("AMAZON_USERNAME_1", "env@x.com")
    monkeypatch.setenv("AMAZON_PASSWORD_1", "envpw")

    cfg = load_config(toml_file)

    assert len(cfg.amazon.accounts) == 1
    assert cfg.amazon.accounts[0].username == "env@x.com"


# ---------------------------------------------------------------------------
# No accounts configured
# ---------------------------------------------------------------------------

def test_no_accounts_configured_returns_empty_list(monkeypatch):
    """No AMAZON_* vars and no TOML → accounts is empty list."""
    _clear_amazon_env(monkeypatch)

    cfg = AppConfig.model_validate({})

    assert cfg.amazon.accounts == []


# ---------------------------------------------------------------------------
# Captcha settings propagate to accounts
# ---------------------------------------------------------------------------

def test_captcha_settings_propagate_to_numbered_accounts(monkeypatch):
    """Global captcha_solver/captcha_api_key flow into each account."""
    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME_1", "a@x.com")
    monkeypatch.setenv("AMAZON_PASSWORD_1", "pw1")
    monkeypatch.setenv("AMAZON_CAPTCHA_SOLVER", "2captcha")
    monkeypatch.setenv("AMAZON_CAPTCHA_API_KEY", "mykey")

    cfg = AppConfig.model_validate({})

    assert cfg.amazon.accounts[0].captcha_solver == "2captcha"
    assert cfg.amazon.accounts[0].captcha_api_key == "mykey"


def test_captcha_settings_propagate_to_compat_account(monkeypatch):
    """Captcha settings also flow into the legacy single-account."""
    _clear_amazon_env(monkeypatch)
    monkeypatch.setenv("AMAZON_USERNAME", "me@x.com")
    monkeypatch.setenv("AMAZON_PASSWORD", "pw")
    monkeypatch.setenv("AMAZON_CAPTCHA_SOLVER", "2captcha")
    monkeypatch.setenv("AMAZON_CAPTCHA_API_KEY", "mykey")

    cfg = AppConfig.model_validate({})

    assert cfg.amazon.accounts[0].captcha_solver == "2captcha"
    assert cfg.amazon.accounts[0].captcha_api_key == "mykey"
