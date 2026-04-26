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


# ---------------------------------------------------------------------------
# Regression: real AmazonSession does not accept cookie_jar_path as a kwarg.
# Reproduces "Error: AmazonSession.__init__() got an unexpected keyword
# argument 'cookie_jar_path'" seen by users running `auth amazon`.
# ---------------------------------------------------------------------------

def test_load_or_login_uses_real_amazon_session_signature(tmp_path):
    """Calling load_or_login without overriding _session_cls must not raise
    TypeError against the real amazonorders.session.AmazonSession signature.

    The path persistence must be wired through AmazonOrdersConfig (the only
    place amazonorders reads `cookie_jar_path` from), and the resulting
    session's effective cookie_jar_path must equal the requested path.
    """
    from amazonorders.session import AmazonSession

    cookie_file = tmp_path / "amazon_cookies.json"
    cookie_file.write_text(json.dumps({}))

    config = _make_config()

    # Use the real AmazonSession class — exercises the actual constructor
    # signature that production code hits.
    session = load_or_login(config, cookie_file=cookie_file, _session_cls=AmazonSession)

    assert isinstance(session, AmazonSession)
    # The library reads the cookie jar location off the config object.
    effective_path = getattr(session.config, "cookie_jar_path", None)
    assert effective_path == str(cookie_file), (
        f"Expected cookie_jar_path={cookie_file!s}, got {effective_path!r}"
    )


# ---------------------------------------------------------------------------
# load_or_login — captcha kwargs forwarded through both paths
# ---------------------------------------------------------------------------

def _make_captcha_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "amazon": {
                "username": "user@example.com",
                "password": "secret",
                "captcha_solver": "2captcha",
                "captcha_api_key": "key-abc",
            }
        }
    )


def test_load_or_login_passes_captcha_kwargs_when_set(tmp_path):
    cookie_file = tmp_path / "missing.json"
    config = _make_captcha_config()

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        session = load_or_login(
            config, force=True, cookie_file=cookie_file, _session_cls=FakeSession
        )

    assert session.captcha_solver == "2captcha"
    assert session.captcha_api_key == "key-abc"


def test_load_or_login_omits_captcha_kwargs_when_unset(monkeypatch, tmp_path):
    monkeypatch.setenv("AMAZON_CAPTCHA_SOLVER", "")
    monkeypatch.setenv("AMAZON_CAPTCHA_API_KEY", "")
    cookie_file = tmp_path / "missing.json"
    config = AppConfig.model_validate(
        {"amazon": {"username": "user@example.com", "password": "secret"}}
    )  # no captcha fields set → empty strings

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        session = load_or_login(
            config, force=True, cookie_file=cookie_file, _session_cls=FakeSession
        )

    assert session.captcha_solver in (None, "")
    assert session.captcha_api_key in (None, "")


def test_load_or_login_passes_captcha_kwargs_on_cookie_path(tmp_path):
    """Cookie-load path also forwards captcha kwargs so silent re-auth works."""
    cookie_file = tmp_path / "amazon_cookies.json"
    cookie_file.write_text(json.dumps({}))

    config = _make_captcha_config()
    session = load_or_login(config, cookie_file=cookie_file, _session_cls=FakeSession)

    assert session.captcha_solver == "2captcha"
    assert session.captcha_api_key == "key-abc"
