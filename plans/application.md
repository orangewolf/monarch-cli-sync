# monarch-cli-sync: Implementation Plan

## Overview

A Python CLI that syncs Amazon order history into Monarch Money without a browser extension. Designed for unattended cron execution with clear observability.

---

## Architecture

```
monarch_cli_sync/
├── __init__.py
├── cli.py                  # Click entry point, exit code handling
├── config.py               # Config loading (env vars, ~/.config/monarch-cli-sync/config.toml)
├── amazon/
│   ├── __init__.py
│   ├── session.py          # amazon-orders AmazonSession wrapper + cookie management
│   └── orders.py           # Fetch and normalize order history
├── monarch/
│   ├── __init__.py
│   ├── session.py          # monarchmoney MonarchMoney wrapper + session persistence
│   └── transactions.py     # Fetch and update transactions
├── sync/
│   ├── __init__.py
│   ├── matcher.py          # Amount + date window matching logic
│   └── runner.py           # Orchestrates full sync flow, produces SyncResult
├── output/
│   ├── __init__.py
│   ├── human.py            # Human-readable terminal output
│   └── json_output.py      # JSON output for machine consumption
└── status.py               # SyncStatus enum and SyncResult dataclass

tests/
├── test_matcher.py
├── test_orders.py
├── test_transactions.py
├── test_runner.py
└── conftest.py

pyproject.toml
.env.example
```

### Key data flow

```
AmazonSession → [Order] → matcher → [Match] → MonarchMoney.update_transaction()
                                          ↓
                               SyncResult → output (human | JSON) → exit code
```

---

## Dependencies

| Package | Purpose |
|---|---|
| `click` | CLI framework |
| `amazon-orders` | Amazon order history scraping (requests + BeautifulSoup4) |
| `monarchmoney` | Unofficial Monarch GraphQL API (async, aiohttp) |
| `rich` | Human-readable terminal output with color/tables |
| `tomli` / `tomllib` (stdlib ≥3.11) | Config file parsing |
| `python-dotenv` | Load `.env` for local dev |
| `pydantic` | Config validation and data models |
| `pytest` | Test framework |
| `pytest-asyncio` | Async test support |
| `pytest-mock` | Mocking |
| `respx` | HTTP mock for aiohttp (Monarch API tests) |
| `responses` | HTTP mock for requests (Amazon scraping tests) |

**Python version:** 3.11+ (uses stdlib `tomllib`, `asyncio.run`, `match` statements).

**Packaging:** `pyproject.toml` with `hatchling` build backend. Entry point: `monarch-cli-sync`.

---

## Exit Code Strategy

| Code | Meaning |
|---|---|
| `0` | Full success (or `no_changes` — nothing to do) |
| `1` | Partial success — some matched, some failed |
| `2` | Auth required — Amazon or Monarch session needs refresh |
| `3` | Rate limited — caller should back off |
| `4` | Hard error — unexpected exception, check logs |

These are enumerated in `status.py` and used consistently. Every CLI subcommand maps its outcome to one of these.

---

## Status Model

```python
# status.py
from enum import Enum
from dataclasses import dataclass, field

class SyncStatus(str, Enum):
    OK = "ok"
    NO_CHANGES = "no_changes"
    PARTIAL = "partial"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"

@dataclass
class SyncResult:
    status: SyncStatus
    orders_inspected: int = 0
    transactions_fetched: int = 0
    matched: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    message: str = ""
```

---

## Auth / Session Handling

### Amazon

- Credentials from env vars (`AMAZON_USERNAME`, `AMAZON_PASSWORD`, `AMAZON_OTP_SECRET_KEY`) or config file.
- Cookie jar written to `~/.config/monarch-cli-sync/amazon_cookies.json`.
- On each run, check `session.auth_cookies_stored()` before attempting full login. If cookies are stale and login fails with a CAPTCHA or MFA prompt, emit `auth_required` + exit code 2.
- TOTP is handled automatically via `otp_secret_key`. Image CAPTCHAs require interactive intervention — if running unattended, fail cleanly with instructions.
- **`auth amazon`** subcommand forces a fresh login and persists cookies for future headless runs.

### Monarch

- Credentials from env vars or config file.
- Session pickle written to `~/.config/monarch-cli-sync/monarch_session.pkl`.
- `mm.load_session()` on startup; if token is invalid, fall through to `mm.login()`.
- **`auth monarch`** subcommand forces a fresh login and saves session.
- MFA handled via `mfa_secret_key` in config if set; otherwise interactive prompt.

