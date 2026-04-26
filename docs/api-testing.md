# API cassette testing

This test suite uses [`vcrpy`](https://vcrpy.readthedocs.io/) through
[`pytest-recording`](https://github.com/kiwicom/pytest-recording) so API tests can
run either against live services while recording responses or offline against
previously recorded cassettes.

## Install test dependencies

```bash
pip install -e '.[dev]'
```

## Offline / recorded test runs

Use the normal pytest command. Existing cassettes are replayed and matching
requests do not hit the network:

```bash
pytest
```

For CI or any run that must not touch the network, use pytest-recording's blocked
network mode:

```bash
pytest --block-network --vcr-record=none
```

## Live recording / re-recording

Live API tests must use only environment variables ending in `_TEST`; do not use
real production credentials for cassette recording.

Supported test credential variables:

```dotenv
MONARCH_EMAIL_TEST=you+test@example.com
MONARCH_PASSWORD_TEST=...
MONARCH_MFA_SECRET_KEY_TEST=...
AMAZON_USERNAME_TEST=you+test@example.com
AMAZON_PASSWORD_TEST=...
AMAZON_OTP_SECRET_TEST=...
```

To record new interactions:

```bash
pytest --record-mode=new_episodes
```

To force refresh all interactions:

```bash
pytest --record-mode=all
```

To focus on the real API end-to-end tests only:

```bash
pytest tests/test_e2e_api_recording.py --record-mode=all
pytest tests/test_e2e_api_recording.py --block-network --record-mode=none
```

Cassettes are stored in `tests/cassettes/`. Current real API cassettes are:

- `tests/cassettes/e2e_monarch_auth_and_transactions.yaml`
- `tests/cassettes/e2e_amazon_auth_and_orders.yaml`

The Monarch E2E test records auth plus a bounded transaction list. The Amazon E2E test is marked `xfail` because Amazon may require CAPTCHA/device approval; when it succeeds or reaches an expected Amazon challenge page, VCR still captures the interaction for replay.

## Credential safety

VCR is configured in `tests/conftest.py` to filter sensitive headers, query
parameters, and JSON body fields before writing cassette files. It scrubs common
credential fields such as `authorization`, `cookie`, `set-cookie`, `token`,
`access_token`, `refresh_token`, `api_key`, `password`, `session`, and secrets.

Before committing any new cassette, inspect it anyway. Automation is great until
it confidently preserves the one token you forgot existed.

```bash
grep -RniE 'authorization|cookie|set-cookie|password|token|secret|api[_-]?key' tests/cassettes || true
```

Only placeholders such as `FILTERED` should appear.
