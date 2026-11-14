# Phase 8.5.1 - Session Integrity

Date: 2026-11-10

## 1. Problems addressed

This phase closes the session-integrity findings from the Phase 8.5 production
readiness review without expanding into the deferred account-lifecycle work.

- **Concurrent refresh rotation:** `AuthService.refresh_session` read a refresh
  token record and later marked it used without an atomic compare-and-swap. Two
  simultaneous requests could both pass the unused-token check and both receive
  valid rotated tokens, defeating the one-token-one-use contract.
- **Concurrent duplicate registration:** the initial email lookup was followed
  by a `flush()` outside the `IntegrityError` handler. A concurrent registration
  could therefore escape the neutral duplicate-account response with an
  unhandled unique-constraint error.
- **Concurrent settings creation:** `get_or_create_user_settings` performed a
  read followed by an insert and commit. Two requests for a user without a
  settings row could race on the `user_settings.user_id` primary key.
- **Logout failure semantics:** the backend correctly rejected logout without a
  matching CSRF token, but the frontend swallowed every logout error and cleared
  local authentication state anyway. That could leave a still-valid HttpOnly
  refresh session in the browser while telling the user they had signed out.

## 2. Implementation decisions

### Atomic refresh-token consumption

`refresh_session` now begins its database work with a single conditional
`UPDATE refresh_tokens SET used_at = ...` filtered by:

- the refresh-token JTI and session ID from the signed JWT;
- the SHA-256 hash of the presented refresh token;
- `used_at IS NULL`;
- `revoked_at IS NULL`;
- `expires_at > now`.

The update changes exactly one row for the first request. Any concurrent request
that reaches the database after that commit sees `used_at` populated and
receives `rowcount == 0`. The rejected request follows the existing reuse policy:
it revokes the affected session and returns `401 refresh_token_reused`.

The update is intentionally the first database statement in the refresh flow.
On SQLite this avoids upgrading an older read snapshot into a write transaction.
On PostgreSQL the update takes the row lock implicitly; the later request waits
for the winner and then observes the consumed token. No database-specific SQL or
`SELECT ... FOR UPDATE` branch was needed.

If the token record is missing, the response remains
`401 refresh_token_invalid` without guessing a session to revoke. If the session
is already expired or revoked, the response remains `401 session_expired`. If a
claiming update succeeds but the session becomes invalid before the new token is
issued, the transaction is rolled back so the token is not consumed.

### Registration and settings races

Registration still performs the cheap pre-check for a clearer existing-account
path, but correctness now depends on the database unique constraint. The user
insert, `flush()`, settings insert, and commit all run inside one
`try/except IntegrityError` block. On conflict the transaction is rolled back
and `register()` returns `None`, preserving the neutral `202` response and
never replacing an existing account.

`get_or_create_user_settings` keeps the fast read path. If no row exists, it
inserts and commits inside a `try/except IntegrityError`. On a unique-key race it
rolls back, re-reads the row created by the winner, and returns that row. It does
not use a dialect-specific upsert, so SQLite and PostgreSQL share the same code
path.

### Logout behavior

The backend route order is preserved: when a refresh cookie is present, CSRF is
verified before any session revocation or cookie clearing. Missing or mismatched
CSRF therefore returns `403 csrf_failed`, leaves the refresh cookie in place, and
leaves the server session active.

The Angular `AuthService.logout()` now:

- normalizes HTTP, network, timeout, and CSRF failures into `ApiError`;
- clears local user, access-token, CSRF, and workspace state only after a
  successful logout response;
- propagates failures to callers instead of converting them to success.

Both the app shell and the settings page expose a recoverable error state. They
keep the authenticated UI visible, tell the user that the session is still
active, and leave the sign-out action available for retry. The sign-out buttons
are disabled while a request is in flight.

No API contract, cookie policy, access-token format, matching path, or LLM code
was changed.

## 3. Files modified

Backend:

- `ai-engine/app/auth/service.py`
  - moved registration `flush()` inside the `IntegrityError` handler;
  - replaced read-then-write refresh consumption with an atomic conditional
    update and reuse handling.
- `ai-engine/app/services/user_records.py`
  - added unique-constraint recovery to `get_or_create_user_settings`.
- `ai-engine/tests/test_auth.py`
  - added concurrent refresh, duplicate-registration, settings-creation, and
    logout-CSRF regression tests.

Frontend:

- `frontend/src/app/core/auth.service.ts`
  - logout now clears local state only after a successful response and
    propagates normalized errors.
- `frontend/src/app/core/auth.service.spec.ts`
  - covers logout success, server failure, network failure, CSRF failure, and
    post-failure authentication state.
- `frontend/src/app/app.ts`
  - added in-flight and recoverable logout-error state.
- `frontend/src/app/app.html`
  - added the sign-out error banner and retry action.
- `frontend/src/app/app.spec.ts`
  - verifies the recoverable sign-out error UI.
- `frontend/src/app/pages/settings/settings.page.ts`
  - reports logout failure without navigating away and prevents duplicate
    submissions.
- `frontend/src/app/pages/settings/settings.page.html`
  - disables the settings-page sign-out button while the request is in flight.

Documentation:

- `ai-engine/docs/phase-8.5.1-session-integrity.md`

No migration was required because the existing `refresh_tokens.jti`,
`refresh_tokens.token_hash`, and `user_settings.user_id` constraints already
support the new logic.

