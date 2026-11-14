# Phase 8.5 - Production Readiness Review

Review date: 2026-11-10

## 1. Current architecture and implementation status

### Runtime architecture

- **Backend:** FastAPI under `ai-engine/app`, with SQLAlchemy persistence and
  Alembic migrations. SQLite is the local default; PostgreSQL is supported through
  `DATABASE_URL`.
- **Authentication:** short-lived JWT access tokens plus database-backed
  sessions and hashed, rotating refresh tokens. Access tokens are checked against
  the live session and user record on every protected request
  (`app/auth/service.py:256-290`).
- **Browser session model:** access tokens remain in Angular memory. The refresh
  token is an HttpOnly cookie and the CSRF token is a readable cookie with a
  matching request header (`app/auth/cookies.py:16-78`).
- **Authorization:** account, CV, matching-history, and settings routes derive
  the user from `AuthContext`. Reviewed handlers do not accept a client-supplied
  user ID. Job browsing and health checks are intentionally public.
- **Matching:** the local hybrid matcher remains the default and does not require
  an LLM. Remote LLM analysis requires a per-request opt-in, a per-user setting,
  and the server-level `ALLOW_REMOTE_LLM_ANALYSIS` gate
  (`app/api/routes/matching.py:75-94`).
- **Frontend:** Angular 22 standalone application with a functional auth
  interceptor, route guards, in-memory access tokens, and workspace state stored
  in browser storage. The interceptor refreshes once after a 401 and retries the
  original request (`frontend/src/app/core/auth.interceptor.ts:38-64`).

### Phase status

| Phase | Status | Notes |
| --- | --- | --- |
| 8.1 - Audit and baseline auth | Complete | Audit, authorization reassessment, and roadmap produced. |
| 8.2 - Interceptor repair | Complete | Endpoint-specific bearer routing and regression tests added. |
| 8.3 - Configuration/production hardening | Complete | Production guards, secure cookies/HSTS enforcement, JWT secret checks, docs disabled by default, auth no-store headers. |
| 8.4 - Abuse controls | Partially complete | Per-route rate limits and neutral registration/login responses implemented. Deferred audit logging and cleanup remain. |
| 8.5 - Account lifecycle (original roadmap) | Not started | No email verification, password reset, account deletion, or active-session management. |
| 8.6 - Frontend completion/E2E (original roadmap) | Not started | No password-reset/verification/session UI and no browser E2E suite. |

The Phase 8.4 report described itself as abuse protection, but the Phase 8.1
roadmap had also assigned structured auth audit logging and expired-token cleanup
to Phase 8.4. Those items were deferred. The roadmap therefore needs to finish
those operational controls before moving to account lifecycle features.

### Verification performed in this review

```text
Backend: 133 passed, 1 skipped (29.47s)
Frontend: 9 test files passed, 45 tests passed
Frontend build: application bundle generated successfully
```

Environment note: tests ran on Node.js 25.9.0, which emits the existing warning
that odd-numbered Node versions are not LTS releases.

Scope limitation: this was a source and test review, not a penetration test.
No dependency vulnerability scan, load test, multi-worker test, or live
PostgreSQL concurrency test was performed.

## 2. Security review findings

### Confirmed controls that are working

- Protected routes use `get_auth_context`/`get_current_user`; access tokens are
  rechecked against the active database session and active user
  (`app/auth/dependencies.py:32-50`, `app/auth/service.py:256-290`).
- Refresh tokens are stored as SHA-256 hashes, rotated on use, and reuse revokes
  the session (`app/auth/service.py:156-254`).
- Refresh and logout require a double-submit CSRF token when cookie credentials
  are used (`app/api/routes/auth.py:181`, `app/auth/cookies.py:66-78`).
- Password changes revoke all other sessions while preserving the current one
  (`app/auth/service.py:309-335`).
- Registration returns a neutral `202`, does not create a session, and does not
  overwrite an existing account (`app/api/routes/auth.py:64-101`,
  `app/auth/service.py:63-97`).
- Unknown, wrong-password, and disabled-account logins return the same generic
  `401 invalid_credentials` response (`app/auth/service.py:99-109`).
- Rate limiting includes per-IP, per-account/token, and per-user scopes, with
  stale-key pruning and a bounded key count
  (`app/api/routes/auth.py:76-92`, `app/api/routes/auth.py:119-133`,
  `app/core/rate_limit.py:37-112`).
- `X-Forwarded-For` is honored only from configured trusted proxies, parsed from
  right to left (`app/core/rate_limit.py:135-159`).
