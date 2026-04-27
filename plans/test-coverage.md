# Test coverage plan: VCR / pytest-recording for Monarch + Amazon

## Verdict

The repo *partially* uses VCR.py appropriately. The Monarch end-to-end path is
recorded and replayable with reasonable secret redaction. The Amazon end-to-end
path is **not** functionally covered by VCR: the cassette has every HTML
response body overwritten with the literal string `FILTERED_HTML_BODY`, and the
test is `@pytest.mark.xfail(strict=False)`, so the suite passes whether replay
works or not. Several supporting issues (record-mode discipline, network
blocking, TOTP redaction, ad-hoc VCR construction) keep the setup from being
something we can rely on without manual review.

---

## Current state — what's already good

- `pyproject.toml` declares `vcrpy>=4.3` and `pytest-recording>=0.13` in the
  `dev` extras, plus an `e2e` marker (`monarch_cli_sync/pyproject.toml:39-41`).
- `tests/conftest.py` provides session-scoped `vcr_config` and
  `vcr_cassette_dir` fixtures with:
  - `filter_headers` for `authorization`, `cookie`, `set-cookie`, `x-api-key`,
    `x-auth-token`, `x-csrf-token`.
  - `filter_query_parameters` for `access_token`, `api_key`, `auth_token`,
    `password`, `refresh_token`, `session`, `token`.
  - `before_record_request` / `before_record_response` scrubbers that walk
    JSON bodies recursively and replace by key name (`email`, `token`,
    `password`, etc.) plus zero-out numeric-sensitive keys (`amount`,
    `balance`).
  - `decode_compressed_response: True` so scrubbers actually see bodies.
  - `record_mode: "once"`.
- Live tests only read `*_TEST`-suffixed env vars
  (`tests/conftest.py:170-179`), so production creds in `.env` are not used by
  the recording flow.
- `tests/test_vcr_config.py` is a TDD spec covering header/body redaction,
  cassette directory, _TEST isolation, and a regex sweep over `tests/cassettes`
  for bare credentials.
- `docs/api-testing.md` documents the workflow.
- The Monarch cassette
  (`tests/cassettes/e2e_monarch_auth_and_transactions.yaml`) shows real
  redaction: `token`, `id`, `email`, `name`, `household.*`, `set-cookie` all
  show as `FILTERED`.

---

## Findings & gaps

1. **Amazon cassette is replay-useless.** All 17 interactions in
   `e2e_amazon_auth_and_orders.yaml` are GETs whose response bodies are
   overwritten with `FILTERED_HTML_BODY` (see `_chain_before_record_response`
   in `tests/test_e2e_api_recording.py:127-142`, used with `scrub_html=True`).
   Since `amazon-orders` parses HTML, replay against this cassette cannot
   exercise `_normalize_order`, login form discovery, OTP step, or paging.
2. **Amazon E2E is `xfail(strict=False)`.** `tests/test_e2e_api_recording.py:200`
   silently swallows every failure, so regressions in either VCR plumbing or
   Amazon parsing are invisible.
3. **Live Amazon parsing logic is untested.** `test_amazon_orders.py` mocks
   `AmazonOrders.get_order_history` entirely. Together with (1) and (2), no
   test ever feeds real Amazon HTML into our code.
4. **Ad-hoc `vcrpy.VCR(...)` instead of `@pytest.mark.vcr`.**
   `tests/test_e2e_api_recording.py:59-100` constructs a VCR instance manually
   and reaches into the `vcr_config` fixture via
   `conftest.vcr_config.__wrapped__()`. This bypasses pytest-recording, so
   `--block-network` / `--record-mode` only work because the test re-implements
   them. It also duplicates filter lists.
5. **One-time codes are not in the redaction list.** The Monarch cassette
   contains `totp: '994113'` from a real recording. TOTP codes expire in 30s
   but they still anchor the recording to a wall-clock and a real account.
   Add `totp`, `otp_code`, `otpCode`, `otp`, `passcode`, `claimcheck`, `mfa`
   to `SENSITIVE_FIELD_NAMES`.
6. **`record_mode: "once"` plus no enforced network block in pytest defaults.**
   If a future test references a missing interaction and the live API is
   reachable, VCR records a new cassette silently. The repo has no `addopts`
   pinning `--block-network` / `--vcr-record=none` for default runs.
7. **Numeric redaction zeros out `amount` and `balance`.** That's safe but
   nukes all replay assertions about transaction signs/values. The Monarch
   E2E test currently asserts only `isinstance(amount, float)` — which 0.0
   trivially satisfies — so the test does not actually verify parsing.
   Consider redacting to a stable non-zero placeholder (e.g. `-12.34`) so
   replay can still check sign/shape.
