# monarch-cli-sync

A Python command-line tool to sync Amazon order history into Monarch Money without relying on a browser extension.

## Setup

### 1. Prerequisites

- Python 3.11+
  - Recommended: Python 3.11–3.13
  - Note: fresh installs currently fail on Python 3.14 because an upstream dependency chain (`amazon-orders` → `Pillow 9.5.0`) does not build cleanly there yet
- A Monarch Money account

### 2. Install

```bash
git clone https://github.com/orangewolf/monarch-cli-sync.git
cd monarch-cli-sync
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .
```

If `python3.13` is not installed, use another supported interpreter such as `python3.12` or `python3.11`.

### 3. Configure credentials

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
# Amazon
AMAZON_USERNAME=you@example.com
AMAZON_PASSWORD=yourpassword

# Monarch Money
MONARCH_EMAIL=you@example.com
MONARCH_PASSWORD=yourpassword
MONARCH_MFA_SECRET_KEY=BASE32SECRETFROMMONARCH
```

`MONARCH_MFA_SECRET_KEY` is the base32 TOTP secret shown when you set up 2FA in Monarch (the string you'd normally scan as a QR code). Leave it empty if your account does not use MFA.

Alternatively, place these values in `~/.config/monarch-cli-sync/config.toml`:

```toml
[amazon]
username = "you@example.com"
password = "yourpassword"

