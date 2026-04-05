"""Tests for amazon/session.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from monarch_cli_sync.amazon.session import get_cookie_file, load_or_login
from monarch_cli_sync.config import AppConfig


def _make_config(username="user@example.com", password="secret") -> AppConfig:
    return AppConfig.model_validate(
        {"amazon": {"username": username, "password": password}}
    )


class FakeSession:
    """Minimal stand-in for AmazonSession."""

    def __init__(self, username, password, cookie_jar_path=""):
        self.username = username
        self.password = password
        self.cookie_jar_path = cookie_jar_path
        self.is_authenticated = False
        self.login_called = False

    def login(self):
        self.login_called = True
        self.is_authenticated = True


# ---------------------------------------------------------------------------
# get_cookie_file
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

    config = _make_config()
    session = load_or_login(config, cookie_file=cookie_file, _session_cls=FakeSession)

    assert session.is_authenticated is True
    assert not session.login_called


def test_load_from_cookies_skips_login(tmp_path):
    cookie_file = tmp_path / "amazon_cookies.json"
    cookie_file.write_text(json.dumps({}))

    config = _make_config()
    called = []

    class TrackingSession(FakeSession):
        def login(self):
            called.append(True)
            super().login()

    load_or_login(config, cookie_file=cookie_file, _session_cls=TrackingSession)
    assert called == [], "login() should not be called when cookies exist"


# ---------------------------------------------------------------------------
# load_or_login — force=True always re-logins
# ---------------------------------------------------------------------------

def test_force_login_even_with_cookies(tmp_path):
    cookie_file = tmp_path / "amazon_cookies.json"
    cookie_file.write_text(json.dumps({}))

    config = _make_config()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        session = load_or_login(
            config, force=True, cookie_file=cookie_file, _session_cls=FakeSession
        )

    assert session.login_called is True


# ---------------------------------------------------------------------------
# load_or_login — no cookies, non-interactive → exit 2
# ---------------------------------------------------------------------------

def test_non_interactive_no_cookies_exits_2(tmp_path):
    cookie_file = tmp_path / "missing_cookies.json"
    config = _make_config()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        with pytest.raises(SystemExit) as exc_info:
            load_or_login(config, cookie_file=cookie_file, _session_cls=FakeSession)

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# load_or_login — missing credentials → exit 2
# ---------------------------------------------------------------------------

def test_missing_credentials_exits_2(tmp_path):
    cookie_file = tmp_path / "missing.json"
    config = _make_config(username="", password="")

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        with pytest.raises(SystemExit) as exc_info:
            load_or_login(config, cookie_file=cookie_file, _session_cls=FakeSession)

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# load_or_login — auth error during login → exit 2
# ---------------------------------------------------------------------------

def test_auth_error_during_login_exits_2(tmp_path):
    from amazonorders.exception import AmazonOrdersAuthError

    cookie_file = tmp_path / "missing.json"
    config = _make_config()

    class FailingSession(FakeSession):
        def login(self):
            raise AmazonOrdersAuthError("CAPTCHA required")

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        with pytest.raises(SystemExit) as exc_info:
            load_or_login(config, cookie_file=cookie_file, _session_cls=FailingSession)

    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# load_or_login — successful interactive login
# ---------------------------------------------------------------------------

def test_interactive_login_success(tmp_path):
    cookie_file = tmp_path / "cookies.json"
    config = _make_config()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        session = load_or_login(config, cookie_file=cookie_file, _session_cls=FakeSession)

    assert session.login_called is True
    assert session.is_authenticated is True
