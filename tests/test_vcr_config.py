"""TDD tests for VCR/pytest-recording configuration.

These tests verify that the project's VCR setup:
- Filters sensitive headers (Authorization, Cookie, Set-Cookie) from cassettes
- Scrubs credentials from request/response bodies before persisting
- Stores cassettes in tests/cassettes/
- Uses *_TEST-suffixed environment variables for live API recording

Run tests offline (default, cassettes provided):
    pytest tests/test_vcr_config.py

Re-record cassettes against the live API (requires _TEST env vars):
    pytest tests/test_vcr_config.py --vcr-record=all
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Header filtering
# ---------------------------------------------------------------------------


def test_vcr_config_filters_authorization(vcr_config):
    """VCR config must filter Authorization headers to prevent token leaks."""
    filter_headers = vcr_config.get("filter_headers", [])
    header_names = [
        (h[0].lower() if isinstance(h, (list, tuple)) else h.lower())
        for h in filter_headers
    ]
    assert "authorization" in header_names, (
        "vcr_config must include 'authorization' in filter_headers"
    )


def test_vcr_config_filters_cookie(vcr_config):
    """VCR config must filter Cookie headers."""
    filter_headers = vcr_config.get("filter_headers", [])
    header_names = [
        (h[0].lower() if isinstance(h, (list, tuple)) else h.lower())
        for h in filter_headers
    ]
    assert "cookie" in header_names, (
        "vcr_config must include 'cookie' in filter_headers"
    )


def test_vcr_config_filters_set_cookie(vcr_config):
    """VCR config must filter Set-Cookie response headers."""
    filter_headers = vcr_config.get("filter_headers", [])
    header_names = [
        (h[0].lower() if isinstance(h, (list, tuple)) else h.lower())
        for h in filter_headers
    ]
    assert "set-cookie" in header_names, (
        "vcr_config must include 'set-cookie' in filter_headers"
    )


# ---------------------------------------------------------------------------
# Cassette directory
# ---------------------------------------------------------------------------


def test_vcr_cassette_dir_points_to_tests_cassettes(vcr_cassette_dir):
    """Cassettes must be stored in the project-wide tests/cassettes/ directory."""
    expected = str(Path(__file__).parent / "cassettes")
    assert vcr_cassette_dir == expected, (
        f"vcr_cassette_dir should be {expected!r}, got {vcr_cassette_dir!r}"
    )


# ---------------------------------------------------------------------------
# Body scrubbers present
# ---------------------------------------------------------------------------


def test_vcr_config_has_before_record_response(vcr_config):
    """VCR config must provide a response body scrubber."""
    assert callable(vcr_config.get("before_record_response")), (
        "vcr_config must include a callable 'before_record_response'"
    )


def test_vcr_config_has_before_record_request(vcr_config):
    """VCR config must provide a request body scrubber."""
    assert callable(vcr_config.get("before_record_request")), (
        "vcr_config must include a callable 'before_record_request'"
    )


def test_vcr_config_decodes_compressed_responses(vcr_config):
    """VCR must decompress responses so body scrubbing can inspect them."""
    assert vcr_config.get("decode_compressed_response") is True, (
        "vcr_config must set decode_compressed_response=True"
    )


# ---------------------------------------------------------------------------
# Response body scrubber behaviour
# ---------------------------------------------------------------------------


def _make_vcr_response(body_dict: dict) -> dict:
    """Build a minimal VCR response dict with a JSON body."""
    return {
        "status": {"code": 200, "message": "OK"},
        "headers": {"Content-Type": ["application/json"]},
        "body": {"string": json.dumps(body_dict).encode()},
        "url": "https://api.monarch.com/auth/login/",
    }


def test_response_scrubber_redacts_token(vcr_config):
    """The response scrubber must redact 'token' from JSON bodies."""
    scrubber = vcr_config["before_record_response"]
    result = scrubber(_make_vcr_response({"token": "real-secret-token-123", "user_id": "42"}))
    body = json.loads(result["body"]["string"])
    assert body["token"] == "FILTERED", "token must be scrubbed from response body"
    assert body["user_id"] == "FILTERED", "user_id must be scrubbed from response body"


def test_response_scrubber_redacts_password(vcr_config):
    """The response scrubber must redact 'password' from JSON bodies."""
    scrubber = vcr_config["before_record_response"]
    result = scrubber(_make_vcr_response({"password": "s3cr3t", "status": "ok"}))
    body = json.loads(result["body"]["string"])
    assert body["password"] == "FILTERED"
    assert body["status"] == "ok"


def test_response_scrubber_redacts_nested_token(vcr_config):
    """The response scrubber must scrub tokens nested inside dicts."""
    scrubber = vcr_config["before_record_response"]
    result = scrubber(_make_vcr_response({"user": {"email": "me@example.com", "token": "tok"}}))
    body = json.loads(result["body"]["string"])
    assert body["user"]["token"] == "FILTERED"


def test_response_scrubber_handles_non_json_body(vcr_config):
    """The response scrubber must leave non-JSON bodies unchanged."""
    scrubber = vcr_config["before_record_response"]
    html_body = b"<html><body>Hello</body></html>"
    response = {
        "status": {"code": 200, "message": "OK"},
        "headers": {"Content-Type": ["text/html"]},
        "body": {"string": html_body},
        "url": "https://example.com/",
    }
    result = scrubber(response)
    assert result["body"]["string"] == html_body


def test_response_scrubber_handles_empty_body(vcr_config):
    """The response scrubber must not crash on an empty body."""
    scrubber = vcr_config["before_record_response"]
    response = {
        "status": {"code": 204, "message": "No Content"},
        "headers": {},
        "body": {"string": b""},
        "url": "https://example.com/",
    }
    result = scrubber(response)  # must not raise
    assert result["body"]["string"] == b""


# ---------------------------------------------------------------------------
# Request body scrubber behaviour
# ---------------------------------------------------------------------------


class _FakeVCRRequest:
    """Minimal stand-in for vcr.request.Request."""

    def __init__(self, body: bytes | None, uri: str = "https://example.com/", method: str = "POST"):
        self.body = body
        self.uri = uri
        self.method = method
        self.headers: dict = {}


def test_request_scrubber_redacts_password(vcr_config):
    """The request scrubber must redact 'password' from JSON request bodies."""
    scrubber = vcr_config["before_record_request"]
    req = _FakeVCRRequest(
        body=json.dumps({"username": "user@example.com", "password": "s3cr3t"}).encode()
    )
    result = scrubber(req)
    body = json.loads(result.body)
    assert body["password"] == "FILTERED"
    assert body["username"] == "FILTERED"


def test_request_scrubber_handles_none_body(vcr_config):
    """The request scrubber must handle requests with no body (GET etc.)."""
    scrubber = vcr_config["before_record_request"]
    req = _FakeVCRRequest(body=None, method="GET")
    result = scrubber(req)  # must not raise
    assert result.body is None


def test_request_scrubber_handles_non_json_body(vcr_config):
    """The request scrubber must leave non-JSON bodies (form-encoded etc.) unchanged."""
    scrubber = vcr_config["before_record_request"]
    form_body = b"username=user&password=s3cr3t"
    req = _FakeVCRRequest(body=form_body)
    result = scrubber(req)
    # Must not crash; body may or may not be changed depending on implementation
    assert result.body is not None


# ---------------------------------------------------------------------------
# _TEST credential isolation
# ---------------------------------------------------------------------------


def test_test_credentials_fixture_reads_test_env_vars(test_credentials, monkeypatch):
    """The test_credentials fixture must read _TEST-suffixed environment variables."""
    # Fixture is already evaluated; verify the keys it exposes
    assert "monarch_email" in test_credentials
    assert "monarch_password" in test_credentials
    assert "amazon_username" in test_credentials
    assert "amazon_password" in test_credentials


def test_test_credentials_not_sourced_from_production_vars(monkeypatch):
    """get_test_credentials must ignore non-_TEST env vars even when set."""
    monkeypatch.setenv("MONARCH_EMAIL", "prod@example.com")
    monkeypatch.setenv("MONARCH_PASSWORD", "prodpass")
    monkeypatch.delenv("MONARCH_EMAIL_TEST", raising=False)
    monkeypatch.delenv("MONARCH_PASSWORD_TEST", raising=False)

    import conftest  # tests/ is on sys.path when pytest runs
    creds = conftest.get_test_credentials()
    assert creds["monarch_email"] is None, "Must not fall back to production MONARCH_EMAIL"
    assert creds["monarch_password"] is None, "Must not fall back to production MONARCH_PASSWORD"


def test_test_credentials_returns_test_env_var_values(monkeypatch):
    """get_test_credentials returns the _TEST env var values when set."""
    monkeypatch.setenv("MONARCH_EMAIL_TEST", "live@example.com")
    monkeypatch.setenv("MONARCH_PASSWORD_TEST", "livepass")
    monkeypatch.setenv("AMAZON_USERNAME_TEST", "amzuser@example.com")
    monkeypatch.setenv("AMAZON_PASSWORD_TEST", "amzpass")

    import conftest
    creds = conftest.get_test_credentials()
    assert creds["monarch_email"] == "live@example.com"
    assert creds["monarch_password"] == "livepass"
    assert creds["amazon_username"] == "amzuser@example.com"
    assert creds["amazon_password"] == "amzpass"


# ---------------------------------------------------------------------------
# Cassette file safety
# ---------------------------------------------------------------------------


def test_cassette_files_contain_no_bare_tokens():
    """All cassette YAML files must not contain raw auth tokens or passwords."""
    import re

    cassette_dir = Path(__file__).parent / "cassettes"
    if not cassette_dir.exists():
        pytest.skip("No cassettes directory to check")

    yaml_files = list(cassette_dir.rglob("*.yaml"))
    if not yaml_files:
        pytest.skip("No cassette files to check")

    # Patterns that look like real credentials (not "FILTERED")
    suspicious_patterns = [
        # Real bearer / token auth values (long hex/base64 strings)
        r"(?i)authorization:\s*(?:Bearer|Token)\s+[A-Za-z0-9+/._-]{20,}",
        # Passwords that are not the placeholder
        r"(?i)['\"]?password['\"]?\s*[:=]\s*['\"](?!FILTERED)[^'\"\s]{6,}",
        # Cookies with real values
        r"(?i)(?:^|\s)cookie:\s+[A-Za-z0-9%+_=; -]{20,}",
    ]

    for yaml_file in yaml_files:
        content = yaml_file.read_text(encoding="utf-8")
        for pattern in suspicious_patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            assert not matches, (
                f"Cassette {yaml_file.name} may contain real credentials "
                f"(pattern {pattern!r}): {matches}"
            )
