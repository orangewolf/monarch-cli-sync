"""Amazon session management: load from stored cookies or perform interactive login."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from amazonorders.conf import AmazonOrdersConfig
from amazonorders.exception import AmazonOrdersAuthError
from amazonorders.session import AmazonSession

from monarch_cli_sync.config import AppConfig, CONFIG_DIR

logger = logging.getLogger(__name__)

COOKIE_FILE = CONFIG_DIR / "amazon_cookies.json"


def get_cookie_file(config_dir: Path | None = None) -> Path:
    base = config_dir or CONFIG_DIR
    return base / "amazon_cookies.json"


def load_or_login(
    config: AppConfig,
    force: bool = False,
    cookie_file: Path | None = None,
    *,
    _session_cls=None,
) -> AmazonSession:
    """Return an AmazonSession ready to make requests.

    If cookies are already stored and force=False, loads them and marks the
    session as authenticated without performing a new login.

    If cookies are missing or force=True, performs a fresh interactive login.
    Requires a TTY; raises SystemExit(2) when running non-interactively.

    Raises SystemExit(2) on any auth failure.
    """
    path = cookie_file or get_cookie_file()
    SessionCls = _session_cls or AmazonSession

    path.parent.mkdir(parents=True, exist_ok=True)

    amazon_config = AmazonOrdersConfig(data={"cookie_jar_path": str(path)})

    # Empty strings → None so we don't trip the upstream guard that fires when
    # captcha_solver is set without captcha_api_key.
    captcha_solver = config.amazon.captcha_solver or None
    captcha_api_key = config.amazon.captcha_api_key or None

    if not force and path.exists():
        session = SessionCls(
            username=config.amazon.username or "",
            password=config.amazon.password or "",
            config=amazon_config,
            captcha_solver=captcha_solver,
            captcha_api_key=captcha_api_key,
        )
        # Cookies loaded by constructor; mark authenticated so orders API works.
        session.is_authenticated = True
        logger.debug("Loaded existing Amazon cookies from %s", path)
        return session

    # Need fresh login — only possible interactively.
    if not sys.stdin.isatty():
        logger.error(
            "Amazon login required but running non-interactively. "
            "Run 'monarch-cli-sync auth amazon' first to persist cookies."
        )
        sys.exit(2)

    if not config.amazon.username or not config.amazon.password:
        logger.error("AMAZON_USERNAME and AMAZON_PASSWORD must be set for login.")
        sys.exit(2)

    session = SessionCls(
        username=config.amazon.username,
        password=config.amazon.password,
        config=amazon_config,
        captcha_solver=captcha_solver,
        captcha_api_key=captcha_api_key,
    )

    try:
        session.login()
    except AmazonOrdersAuthError as exc:
        logger.error("Amazon login failed: %s", exc)
        sys.exit(2)

    logger.debug("Amazon login successful, cookies saved to %s", path)
    return session
