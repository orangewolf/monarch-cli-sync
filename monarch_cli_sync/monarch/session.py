"""Monarch Money session management: load from pickle or perform interactive login."""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from pathlib import Path

from monarchmoney import MonarchMoney, RequireMFAException, LoginFailedException
from monarchmoney.monarchmoney import MonarchMoneyEndpoints

# Upstream library (v0.1.15) still uses the old domain. Monarch rebranded and
# moved their API to api.monarch.com — patch it until a new release ships.
# Track: https://github.com/hammem/monarchmoney/issues/184
MonarchMoneyEndpoints.BASE_URL = "https://api.monarch.com"

from monarch_cli_sync.config import AppConfig, CONFIG_DIR

logger = logging.getLogger(__name__)

SESSION_FILE = CONFIG_DIR / "monarch_session.pkl"


def get_session_file(config_dir: Path | None = None) -> Path:
    base = config_dir or CONFIG_DIR
    return base / "monarch_session.pkl"


async def load_or_login(
    config: AppConfig,
    force: bool = False,
    session_file: Path | None = None,
) -> MonarchMoney:
    """Return an authenticated MonarchMoney instance.

    Attempts to load an existing session from pickle. Falls back to login().
    Raises SystemExit(2) on auth failure so the CLI can exit cleanly.
    """
    path = session_file or get_session_file()
    mm = MonarchMoney(session_file=str(path))

    if not force and path.exists():
        try:
            loaded_session = mm.load_session()
            if inspect.isawaitable(loaded_session):
                await loaded_session
            logger.debug("Loaded existing Monarch session from %s", path)
            return mm
        except Exception as exc:
            logger.warning("Failed to load session (%s), falling back to login.", exc)

    # Ensure config dir exists
    path.parent.mkdir(parents=True, exist_ok=True)

    email = config.monarch.email
    password = config.monarch.password
    mfa_secret_key = config.monarch.mfa_secret_key or None

    if not email or not password:
        logger.error("MONARCH_EMAIL and MONARCH_PASSWORD must be set for login.")
        sys.exit(2)

    try:
        await mm.login(
            email=email,
            password=password,
            use_saved_session=False,
            save_session=True,
            mfa_secret_key=mfa_secret_key or None,
        )
        logger.debug("Monarch login successful, session saved to %s", path)
    except RequireMFAException:
        if sys.stdin.isatty():
            mfa_code = input("Monarch MFA code: ").strip()
            await mm.multi_factor_authenticate(email, password, mfa_code)
            await mm.save_session(filename=str(path))
            logger.debug("Monarch MFA succeeded, session saved.")
        else:
            logger.error("Monarch requires MFA but running non-interactively.")
            sys.exit(2)
    except LoginFailedException as exc:
        logger.error("Monarch login failed: %s", exc)
        sys.exit(2)

    return mm