8. **Cassette safety scan is narrow.** `test_cassette_files_contain_no_bare_tokens`
   matches bearer headers, raw `password=...`, and cookie strings. It would
   miss bare emails, account display names, plaid merchant names, household
   IDs, and any URL with `?email=...`. Extend the regex set; add a positive
   check that recorded URIs do not include any value present in `_TEST` env
   vars.
9. **No CLI/sync command coverage via VCR.** `monarch_cli_sync/sync/` and the
   note-writing path (presumably `mm.update_transaction` / similar) are not
   exercised end to end. Read paths only.
10. **Response headers are not filtered through the same allowlist.** Inspect
    of `e2e_amazon_auth_and_orders.yaml` line 116 shows `x-amz-rid` retained
    in responses; `_redact_headers` only scrubs the `SENSITIVE_HEADERS` set.
    Low risk, but unwanted fingerprinting.
11. **`MONARCH_MFA_SECRET_KEY_TEST` placeholder is a known public base32**
    (`JBSWY3DPEHPK3PXP`). Fine as a placeholder — flag it explicitly in
    `docs/api-testing.md` so nobody mistakes it for the user's secret.
12. **`xfail(strict=False)` should become `xfail(strict=True)` once Amazon
    cassette is real**, otherwise it will go green on the wrong reason.

---

## Recommended strategy

**Goal.** Use `pytest-recording`'s `@pytest.mark.vcr` exclusively, with one
`vcr_config` fixture for everything. Have two layers of cassette-backed tests:

1. **Recorded-replay tests** (`@pytest.mark.vcr`, marker `e2e`) — full network
   loop captured once, replayed deterministically. These are the tests we
   maintain real cassettes for.
2. **HTML fixture tests** for Amazon parsing — store one or two real-but-
   sanitized order-history HTML snippets under `tests/fixtures/amazon/` and
   feed them into `amazonorders.parsers` directly, bypassing the network.
   Cassette-based replay is brittle for HTML-heavy sites (CSRF tokens, anti-
   bot challenges, query-param ordering). HTML-fixture parsing tests stay
   green even when Amazon rotates their flow.

**Network discipline.** Default `addopts` should include
`--block-network --record-mode=none`. Re-record explicitly via
`pytest --record-mode=all -m e2e` with `_TEST` env vars set.

**Redaction.** Keep the recursive JSON scrubber but extend the key set; add a
post-record validation step that fails the test run if any cassette contains
a known live secret value (i.e. compare cassette text against `_TEST` env var
values after recording — implemented as a fixture finalizer, not a manual
grep).

**Single source of truth for VCR config.** Stop reaching into the fixture
from `test_e2e_api_recording.py`. Replace the manual `vcrpy.VCR(...)` with
`@pytest.mark.vcr(scope="function", record_mode="...")` and let
`vcr_config_kwargs` handle the per-test additions (extra filters,
HTML-scrubbing toggle, replacements list).

---

## Cassette / secret-redaction policy

**Always scrubbed** (request body, response body, query params, headers):

- Auth: `authorization`, `cookie`, `set-cookie`, `x-api-key`, `x-auth-token`,
  `x-csrf-token`, `csrftoken`, `csrf`.
- Credentials and codes: `password`, `passcode`, `pwd`, `otp`, `otp_code`,
  `otpCode`, `totp`, `mfa`, `mfa_secret`, `mfa_secret_key`, `claimcheck`.
- Tokens: `token`, `access_token`, `refresh_token`, `id_token`,
  `session`, `session_id`, `sid`, `swfid`.
- Identity: `email`, `username`, `name`, `display_name`, `displayName`, `id`,
  `external_id`, `user_external_id`, `anonymous_id`, `birthday`, `phone`,
  `address`, `city`, `state`, `zip_code`.
- Account-level: `plaidName`, `plaid_name`, `accountNumber`, `last4`.

**Numeric placeholders, not zero:** `amount`, `balance`, `available_balance`
→ `-12.34` so sign and float type are testable while exact values are gone.

**HTML responses (Amazon):** Do **not** wholesale replace with
`FILTERED_HTML_BODY`. Instead:
- Run a content-aware scrub: strip elements with `data-*-token`,
  `<input name="appActionToken">`, `<input name="metadata1">`, etc.
- Replace any occurrence of any `_TEST` env var value (email, password, OTP
  secret) with `FILTERED`.
