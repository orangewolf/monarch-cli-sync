# API cassette testing

This test suite uses [`vcrpy`](https://vcrpy.readthedocs.io/) through
[`pytest-recording`](https://github.com/kiwicom/pytest-recording) so API tests can
run either against live services while recording responses or offline against
previously recorded cassettes.

## Install test dependencies

```bash
pip install -e '.[dev]'
```

## Default offline / network-blocked runs

The default test command is fully offline: `pyproject.toml` pins
`addopts = "--block-network --record-mode=none"`. Existing cassettes replay,
any accidental real socket fails loudly, and a missing cassette fails loudly
instead of silently re-recording:

```bash
source .venv/bin/activate
pytest
```

You can drop the defaults for a single invocation by passing the override on
the command line — pytest's later flag wins:

```bash
pytest --record-mode=once     # allow new recordings if cassettes are missing
pytest --record-mode=all      # force re-record everything
```

## Live recording / re-recording

Live API tests must use only environment variables ending in `_TEST`; do not use
real production credentials for cassette recording. Only `_TEST`-suffixed
variables are exposed to test code (see `get_test_credentials` in
`tests/conftest.py`).

Supported test credential variables:

```dotenv
MONARCH_EMAIL_TEST=you+test@example.com
MONARCH_PASSWORD_TEST=...
MONARCH_MFA_SECRET_KEY_TEST=...   # 30s TOTP secret; redacted on record (see below)
AMAZON_USERNAME_TEST=you+test@example.com
AMAZON_PASSWORD_TEST=...
AMAZON_OTP_SECRET_TEST=...
```

> Note: the placeholder MFA secret `JBSWY3DPEHPK3PXP` that appears in
> `tests/test_e2e_api_recording.py` is the well-known RFC 6238 example
> base32 string. It is intentional: a syntactically valid placeholder so
> `pyotp.TOTP(...).now()` does not crash before VCR can intercept the
> request. **Never replace it with your real `_TEST` MFA secret in a
> committed file** — set `MONARCH_MFA_SECRET_KEY_TEST` in your local `.env`
> instead.

To force-refresh every cassette (only `e2e`-marked tests touch the network):

```bash
pytest --record-mode=all -m e2e
```

To re-record a specific test:

```bash
rm tests/cassettes/<name>.yaml
pytest tests/test_e2e_api_recording.py::<test_name> --record-mode=once
```

To focus on the real API end-to-end tests only:

```bash
pytest tests/test_e2e_api_recording.py --record-mode=all
pytest tests/test_e2e_api_recording.py --block-network --record-mode=none
```

Cassettes are stored in `tests/cassettes/`. Current cassette files:

- `tests/cassettes/e2e_monarch_auth_and_transactions.yaml`
- `tests/cassettes/e2e_amazon_auth_and_orders.yaml`
  (current contents have HTML bodies wholesale-replaced with
  `FILTERED_HTML_BODY` — see "Amazon strategy" below for why parsing
  coverage lives elsewhere)

## VCR config: single source of truth

All VCR configuration is in `tests/conftest.py` via the session-scoped
`vcr_config` fixture, consumed automatically by `@pytest.mark.vcr`. End-to-end
tests in `tests/test_e2e_api_recording.py` use `@pytest.mark.vcr` plus
`@pytest.mark.default_cassette("<name>")` to pin the cassette filename.

The fixture supplies:

- `filter_headers` for `authorization`, `cookie`, `set-cookie`, `x-api-key`,
  `x-auth-token`, `x-csrf-token`.
- `filter_query_parameters` for credentials, tokens, `email`, `username`, `otp`.
- `filter_post_data_parameters` for credentials and OTP/MFA secrets.
- `before_record_request` / `before_record_response` scrubbers that:
  1. Walk JSON bodies recursively and replace by key name (extended set
     covers `totp`, `otp_code`, `passcode`, `pwd`, `claimcheck`, `id_token`,
     `accountnumber`, `last4`, `phone`, `birthday`, `sid`, `swfid`, `mfa`,
     `csrftoken`, `plaid_name`, etc. — see `SENSITIVE_FIELD_NAMES`).
  2. Replace numeric-sensitive keys (`amount`, `balance`,
     `available_balance`) with the stable placeholder `-12.34` rather than
     `0`. The non-zero negative lets replay assertions still validate sign
     (Monarch debits are negative) and float shape — a literal `0` silently
     passes `isinstance(x, float)` and masks parser regressions.
  3. Strip response-only fingerprint headers (`x-amz-rid`, `cf-ray`,
     `x-request-id`, etc. — see `RESPONSE_FINGERPRINT_HEADERS`).
  4. Replace any non-empty `_TEST` env-var value with `FILTERED` everywhere
     it appears (request URI, request body, response body) so an email or
     password leaking into a return-to URL or echoed-back HTML still gets
     redacted.

## Amazon strategy: HTML fixtures, not VCR

VCR replay for Amazon is brittle — Amazon's HTML changes often, embeds CSRF
tokens and anti-bot challenges, and the auth flow may require CAPTCHA or
device approval that VCR cannot replay. The repository therefore covers
Amazon order-history parsing with **sanitized HTML fixtures**, not cassettes:

- Fixture file: `tests/fixtures/amazon/order_history_2024.html` — a
  hand-crafted, fully fictional order-history page modeled on Amazon's real
  HTML structure (uses the same `data-component` attributes and
  `.order-card` / `.yohtmlc-*` hooks the upstream `amazonorders.Selectors`
  consume).
- Tests: `tests/test_amazon_html_parsing.py` parses the fixture through
  `amazonorders.entity.order.Order` and feeds the result into
  `monarch_cli_sync.amazon.orders._normalize_order`, asserting on order
  number, date, amount, and items_desc for normal, four-figure-total, and
  cancelled orders.

`tests/test_e2e_api_recording.py::test_e2e_amazon_auth_and_list_orders`
remains in the suite as a hook for future live re-recording but is marked
`xfail(strict=False)`. Its existing cassette has every HTML body replaced
with `FILTERED_HTML_BODY` (a remnant of the prior heavyweight scrubbing
strategy) so replay cannot exercise the parser; the HTML-fixture tests are
the ones that actually pin Amazon parsing behavior.

When Amazon HTML drifts, update both `tests/fixtures/amazon/` and the
selectors documented at the top of the fixture comment.

## Credential safety

Two layers protect cassettes from leaking real credentials:

1. **Always-on offline scan.** `tests/test_vcr_config.py::
   test_cassette_files_contain_no_bare_tokens` runs in every default offline
   pytest invocation and rejects committed cassettes that contain
   bearer-style auth headers, raw `password=...` values, JWT-shaped tokens
   (`eyJ…`), or long opaque token shapes. A second test —
   `test_cassettes_contain_no_live_test_credential_values` — additionally
   scans for verbatim `_TEST` env-var values whenever those are loaded
   locally (skipped in CI where `_TEST` vars are absent).
2. **Post-record session-teardown guard.** When a session is launched in
   any recording mode (`--record-mode=once|all|new_episodes|rewrite`), the
   autouse `_post_record_secret_leak_guard` fixture in `tests/conftest.py`
   walks every YAML in `tests/cassettes/` after the run and fails the
   session if any `_TEST` env-var value or credential-shape pattern leaked
   in. This catches scrubber regressions at record time, not commit time.

If the post-record guard fires:

- Identify the cassette and the leaking value from the failure message.
- Add the missing key name to `SENSITIVE_FIELD_NAMES` in
  `tests/conftest.py`, or extend `SENSITIVE_HEADERS` /
  `SENSITIVE_QUERY_PARAMETERS` / `SENSITIVE_POST_DATA_PARAMETERS` if it's
  on a header or parameter.
- Delete the bad cassette and re-record.

In addition, before committing any new cassette, eyeball it. Automation is
great until it confidently preserves the one token you forgot existed:

```bash
grep -RniE 'authorization|cookie|set-cookie|password|token|secret|api[_-]?key' tests/cassettes || true
```

Only placeholders such as `FILTERED` or `-12.34` should appear.

## CI behavior

- CI default command: `pytest`. With the `addopts` defaults this is
  equivalent to `pytest --block-network --record-mode=none`. No network, no
  recording, missing cassettes fail loudly.
- The `e2e` marker is included in the default run; tests with no cassette
  and no `_TEST` credentials are `pytest.skip`-ed (skip messages surface in
  CI logs).
- Re-recording is a manual job, run locally with `_TEST` creds; never in CI.
- The cassette-safety scan runs as part of the default suite, so it is
  impossible to merge a cassette containing the patterns it knows about
  even if every other test is skipped.
