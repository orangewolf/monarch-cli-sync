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
    "address",
    "anonymous_id",
    "api_key",
    "auth",
    "authorization",
    "city",
    "cookie",
    "csrf",
    "display_name",
    "displayname",
    "email",
    "external_id",
    "externalid",
    "id",
    "mfa_secret",
    "mfa_secret_key",
    "name",
    "notes",
    "otp",
    "passcode",
    "password",
    "plaidname",
    "refresh_token",
    "secret",
    "session",
    "session_id",
    "set-cookie",
    "state",
    "token",
    "tokenexpiration",
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

SENSITIVE_QUERY_PARAMETERS = [
    "access_token",
    "api_key",
    "auth_token",
    "password",
    "refresh_token",
    "session",
    "token",
]


SENSITIVE_NUMERIC_FIELD_NAMES = {
    "amount",
    "balance",
}


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
                0
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


def _redact_headers(headers: Any) -> Any:
    """Replace sensitive request/response header values regardless of case."""
    if not isinstance(headers, MutableMapping):
        return headers
    for header_name in list(headers.keys()):
        if str(header_name).lower() in SENSITIVE_HEADERS:
            headers[header_name] = ["FILTERED"]
    return headers


def before_record_response(response: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Scrub sensitive values from a VCR response before it is persisted."""
    _redact_headers(response.get("headers"))
    body = response.get("body")
    if isinstance(body, MutableMapping) and "string" in body:
        body["string"] = _redact_json_body(body["string"])
    return response


def before_record_request(request: Any) -> Any:
    """Scrub sensitive values from a VCR request before it is persisted."""
    _redact_headers(getattr(request, "headers", None))
    if getattr(request, "body", None) is not None:
        request.body = _redact_json_body(request.body)
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
        "before_record_request": before_record_request,
        "before_record_response": before_record_response,
        "decode_compressed_response": True,
        "record_mode": "once",
    }


@pytest.fixture
def test_credentials() -> dict[str, str | None]:
    """Expose only _TEST-suffixed credentials to live API tests."""
    return get_test_credentials()