- Strip Set-Cookie headers (already done) and any inline `csrf` tokens via
  regex.

**Cassette URL scrubbing:** `before_record_request` should also scrub the URI
of any literal `_TEST` env-var value, in case Amazon embeds the email in a
return-to URL.

**Post-record verification fixture:** A session-scoped fixture, finalized
after recording runs, opens every YAML in `tests/cassettes/` and asserts no
substring of any `_TEST` env-var value appears (case-insensitive) and that
known credential-shape patterns (long base64, `Bearer\s+\S{20,}`) do not
appear. Fail loudly if they do.

---

## Cassette storage and re-record workflow

- Cassettes live under `tests/cassettes/` (already true).
- One cassette per logical scenario, named `<service>_<scenario>.yaml`.
  Initial set:
  - `e2e_monarch_login_and_list_transactions.yaml`
  - `e2e_monarch_login_with_mfa.yaml`
  - `e2e_monarch_update_transaction_notes.yaml`
  - `e2e_amazon_login_and_list_orders.yaml` (only useful if rebuildable; if
    Amazon proves too flaky, drop and rely on HTML fixtures)
- Re-record one cassette:
  ```bash
  source .venv/bin/activate
  rm tests/cassettes/<name>.yaml
  pytest tests/test_e2e_api_recording.py::<test_name> --record-mode=once
  ```
- Force-refresh all e2e cassettes:
  ```bash
  source .venv/bin/activate
  pytest -m e2e --record-mode=all
  ```
- Default offline run:
  ```bash
  source .venv/bin/activate
  pytest --block-network --vcr-record=none
  ```
- Required env vars for re-record: `MONARCH_EMAIL_TEST`,
  `MONARCH_PASSWORD_TEST`, `MONARCH_MFA_SECRET_KEY_TEST`,
  `AMAZON_USERNAME_TEST`, `AMAZON_PASSWORD_TEST`, `AMAZON_OTP_SECRET_TEST`.
- After recording, run `pytest tests/test_vcr_config.py` to validate
  redaction. Reviewer must also `git diff tests/cassettes/` before commit.

---

## CI behavior

- CI default command: `pytest --block-network --vcr-record=none`. This
  ensures: no network, no recording, missing cassettes fail loudly.
- `e2e` marker is included in the default run since cassettes are committed;
  if cassettes are missing, the test is `pytest.skip`-ed (not failed) by the
  helper that already exists, but the skip message must surface in CI logs.
- Re-recording is a manual job, run locally with `_TEST` creds, never in CI.
- Add a CI job step that runs the cassette-safety scan
  (`tests/test_vcr_config.py::test_cassette_files_contain_no_bare_tokens`)
  separately so it is impossible to merge a cassette containing real creds
  even if the rest of the suite is skipped.

---

## Concrete tasks

Each task is small enough to land independently. File paths are relative to
`/Users/rob/Work/Personal/monarch-cli-sync`.

### T1. Extend redaction key set (small)

- Edit `tests/conftest.py:26-62` (`SENSITIVE_FIELD_NAMES`):
  add `totp`, `otp_code`, `otpcode`, `passcode`, `pwd`, `claimcheck`,
  `id_token`, `idtoken`, `accountnumber`, `last4`, `phone`, `birthday`, `sid`,
  `swfid`.
- Add a test in `tests/test_vcr_config.py` per new key.
- Verify by re-running `pytest tests/test_vcr_config.py` after activating
  `.venv`:
  ```bash
  source .venv/bin/activate && pytest tests/test_vcr_config.py
  ```

### T2. Replace numeric zero-redaction with stable placeholder (small)

- Edit `tests/conftest.py:_redact_sensitive_values` so numeric-sensitive keys
  return `-12.34` (or another sentinel) instead of `0`.
- Update Monarch E2E assertion in `tests/test_e2e_api_recording.py:197` to
  assert `transaction.amount == -12.34` for replayed-cassette runs.
- Re-record `e2e_monarch_auth_and_transactions.yaml` once T1 lands so the
  TOTP also goes away.

### T3. Switch e2e tests to `@pytest.mark.vcr` (medium)

- In `tests/test_e2e_api_recording.py`, remove the manual `vcrpy.VCR(...)`
  construction and `_chain_before_record_*` helpers.
- Use `@pytest.mark.vcr(record_mode="...")` and pass per-test extras through a
  `vcr_cassette_name` / `vcr_config_kwargs` fixture (pytest-recording
  feature). Keep the HTML-scrubbing toggle as a parametrized
  `before_record_response` registered via `vcr_config_kwargs`.