- Production guards reject weak/placeholder JWT secrets, insecure cookies,
  non-HTTPS CORS origins, localhost CORS origins, and missing HSTS
  (`app/auth/settings.py:100-182`, `app/api/settings.py:199-212`).
- Security headers are applied globally, auth responses are `no-store`, and API
  docs are disabled by default in production (`app/main.py:79-107`).
- Reviewed cross-user isolation tests cover profile, CV latest/history/detail,
  saved matches, and account settings (`ai-engine/tests/test_auth.py:225-457`).
- No authorization bypass or cross-user data leak was found in the reviewed
  routes.

### Confirmed gaps and weaknesses

| ID | Finding | Evidence | Type | Severity |
| --- | --- | --- | --- | --- |
| F1 | No structured authentication/security audit log. Login failures, refresh reuse, password changes, rate-limit lockouts, and session revocation are not centrally recorded. The only structured logger is the filtered LLM logger. | `app/core/logging.py:43-49`, `app/api/routes/auth.py`, `app/auth/service.py` have no security-event emission | Operational security gap | High for production operations |
| F2 | Rate limiting is process-local. Multiple workers or instances multiply the effective limits and do not share state. | `app/core/rate_limit.py:37-115` uses an in-memory `OrderedDict` | Deployment/scaling gap | High when running more than one process |
| F3 | The login "account" limit is keyed by `email:IP`, not by normalized email alone. An attacker rotating source IPs can exceed the intended per-account attempt budget. | `app/api/routes/auth.py:117-132` | Abuse-control design gap | High for distributed password guessing |
| F4 | Refresh rotation is not concurrency-safe. The refresh record is read without a row lock or compare-and-swap update, so two simultaneous requests with the same refresh token may both pass the `used_at` check and both issue valid rotated tokens. | `app/auth/service.py:170-249`; no `with_for_update()` or conditional update | Potential token-rotation weakness; requires concurrent replay | High if validated, otherwise medium |
| F5 | No expired/revoked session or refresh-token cleanup job. Rows accumulate indefinitely. | `app/db/models.py:92-131`; no cleanup task or scheduled command exists | Data lifecycle/availability gap | Medium |
| F6 | No JWT signing-key rotation or `kid` overlap support. | `app/auth/tokens.py:60-64`; single configured secret | Key-management gap | Medium |
| F7 | No global JSON request-body size limit. Text-length validation happens after FastAPI/Pydantic has read and parsed the body. Upload byte limits exist only for the upload route. | `app/main.py:57-108`; `app/api/routes/cv.py:28-34`; `app/api/routes/matching.py:40-60` | Denial-of-service exposure | Medium |
| F8 | A failed logout caused by missing/mismatched CSRF does not clear the server session or refresh cookie. The frontend clears local state even when logout fails, so the browser can retain a still-valid HttpOnly refresh session. | `app/api/routes/auth.py:201-218`, `frontend/src/app/core/auth.service.ts:152-173` | Session-lifecycle gap | Medium |
| F9 | Registration performs `flush()` outside the `IntegrityError` handler. A concurrent duplicate registration can raise an unhandled unique-constraint error instead of returning the neutral response. `get_or_create_user_settings` has the same class of race. | `app/auth/service.py:81-96`; `app/services/user_records.py:244-251` | Reliability/concurrency defect | Medium |
| F10 | Only `/auth/*` responses get `Cache-Control: no-store`. Sensitive account settings, CV history, latest CV, and match history responses do not. | `app/main.py:93-96` | Privacy/caching gap | Medium |
| F11 | The frontend has no deployment CSP and no Trusted Types configuration. The backend CSP only sets `frame-ancestors 'none'`. | `frontend/src/index.html`; `app/main.py:89-92` | Defense-in-depth gap | Medium |
| F12 | Password policy enforces length and character classes but does not reject common or breached passwords. | `app/auth/passwords.py:36-57` | Account-takeover resistance gap | Medium |
| F13 | No email verification. An attacker can register an email address they do not control; the neutral response reduces enumeration but does not establish ownership. | Registration flow; `app/api/routes/auth.py:64-101` | Identity lifecycle gap | Medium |
| F14 | No password reset/forgot-password flow. Users who forget a password have no recovery path. | No reset endpoint in `app/api/routes/auth.py` | Account lifecycle gap | Medium |
| F15 | No account deletion or active-session management/logout-all endpoints. Only CV and match history can be deleted. | `app/api/routes/auth.py`, `app/api/routes/account.py` | Account lifecycle/privacy gap | Medium |
| F16 | `APP_ENV` defaults to `development` when unset. A production deployment that forgets to set it silently skips production guards. | `app/api/settings.py:24-30` | Deployment misconfiguration risk | Medium |
| F17 | Authenticated, CPU-heavy CV and matching endpoints have no per-user quota beyond the global IP limit, and there is no per-user LLM cost quota when external analysis is enabled. | `app/api/dependencies.py:24-36`; `app/api/routes/matching.py:75-94` | Cost/abuse-control gap | Medium |
| F18 | `docs/api.md` says password changes require CSRF, but the route does not verify CSRF and the frontend does not send the header. This is not a direct CSRF vulnerability because the route requires a bearer token, but the documented security contract is wrong. | `ai-engine/docs/api.md:101-124`; `app/api/routes/auth.py:244-279`; `frontend/src/app/core/auth.service.ts:188-200` | Documentation/contract drift | Low |
| F19 | `updateProfile` and `changePassword` do not use the auth timeout/error-normalization wrapper used by login and registration. | `frontend/src/app/core/auth.service.ts:176-200` | UX/reliability gap | Low |
| F20 | CV profile data and pasted resume text are persisted in `sessionStorage`. Access tokens are correctly kept out of storage, but CV content is still sensitive and readable by any XSS in the origin. | `frontend/src/app/core/workspace.store.ts:26-74` | Privacy/defense-in-depth gap | Low |
| F21 | The frontend does not specifically handle `429 Retry-After`; `ApiError` has no retry metadata and pages display the generic API message. | `frontend/src/app/core/api-error.ts`; no `429`/`Retry-After` handling in frontend source | UX/abuse-feedback gap | Low |

