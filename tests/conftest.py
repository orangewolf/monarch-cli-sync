"""Shared pytest fixtures."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from dotenv import load_dotenv

from monarch_cli_sync.cli import main

# Make `import conftest` work from tests that need to inspect helper functions.
sys.modules.setdefault("conftest", sys.modules[__name__])

# Load local .env values for live recording, but test helpers below only expose
# variables with the _TEST suffix so production credentials are not used by
# cassette-recording tests by accident.
load_dotenv()

SENSITIVE_FIELD_NAMES = {
    "access_token",
    "accountnumber",
    "address",
    "anonymous_id",
    "api_key",
    "auth",
    "authorization",
    "birthday",
    "city",
    "claimcheck",
    "cookie",
    "csrf",
    "csrftoken",
    "display_name",
    "displayname",
    "email",
    "external_id",
    "externalid",
    "id",
    "id_token",
    "idtoken",
    "last4",
    "mfa",
    "mfa_secret",
    "mfa_secret_key",
    "name",
    "notes",
    "otp",
    "otp_code",
    "otpcode",
    "passcode",
    "password",
    "phone",
    "plaid_name",
    "plaidname",
    "pwd",
    "refresh_token",
    "secret",
    "session",
    "session_id",
    "set-cookie",
    "sid",
    "state",
    "swfid",
    "token",
    "tokenexpiration",
    "totp",
    "user_external_id",
    "user_id",
    "username",
    "zip_code",
}

SENSITIVE_HEADERS = [
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
]

# Response-only headers that don't carry secrets but uniquely fingerprint a
# request/account; scrubbed only on the response side.
RESPONSE_FINGERPRINT_HEADERS = [
    "x-amz-rid",
    "x-amz-id-1",
    "x-amz-id-2",
    "x-amz-cf-id",
    "x-amz-request-id",
    "cf-ray",
    "http_x_request_id",
    "x-request-id",
]

SENSITIVE_QUERY_PARAMETERS = [
    "access_token",
    "api_key",
    "auth_token",
    "email",
    "otp",
    "password",
    "refresh_token",
    "session",
    "token",
    "username",
]

SENSITIVE_POST_DATA_PARAMETERS = [
    "email",
    "mfa_secret_key",
    "otp",
    "otp_secret_key",
    "password",
    "totp",
    "username",
]


SENSITIVE_NUMERIC_FIELD_NAMES = {
    "amount",
    "available_balance",
    "balance",
}

# Stable placeholder for numeric-sensitive fields. Non-zero negative so replay
# assertions can still validate sign (Monarch debits are negative) and float
# shape — a literal 0 silently satisfies `isinstance(x, float)` and `x <= 0`,
# masking parser regressions.
NUMERIC_REDACTION_PLACEHOLDER = -12.34


def _normalized_key(key: Any) -> str:
    return str(key).lower().replace("-", "_").replace(".", "_")


def _is_sensitive_key(key: Any) -> bool:
    return _normalized_key(key) in SENSITIVE_FIELD_NAMES


def _is_sensitive_numeric_key(key: Any) -> bool:
    return _normalized_key(key) in SENSITIVE_NUMERIC_FIELD_NAMES


def _redact_sensitive_values(value: Any) -> Any:
    """Recursively replace sensitive values in JSON-like cassette bodies."""
    if isinstance(value, Mapping):
        return {
            key: (
                NUMERIC_REDACTION_PLACEHOLDER
                if _is_sensitive_numeric_key(key)
                else "FILTERED"
                if _is_sensitive_key(key)
                else _redact_sensitive_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value


def _decode_body(body: Any) -> tuple[str | None, bool]:
    if body in (None, b"", ""):
        return None, isinstance(body, bytes)
    if isinstance(body, bytes):
        return body.decode("utf-8"), True
    if isinstance(body, str):
        return body, False
    return None, False


def _redact_json_body(body: Any) -> Any:
    text, was_bytes = _decode_body(body)
    if text is None:
        return body
    try:
        parsed = json.loads(text)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return body

    redacted = json.dumps(_redact_sensitive_values(parsed))
    return redacted.encode("utf-8") if was_bytes else redacted


def _redact_headers(
    headers: Any, *, extra_filtered: tuple[str, ...] = ()
) -> Any:
    """Replace sensitive request/response header values regardless of case."""
    if not isinstance(headers, MutableMapping):
        return headers
    filter_set = set(SENSITIVE_HEADERS) | {h.lower() for h in extra_filtered}
    for header_name in list(headers.keys()):
        if str(header_name).lower() in filter_set:
            headers[header_name] = ["FILTERED"]
    return headers


def _live_test_credential_values() -> list[str]:
    """Snapshot non-empty `_TEST` env-var values to scrub from cassettes.

    Read at scrub-time (not import-time) so a test that monkeypatches a
    `_TEST` variable still gets its value scrubbed. Empty strings are skipped
    to avoid replacing every empty span in a body.
    """
    creds = get_test_credentials()
    return [v for v in creds.values() if v]


def _scrub_known_credentials(text: Any) -> Any:
    """Replace any live `_TEST` credential values with FILTERED in text/bytes."""
    values = _live_test_credential_values()
    if not values:
        return text
    if isinstance(text, bytes):
        decoded = text.decode("utf-8", errors="ignore")
        for value in values:
            decoded = decoded.replace(value, "FILTERED")
        return decoded.encode("utf-8")
    if isinstance(text, str):
        for value in values:
            text = text.replace(value, "FILTERED")
    return text


def before_record_response(response: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Scrub sensitive values from a VCR response before it is persisted."""
    _redact_headers(response.get("headers"), extra_filtered=tuple(RESPONSE_FINGERPRINT_HEADERS))
    body = response.get("body")
    if isinstance(body, MutableMapping) and "string" in body:
        body["string"] = _redact_json_body(body["string"])
        body["string"] = _scrub_known_credentials(body["string"])
    return response