---

## Matching Logic

Reimplements the alex-peck approach:

1. For each Amazon order, flatten into individual charge-level records: `(amount, date, order_id, items_text)`.
   - Use `full_details=True` only for orders that match a Monarch transaction (to avoid N+1 charges).
2. Fetch Monarch transactions filtered by `search="Amazon"` for the target date range (default: last 30 days).
3. For each Monarch transaction, find unmatched Amazon charge records where:
   - `abs(monarch.amount) == amazon.amount` (amounts are positive on Amazon, negative debits on Monarch)
   - `abs((monarch.date - amazon.date).days) <= 7`
4. Among candidates, pick the one with the smallest date distance.
5. Mark the Amazon record as used to prevent duplicate matches.
6. Skip Monarch transactions that already have notes unless `--force` is passed.
7. Produce a list of `Match` objects (confirmed) and `Unmatched` records (warnings).

---

## Observability / Logging

- Structured log lines go to stderr (so stdout can carry JSON output cleanly).
- Log format: `[ISO-8601 timestamp] [LEVEL] message`
- Log levels: `DEBUG` (verbose), `INFO` (default), `WARNING`, `ERROR`
- `--verbose` / `-v` flag lowers threshold to DEBUG.
- `--quiet` / `-q` suppresses all non-error output.
- `--json` flag writes a JSON `SyncResult` to stdout instead of human output.
- Every run prints a one-line summary at the end regardless of quiet mode:
  ```
  monarch-cli-sync: ok | matched=12 updated=12 skipped=3 errors=0
  ```
  This line goes to stdout, making it easy to grep in cron logs.

---

## CLI Commands

```
monarch-cli-sync doctor           # Check config, auth, connectivity. Exit 0 if all good.
monarch-cli-sync auth amazon      # Interactive Amazon login, persist cookies.
monarch-cli-sync auth monarch     # Interactive Monarch login, persist session.
monarch-cli-sync sync             # Run full sync (last 30 days by default).
  --days N                        # How many days back to look (default: 30).
  --year YYYY                     # Sync a full calendar year instead.
  --dry-run                       # Match but do not write to Monarch.
  --force                         # Overwrite existing notes.
  --json                          # Output JSON SyncResult to stdout.
  --verbose / -v                  # Debug logging.
  --quiet / -q                    # Suppress non-error output.
monarch-cli-sync status           # Show last run result from a status file.
```

---

## Phases and Milestones

### Phase 0 — Project Scaffold (ends: working `pip install -e .` and `monarch-cli-sync --help`)

**Goal:** Runnable CLI shell. Nothing real happens yet but the skeleton is fully wired.

**Tasks:**
1. Write `pyproject.toml` with dependencies, entry point `monarch-cli-sync = monarch_cli_sync.cli:main`.
2. Create `monarch_cli_sync/cli.py` with a Click group and stub subcommands (`doctor`, `auth`, `sync`, `status`).
3. Create `status.py` with `SyncStatus` and `SyncResult`.
4. Create `config.py` that reads `~/.config/monarch-cli-sync/config.toml` and falls back to env vars. Validate with Pydantic.
5. Wire exit codes: every subcommand calls `sys.exit(result.exit_code)`.
6. Write `tests/conftest.py` and a smoke test that invokes `monarch-cli-sync --help` via `click.testing.CliRunner`.

**Deliverable:** `monarch-cli-sync --help` runs. CI (GitHub Actions) runs `pytest` and passes.

---

### Phase 1 — Monarch Auth + Transaction Fetch (ends: `monarch-cli-sync auth monarch` works and `sync --dry-run` prints transactions)

**Goal:** Can authenticate with Monarch and list transactions. No Amazon yet.

**Tasks:**
1. Implement `monarch/session.py`: wrap `MonarchMoney`, implement `load_or_login()`, persist pickle.
2. Implement `monarch/transactions.py`: `fetch_amazon_transactions(start_date, end_date) -> list[MonarchTransaction]`.
3. Wire `auth monarch` subcommand: call `load_or_login(force=True)`.
4. Wire `sync --dry-run`: fetch transactions, print a table with rich, exit 0.
5. Handle `RequireMFAException` cleanly: prompt for code if interactive, or fail with `auth_required` if unattended.
6. Handle invalid session (expired token): clear pickle, re-login once, fail with `auth_required` if that also fails.