### Potential/theoretical risks

1. **Refresh-race token welfare.** F4 requires concurrent replay of the same
   refresh token. The frontend collapses refreshes within one tab
   (`frontend/src/app/core/auth.service.ts:110-149`), but separate tabs and
   separate clients do not coordinate. A stolen refresh token replayed
   concurrently is the highest-risk scenario. This should be validated with a
   database row lock or conditional update before relying on rotation as a
   strict one-token-one-use guarantee.
2. **Compressed document resource exhaustion.** DOCX validation checks the ZIP
   signature and required entries, but it does not cap the decompressed size of
   archive members; PDF extraction converts the full page list into memory.
   A small crafted upload can therefore cause disproportionate CPU/memory use.
   `MAX_UPLOAD_BYTES` limits the compressed upload, not decompressed work.
3. **Return URL hardening.** Login accepts any `returnUrl` beginning with `/`
   and passes it to `router.navigateByUrl`
   (`frontend/src/app/pages/login/login.page.ts:82-85`). Angular normally keeps
   this internal, but rejecting protocol-relative forms such as `//host` would
   remove ambiguity.
4. **Cross-worker catalog memory.** The job catalog and hybrid matcher are cached
   per process (`app/api/dependencies.py:19-53`). Multiple workers load multiple
   copies; this is an operational memory concern, not a security bypass.
5. **Database deployment behavior.** Tests use SQLite. PostgreSQL row locking,
   connection-pool exhaustion, and migration rollback behavior are not exercised
   by the current suite.

## 3. Remaining risks ranked by severity

### P0 - Confirmed direct authorization bypass or cross-user data exposure

None found in the reviewed routes. The existing ownership filtering and
`AuthContext` usage are consistent across the account, CV, and matching APIs.

### P1 - Resolve before internet-facing or multi-worker production

1. **Add security audit logging (F1).** Without it, brute-force attempts, refresh
   replay, password changes, and administrator actions cannot be investigated.
2. **Make refresh rotation concurrency-safe (F4).** Add row-level locking or an
   atomic conditional update and verify that only one concurrent refresh succeeds.
3. **Use a shared rate-limit store or explicitly constrain deployment to one
   worker (F2).** Redis or an equivalent shared backend is required for consistent
   limits across workers/instances.
4. **Use a truly per-account login budget (F3).** Keep the current IP/account key
   for anti-DoS behavior, but add a normalized-email bucket or a progressive
   lockout/backoff layer that cannot be reset by rotating IPs.
5. **Enforce request-size and decompression limits (F7).** Add a reverse-proxy
   body limit and application-level caps for JSON bodies, PDF pages, and DOCX
   decompressed content.
6. **Complete identity lifecycle controls (F13-F15).** Email verification,
   password reset, account deletion, and session management are required for a
   credible multi-user production service.
