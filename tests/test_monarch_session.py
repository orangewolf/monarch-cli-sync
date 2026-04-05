"""Tests for monarch/session.py."""

from __future__ import annotations

import pickle
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monarch_cli_sync.config import AppConfig, MonarchConfig
from monarch_cli_sync.monarch.session import load_or_login


def _config(email="user@example.com", password="secret", mfa="") -> AppConfig:
    return AppConfig(monarch=MonarchConfig(email=email, password=password, mfa_secret_key=mfa))


@pytest.mark.asyncio
async def test_load_or_login_loads_existing_session(tmp_path):
    session_file = tmp_path / "monarch_session.pkl"
    session_file.write_bytes(b"fake-pickle-data")  # just needs to exist

    mm_mock = MagicMock()
    mm_mock.load_session = AsyncMock()

    with patch("monarch_cli_sync.monarch.session.MonarchMoney", return_value=mm_mock):
        result = await load_or_login(_config(), session_file=session_file)

    mm_mock.load_session.assert_called_once()
    mm_mock.login.assert_not_called()
    assert result is mm_mock


@pytest.mark.asyncio
async def test_load_or_login_falls_back_to_login_when_no_session(tmp_path):
    session_file = tmp_path / "monarch_session.pkl"
    # File does not exist

    mm_mock = MagicMock()
    mm_mock.login = AsyncMock()

    with patch("monarch_cli_sync.monarch.session.MonarchMoney", return_value=mm_mock):
        result = await load_or_login(_config(), session_file=session_file)

    mm_mock.load_session.assert_not_called()
    mm_mock.login.assert_called_once_with(
        email="user@example.com",
        password="secret",
        use_saved_session=False,
        save_session=True,
        mfa_secret_key=None,
    )
    assert result is mm_mock


@pytest.mark.asyncio
async def test_load_or_login_falls_back_when_load_raises(tmp_path):
    session_file = tmp_path / "monarch_session.pkl"
    session_file.write_bytes(b"corrupt")

    mm_mock = MagicMock()
    mm_mock.load_session = AsyncMock(side_effect=Exception("bad pickle"))
    mm_mock.login = AsyncMock()

    with patch("monarch_cli_sync.monarch.session.MonarchMoney", return_value=mm_mock):
        result = await load_or_login(_config(), session_file=session_file)

    mm_mock.login.assert_called_once()
    assert result is mm_mock


@pytest.mark.asyncio
async def test_load_or_login_force_skips_existing_session(tmp_path):
    session_file = tmp_path / "monarch_session.pkl"
    session_file.write_bytes(b"valid-looking-data")

    mm_mock = MagicMock()
    mm_mock.login = AsyncMock()

    with patch("monarch_cli_sync.monarch.session.MonarchMoney", return_value=mm_mock):
        await load_or_login(_config(), force=True, session_file=session_file)

    mm_mock.load_session.assert_not_called()
    mm_mock.login.assert_called_once()


@pytest.mark.asyncio
async def test_load_or_login_exits_2_when_login_fails(tmp_path):
    from monarchmoney import LoginFailedException

    session_file = tmp_path / "monarch_session.pkl"

    mm_mock = MagicMock()
    mm_mock.login = AsyncMock(side_effect=LoginFailedException("bad creds"))

    with patch("monarch_cli_sync.monarch.session.MonarchMoney", return_value=mm_mock):
        with pytest.raises(SystemExit) as exc_info:
            await load_or_login(_config(), session_file=session_file)

    assert exc_info.value.code == 2


@pytest.mark.asyncio
async def test_load_or_login_exits_2_when_missing_credentials(tmp_path):
    session_file = tmp_path / "monarch_session.pkl"

    mm_mock = MagicMock()

    with patch("monarch_cli_sync.monarch.session.MonarchMoney", return_value=mm_mock):
        with pytest.raises(SystemExit) as exc_info:
            await load_or_login(_config(email="", password=""), session_file=session_file)

    assert exc_info.value.code == 2
    mm_mock.login.assert_not_called()


@pytest.mark.asyncio
async def test_load_or_login_mfa_secret_key_passed_to_login(tmp_path):
    session_file = tmp_path / "monarch_session.pkl"

    mm_mock = MagicMock()
    mm_mock.login = AsyncMock()

    with patch("monarch_cli_sync.monarch.session.MonarchMoney", return_value=mm_mock):
        await load_or_login(_config(mfa="TOTPSECRET"), session_file=session_file)

    mm_mock.login.assert_called_once_with(
        email="user@example.com",
        password="secret",
        use_saved_session=False,
        save_session=True,
        mfa_secret_key="TOTPSECRET",
    )
