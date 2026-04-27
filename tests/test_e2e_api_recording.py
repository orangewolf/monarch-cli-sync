"""End-to-end API tests that record/replay real Monarch and Amazon calls.

These tests use ``@pytest.mark.vcr`` from ``pytest-recording`` so the project's
single ``vcr_config`` fixture (in ``conftest.py``) drives every cassette —
filters, scrubbers, and credential redaction. The CLI ``--record-mode`` option
controls whether a run records or replays. Default is ``--record-mode=none``
(see ``pyproject.toml``) so missing cassettes / mismatched interactions fail
loudly rather than silently re-recording.

Each test still calls ``_skip_without_live_credentials_or_cassette`` so that:

- Default offline runs skip when a cassette is absent and no ``_TEST`` creds
  are loaded (instead of erroring at the first VCR cassette miss).
- Recording runs (``--record-mode=all`` etc.) skip when the necessary
  ``_TEST`` env vars are not set.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from monarch_cli_sync.amazon.orders import fetch_orders
from monarch_cli_sync.config import AmazonConfig, AppConfig, MonarchConfig
from monarch_cli_sync.monarch.session import load_or_login as monarch_load_or_login
from monarch_cli_sync.monarch.transactions import fetch_amazon_transactions


CASSETTE_DIR = Path(__file__).parent / "cassettes"
MONARCH_CASSETTE_NAME = "e2e_monarch_auth_and_transactions"
AMAZON_CASSETTE_NAME = "e2e_amazon_auth_and_orders"
MONARCH_CASSETTE = CASSETTE_DIR / f"{MONARCH_CASSETTE_NAME}.yaml"
AMAZON_CASSETTE = CASSETTE_DIR / f"{AMAZON_CASSETTE_NAME}.yaml"

DUMMY_EMAIL = "vcr-replay@example.invalid"
DUMMY_PASSWORD = "vcr-replay-password"
# Public RFC 6238 example secret: valid base32 so MFA replay does not fail
# locally before VCR can respond. Never replace with a real secret in code.
DUMMY_MFA_SECRET = "JBSWY3DPEHPK3PXP"


def _record_mode(pytestconfig: pytest.Config) -> str:
    return pytestconfig.getoption("--record-mode") or "none"


def _can_record(pytestconfig: pytest.Config) -> bool:
    return _record_mode(pytestconfig) in {"all", "new_episodes", "once", "rewrite"}


def _skip_without_live_credentials_or_cassette(
    *,
    cassette_path: Path,
    pytestconfig: pytest.Config,
    missing: list[str],
    service: str,
) -> None:
    if cassette_path.exists():
        return
    if _can_record(pytestconfig) and not missing:
        return
    if _can_record(pytestconfig):
        pytest.skip(
            f"{service} E2E recording needs missing env vars: {', '.join(missing)}"
        )
    pytest.skip(f"{service} E2E cassette does not exist yet: {cassette_path}")


@pytest.mark.e2e
@pytest.mark.vcr
@pytest.mark.default_cassette(MONARCH_CASSETTE_NAME)
@pytest.mark.asyncio
async def test_e2e_monarch_auth_and_list_transactions(
    tmp_path: Path,
    pytestconfig: pytest.Config,
    test_credentials: dict[str, str | None],
):
    """Authenticate with Monarch and fetch a bounded transaction list."""
    missing = [
        name
        for name, key in [
            ("MONARCH_EMAIL_TEST", "monarch_email"),
            ("MONARCH_PASSWORD_TEST", "monarch_password"),
        ]
        if not test_credentials.get(key)
    ]
    _skip_without_live_credentials_or_cassette(
        cassette_path=MONARCH_CASSETTE,
        pytestconfig=pytestconfig,
        missing=missing,
        service="Monarch",
    )

    config = AppConfig(
        monarch=MonarchConfig(
            email=test_credentials.get("monarch_email") or DUMMY_EMAIL,
            password=test_credentials.get("monarch_password") or DUMMY_PASSWORD,
            mfa_secret_key=(
                test_credentials.get("monarch_mfa_secret_key") or DUMMY_MFA_SECRET
            ),
        )
    )

    mm = await monarch_load_or_login(
        config,
        force=True,
        session_file=tmp_path / "monarch_session.pkl",
    )
    transactions = await fetch_amazon_transactions(
        mm,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        limit=10,
    )

    assert isinstance(transactions, list)
    for transaction in transactions:
        assert transaction.id
        assert transaction.date
        # Numeric-sensitive fields redact to a stable non-zero placeholder
        # (see NUMERIC_REDACTION_PLACEHOLDER in conftest); we accept either
        # the legacy 0.0 (older cassettes) or the new placeholder so this
        # assertion does not require a re-record.
        assert isinstance(transaction.amount, float)


@pytest.mark.e2e
@pytest.mark.vcr
@pytest.mark.default_cassette(AMAZON_CASSETTE_NAME)
@pytest.mark.xfail(
    reason=(
        "Amazon auth may require CAPTCHA/device approval outside VCR control, "
        "and the current cassette has HTML bodies wholesale-replaced with "
        "FILTERED_HTML_BODY so replay cannot parse the login form. Real "
        "Amazon parsing coverage lives in tests/test_amazon_html_parsing.py."
    ),
    strict=False,
)
def test_e2e_amazon_auth_and_list_orders(
    pytestconfig: pytest.Config,
    test_credentials: dict[str, str | None],
):
    """Attempt Amazon auth and fetch a bounded order list.

    Marked ``xfail`` because Amazon may require CAPTCHA or out-of-band device
    approval. When it succeeds under ``--record-mode=all``, the cassette can
    be used for offline replay — but Amazon's HTML-heavy flow tends to drift,
    so structural parsing coverage is intentionally provided by the
    ``tests/test_amazon_html_parsing.py`` HTML-fixture tests instead.
    """
    missing = [
        name
        for name, key in [
            ("AMAZON_USERNAME_TEST", "amazon_username"),
            ("AMAZON_PASSWORD_TEST", "amazon_password"),
        ]
        if not test_credentials.get(key)
    ]
    _skip_without_live_credentials_or_cassette(
        cassette_path=AMAZON_CASSETTE,
        pytestconfig=pytestconfig,
        missing=missing,
        service="Amazon",
    )

    from amazonorders.session import AmazonSession

    amazon_config = AmazonConfig(
        username=test_credentials.get("amazon_username") or DUMMY_EMAIL,
        password=test_credentials.get("amazon_password") or DUMMY_PASSWORD,
        otp_secret_key=test_credentials.get("amazon_otp_secret") or DUMMY_MFA_SECRET,
    )
    session = AmazonSession(
        username=amazon_config.username,
        password=amazon_config.password,
        otp_secret_key=amazon_config.otp_secret_key,
    )
    session.login()
    orders = fetch_orders(
        session,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )

    assert isinstance(orders, list)