- Drop `pytest.importorskip("conftest")` and `vcr_config.__wrapped__()`.
- Rename cassettes to match per-test names emitted by pytest-recording (or
  pin via `vcr_cassette_name`). Move the existing files to the new names in
  the same commit so git tracks the rename.

### T4. Build Amazon HTML fixture tests (medium)

- Create `tests/fixtures/amazon/order_history_2024.html` from one real
  recording, manually scrubbed (replace name/address/order numbers/totals
  with placeholders).
- Add `tests/test_amazon_html_parsing.py` that loads the fixture, hands it to
  `amazonorders.orders.AmazonOrders` via the library's parser API (or by
  monkey-patching `session.get` to return a `requests.Response` whose `.text`
  is the fixture), and asserts `_normalize_order` outputs match expected
  rows.
- This replaces the structural value of the broken Amazon cassette.
- Remove `xfail(strict=False)` from `test_e2e_amazon_auth_and_list_orders` —
  if VCR replay is kept, mark `strict=True`; otherwise delete the test once
  T4 lands and HTML-fixture coverage is in.

### T5. Network-block defaults & `addopts` (small)

- In `pyproject.toml` under `[tool.pytest.ini_options]` add:
  ```toml
  addopts = "--block-network --record-mode=none"
  ```
  (`pytest-recording` honors `--record-mode`; per-run override on the CLI
  still works.)
- Document the override (`pytest --record-mode=all -m e2e`) in
  `docs/api-testing.md`.

### T6. Post-record secret-leak guard (small)

- Add a session-scoped fixture in `tests/conftest.py` that, when
  `--record-mode` is anything other than `none`, runs at session teardown
  over every YAML in `tests/cassettes/` and:
  - Errors if any non-empty `_TEST` env-var value appears as a substring.
  - Errors on regex matches for `Bearer\s+[A-Za-z0-9._\-+/=]{20,}`,
    `[A-Za-z0-9_\-]{32,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}` (JWT
    shape), and `eyJ[A-Za-z0-9_\-]{20,}`.
- Extend the existing `test_cassette_files_contain_no_bare_tokens` regex set
  with the JWT and long-token patterns above so the guard also runs in
  default offline pytest.

### T7. Scrub URI of `_TEST` values (small)

- In `before_record_request` in `tests/conftest.py`, after the existing
  header/body scrub, walk the request URI and replace every non-empty
  `_TEST` env-var value with `FILTERED`. Use the same `replacements` list
  that `test_e2e_api_recording.py` currently builds locally.

### T8. Filter response headers consistently (small)

- In `tests/conftest.py:_redact_headers`, also lower-case match against an
  `RESPONSE_FINGERPRINT_HEADERS` list (e.g. `x-amz-rid`, `cf-ray`,
  `http_x_request_id`) and replace with `FILTERED`. Apply via
  `before_record_response` only.

### T9. Cover the sync write path (medium)

- Once T3 lands, add `test_e2e_monarch_update_transaction_notes` that
  exercises the actual mutation `monarch_cli_sync/sync/...` calls with
  `@pytest.mark.vcr`. Initial recording must use a `_TEST` Monarch account
  and a transaction in a controlled sandbox category to avoid mutating real
  data.
- If a sandbox account is not feasible, mock the GraphQL mutation in a unit
  test instead and document the gap here.

### T10. Stronger replay assertions (small)

- After T2 + T3, tighten the Monarch E2E assertions:
  - `assert len(transactions) > 0`
  - `assert all(t.amount < 0 for t in transactions)` (Monarch convention:
    debits negative — the placeholder from T2 must respect this)
  - `assert all(t.merchant_name == "FILTERED" for t in transactions)` to
    detect redaction regressions.
- This converts the test from a smoke check into a real shape contract.

### T11. Documentation updates (small)

- Update `docs/api-testing.md` to describe:
  - The new `addopts` (block-network default).
  - The HTML-fixture path for Amazon and why VCR is not the primary
    Amazon strategy.
  - The post-record secret-leak guard (T6) and what to do if it triggers.
  - That `MONARCH_MFA_SECRET_KEY_TEST` placeholder `JBSWY3DPEHPK3PXP` is a
    public RFC 6238 example — replace it with the real `_TEST` secret only
    locally, never commit.

---

## Out of scope for this plan

- Replacing `monarchmoney` with a typed client.
- Migrating off the pinned `amazon-orders` fork.
- A mutation-recording sandbox in a dedicated Monarch household (would unlock
  T9 properly but requires user setup beyond this plan).