7. **Add cleanup and retention for sessions/tokens (F5).** Expired and revoked
   rows should be purged according to a documented retention policy.
8. **Add JWT key rotation support (F6)** before the first long-lived production
   deployment, or document an emergency re-issuance procedure.

### P2 - Resolve before broad production rollout

1. Fix the registration/settings creation races and add concurrency tests (F9).
2. Correct the CSRF documentation mismatch or, if cookie auth is ever added to
   password changes, enforce CSRF there (F18).
3. Add `no-store` to authenticated user-data responses (F10).
4. Add a frontend CSP/Trusted Types deployment policy (F11).
5. Add breached/common-password screening (F12).
6. Add per-user quotas for CPU-heavy matching and optional LLM spending (F17).
7. Make logout failure explicit and recoverable; do not silently imply the server
   session is gone when the request failed (F8).
8. Make `APP_ENV=production` mandatory in deployment manifests and add a startup
   assertion or deployment check that the environment is not left at the default
   (F16).
9. Complete login/register/settings page tests and browser E2E coverage; add
   `Retry-After` UX handling (F19/F21).

### P3 - Hardening and maintainability

1. Harden `returnUrl` validation and add a test for protocol-relative inputs.
2. Minimize CV content in `sessionStorage`, or document the XSS trade-off and
   clear it aggressively (F20).
3. Add dependency scanning, container/image scanning, and secret scanning to CI.
4. Document database backup/restore, TLS to PostgreSQL, log retention, and
   incident-response procedures.

## 4. Missing tests

### Backend

- JWT edge cases: expired access token, tampered signature, wrong algorithm,
  wrong issuer, wrong audience, and access/refresh token-type confusion.
- Concurrent refresh using the same token from two independent clients; assert
  exactly one rotation succeeds and the replay revokes the session.
- Concurrent duplicate registration; assert neutral response/no unhandled 500.
- Logout with missing, mismatched, and valid CSRF, including the state of the
  server session and refresh token after failure.
- Session and refresh-token cleanup behavior, including retention boundaries.
- Audit-event emission, redaction, request-ID correlation, and alert thresholds.
- Request-body size limits and decompression/resource-exhaustion limits.
- Password-reset, email-verification, account-deletion, and session-management
  flows once implemented.
- PostgreSQL-backed integration tests for locking, migrations, and cascade
  behavior; SQLite-only coverage is insufficient for F4 and F9.
- Multi-worker/rate-limit behavior with a shared store.

### Frontend

- Page-level tests for login, register, and settings actions. Current page specs
  cover CV, jobs, and matches only.
- Profile update, password change, logout failure, session expiry, and
  `429 Retry-After` behavior.
- Cross-tab session synchronization and refresh coordination.
- CSP-compatible production build and browser E2E flows for register, login,
  refresh, logout, password change, and account-data deletion.
- Tests for `returnUrl` validation and any future account-lifecycle UI.

### Deployment/operations

- Startup checks in the actual production process manager/container, not only
  unit-level settings parsing.
- Reverse-proxy trust, body-size limits, HSTS, CORS, and cookie behavior over
  HTTPS.
- Multi-worker rate-limit behavior, session cleanup scheduling, backup/restore,
  and log-redaction checks.

## 5. Recommended next implementation phase

### Recommendation: Phase 8.5 - Auth operations and session integrity

The original roadmap's "account lifecycle" phase should not be started until the
deferred security-operations work from Phase 8.4 is finished. Account lifecycle
features add more tokens, sessions, and identity state; building them on top of
an unaudited, non-concurrency-safe session layer increases the cost of fixing
those foundations later.

Recommended order:

1. **Session integrity:** refresh row locking/conditional update; registration
   and settings creation race handling; logout failure semantics.
2. **Security observability:** structured auth audit events with request ID,
   user ID, event type, outcome, IP, and redacted metadata; alert hooks; explicit
   retention policy.
3. **Data lifecycle:** scheduled cleanup for expired/revoked sessions and refresh
   tokens; request-body/decompression limits; authenticated-response `no-store`.
4. **Abuse hardening:** per-account login budget independent of IP; per-user
   matching/LLM quotas; common-password rejection.
5. **Deployment hardening:** shared limiter or single-worker constraint; JWT key
   rotation; mandatory production configuration checks; CSP.
6. **Then account lifecycle:** email verification, password reset, account
   deletion, active-session list, and logout-all.
7. **Then frontend completion/E2E:** recovery and session-management UI plus
   Playwright/Cypress coverage.

