"""Tests for amazon/session.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from monarch_cli_sync.amazon.session import (
    _select_accounts,
    get_cookie_file,
    load_all_sessions,
    load_or_login,
)
from monarch_cli_sync.config import AmazonAccountConfig, AppConfig


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_account(
    username: str = "user@example.com",
    password: str = "secret",
    index: int = 1,
    label: str = "account-1",
    otp_secret_key: str = "",
    captcha_solver: str = "",
    captcha_api_key: str = "",
) -> AmazonAccountConfig:
    return AmazonAccountConfig(
        index=index,
        label=label,
        username=username,
        password=password,
        otp_secret_key=otp_secret_key,
        captcha_solver=captcha_solver,
        captcha_api_key=captcha_api_key,
    )


def _make_two_account_config(monkeypatch) -> AppConfig:
    """Return an AppConfig with two Amazon accounts via numbered env vars."""
    for var in ("AMAZON_USERNAME", "AMAZON_PASSWORD"):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("AMAZON_USERNAME_1", "a@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_1", "pw1")
    monkeypatch.setenv("AMAZON_USERNAME_2", "b@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_2", "pw2")
    return AppConfig.model_validate({})


class FakeSession:
    """Minimal stand-in for AmazonSession.

    Mirrors the real `amazonorders.session.AmazonSession.__init__` shape:
    cookie persistence is configured via the `config` kwarg
    (an `AmazonOrdersConfig`), not a top-level `cookie_jar_path` kwarg.
    """

    def __init__(
        self,
        username,
        password,
        config=None,
        captcha_solver=None,
        captcha_api_key=None,
    ):
        self.username = username
        self.password = password
        self.config = config
        self.captcha_solver = captcha_solver
        self.captcha_api_key = captcha_api_key
        self.cookie_jar_path = (
            getattr(config, "cookie_jar_path", "") if config is not None else ""
        )
        self.is_authenticated = False
        self.login_called = False

    def login(self):
        self.login_called = True
        self.is_authenticated = True


# ---------------------------------------------------------------------------
# get_cookie_file (legacy helper — still used by doctor command)
# ---------------------------------------------------------------------------

def test_get_cookie_file_default():
    path = get_cookie_file()
    assert path.name == "amazon_cookies.json"


def test_get_cookie_file_custom(tmp_path):
    path = get_cookie_file(config_dir=tmp_path)
    assert path == tmp_path / "amazon_cookies.json"


# ---------------------------------------------------------------------------
# load_or_login — cookies already stored
# ---------------------------------------------------------------------------

def test_load_from_existing_cookies(tmp_path):
    cookie_file = tmp_path / "amazon_cookies.json"
    cookie_file.write_text(json.dumps({}))

    account = _make_account()
    session = load_or_login(account, cookie_file=cookie_file, _session_cls=FakeSession)

    assert session.is_authenticated is True
    assert not session.login_called


def test_load_from_cookies_skips_login(tmp_path):
    cookie_file = tmp_path / "amazon_cookies.json"
    cookie_file.write_text(json.dumps({}))

    account = _make_account()
    called = []

    class TrackingSession(FakeSession):
        def login(self):
            called.append(True)
            super().login()

    load_or_login(account, cookie_file=cookie_file, _session_cls=TrackingSession)
    assert called == [], "login() should not be called when cookies exist"


# ---------------------------------------------------------------------------
# load_or_login — force=True always re-logins
# ---------------------------------------------------------------------------

def test_force_login_even_with_cookies(tmp_path):
    cookie_file = tmp_path / "amazon_cookies.json"
    cookie_file.write_text(json.dumps({}))

    account = _make_account()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        session = load_or_login(
            account, force=True, cookie_file=cookie_file, _session_cls=FakeSession
        )

    assert session.login_called is True


# ---------------------------------------------------------------------------
# load_or_login — no cookies, non-interactive → exit 2
# ---------------------------------------------------------------------------

def test_non_interactive_no_cookies_exits_2(tmp_path):
    cookie_file = tmp_path / "missing_cookies.json"
    account = _make_account()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        with pytest.raises(SystemExit) as exc_info:
            load_or_login(account, cookie_file=cookie_file, _session_cls=FakeSession)

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# load_or_login — missing credentials → exit 2
# ---------------------------------------------------------------------------

def test_missing_credentials_exits_2(tmp_path):
    cookie_file = tmp_path / "missing.json"
    account = _make_account(username="", password="")

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        with pytest.raises(SystemExit) as exc_info:
            load_or_login(account, cookie_file=cookie_file, _session_cls=FakeSession)

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# load_or_login — auth error during login → exit 2
# ---------------------------------------------------------------------------

def test_auth_error_during_login_exits_2(tmp_path):
    from amazonorders.exception import AmazonOrdersAuthError

    cookie_file = tmp_path / "missing.json"
    account = _make_account()

    class FailingSession(FakeSession):
        def login(self):
            raise AmazonOrdersAuthError("CAPTCHA required")

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        with pytest.raises(SystemExit) as exc_info:
            load_or_login(account, cookie_file=cookie_file, _session_cls=FailingSession)

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# load_or_login — successful interactive login
# ---------------------------------------------------------------------------

def test_interactive_login_success(tmp_path):
    cookie_file = tmp_path / "cookies.json"
    account = _make_account()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        session = load_or_login(account, cookie_file=cookie_file, _session_cls=FakeSession)

    assert session.login_called is True
    assert session.is_authenticated is True


# ---------------------------------------------------------------------------
# Regression: real AmazonSession does not accept cookie_jar_path as a kwarg.
# ---------------------------------------------------------------------------

def test_load_or_login_uses_real_amazon_session_signature(tmp_path):
    """Calling load_or_login without overriding _session_cls must not raise
    TypeError against the real amazonorders.session.AmazonSession signature.
    """
    from amazonorders.session import AmazonSession

    cookie_file = tmp_path / "amazon_cookies.json"
    cookie_file.write_text(json.dumps({}))

    account = _make_account()

    session = load_or_login(account, cookie_file=cookie_file, _session_cls=AmazonSession)

    assert isinstance(session, AmazonSession)
    effective_path = getattr(session.config, "cookie_jar_path", None)
    assert effective_path == str(cookie_file), (
        f"Expected cookie_jar_path={cookie_file!s}, got {effective_path!r}"
    )


# ---------------------------------------------------------------------------
# load_or_login — captcha kwargs forwarded through both paths
# ---------------------------------------------------------------------------

def _make_captcha_account() -> AmazonAccountConfig:
    return _make_account(
        captcha_solver="2captcha",
        captcha_api_key="key-abc",
    )


def test_load_or_login_passes_captcha_kwargs_when_set(tmp_path):
    cookie_file = tmp_path / "missing.json"
    account = _make_captcha_account()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        session = load_or_login(
            account, force=True, cookie_file=cookie_file, _session_cls=FakeSession
        )

    assert session.captcha_solver == "2captcha"
    assert session.captcha_api_key == "key-abc"


def test_load_or_login_omits_captcha_kwargs_when_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("AMAZON_CAPTCHA_SOLVER", "")
    monkeypatch.setenv("AMAZON_CAPTCHA_API_KEY", "")
    cookie_file = tmp_path / "missing.json"
    account = _make_account()  # no captcha fields

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        session = load_or_login(
            account, force=True, cookie_file=cookie_file, _session_cls=FakeSession
        )

    assert session.captcha_solver in (None, "")
    assert session.captcha_api_key in (None, "")


def test_load_or_login_passes_captcha_kwargs_on_cookie_path(tmp_path):
    """Cookie-load path also forwards captcha kwargs so silent re-auth works."""
    cookie_file = tmp_path / "amazon_cookies.json"
    cookie_file.write_text(json.dumps({}))

    account = _make_captcha_account()
    session = load_or_login(account, cookie_file=cookie_file, _session_cls=FakeSession)

    assert session.captcha_solver == "2captcha"
    assert session.captcha_api_key == "key-abc"


# ---------------------------------------------------------------------------
# Phase 2: per-account cookie file path
# ---------------------------------------------------------------------------

def test_load_or_login_uses_account_cookie_file_stem(tmp_path):
    """load_or_login derives cookie file from account.cookie_file_stem when no
    explicit cookie_file is given."""
    import monarch_cli_sync.amazon.session as session_mod

    account = _make_account(index=1, label="personal")
    # account.cookie_file_stem == "amazon_cookies_personal"
    expected = tmp_path / "amazon_cookies_personal.json"
    expected.write_text(json.dumps({}))

    with patch.object(session_mod, "CONFIG_DIR", tmp_path):
        session = load_or_login(account, _session_cls=FakeSession)

    assert session.is_authenticated is True


def test_compat_account_uses_original_cookie_stem(tmp_path):
    """index=1, label='account-1' → cookie file is amazon_cookies.json (compat path)."""
    import monarch_cli_sync.amazon.session as session_mod

    account = _make_account(index=1, label="account-1")
    # cookie_file_stem == "amazon_cookies"
    expected = tmp_path / "amazon_cookies.json"
    expected.write_text(json.dumps({}))

    with patch.object(session_mod, "CONFIG_DIR", tmp_path):
        session = load_or_login(account, _session_cls=FakeSession)

    assert session.is_authenticated is True


# ---------------------------------------------------------------------------
# Phase 2: _select_accounts
# ---------------------------------------------------------------------------

def test_select_accounts_none_returns_all():
    accounts = [
        _make_account(index=1, label="account-1"),
        _make_account(index=2, label="work"),
    ]
    assert _select_accounts(accounts, None) == accounts


def test_select_accounts_by_index():
    accounts = [
        _make_account(index=1, label="account-1"),
        _make_account(index=2, label="work"),
    ]
    result = _select_accounts(accounts, 2)
    assert len(result) == 1
    assert result[0].index == 2


def test_select_accounts_by_label():
    accounts = [
        _make_account(index=1, label="personal"),
        _make_account(index=2, label="work"),
    ]
    result = _select_accounts(accounts, "personal")
    assert len(result) == 1
    assert result[0].label == "personal"


def test_select_accounts_nonexistent_returns_empty():
    accounts = [_make_account(index=1, label="personal")]
    assert _select_accounts(accounts, 99) == []
    assert _select_accounts(accounts, "nonexistent") == []


# ---------------------------------------------------------------------------
# Phase 2: load_all_sessions
# ---------------------------------------------------------------------------

def test_load_all_sessions_returns_both_accounts(monkeypatch, tmp_path):
    """load_all_sessions returns (account, session) pairs for all accounts.

    Account 1 (label='account-1', index=1) uses the compat stem 'amazon_cookies';
    account 2 (label='account-2') uses stem 'amazon_cookies_account-2'.
    """
    import monarch_cli_sync.amazon.session as session_mod

    config = _make_two_account_config(monkeypatch)
    # compat stem for account 1:
    (tmp_path / "amazon_cookies.json").write_text(json.dumps({}))
    # numbered stem for account 2:
    (tmp_path / "amazon_cookies_account-2.json").write_text(json.dumps({}))

    with patch.object(session_mod, "CONFIG_DIR", tmp_path):
        results = load_all_sessions(config, _session_cls=FakeSession)

    assert len(results) == 2
    assert results[0][0].username == "a@example.com"
    assert results[1][0].username == "b@example.com"
    assert results[0][1].is_authenticated is True
    assert results[1][1].is_authenticated is True


def test_load_all_sessions_with_account_selector(monkeypatch, tmp_path):
    """load_all_sessions with selector only returns the selected account."""
    import monarch_cli_sync.amazon.session as session_mod

    config = _make_two_account_config(monkeypatch)
    # Account 1 uses compat stem
    (tmp_path / "amazon_cookies.json").write_text(json.dumps({}))

    with patch.object(session_mod, "CONFIG_DIR", tmp_path):
        results = load_all_sessions(config, account_selector=1, _session_cls=FakeSession)

    assert len(results) == 1
    assert results[0][0].username == "a@example.com"


def test_load_all_sessions_skips_failed_auth_and_warns(monkeypatch, tmp_path, caplog):
    """If one account fails auth (exits 2), it is skipped and a warning logged."""
    import logging
    import monarch_cli_sync.amazon.session as session_mod

    config = _make_two_account_config(monkeypatch)
    # Account 1 has cookies (compat stem); account 2 does not → non-interactive exit 2
    (tmp_path / "amazon_cookies.json").write_text(json.dumps({}))

    with patch.object(session_mod, "CONFIG_DIR", tmp_path), \
         patch("sys.stdin") as mock_stdin, \
         caplog.at_level(logging.WARNING, logger="monarch_cli_sync.amazon.session"):
        mock_stdin.isatty.return_value = False
        results = load_all_sessions(config, _session_cls=FakeSession)

    assert len(results) == 1
    assert results[0][0].username == "a@example.com"
    assert any("skipping" in msg.lower() or "auth" in msg.lower() for msg in caplog.messages)


def test_load_all_sessions_empty_when_all_fail(monkeypatch, tmp_path, caplog):
    """If all accounts fail auth, returns empty list."""
    import logging
    import monarch_cli_sync.amazon.session as session_mod

    config = _make_two_account_config(monkeypatch)
    # No cookie files → both accounts fail non-interactively

    with patch.object(session_mod, "CONFIG_DIR", tmp_path), \
         patch("sys.stdin") as mock_stdin, \
         caplog.at_level(logging.WARNING, logger="monarch_cli_sync.amazon.session"):
        mock_stdin.isatty.return_value = False
        results = load_all_sessions(config, _session_cls=FakeSession)

    assert results == []
