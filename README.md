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

Copy the example env file and fill in your Monarch credentials:

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
MONARCH_EMAIL=you@example.com
MONARCH_PASSWORD=yourpassword
MONARCH_MFA_SECRET_KEY=BASE32SECRETFROMMONARCH
```

`MONARCH_MFA_SECRET_KEY` is the base32 TOTP secret shown when you set up 2FA in Monarch (the string you'd normally scan as a QR code). Leave it empty if your account does not use MFA.

Alternatively, place these values in `~/.config/monarch-cli-sync/config.toml`:

```toml
[monarch]
email = "you@example.com"
password = "yourpassword"
mfa_secret_key = "BASE32SECRETFROMMONARCH"
```

### 4. Authenticate with Monarch

```bash
monarch-cli-sync auth monarch
```

This performs an interactive Monarch login and persists a session token for later runs.

### 5. Test with a dry run

```bash
monarch-cli-sync sync --dry-run
```

Current behavior: this fetches recent Monarch transactions whose merchant is Amazon and prints them in a table. It does not write any changes yet.

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
monarch-cli-sync auth monarch
monarch-cli-sync sync --dry-run
```

Scaffolded but not implemented yet:

```bash
monarch-cli-sync doctor
monarch-cli-sync auth amazon
monarch-cli-sync sync
monarch-cli-sync status
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

## Repository status

This repository is past the initial scaffold stage, but it is still early.

Implemented now:

- packaging / editable install
- config loading from `.env` and `~/.config/monarch-cli-sync/config.toml`
- `auth monarch`
- `sync --dry-run` for listing matching Monarch-side Amazon transactions
- test suite coverage for the current CLI/config/session behavior

Not implemented yet:

- `auth amazon`
- full Amazon order ingestion
- matching engine
- write path back to Monarch
- `doctor`
- `status`

## License

TBD