## 4. Database concurrency strategy

| Operation | Strategy | SQLite | PostgreSQL |
| --- | --- | --- | --- |
| Refresh rotation | Conditional `UPDATE` guarded by token hash, unused state, unrevoked state, and expiry; require `rowcount == 1` | First statement is a write, so there is no stale read-to-write upgrade in the refresh path. Concurrent writers serialize on SQLite's database write lock. | The `UPDATE` acquires the row lock. The loser waits, then observes `used_at` and gets `rowcount == 0`. |
| Duplicate registration | Rely on the `users.email` unique constraint; wrap `flush()` and commit in `IntegrityError` handling and roll back | Unique-constraint conflict is caught and converted to the neutral response. | Same code path; unique-constraint conflict is caught after the concurrent transaction resolves. |
| Settings creation | Rely on the `user_settings.user_id` primary key; catch `IntegrityError`, roll back, and return the winner's row | Re-read after rollback returns the row committed by the winning request. | Same code path; PostgreSQL unique enforcement rejects the losing insert. |

The application does not use SQLite-only locking hints or PostgreSQL-only SQL.
The trade-off is that SQLite serializes writers at the database level, while
PostgreSQL provides row-level concurrency. No advisory locks, retry queues, or
distributed locks were introduced.

## 5. Tests executed and results

Backend focused auth suite:

```text
venv/Scripts/python.exe -m pytest tests/test_auth.py -q
22 passed in 11.37s
```

The concurrency/logout subset was repeated three times:

```text
venv/Scripts/python.exe -m pytest tests/test_auth.py -q -k "concurrent or csrf_failure"
5 passed, 17 deselected in 2.31s
5 passed, 17 deselected in 2.25s
5 passed, 17 deselected in 2.19s
```

Full backend suite:

```text
venv/Scripts/python.exe -m pytest -q
138 passed, 1 skipped in 25.12s
```

Frontend suite:

```text
npm test -- --watch=false
9 test files passed, 49 tests passed
```

Frontend production build:

```text
npm run build
Application bundle generation complete.
```

Coverage added for:

- two independent clients refreshing the same token, with exactly one `200` and
  one `401`;
- the winner's newly rotated token being unusable after the losing request
  revokes the session;
- concurrent duplicate registration creating exactly one user and one settings
  row;
- concurrent settings creation returning one user ID and one persisted row;
- deterministic recovery when a settings row is inserted between the initial
  read and the attempted create;
- logout with missing and invalid CSRF preserving cookies and the active server
  session;
- frontend logout success, server failure, network failure, CSRF rejection, and
  post-failure authentication state;
- the app-shell recoverable logout error and retry action.

Node.js emitted the existing warning that Node 25 is an odd-numbered,
non-LTS release. This did not affect the test or build results.

## 6. Remaining limitations and risks

- **No live PostgreSQL verification.** The new logic is written to be portable,
  but PostgreSQL locking and multi-process behavior were not exercised because
  no PostgreSQL test database was available. SQLite tests confirm application
  semantics, not deployment-scale behavior.
- **No multi-worker or load test.** Process-local rate limiting and per-process
  application caches remain unchanged. High-contention refresh and logout
  behavior under multiple application workers is still unverified.
- **Session revocation on refresh reuse is strict.** The intended policy revokes
  the whole session. If two legitimate browser tabs race with the same refresh
  token, the loser revokes the session and the winner's new token becomes
  unusable. The user may need to sign in again. This is safer than allowing two
  valid token chains but is not a transparent multi-tab experience.
- **Settings race recovery depends on constraint errors rather than an upsert.**
  This keeps SQLite/PostgreSQL code shared, but extreme SQLite write contention
  could still surface as a database-lock error rather than a uniqueness error.
  No retry budget or separate connection strategy was added.
- **No security audit events were added.** Refresh reuse, logout failure, and
  registration conflicts are still not recorded centrally; audit logging remains
  deferred.
- **No expired-token cleanup job was added.** Consumed, revoked, and expired
  refresh tokens and sessions continue to accumulate.
- **Logout failure keeps the user signed in locally.** This is deliberate: the
  UI says the session is still active and offers retry. A user who cannot clear a
  bad CSRF cookie must refresh the page or sign in again after the session
  expires.
- **No browser E2E suite.** Frontend coverage uses Angular unit/component tests
  with mocked HTTP; real cookie, CORS, and navigation behavior is not covered by
  an end-to-end browser test.

## 7. Recommendations for Phase 8.5.2

1. Add structured authentication audit logging for registration conflicts,
   refresh-token reuse, logout failures, password changes, and session
   revocation, with request-ID correlation and secret redaction.
2. Add a session/refresh-token cleanup job with a documented retention policy.
3. Add PostgreSQL-backed concurrency tests and a multi-worker load test before
   relying on the new behavior in an internet-facing deployment.
4. Decide whether a short refresh-reuse grace window or per-tab coordination is
   appropriate; otherwise document that a concurrent-tab race can force a new
   sign-in.
5. Move rate limiting to a shared store and add a normalized-email login budget
   that cannot be reset by rotating source IPs.
6. Add JWT signing-key rotation support with `kid` overlap.
7. Add `Cache-Control: no-store` to authenticated account/CV/match responses and
   review request-body and decompression limits.
8. Add browser E2E coverage for logout success/failure, CSRF expiry, session
   expiry, and multi-tab refresh behavior.
