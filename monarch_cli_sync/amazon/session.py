"""Amazon session management: load from stored cookies or perform interactive login."""

from __future__ import annotations

import inspect
import logging
import sys
from pathlib import Path

from amazonorders.conf import AmazonOrdersConfig
from amazonorders.exception import AmazonOrdersAuthError
from amazonorders.session import AmazonSession

from monarch_cli_sync.config import AmazonAccountConfig, AppConfig, CONFIG_DIR

logger = logging.getLogger(__name__)

COOKIE_FILE = CONFIG_DIR / "amazon_cookies.json"


def get_cookie_file(config_dir: Path | None = None) -> Path:
    """Return the legacy single-account cookie file path (used by doctor command)."""
    base = config_dir or CONFIG_DIR
    return base / "amazon_cookies.json"


def _select_accounts(
    accounts: list[AmazonAccountConfig],
    selector: str | int | None,
) -> list[AmazonAccountConfig]:
    """Filter accounts by index (int) or label (str), or return all if selector is None."""
    if selector is None:
        return accounts
    if isinstance(selector, int):
        return [a for a in accounts if a.index == selector]
    return [a for a in accounts if a.label == selector]


def load_or_login(
    account: AmazonAccountConfig,
    force: bool = False,
    cookie_file: Path | None = None,
    *,
    _session_cls=None,
) -> AmazonSession:
    """Return an AmazonSession ready to make requests for the given account.

    If cookies are already stored and force=False, loads them and marks the
    session as authenticated without performing a new login.

    If cookies are missing or force=True, performs a fresh interactive login.
    Requires a TTY; raises SystemExit(2) when running non-interactively.

    Raises SystemExit(2) on any auth failure.
    """
    # Resolve cookie file: explicit override > per-account stem > default
    resolved_cookie_file = cookie_file or (CONFIG_DIR / f"{account.cookie_file_stem}.json")

    SessionCls = _session_cls or AmazonSession

    resolved_cookie_file.parent.mkdir(parents=True, exist_ok=True)

    amazon_config = AmazonOrdersConfig(data={"cookie_jar_path": str(resolved_cookie_file)})

    # Empty strings → None so we don't trip the upstream guard that fires when
    # captcha_solver is set without captcha_api_key.
    captcha_solver = account.captcha_solver or None
    captcha_api_key = account.captcha_api_key or None

    if not force and resolved_cookie_file.exists():
        session = _build_session(
            SessionCls,
            username=account.username or "",
            password=account.password or "",
            amazon_config=amazon_config,
            captcha_solver=captcha_solver,
            captcha_api_key=captcha_api_key,
        )
        # Cookies loaded by constructor; mark authenticated so orders API works.
        session.is_authenticated = True
        logger.debug(
            "[amazon:%s] Loaded existing cookies from %s",
            account.label,
            resolved_cookie_file,
        )
        return session

    # Need fresh login — only possible interactively.
    if not sys.stdin.isatty():
        logger.error(
            "[amazon:%s] Login required but running non-interactively. "
            "Run 'monarch-cli-sync auth amazon --account %s' first.",
            account.label,
            account.label,
        )
        sys.exit(2)

    if not account.username or not account.password:
        logger.error(
            "[amazon:%s] AMAZON_USERNAME and AMAZON_PASSWORD must be set for login.",
            account.label,
        )
        sys.exit(2)

    session = _build_session(
        SessionCls,
        username=account.username,
        password=account.password,
        amazon_config=amazon_config,
        captcha_solver=captcha_solver,
        captcha_api_key=captcha_api_key,
    )

    try:
        session.login()
    except AmazonOrdersAuthError as exc:
        logger.error("[amazon:%s] Login failed: %s", account.label, exc)
        sys.exit(2)

    logger.debug(
        "[amazon:%s] Login successful, cookies saved to %s",
        account.label,
        resolved_cookie_file,
    )
    return session


def load_all_sessions(
    config: AppConfig,
    account_selector: str | int | None = None,
    force: bool = False,
    *,
    _session_cls=None,
) -> list[tuple[AmazonAccountConfig, AmazonSession]]:
    """Authenticate all configured accounts (or a selected subset).

    Returns a list of (account_config, session) pairs for accounts that
    authenticated successfully.  Logs a warning and continues when an account
    fails auth non-interactively (SystemExit(2)).  Re-raises on any other
    SystemExit code (e.g. 4 for errors).
    """
    accounts = _select_accounts(config.amazon.accounts, account_selector)
    results: list[tuple[AmazonAccountConfig, AmazonSession]] = []
    for acct in accounts:
        try:
            sess = load_or_login(acct, force=force, _session_cls=_session_cls)
            results.append((acct, sess))
        except SystemExit as exc:
            if exc.code == 2:
                logger.warning(
                    "[amazon:%s] Auth failed, skipping account.", acct.label
                )
            else:
                raise
    return results


def _build_session(
    session_cls,
    *,
    username: str,
    password: str,
    amazon_config: AmazonOrdersConfig,
    captcha_solver: str | None,
    captcha_api_key: str | None,
) -> AmazonSession:
    """Build an AmazonSession across amazonorders versions.

    Recent forks accept captcha_solver/captcha_api_key constructor kwargs;
    upstream amazonorders does not. Keep the optional solver integration from
    exploding against the real constructor used in production/tests.
    """
    kwargs = {
        "username": username,
        "password": password,
        "config": amazon_config,
    }
    signature = inspect.signature(session_cls)
    if "captcha_solver" in signature.parameters:
        kwargs["captcha_solver"] = captcha_solver
    if "captcha_api_key" in signature.parameters:
        kwargs["captcha_api_key"] = captcha_api_key
    return session_cls(**kwargs)