**Tests:**
- Unit test `fetch_amazon_transactions` with mocked GraphQL responses (use `respx` or monkeypatch).
- Test session load from pickle and fallback to login.
- Test `auth_required` exit code when login raises.

**Deliverable:** `monarch-cli-sync sync --dry-run` prints a table of Monarch transactions tagged "Amazon".

---

### Phase 2 — Amazon Auth + Order Fetch (ends: `monarch-cli-sync auth amazon` works and orders print in dry-run)

**Goal:** Can authenticate with Amazon and list recent orders. No matching yet.

**Tasks:**
1. Implement `amazon/session.py`: wrap `AmazonSession`, check `auth_cookies_stored()`, call `session.login()`, detect CAPTCHA/MFA failures.
2. Implement `amazon/orders.py`: `fetch_orders(year=None, days=30) -> list[AmazonOrder]`. Normalize into a flat dataclass.
3. Wire `auth amazon` subcommand: force fresh login, persist cookies.
4. In `sync --dry-run`, also print fetched Amazon orders alongside Monarch transactions.
5. Handle CAPTCHA: if running non-interactively (no TTY), log `auth_required` and exit 2.
6. Implement `doctor` subcommand: check config presence, check cookie/session files exist, attempt a lightweight connectivity check (e.g., fetch 1 order, fetch 1 Monarch transaction).

**Tests:**
- Unit test `fetch_orders` with mocked HTML responses (`responses` library or pre-saved fixtures).
- Test CAPTCHA detection returns `auth_required`.
- Test `doctor` output when config is missing vs. complete.

**Deliverable:** `monarch-cli-sync sync --dry-run` prints both Amazon orders and Monarch transactions without crashing.

---

### Phase 3 — Matching Engine (ends: `sync --dry-run` shows match results without writing)

**Goal:** The core matching algorithm is implemented and tested independently of any I/O.

**Tasks:**
1. Implement `sync/matcher.py`:
   - `flatten_to_charges(orders: list[AmazonOrder]) -> list[AmazonCharge]`
   - `match(charges, transactions, date_window=7, force=False) -> MatchResult`
   - `MatchResult` contains: `matches: list[Match]`, `unmatched_charges: list[AmazonCharge]`, `unmatched_transactions: list[MonarchTransaction]`
2. Write exhaustive unit tests covering:
   - Exact match
   - Date within window (both directions)
   - Date outside window (no match)
   - Tie-breaking (closest date wins)
   - Duplicate prevention (charge used only once)
   - Skip when notes already exist (`force=False`)
   - Force override (`force=True`)
   - Refund handling (negative amounts)
   - Empty inputs
3. Wire matcher into `sync --dry-run`: print match table with rich.

**Tests:** This phase is test-heavy by design. Target >90% branch coverage on `matcher.py`.

**Deliverable:** `sync --dry-run` shows a formatted match preview. Matcher tests all pass.

---

### Phase 4 — Write Path + Full Sync (ends: `sync` actually updates Monarch)

**Goal:** First real end-to-end sync.

**Tasks:**
1. Implement `monarch/transactions.py`: `update_transaction(id, notes) -> bool`. Catch and log any GraphQL errors.
2. Implement `sync/runner.py`: orchestrate fetch → match → write, populate `SyncResult`.
3. Wire `sync` subcommand (without `--dry-run`) to runner. Print summary line.
4. Implement `--json` output: serialize `SyncResult` to stdout as JSON.
5. Write last-run status to `~/.config/monarch-cli-sync/last_run.json` for `status` subcommand.
6. Implement `status` subcommand: load and print `last_run.json`.

**Tests:**
- Integration test of full runner with mocked Amazon + Monarch HTTP (no real network calls).
- Test `--dry-run` does not call `update_transaction`.
- Test JSON output format is stable (add a schema fixture).
- Test `status` reads and formats `last_run.json`.

**Deliverable:** `monarch-cli-sync sync` runs end-to-end and updates Monarch notes. Exit codes are correct.

---

### Phase 5 — Cron Polish (ends: safe to drop into crontab)

**Goal:** The tool is reliable, observable, and safe to run unattended.