def before_record_request(request: Any) -> Any:
    """Scrub sensitive values from a VCR request before it is persisted."""
    _redact_headers(getattr(request, "headers", None))
    if getattr(request, "body", None) is not None:
        request.body = _redact_json_body(request.body)
        request.body = _scrub_known_credentials(request.body)
    if getattr(request, "uri", None):
        request.uri = _scrub_known_credentials(request.uri)
    return request


def get_test_credentials() -> dict[str, str | None]:
    """Return only live-test credentials from _TEST-suffixed env vars."""
    return {
        "monarch_email": os.getenv("MONARCH_EMAIL_TEST"),
        "monarch_password": os.getenv("MONARCH_PASSWORD_TEST"),
        "monarch_mfa_secret_key": os.getenv("MONARCH_MFA_SECRET_KEY_TEST"),
        "amazon_username": os.getenv("AMAZON_USERNAME_TEST"),
        "amazon_password": os.getenv("AMAZON_PASSWORD_TEST"),
        "amazon_otp_secret": os.getenv("AMAZON_OTP_SECRET_TEST"),
    }


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli(runner: CliRunner):
    """Convenience wrapper: invoke(args) -> Result."""

    def invoke(*args: str, **kwargs):
        return runner.invoke(main, list(args), catch_exceptions=False, **kwargs)

    return invoke


@pytest.fixture(scope="session")
def vcr_cassette_dir() -> str:
    """Store all pytest-recording cassettes under tests/cassettes/."""
    return str(Path(__file__).parent / "cassettes")


@pytest.fixture(scope="session")
def vcr_config() -> dict[str, Any]:
    """Project-wide VCR.py configuration for safe API recording/replay."""
    return {
        "filter_headers": [(header, "FILTERED") for header in SENSITIVE_HEADERS],
        "filter_query_parameters": [
            (parameter, "FILTERED") for parameter in SENSITIVE_QUERY_PARAMETERS
        ],
        "filter_post_data_parameters": [
            (parameter, "FILTERED") for parameter in SENSITIVE_POST_DATA_PARAMETERS
        ],
        "before_record_request": before_record_request,
        "before_record_response": before_record_response,
        "decode_compressed_response": True,
        "record_mode": "once",
    }


@pytest.fixture
def test_credentials() -> dict[str, str | None]:
    """Expose only _TEST-suffixed credentials to live API tests."""
    return get_test_credentials()


# Patterns that should never appear in a freshly recorded cassette. Mirrors
# (and is checked alongside) the patterns in
# `test_cassette_files_contain_no_bare_tokens` so the offline guard and the
# post-record guard cannot drift apart.
_POST_RECORD_BAD_PATTERNS: tuple[str, ...] = (
    r"(?i)authorization:\s*(?:Bearer|Token)\s+[A-Za-z0-9+/._\-]{20,}",
    r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b",
    r"\b[A-Za-z0-9_\-]{40,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b",
)


@pytest.fixture(scope="session", autouse=True)
def _post_record_secret_leak_guard(request: pytest.FixtureRequest, vcr_cassette_dir: str):
    """At session teardown after a recording run, fail loudly if any cassette
    on disk contains a live `_TEST` credential value or a known credential-
    shape pattern.

    Only runs when the session was launched in a recording mode (anything
    other than `none`). Recording mode is read from the `--record-mode` CLI
    option provided by `pytest-recording`; if the option is not registered
    (e.g. the plugin is uninstalled) this fixture is a no-op.
    """
    yield

    try:
        record_mode = request.config.getoption("--record-mode")
    except (ValueError, KeyError):
        return
    if not record_mode or record_mode == "none":
        return

    import re

    cassette_dir = Path(vcr_cassette_dir)
    if not cassette_dir.exists():
        return

    creds = get_test_credentials()
    live_values = [v for v in creds.values() if v]
    compiled_patterns = [re.compile(p) for p in _POST_RECORD_BAD_PATTERNS]

    failures: list[str] = []
    for yaml_file in cassette_dir.rglob("*.yaml"):
        content = yaml_file.read_text(encoding="utf-8")
        lower = content.lower()
        for value in live_values:
            if value.lower() in lower:
                failures.append(
                    f"{yaml_file.name}: contains a live _TEST credential "
                    f"value (length {len(value)}); redaction missed it"
                )
        for pattern in compiled_patterns:
            for match in pattern.findall(content):
                snippet = match if isinstance(match, str) else str(match)
                failures.append(
                    f"{yaml_file.name}: matches credential-shape pattern "
                    f"{pattern.pattern!r}: {snippet[:60]}"
                )

    if failures:
        pytest.fail(
            "Post-record secret-leak guard found leaks:\n  - "
            + "\n  - ".join(failures)
        )