[monarch]
email = "you@example.com"
password = "yourpassword"
mfa_secret_key = "BASE32SECRETFROMMONARCH"
```

### 4. Authenticate with Monarch

```bash
monarch-cli-sync auth monarch
```

Performs an interactive Monarch login and persists a session token for later runs.

### 5. Authenticate with Amazon

```bash
monarch-cli-sync auth amazon
```

Performs an interactive Amazon login and persists cookies for later headless runs. Amazon may prompt for a CAPTCHA or OTP code during this step — that is expected. Once cookies are saved, subsequent runs (including cron) will reuse them without prompting.

> **Note:** Amazon OTP auto-login (equivalent to `MONARCH_MFA_SECRET_KEY` for Monarch) is not yet supported. If your account requires OTP on every login, you will need to re-run `auth amazon` to refresh cookies when they expire.

### 6. Test with a dry run

```bash
monarch-cli-sync sync --dry-run
```

Fetches recent Amazon orders and Monarch transactions and prints both in tables. No changes are written to Monarch. Pass `--year YYYY` to inspect a full calendar year, or `--days N` to look back N days (default: 30).

---

## Why this exists

There is an existing project, [`alex-peck/monarch-amazon-sync`](https://github.com/alex-peck/monarch-amazon-sync), plus Monarch’s own Chrome extension approach, that help match Amazon orders to Monarch transactions.

That’s useful, but this project has a different goal:

- no Chrome extension
- no ongoing browser dependency for normal runs
- something that can be executed from the command line
- something safe and observable enough to run from cron
- clear output showing whether a run succeeded, failed, or needs attention

## Project goal

Build a CLI that performs the same core job as the Monarch Amazon extension workflow:

1. retrieve or derive Amazon order data
2. correlate that data with Monarch transactions
3. apply the appropriate merchant / note / review updates needed to make transactions meaningful
4. emit structured, human-readable status for each run

## Non-goals

At least initially, this project is **not** trying to:

- be a browser extension
- require interactive manual use for every sync
- hide failures behind silent automation

If a cron job runs, it should be obvious whether it:

- worked
- partially worked
- found nothing to do
- failed
- needs re-authentication or manual intervention

## Design principles

### 1. Cron-first

The tool should work well unattended. That means:

- deterministic exit codes
- machine-readable output option (JSON)
- concise human-readable logs
- explicit health / status reporting

### 2. Observable by default

A successful run should say what it did.
A failed run should say why.
An uncertain run should say what needs to happen next.

### 3. Minimal moving parts

Prefer a straightforward CLI architecture over anything overly clever.
If browser automation is ever needed for login/bootstrap, keep it isolated from the normal sync path.

### 4. Safe automation

Avoid anything brittle or likely to get silently broken by UI tweaks unless there is no better option.
Authentication, retries, rate limiting, and idempotency should be first-class concerns.

## Language choice

This project will be implemented in **Python**.

Why Python:

- the strongest existing library support for **personal Amazon order history** is currently in Python
- there is already an unofficial Python library for the **Monarch Money API**
- this lowers implementation risk for the hardest parts of the project
- it gives us the best chance of getting to a reliable cron-friendly tool quickly

## Current CLI shape

Available today:

```bash
monarch-cli-sync auth monarch          # interactive Monarch login, saves session token
monarch-cli-sync auth amazon           # interactive Amazon login, saves cookies
monarch-cli-sync sync --dry-run        # fetch both sides and print tables; no writes
monarch-cli-sync sync --dry-run --year 2024
monarch-cli-sync sync --dry-run --days 90
monarch-cli-sync doctor                # check config, auth files, and connectivity
```

Not yet implemented:

```bash
monarch-cli-sync sync                  # full sync with writes back to Monarch
monarch-cli-sync status                # show last run result
```

## What “working” should mean

A good run should clearly answer:

- Did the command complete successfully?
- How many Amazon orders were inspected?
- How many Monarch transactions were matched?
- How many records were updated?
- Were any items skipped or ambiguous?
- Does the system need re-authentication?
- Is the next cron run expected to succeed?

## Example cron expectations

A cron-friendly run should support patterns like:

```bash
monarch-cli-sync sync >> ~/Library/Logs/monarch-cli-sync.log 2>&1
```

And ideally return:

- `0` for success
- non-zero for failure or required intervention

## Status model

A future version should expose clear states such as:

- `ok`
- `no_changes`
- `partial`
- `auth_required`
- `rate_limited`
- `error`

## Initial implementation plan

1. Research how the existing extension/project works
2. Identify the actual Monarch and Amazon data flows involved
3. Decide whether this should use APIs, exported data, email parsing, or browser automation as a fallback
4. Build a CLI skeleton with logging, config, and exit code semantics first
5. Implement a minimal end-to-end sync path
6. Add cron-oriented observability and health checks

## API cassette testing

API tests use [`vcrpy`](https://vcrpy.readthedocs.io/) through [`pytest-recording`](https://github.com/kiwicom/pytest-recording) so tests can run against live services while recording or offline against previously recorded cassettes. See [`docs/api-testing.md`](docs/api-testing.md) for deeper background.

### 1. Install dev dependencies

```bash
pip install -e '.[dev]'
```

### 2. Run tests using existing recordings (default)

Cassettes stored in `tests/cassettes/` are replayed automatically. No network access occurs for recorded interactions:

```bash
pytest
```

### 3. Run tests offline / in CI (network fully blocked)

Forces all HTTP through cassettes only — any unrecorded request raises an error instead of hitting the network:

```bash
pytest --block-network --record-mode=none
```

Use this mode in CI or any environment where live network calls must not happen.

### 4. Record new cassettes for new tests

Records interactions that do not already have a cassette, leaves existing cassettes untouched:

```bash
pytest --record-mode=new_episodes
```

To record just the real API end-to-end tests:

```bash
pytest tests/test_e2e_api_recording.py --record-mode=all
```

The Monarch E2E test records auth plus a bounded transaction list. The Amazon E2E test is marked `xfail` because Amazon may require CAPTCHA/device approval; the current cassette captures the Amazon challenge path for replay.

### 5. Re-record all cassettes

Replaces every cassette by making fresh live API calls. Use this when upstream API responses have changed:

```bash
pytest --record-mode=all
```

### Credentials for live recording

**Live recording must use only `_TEST`-suffixed environment variables.** Do not use production credentials when recording cassettes. The fixture in `tests/conftest.py` reads exactly these variables and exposes nothing else to recording tests:

```dotenv
MONARCH_EMAIL_TEST=you+test@example.com
MONARCH_PASSWORD_TEST=...
MONARCH_MFA_SECRET_KEY_TEST=...
AMAZON_USERNAME_TEST=you+test@example.com
AMAZON_PASSWORD_TEST=...
AMAZON_OTP_SECRET_TEST=...
```

Set these in your `.env` file (or export them in the shell) before running commands 4 or 5 above.

### Before committing a cassette

VCR is configured to scrub sensitive headers (`authorization`, `cookie`, `set-cookie`, `x-api-key`, `x-auth-token`, `x-csrf-token`), query parameters, and common JSON body fields (`token`, `password`, `secret`, `session`, etc.) before writing cassette files. Still, **inspect every new or changed cassette before committing** — automation is only as good as the scrub list:

```bash
grep -RniE 'authorization|cookie|set-cookie|password|token|secret|api[_-]?key' tests/cassettes || true
```

Only placeholders such as `FILTERED` should appear. Do not commit cassettes that contain real credentials, cookies, or session tokens.

## Repository status

This repository is past the initial scaffold stage, but it is still early.

Implemented now:

- packaging / editable install
- config loading from `.env` and `~/.config/monarch-cli-sync/config.toml`
- `auth monarch` — interactive login, session token persisted to disk
- `auth amazon` — interactive login, cookies persisted to disk
- `sync --dry-run` — fetches Amazon orders and Monarch transactions, prints both tables
- `doctor` — checks config file, Monarch session, and Amazon cookie presence
- test suite coverage for CLI, config, session, and order fetch behavior

Not implemented yet:

- matching engine (correlating Amazon orders to Monarch transactions)
- write path back to Monarch (`sync` without `--dry-run`)
- `status` command

## License

TBD