**Tasks:**
1. Add `--days` and `--year` flags with validation.
2. Rate limiting: add a configurable delay between Amazon page fetches (`request_delay_seconds`, default 1.0).
3. Add retry logic for transient Monarch API errors (3 retries, exponential backoff).
4. Handle partial failure cleanly: if `update_transaction` fails for one item, log and continue; produce `partial` status.
5. Write a `SIGTERM` handler that saves partial results before exit.
6. Document a crontab example in README:
   ```
   0 6 * * * /path/to/venv/bin/monarch-cli-sync sync >> ~/Library/Logs/monarch-cli-sync.log 2>&1
   ```
7. Add `--version` flag.

**Tests:**
- Test partial failure produces `SyncStatus.PARTIAL` and exit code 1.
- Test retry logic with a mock that fails twice then succeeds.
- Test `--days 7` and `--year 2024` produce correct date ranges passed to both APIs.

**Deliverable:** Tool can run from crontab without manual intervention (given valid sessions).

---

### Phase 6 — Hardening (ongoing, not a gate)

These are improvements to add as real-world usage reveals gaps:

- Support Amazon accounts with multiple regions.
- Configurable `date_window` (currently hardcoded to ±7 days).
- Optional item-level split transactions in Monarch (one split per shipment).
- Webhook / healthcheck URL ping on success (for dead-man's-switch monitoring like healthchecks.io).
- Support `--since-last-run` to automatically pick up where the previous run left off.

---

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Amazon HTML structure changes break `amazon-orders` | Medium | Pin to a specific version; write regression fixtures; monitor library issues |
| JavaScript CAPTCHA blocks headless login | High (for new devices/IPs) | `auth amazon` must be run interactively first to seed cookies; fail gracefully when CAPTCHA appears in unattended mode |
| Monarch's unofficial GraphQL API changes | Low–Medium | Abstract behind `monarch/` module so swapping is isolated; write contract tests against the schema |
| False-positive matches (same amount within ±7 days) | Medium | Log all matches in DEBUG; `--dry-run` lets users verify before writing; future configurable window |
| Session expiry causes silent cron failure | Medium | `doctor` subcommand; `status` subcommand; one-line summary always printed |
| Notes field overwritten by accident | Low | Default `force=False`; prominently document `--force` |
| Rate limiting from Amazon | Medium | Default 1s delay between requests; `rate_limited` exit code lets caller back off |

---

## First Three Implementation Steps

When starting from zero, do these in order:

1. **`pyproject.toml` + CLI skeleton** (`Phase 0`): get `pip install -e .` and `pytest` working before any feature code.
2. **Monarch auth + dry-run transaction list** (`Phase 1`): validate the `monarchmoney` library works against a real account early, so any API surprises surface before the Amazon work is built on top.
3. **Matcher unit tests** (start of `Phase 3`): write the matching tests against a pure-function interface before implementing the function — this drives the data model for `AmazonCharge` and `MonarchTransaction` and flushes out edge cases cheaply.

---

## Config File Schema

`~/.config/monarch-cli-sync/config.toml`:

```toml
[amazon]
username = ""               # or AMAZON_USERNAME env var
password = ""               # or AMAZON_PASSWORD env var
otp_secret_key = ""         # TOTP base32 secret; or AMAZON_OTP_SECRET_KEY env var
request_delay_seconds = 1.0

[monarch]
email = ""                  # or MONARCH_EMAIL env var
password = ""               # or MONARCH_PASSWORD env var
mfa_secret_key = ""         # or MONARCH_MFA_SECRET_KEY env var

[sync]
default_days = 30
date_window_days = 7
force = false               # overwrite existing notes by default?
```

Pydantic model validates required fields and raises a clear error if missing, rather than crashing deep in the HTTP layer.

---

## Testing Strategy

- **Unit tests** for all pure logic: matcher, config parsing, output formatting.
- **Integration tests** with mocked HTTP: runner, session loading, auth flows.
- **No real network calls in CI.** All Amazon HTML responses come from fixtures in `tests/fixtures/`. All Monarch responses come from `respx` mocks.
- **CliRunner tests** for all subcommands: verify exit codes, stdout/stderr content.
- Aim for >80% overall coverage. Matcher and runner get stricter attention (>90%).
- Tests run on every commit via GitHub Actions.

---

## GitHub Actions (CI)

```yaml
# .github/workflows/ci.yml
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[dev]"
      - run: pytest --cov=monarch_cli_sync --cov-report=term-missing
```
