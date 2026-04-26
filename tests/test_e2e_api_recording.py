"""End-to-end API tests that record/replay real Monarch and Amazon calls.

These tests intentionally use VCR.py directly so they can decide whether to skip
before opening a cassette. That keeps normal offline runs green when no live
*_TEST credentials and no cassette exist yet, while still recording real API
traffic when run with --record-mode=all or --record-mode=new_episodes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import vcr as vcrpy

from monarch_cli_sync.amazon.orders import fetch_orders
from monarch_cli_sync.config import AmazonConfig, AppConfig, MonarchConfig
from monarch_cli_sync.monarch.session import load_or_login as monarch_load_or_login
from monarch_cli_sync.monarch.transactions import fetch_amazon_transactions


CASSETTE_DIR = Path(__file__).parent / "cassettes"
MONARCH_CASSETTE = CASSETTE_DIR / "e2e_monarch_auth_and_transactions.yaml"
AMAZON_CASSETTE = CASSETTE_DIR / "e2e_amazon_auth_and_orders.yaml"
DUMMY_EMAIL = "vcr-replay@example.invalid"
DUMMY_PASSWORD = "vcr-replay-password"
# Valid base32 so replay does not fail locally before VCR can respond.
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


def _vcr(test_credentials: dict[str, str | None], *, scrub_html: bool = False) -> vcrpy.VCR:
    conftest = pytest.importorskip("conftest")
    config = conftest.vcr_config.__wrapped__()
    replacements: list[tuple[str, str]] = []
    for value in test_credentials.values():
        if value:
            replacements.append((value, "FILTERED"))

    # Also redact deterministic replay placeholders in case a request body is
    # persisted before JSON scrubbing sees it.
    replacements.extend(
        [
            (DUMMY_EMAIL, "FILTERED"),
            (DUMMY_PASSWORD, "FILTERED"),
            (DUMMY_MFA_SECRET, "FILTERED"),
        ]
    )

    return vcrpy.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode=config["record_mode"],
        decode_compressed_response=config["decode_compressed_response"],
        filter_headers=[*config["filter_headers"], ("x-amz-rid", "FILTERED")],
        filter_query_parameters=[
            *config["filter_query_parameters"],
            ("email", "FILTERED"),
            ("username", "FILTERED"),
        ],
        filter_post_data_parameters=[
            ("email", "FILTERED"),
            ("username", "FILTERED"),
            ("password", "FILTERED"),
            ("mfa_secret_key", "FILTERED"),
            ("otp_secret_key", "FILTERED"),
        ],
        before_record_request=_chain_before_record_request(
            config["before_record_request"], replacements
        ),
        before_record_response=_chain_before_record_response(
            config["before_record_response"], replacements, scrub_html=scrub_html
        ),
    )


def _replace_known_values(text: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(text, bytes):
        decoded = text.decode("utf-8", errors="ignore")
        for old, new in replacements:
            decoded = decoded.replace(old, new)
        return decoded.encode("utf-8")
    if isinstance(text, str):
        for old, new in replacements:
            text = text.replace(old, new)
    return text


def _chain_before_record_request(scrubber, replacements: list[tuple[str, str]]):
    def _scrub(request):
        request = scrubber(request)
        if getattr(request, "body", None) is not None:
            request.body = _replace_known_values(request.body, replacements)
        if getattr(request, "uri", None):
            request.uri = _replace_known_values(request.uri, replacements)
        return request

    return _scrub


def _chain_before_record_response(
    scrubber, replacements: list[tuple[str, str]], *, scrub_html: bool = False
):
    def _scrub(response):
        response = scrubber(response)
        body = response.get("body")
        headers = response.get("headers") or {}
        content_types = headers.get("Content-Type") or headers.get("content-type") or []
        is_html = any("html" in str(content_type).lower() for content_type in content_types)
        if scrub_html and isinstance(body, dict) and "string" in body and is_html:
            body["string"] = b"FILTERED_HTML_BODY"
        elif isinstance(body, dict) and "string" in body:
            body["string"] = _replace_known_values(body["string"], replacements)
        return response

    return _scrub


@pytest.mark.e2e
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

    cassette = _vcr(test_credentials)
    cassette.record_mode = _record_mode(pytestconfig)
    with cassette.use_cassette(MONARCH_CASSETTE.name):
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
        assert isinstance(transaction.amount, float)


@pytest.mark.e2e
@pytest.mark.xfail(reason="Amazon auth may require CAPTCHA/device approval outside VCR control", strict=False)
def test_e2e_amazon_auth_and_list_orders(
    pytestconfig: pytest.Config,
    test_credentials: dict[str, str | None],
):
    """Attempt Amazon auth and fetch a bounded order list.

    This is an xfail because Amazon may require CAPTCHA or out-of-band device
    approval. When it succeeds under --record-mode=all, the cassette can be used
    for offline replay.
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
    cassette = _vcr(test_credentials, scrub_html=True)
    cassette.record_mode = _record_mode(pytestconfig)
    with cassette.use_cassette(AMAZON_CASSETTE.name):
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