## 6. Exact files/modules likely requiring modification

Backend:

- `ai-engine/app/auth/service.py` - refresh locking, registration race handling,
  password/session lifecycle.
- `ai-engine/app/api/routes/auth.py` - audit events, logout failure behavior,
  per-account limit, future reset/verification/session routes.
- `ai-engine/app/api/routes/account.py` - account deletion/session management if
  added.
- `ai-engine/app/api/dependencies.py` - rate-limit scopes and request-body
  controls.
- `ai-engine/app/api/errors.py` - safe audit/error correlation and no-store
  behavior.
- `ai-engine/app/main.py` - body-size middleware, security headers/CSP
  integration, startup checks.
- `ai-engine/app/core/rate_limit.py` - shared-store abstraction and per-account
  limits.
- New `ai-engine/app/core/audit.py` or equivalent - structured security events.
- `ai-engine/app/core/logging.py` - shared redaction rules for audit/API logs.
- `ai-engine/app/auth/settings.py` - key rotation/`kid` configuration and production
  assertions.
- `ai-engine/app/db/models.py` and `ai-engine/migrations/versions/` - cleanup
  indexes, email-verification/reset/session metadata, account-deletion support.
- `ai-engine/app/services/user_records.py` - settings creation race and deletion
  semantics.
- `ai-engine/tests/test_auth.py`, `test_rate_limits.py`,
  `test_security_settings.py`, plus new concurrency/audit/cleanup tests.
- `ai-engine/docs/api.md`, `.env.example`, `README.md` - security contract,
  deployment, retention, and CSRF accuracy.

Frontend:

- `frontend/src/app/core/auth.service.ts` - timeouts, logout failure handling,
  cross-tab coordination, future reset/verification flows.
- `frontend/src/app/core/api-error.ts` - `429`/`Retry-After` metadata.
- `frontend/src/app/core/auth.interceptor.ts` - refresh coordination if
  cross-tab synchronization is added.
- `frontend/src/app/pages/login/login.page.ts` - return-URL hardening and reset
  links.
- `frontend/src/app/pages/register/register.page.ts` - verification flow.
- `frontend/src/app/pages/settings/settings.page.ts` and template - session
  management, account deletion, logout feedback.
- `frontend/src/index.html` or deployment server configuration - CSP/Trusted
  Types.
- New page specs and Playwright/Cypress configuration.

## 7. Acceptance criteria for the next phase

### Session integrity

- Two concurrent refresh requests using the same token produce one success and
  one controlled rejection; the replay revokes the session and no second valid
  refresh chain is created.
- Concurrent duplicate registration returns the neutral `202` response without
  an unhandled `IntegrityError` or `500`.
- Logout failure leaves an explicit, user-visible error and does not claim the
  session was revoked when the server rejected the request.
- Password change still revokes other sessions and retains the current session.

### Audit and observability

- Login success/failure, refresh reuse, CSRF failure, rate-limit rejection,
  logout, password change, registration, and account-state changes emit
  structured events with request ID, outcome, and redacted identifiers.
- Audit logs never contain passwords, refresh/access tokens, full CV text, email
  bodies, or LLM prompts.
- Alerting hooks exist for repeated login failures, refresh reuse, and mass
  session revocation.
- Retention, access control, and redaction behavior are documented and tested.

### Abuse and resource controls

- Request bodies above configured limits are rejected before expensive parsing.
- DOCX/PDF extraction has explicit decompressed-size/page-count/time limits and
  tests for crafted inputs.
- Login attempts are limited per normalized account across IPs while preserving
  anti-DoS IP controls.
- Optional LLM calls have per-user cost/rate quotas.
- Multi-worker deployment uses a shared rate-limit store, or documentation and
  startup checks explicitly restrict deployment to one worker.

### Account lifecycle

- Email verification proves ownership before the address is treated as trusted.
- Password reset uses neutral responses, single-use expiring tokens, rate limits,
  and session revocation after reset.
- Account deletion removes user-owned records or anonymizes them according to a
  documented retention policy and revokes all sessions.
- Users can list and revoke active sessions, including logout-all.

### Frontend and verification

- Login, register, settings, recovery, verification, and session-management pages
  have unit/component tests.
- Playwright/Cypress covers register, login, refresh, expiry, logout failure,
  password change, password reset, account deletion, and session revocation
  against a live test API.
- `npm test -- --watch=false`, `npm run build`, and the full backend suite pass.
- Production settings are exercised in a container/process-manager smoke test,
  not only in unit tests.
