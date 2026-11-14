# Phase 8.1 — Authentication & Security Audit

**Project:** JobMatch AI  
**Scope:** FastAPI backend, Angular frontend, authentication/session lifecycle, authorization, secrets, input validation, rate limiting, and related tests.  
**Audit date:** 2026-11-10  
**Method:** Static review of the repository plus existing automated tests. No source files, schemas, dependencies, or configuration were changed.

**Verification run during the audit:**

```text
Backend:  venv/Scripts/python.exe -m pytest tests/test_auth.py -q
          12 passed in 5.70s

Frontend: npm test -- --watch=false
          9 test files, 39 tests passed (8.55s)
```

**Finding labels used below:**

- **Confirmed issue** — directly observable in the current code and reproducible behavior.
- **Potential risk** — depends on deployment, configuration, browser behavior, concurrency, or future feature work.

---

## 1. Existing implementation

### 1.1 Backend authentication routes

The API exposes the following authentication/account endpoints in `ai-engine/app/api/routes/auth.py` and `ai-engine/app/api/routes/account.py`:

| Endpoint | Auth required | Implementation |
|---|---:|---|
| `POST /api/v1/auth/register` | No | Creates a user, settings row, session, access token, refresh token, CSRF token. `auth.py:62` |
| `POST /api/v1/auth/login` | No | Validates credentials, issues a new session. `auth.py:94` |
| `POST /api/v1/auth/refresh` | Refresh cookie + CSRF | Rotates refresh token and issues a new access token. `auth.py:133` |
| `POST /api/v1/auth/logout` | Optional cookie/Bearer | Revokes the current session and clears cookies. `auth.py:161` |
| `GET /api/v1/auth/me` | Bearer | Returns the current user. `auth.py:191` |
| `PATCH /api/v1/auth/me` | Bearer | Updates the user's full name. `auth.py:200` |
| `POST /api/v1/auth/change-password` | Bearer | Verifies the current password, changes the password, and revokes other sessions. `auth.py:214` |
| `GET /api/v1/account/settings` | Bearer | Reads per-user matching settings. `account.py:15` |
| `PUT /api/v1/account/settings` | Bearer | Updates per-user matching settings. `account.py:28` |

Protected feature routes are also wired:

- CV analysis, CV history, and CV deletion: `app/api/routes/cv.py:37`, `:60`, `:93`, `:112`, `:133`, `:157`.
- Job matching, recommendations, and match history: `app/api/routes/matching.py:62`, `:127`, `:212`, `:233`, `:253`.
- Public routes: health (`app/api/routes/health.py:12`) and job catalog browsing (`app/api/routes/jobs.py:13`, `:39`).

### 1.2 Password hashing and verification

Implemented in `ai-engine/app/auth/passwords.py`:

- Argon2id with explicit parameters: `time_cost=3`, `memory_cost=65536`, `parallelism=4`, 32-byte hash, 16-byte salt (`passwords.py:13`).
- Password length enforcement: 12–128 characters (`passwords.py:9`, `:23`).
- Strength policy: at least three of lowercase, uppercase, digit, symbol (`passwords.py:36`).
- Control characters rejected (`passwords.py:42`).
- Verification catches Argon2 verification/invalid-hash errors and returns `False` (`passwords.py:29`).
- A dummy Argon2 hash is used for unknown-account login attempts to reduce timing-based user enumeration (`passwords.py:60`; `service.py:106`).

### 1.3 JWT generation, validation, and expiry

Implemented in `ai-engine/app/auth/tokens.py`:

- Access tokens are JWTs signed with `JWT_SECRET_KEY` using a configured HMAC algorithm (`tokens.py:39`, `:77`).
- Refresh tokens use the same signing mechanism with token type `refresh` (`tokens.py:95`).
- Claims include `sub`, `sid`, `jti`, `type`, `iss`, `aud`, `iat`, `nbf`, and `exp` (`tokens.py:49`).
- Decoding requires all of those claims, verifies issuer/audience, and restricts the algorithm to the configured allowed algorithm (`tokens.py:114`).
- Token type is checked after decoding (`tokens.py:146`).
- Refresh token hashes are SHA-256 stored in the database; token comparison uses `hmac.compare_digest` (`tokens.py:168`, `:172`).

Default durations are configured in `app/auth/settings.py`:

- Access token: 900 seconds (15 minutes).
- Refresh token: 604 800 seconds (7 days).
- Session: 2 592 000 seconds (30 days).

Startup validation rejects a JWT secret shorter than 32 characters (`auth/settings.py:70`).

### 1.4 Session lifecycle, refresh rotation, and revocation

Implemented in `ai-engine/app/auth/service.py`:

- Login/registration creates an `AuthSession` and a hashed refresh-token record (`service.py:123`).
- Every refresh rotates the refresh token and marks the old token as used (`service.py:168`).
- A used or revoked refresh token is treated as reuse: the whole session is revoked (`service.py:209`, `:215`).
- Access-token authentication re-checks the database session on every request, including revocation, expiry, user existence, and `is_active` (`service.py:268`).
- Logout revokes the current session (`service.py:304`).
- Password changes verify the current password, update the hash, and revoke all other active sessions (`service.py:321`).

### 1.5 Authentication dependencies and protected endpoints

- `get_auth_context` uses `HTTPBearer(auto_error=False)` and requires a Bearer credential (`app/auth/dependencies.py:13`, `:32`).
- Missing/invalid credentials return structured 401 errors with `WWW-Authenticate: Bearer` (`auth/dependencies.py:23`, `:36`).
- `get_current_user` derives the user from the validated context (`auth/dependencies.py:49`).
- Protected routes consistently depend on `get_auth_context`.

### 1.6 User ownership validation

Ownership is enforced by filtering database queries by `user_id`:

- CV latest/detail/history: `app/services/user_records.py:105`, `:114`, `:123`.
- Match detail/history: `app/services/user_records.py:189`, `:217`.
- Settings: `get_or_create_user_settings` and `update_user_settings` use the authenticated user ID (`user_records.py:244`, `:254`).
- Cross-user access tests exist for CV history and saved matches (`tests/test_auth.py:286`, `:313`).

No endpoint in the inspected routes accepts a user ID from the client for record access.

### 1.7 Database models and relationships

Defined in `ai-engine/app/db/models.py`:

- `User` with unique indexed email and password hash (`models.py:31`).
- `AuthSession` linked to `User` with expiry/revocation timestamps (`models.py:92`).
- `RefreshToken` linked to `AuthSession`, with unique `jti`, token hash, expiry, used-at, and revoked-at (`models.py:114`).
- `UserSettings`, `CandidateProfile`, `CVAnalysis`, and `SavedMatch` all carry `user_id` foreign keys with cascade behavior (`models.py:70`, `:134`, `:167`, `:199`).
- Initial Alembic migration creates all auth/user-data tables and indexes (`migrations/versions/31a227204242_initial_auth_and_user_data_schema.py:21`).

### 1.8 Secrets and environment configuration

- `JWT_SECRET_KEY`, JWT issuer/audience, token durations, cookie flags, and rate limits are read from environment variables (`app/auth/settings.py:70`; `app/api/settings.py:92`).
- `.env` is ignored by Git; `.env.example` is allowed (`.gitignore:7`).
- No real production secret was found in the repository. Test secrets are confined to tests (`tests/conftest.py:18`).
- CORS wildcard is rejected when credentials are enabled (`app/api/settings.py:51`).
- Security headers are applied globally (`app/main.py:71`): `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, a minimal CSP (`frame-ancestors 'none'`), and optional HSTS.

### 1.9 Rate limiting and input validation

- In-memory sliding-window limiter (`app/core/rate_limit.py:20`).
- Global API rate limit dependency (`app/api/dependencies.py:24`; `app/api/router.py:6`).
- Login limits: IP and account scopes (`app/api/routes/auth.py:108`).
- Registration limit by IP (`app/api/routes/auth.py:76`).
- CSRF double-submit verification for refresh/logout (`app/auth/cookies.py:66`).
- Strict Pydantic schemas use `extra="forbid"` and explicit length bounds (`app/api/auth_schemas.py:17`, `:21`, `:50`; `app/schemas/common.py`).
- Resume text, job text, upload bytes, and upload file types are validated (`app/api/routes/cv.py:28`; `app/api/uploads.py:25`; `app/services/documents/extraction.py`).

### 1.10 Frontend authentication implementation

- `AuthService` keeps the access token and CSRF token in Angular signals in memory (`frontend/src/app/core/auth.service.ts:39`).
- `initialize()` restores a session using the HttpOnly refresh cookie and hydrates account settings/latest CV (`auth.service.ts:58`).
- Registration/login use `withCredentials: true` (`auth.service.ts:77`, `:95`).
- Refresh is single-flight within a service instance (`auth.service.ts:113`).
- Logout sends the Bearer access token and CSRF header, clears local state even on failure (`auth.service.ts:155`).
- `authInterceptor` adds `withCredentials` to all requests and attaches the Bearer token to non-auth paths (`frontend/src/app/core/auth.interceptor.ts:12`).
- On a 401 for a request that had a token, the interceptor refreshes once and retries (`auth.interceptor.ts:28`).
- Guards protect app routes and keep authenticated users out of login/register (`auth.guard.ts:10`, `:36`; `app.routes.ts:4`).
- Login, registration, settings/profile/password/logout UI exists (`pages/login`, `pages/register`, `pages/settings`).
- Frontend tests exist for `AuthService`, guards, the interceptor, and the app shell.

---

## 2. Working features

The following are implemented and, where noted, confirmed by tests.

### Backend

1. **Registration** creates a user, default user settings, a session, and tokens; duplicate emails return 409 (`tests/test_auth.py:90`, `:125`).
2. **Login** returns generic invalid-credentials errors for wrong password and unknown account (`tests/test_auth.py:148`).
3. **Password hashing** uses Argon2id, and plain-text passwords are not returned in responses (`tests/test_auth.py:108`).
4. **Password strength validation** rejects weak passwords and does not echo the submitted password (`tests/test_auth.py:140`).
5. **JWT access tokens** include and require issuer, audience, expiry, session ID, JTI, and token type (`app/auth/tokens.py:49`, `:114`).
6. **Refresh token rotation** issues a new refresh token and detects reuse (`tests/test_auth.py:196`).
7. **Refresh/logout CSRF protection** rejects missing CSRF headers (`tests/test_auth.py:234`).
8. **Session revocation** invalidates access tokens immediately after logout or reuse detection (`tests/test_auth.py:230`, `:252`).
9. **Disabled accounts** are rejected on login and authenticated requests (`tests/test_auth.py:179`).
10. **Password change** verifies the current password and revokes other sessions (`tests/test_auth.py:256`).
11. **Per-user isolation** is enforced for CV history and saved matches (`tests/test_auth.py:286`, `:313`).
12. **Per-user settings** are persisted and isolated (`tests/test_auth.py:337`).
13. **Login rate limiting** returns 429 with `Retry-After` (`tests/test_auth.py:365`).
14. **Security headers and credentialed CORS** are present (`tests/test_auth.py:390`).
15. **No hardcoded production secrets** were found in application source.

### Frontend

1. Login and registration pages exist with reactive form validation.
2. Access tokens are kept in memory rather than `localStorage`/`sessionStorage` (`auth.service.ts:39`).
3. Refresh tokens are not accessible to JavaScript because they are stored in HttpOnly cookies.
4. Route guards protect all main application pages.
5. The interceptor performs a single in-instance refresh-and-retry flow on 401.
6. App startup attempts session restoration with the CSRF cookie and refresh endpoint.
7. Logout clears local user/workspace state even if the server call fails.
8. Session-expired navigation to `/login` is implemented.
9. Frontend tests pass (39/39).

### Reviewed non-issues

- CORS is not wildcarded with credentials; wildcard configuration is rejected (`app/api/settings.py:62`).
- JWT algorithms are restricted to an allowlist; no `none` algorithm path was found (`auth/settings.py:10`, `tokens.py:121`).
- The refresh token is stored hashed in the database, not in plain text (`service.py:146`).
- Ownership checks are applied server-side, not only hidden in the UI.
- Registration/login use JSON bodies, explicit CORS origins, and SameSite cookies; the absence of a CSRF token on those two endpoints is not currently an exploitable cross-site form flow from the inspected code.

---

## 3. Missing features

The authentication foundation is unusually complete, but the following are absent.

### 3.1 Account recovery and email lifecycle

1. **Forgot-password flow** — no endpoint for requesting a reset, no reset token model, no email delivery, and no Angular page.
2. **Password reset** — no reset-token generation, one-time use, hashing, expiry, or session revocation on reset.
3. **Email verification** — `User` has no `email_verified`/verification-token state (`app/db/models.py:31`).
4. **Resend verification** — no endpoint or UI.
5. **Email change** — no endpoint; `User.email` can only be set at registration.
6. **Account recovery for locked-out users** — the only recovery path is an existing password.

These require database/schema changes and email infrastructure, so they should be a separate implementation phase rather than bundled into a small security patch.

### 3.2 Session and account management

1. **Active session/device list** — sessions exist in the database but there is no endpoint or UI to list them.
2. **Revoke an individual session** — only the current session or all-other-sessions-on-password-change paths exist.
3. **Logout all sessions** — no dedicated endpoint or UI.
4. **Session metadata** — no user-agent, IP, device label, or last-used display.
5. **Self-service account deletion** — users can delete CV/match data but cannot close the account.
6. **Account data export** — not implemented.
7. **Cross-tab session synchronization** — no `BroadcastChannel`/storage-event coordination; logout in one tab does not immediately update other tabs.

### 3.3 Security operations

1. **Authentication audit logging** — no structured logs for login success/failure, refresh reuse, logout, password change, or account disablement. The only structured logger found is for LLM telemetry (`app/core/logging.py:43`).
2. **Alerting on suspicious activity** — refresh-token reuse revokes the session but does not log or alert.
3. **Expired session/token cleanup** — no scheduled or startup cleanup of expired `auth_sessions` and `refresh_tokens`.
4. **Persistent lockout / failed-attempt tracking** — rate limits are in-memory only.
5. **MFA / TOTP / WebAuthn** — not implemented.
6. **Password breach checks** — no Have I Been Pwned/k-anonymity or equivalent check.
7. **Password reuse prevention** — changing to the same password is allowed.

### 3.4 API/documentation gaps

1. **`ai-engine/docs/api.md` is stale** — it documents an architecture without auth/account routes and explicitly states there is no authentication, authorization, or rate limiting (`docs/api.md:240`). This contradicts the implementation and README.
2. **No auth endpoint documentation or examples** in `docs/api.md`.
3. **OpenAPI/Swagger UI is always enabled** — `/docs`, `/redoc`, and `/openapi.json` are exposed by default (`app/main.py:36`).
4. **No documented production security checklist beyond README notes** for cookies, secret rotation, proxy headers, and multi-worker rate limiting.

### 3.5 Rate-limit coverage gaps

1. No dedicated per-session/per-IP limit on `/auth/refresh`.
2. No dedicated limit on `/auth/change-password`, `/auth/me`, or `/account/settings`; only the global API limit applies.
3. No limit on failed current-password attempts during password change beyond the global limit.
4. No persistent/shared rate-limit store.

---

## 4. Security vulnerabilities or risks

### 4.1 Confirmed issues

#### C1 — High: Frontend interceptor strips Bearer tokens from protected `/auth/*` endpoint calls

**Confirmed by code.**

`auth.interceptor.ts` treats every URL containing `/api/v1/auth/` as unauthenticated and returns early without adding an Authorization header:

- Excluded path check: `frontend/src/app/core/auth.interceptor.ts:10`, `:16`.
- Early return: `auth.interceptor.ts:17`.

However, these auth-prefixed endpoints require a Bearer token:

- `PATCH /api/v1/auth/me` (`app/api/routes/auth.py:200`; `get_auth_context` at `:207`).
- `POST /api/v1/auth/change-password` (`auth.py:214`; `get_auth_context` at `:221`).

The frontend methods that call them do not set the Authorization header manually:

- `updateProfile` (`frontend/src/app/core/auth.service.ts:179`).
- `changePassword` (`auth.service.ts:191`).

**Impact:** The Settings page's "Save profile" and "Change password" actions will send unauthenticated requests and receive 401 responses. Logout works because `AuthService.logout()` manually sets the Authorization header (`auth.service.ts:155`), and login/register/refresh are intentionally anonymous.

The existing interceptor test only checks that login does not receive a Bearer token (`frontend/src/app/core/auth.interceptor.spec.ts:59`), so the regression is not covered.

**Required direction:** Exclude only the truly anonymous auth endpoints (`login`, `register`, `refresh`) from bearer injection, or explicitly attach the token in `updateProfile`/`changePassword`. Add regression tests for both protected calls.

#### C2 — Medium: JWT placeholder values pass length validation

**Confirmed configuration risk.**

The JWT secret is only checked for a minimum length of 32 characters (`app/auth/settings.py:72`). The repository's placeholder values satisfy that check:

- `.env.example:40`: `JWT_SECRET_KEY=replace-with-a-random-secret-of-at-least-32-characters`
- `README.md:30`: `$env:JWT_SECRET_KEY = "replace-with-a-long-random-secret"`

If a deployer copies either value without replacing it, the API starts with a publicly known signing key. An attacker could then forge access/refresh tokens and impersonate any user.

**Required direction:** Reject known placeholder strings, reject low-entropy/all-repeated secrets, and add a startup production-mode guard. Do not store real secrets in the repository.

#### C3 — Medium: Insecure cookie/HSTS defaults are accepted without a production guard

**Confirmed default configuration risk.**

- `COOKIE_SECURE` defaults to `false` (`auth/settings.py:111`; `api/settings.py` does not define an environment concept).
- `ENABLE_HSTS` defaults to `false` (`api/settings.py:89`).
- `COOKIE_SAMESITE` defaults to `strict`, which is good, but `SameSite=None` is allowed with `Secure` (`auth/settings.py:106`).
- `.env.example:50` also ships `COOKIE_SECURE=false`.

If deployed as-is over HTTP, refresh tokens may be transmitted without transport encryption, and HSTS is absent. This is safe for local development but unsafe as an unmodified production default.

**Required direction:** Add an explicit `APP_ENV`/`ENVIRONMENT` mode and fail startup in production when `COOKIE_SECURE=false`, `ENABLE_HSTS=false` on public HTTPS deployments, or when the CORS origin is localhost.

#### C4 — Medium: Rate limiting is per-process, proxy-unaware, and not garbage-collected

**Confirmed implementation limitation with denial-of-service potential.**

- The limiter stores events in a plain process-local dict with a threading lock (`app/core/rate_limit.py:20`).
- Each unique key creates a deque that is only pruned when that same key is checked again (`rate_limit.py:31`).
- There is no eviction of inactive unique keys; unique email discriminators or IPs can grow memory.
- `client_ip` uses `request.client.host` and ignores `X-Forwarded-For` / trusted proxy configuration (`rate_limit.py:59`).
- With multiple Uvicorn workers or app instances, each process has its own limit, multiplying the effective allowance.
- Behind a reverse proxy without correct client-IP handling, all users may share one key, causing false 429s or an attacker to affect other users.

**Required direction:** Use a shared store (Redis) in production, configure trusted proxy handling deliberately, evict stale keys, and add per-account/per-session limits for sensitive auth endpoints.

#### C5 — Low: Account enumeration through registration and disabled-account responses

**Confirmed behavior.**

- Registration returns `409 email_already_registered` when an email exists (`service.py:71`, `:99`).
- Login returns `403 account_disabled` after correct-password authentication of a disabled account (`service.py:115`; `routes/auth.py:127`), while unknown/wrong credentials return the generic 401.
- The dummy password hash reduces timing differences for unknown accounts (`passwords.py:60`; `service.py:108`), but explicit status differences remain.

This is common product behavior, but it enables email/account enumeration and reveals disabled-account status.

#### C6 — Low: No authentication audit logging or alerting

**Confirmed absence.**

Refresh-token reuse detection revokes the session (`service.py:209`) but emits no log or alert. There is no audit trail for successful logins, failed logins, password changes, logout, or disabled-account access. This limits detection of credential stuffing, token theft, and account takeover.

#### C7 — Low: Sensitive auth responses lack consistent `Cache-Control: no-store`

**Confirmed partial implementation.**

- Successful login/register/refresh responses set `Cache-Control: no-store` (`routes/auth.py:43`).
- `GET/PATCH /auth/me`, `POST /auth/change-password`, and most auth error responses do not set it.
- The `AuthResponse` body contains an access token, but those responses are already no-store. The remaining endpoints contain account PII rather than tokens.

This is a defense-in-depth gap, not evidence of a token leak.

#### C8 — Low: API documentation and schema endpoints are exposed by default

**Confirmed default behavior.**

FastAPI exposes `/docs`, `/redoc`, and `/openapi.json` (`app/main.py:36`). This is useful in development but can disclose route structure and schemas in production. No environment switch disables them.

#### C9 — Low: Logout failure can leave the server session and HttpOnly cookie intact

**Confirmed edge behavior.**

- If the logout request fails at the network layer, `AuthService.logout()` catches the error and clears only local state (`auth.service.ts:174`, `:205`). The HttpOnly refresh cookie cannot be cleared by JavaScript, and the server session remains active.
- If CSRF verification fails, the backend raises before `clear_auth_cookies` is called (`routes/auth.py:172`–`187`), so the refresh cookie is not cleared.
- On reload, the CSRF cookie may still allow session restoration.

This is most relevant on shared devices and when the server is unreachable during logout.

#### C10 — Low: Password policy accepts common/breached passwords

**Confirmed policy limitation.**

The policy checks length, character classes, and control characters (`passwords.py:36`), but not known-breached passwords, dictionary words, or repetition. A password such as `Password123!` satisfies the current policy.

### 4.2 Potential risks

#### P1 — Multi-tab refresh rotation race can revoke a valid session

Refresh-token reuse detection revokes the whole session (`service.py:209`). Refresh single-flight is only inside one `AuthService` instance (`auth.service.ts:113`). Two browser tabs sharing the same HttpOnly refresh cookie can refresh concurrently; one request may rotate the token while the other presents the old token, triggering reuse detection and forcing re-login. This is not observable in the current tests.

#### P2 — Login-account rate limiting enables targeted lockout

The account scope uses only the normalized email and allows five attempts per five minutes by default (`routes/auth.py:114`; `api/settings.py:85`). An attacker with rotating IPs can intentionally lock a target out by submitting failed attempts. The IP scope (`limit * 4`) mitigates but does not eliminate this.

#### P3 — No token/key rotation story

JWTs have no `kid` and the application accepts only one configured secret (`auth/tokens.py:39`). Rotating `JWT_SECRET_KEY` invalidates every session/token immediately; there is no overlap or staged rotation path. This is an operational security risk during incident response.

#### P4 — Frontend token-in-memory XSS exposure

Access tokens are intentionally kept in memory (`auth.service.ts:39`) and are sent in `Authorization` headers. That avoids persistent storage token theft, but any XSS in the Angular app can still read the in-memory token and make authenticated requests. The backend CSP only sets `frame-ancestors 'none'` (`main.py:81`); the frontend `index.html` has no CSP meta tag and no deployment CSP is defined in the repository. Full CSP/Trusted Types protection should be added at the hosting layer.

#### P5 — Session and token retention has no cleanup policy

`auth_sessions` and `refresh_tokens` accumulate until explicitly deleted. There is no cleanup job for expired/revoked rows. This increases database size and prolongs retention of security metadata.

#### P6 — No email ownership verification

Because email verification is absent, an account can be registered with an email address the registrant does not control. This becomes more significant if password reset/email change is added later without verification gates.

#### P7 — Refresh endpoint has only the global rate limit

An attacker cannot feasibly guess a high-entropy signed refresh token, but the endpoint has no dedicated per-session/IP abuse limit. A stolen refresh cookie could be exercised at the global API limit. Reuse detection limits the window, but auditing/limiting should be explicit.

#### P8 — In-memory rate limiter key growth

As described in C4, unique account discriminators or IPs are retained in memory. Under sustained abuse with rotating identities, memory can grow. This is a resource-exhaustion risk rather than an authentication bypass.

---

## 5. Backend improvements required

### P0 — Correctness before new features

1. **Do not regress working auth behavior.** Keep Argon2id, JWT claim validation, refresh rotation, reuse detection, CSRF, and user-scoped queries as they are.
2. **Fix the corresponding frontend request path** (C1) and add backend regression tests for `PATCH /auth/me` and `POST /auth/change-password` through the same auth dependency.
3. **Add `Cache-Control: no-store`** to `/auth/me`, `/auth/change-password`, logout errors, and auth error responses (`routes/auth.py`, `app/api/errors.py`).

### P1 — Configuration and secret hardening

1. Add an explicit environment mode (development/test/production).
2. In production mode, fail startup when:
   - `JWT_SECRET_KEY` is missing, too short, a known placeholder, or low-entropy.
   - `COOKIE_SECURE=false`.
   - `CORS_ORIGINS` contains localhost or a non-HTTPS origin for a public deployment.
   - HSTS remains disabled when the configured public origin is HTTPS.
3. Reject placeholder secrets from `.env.example`/README examples in all non-test environments.
4. Add optional key-rotation support (`kid`, overlapping verification keys) before production hardening.

### P2 — Rate limiting and abuse controls

1. Replace or supplement the process-local limiter with a shared store for multi-worker deployments.
2. Make proxy client-IP handling explicit and configurable; do not blindly trust `X-Forwarded-For` unless a trusted proxy is configured.
3. Evict stale limiter keys and cap memory usage.
4. Add dedicated limits for refresh, change-password, and account settings.
5. Consider adding a persistent failed-login counter, progressive backoff, or CAPTCHA for repeated failures.
6. Add a test for multi-worker behavior only if a shared limiter is introduced; otherwise document the single-process limitation clearly.

### P3 — Security observability

1. Log structured auth events without sensitive values: login success/failure, refresh reuse, logout, password change, disabled account, rate-limit trips, and session revocation.
2. Never log passwords, tokens, hashes, or full email addresses if avoidable; use hashed/pseudonymous identifiers for security correlation.
3. Add alert hooks for refresh-token reuse and repeated failed logins.

### P4 — Session lifecycle and data retention

1. Add a cleanup command/job for expired/revoked `auth_sessions` and `refresh_tokens`.
2. Add endpoints for listing active sessions and revoking one/all sessions.
3. Add session metadata (user-agent, IP, created/last-used) for a future UI and audit trail.
4. Add self-service account deletion with cascading data removal and explicit reauthentication.
5. Add a data-export path if required by product/privacy policy.

### P5 — Account recovery and email verification (requires schema work)

1. Add password-reset tokens with one-time use, short expiry, hashed storage, and session revocation after reset.
2. Add email-verification tokens and a `User.email_verified` field.
3. Add resend-verification and email-change flows.
4. Add rate limits to all recovery/verification endpoints.
5. Do not implement these until the schema migration plan is approved; the current audit must not alter the schema.

### P6 — API exposure and documentation

1. Disable `/docs`, `/redoc`, and `/openapi.json` by default in production or protect them.
2. Update `ai-engine/docs/api.md` to document authentication, all auth/account endpoints, token lifecycle, rate limits, and security configuration.
3. Remove the stale statement that the repository has no authentication/authorization/rate limiting (`docs/api.md:240`).
4. Document the `JWT_SECRET_KEY` placeholder risk and production rollout checklist.

---

## 6. Frontend improvements required

### P0 — Fix the confirmed protected-auth routing bug

1. Change `authInterceptor` so it excludes only anonymous auth endpoints:
   - `POST /api/v1/auth/login`
   - `POST /api/v1/auth/register`
   - `POST /api/v1/auth/refresh`
   - Carefully handle `/api/v1/auth/logout` (it is protected by its own manually set header today).
2. Ensure `PATCH /api/v1/auth/me` and `POST /api/v1/auth/change-password` receive `Authorization: Bearer <access_token>`.
3. Add interceptor regression tests asserting that:
   - login/register/refresh do not receive a Bearer token;
   - `/auth/me` and `/auth/change-password` do receive one;
   - a 401 on those protected calls triggers the refresh/retry path.

### P1 — Authentication UI completeness

1. Add a **Forgot password** link on the login page and a reset-password page/route.
2. Add **email verification** status/banner and resend-verification UI once the backend exists.
3. Add **account deletion** UI with reauthentication and clear destructive-action confirmation.
4. Add **active sessions / log out all devices** UI if the backend session endpoints are added.
5. Add a **password strength meter** that mirrors backend policy (12–128 chars, three character classes) on register and change-password forms.
6. Add a **caps-lock/visibility toggle** if product UX requires it.
7. Show specific handling for `429` responses using `Retry-After` rather than a generic error.

### P2 — Session resilience and UX

1. Add cross-tab session synchronization (e.g., `BroadcastChannel`) so logout/session expiry in one tab updates others.
2. Add a safe, user-visible message when logout fails; consider clearing the CSRF cookie client-side and retrying, while acknowledging HttpOnly refresh cookies can only be cleared server-side.
3. Consider proactive refresh before access-token expiry or a countdown, while retaining the current 401 retry path.
4. Make `updateProfile` and `changePassword` use the same timeout/error normalization as login (`AUTH_TIMEOUT_MS`, `toApiError`).

### P3 — Frontend security hardening

1. Define a deployment Content-Security-Policy for the Angular app. The current `index.html` has no CSP meta tag (`frontend/src/index.html`), and the backend CSP is only `frame-ancestors 'none'`.
2. Keep access tokens out of `localStorage`/`sessionStorage` and avoid logging them.
3. Ensure no tokens or sensitive auth payloads are included in frontend telemetry.
4. Add page-level tests for login, register, and settings actions, especially profile update, password change, and logout failure.

---

## 7. Recommended implementation order

### Phase 8.2 — Repair confirmed auth-path regression

1. Fix `authInterceptor` bearer routing for `/auth/me` and `/auth/change-password`.
2. Add frontend regression tests for those routes and the 401 refresh/retry path.
3. Add backend route tests for `PATCH /auth/me` and `POST /auth/change-password` using the Bearer dependency.
4. Verify the Settings page profile and password forms end-to-end against the API.

### Phase 8.3 — Configuration and operability hardening

1. Add environment-mode validation and reject placeholder/low-entropy JWT secrets.
2. Enforce secure cookies/HSTS in production mode.
3. Add no-store headers to remaining auth responses.
4. Disable API docs in production.
5. Update `docs/api.md` and the README security instructions.

### Phase 8.4 — Abuse controls and security logging

1. Add structured auth audit logs and alerting hooks.
2. Improve rate limiting (shared store, proxy handling, stale-key eviction, refresh/password-change limits).
3. Add cleanup for expired sessions/tokens.

### Phase 8.5 — Account lifecycle features

1. Email verification.
2. Password reset/forgot-password.
3. Account deletion.
4. Active session list and logout-all.

Each of these requires schema and migration work; keep them separate from the current audit and the P0 frontend fix.

### Phase 8.6 — Frontend completion and E2E coverage

1. Forgot/reset password UI.
2. Email-verification UI.
3. Account deletion/session management UI.
4. Cross-tab session handling.
5. Playwright/Cypress end-to-end auth flows.

---

## 8. Testing strategy

### 8.1 Backend tests

**Authentication regression suite — extend `tests/test_auth.py`:**

- Register/login/logout/refresh happy paths.
- Duplicate email normalization and 409 behavior.
- Weak password rejection and password not echoed in errors.
- Generic invalid-credential behavior for unknown user and wrong password.
- Disabled-account login and authenticated-request rejection.
- Expired access token, wrong audience, wrong issuer, wrong algorithm, tampered signature, and token-type confusion (`access` used as `refresh`, and vice versa).
- Refresh rotation, reuse detection, and session revocation.
- CSRF required on refresh/logout; verify missing, mismatched, and valid cases.
- Logout revocation with Bearer-only, cookie-only, and both credentials.
- Password change revokes other sessions and leaves the current session valid.
- `PATCH /auth/me` succeeds with Bearer and updates only the current user.
- `POST /auth/change-password` succeeds with Bearer and rejects a wrong current password.
- Cross-user isolation for CV analyses, saved matches, settings, and all history-detail endpoints.
- Rate limits for login IP/account, registration, global API, refresh, and password change.
- `Retry-After` presence and sensible value.
- Security headers and credentialed CORS.
- `Cache-Control: no-store` on all auth responses that return tokens/PII.
- Placeholder/low-entropy JWT secret rejection once implemented.
- Production-mode cookie/HSTS validation once implemented.
- Session/token cleanup removes only expired/revoked rows.
- Password reset/email verification tests once those features exist.

**Ownership and authorization matrix:**

| Actor | Resource | Expected |
|---|---|---|
| User A | User A CV analysis | 200 |
| User A | User B CV analysis ID | 404 |
| User A | User A saved match | 200 |
| User A | User B saved match ID | 404 |
| Unauthenticated | Any protected endpoint | 401 |
| Disabled user | Any protected endpoint | 401 |
| User A | Change User B settings via payload | Not possible; no user ID accepted |

### 8.2 Frontend tests

**Interceptor tests:**

- Bearer attached to `/jobs`, `/cv`, `/matches`, `/account`.
- Bearer **not** attached to `/auth/login`, `/auth/register`, `/auth/refresh`.
- Bearer **is** attached to `/auth/me` and `/auth/change-password` (new regression tests).
- 401 on a protected call triggers exactly one refresh and retries with the new token.
- Refresh failure clears the session and redirects to `/login` with `reason=session-expired`.
- No infinite retry loop after a retried request also returns 401.
- Logout sends its manually constructed Authorization/CSRF headers.

**Guard and app tests:**

- Unauthenticated user is redirected from protected routes with `returnUrl`.
- Authenticated user is redirected away from login/register.
- Startup initialization treats missing CSRF cookie as signed out.
- Session restoration hydrates settings and latest CV.
- Session-expired login page displays the expected message.

**Page tests (currently missing):**

- Login form validation, successful navigation, API error display.
- Register form validation, password mismatch, successful navigation.
- Settings profile update sends `PATCH /auth/me` with Bearer.
- Settings password change sends `POST /auth/change-password` with Bearer.
- Settings logout navigates to `/login` and clears workspace.
- 429 responses display a retry message.
- Logout network failure gives a safe error/clears local state.

### 8.3 Security-focused manual/automated checks

1. Verify cookies in a real browser:
   - refresh cookie: `HttpOnly`, `Secure` in production, `SameSite=Strict` (or deliberate `None; Secure` for cross-site), correct path;
   - CSRF cookie: readable by JS, correct path, no `HttpOnly`.
2. Verify `Authorization` header behavior for every `/auth/*` route with browser dev tools.
3. Verify CORS preflight for `X-CSRF-Token` with the real frontend origin.
4. Verify that deleting/rotating a session immediately rejects the old access token.
5. Verify that a refresh-token replay revokes the session and produces a 401.
6. Verify that password reset/email-verification links are one-time, expire, and are invalidated after use (after implementation).
7. Verify rate limiting under two Uvicorn workers and behind a reverse proxy; document the shared-store requirement.
8. Verify production startup fails when `COOKIE_SECURE=false`, a placeholder JWT secret, or localhost CORS is configured.
9. Verify `/docs`, `/redoc`, and `/openapi.json` are disabled or protected in production.
10. Verify CSP and other security headers in the deployed frontend/API.

### 8.4 CI gate

Run on every change:

```bash
# backend
cd ai-engine
venv/Scripts/python.exe -m pytest -q

# frontend
cd frontend
npm test -- --watch=false
npm run build
```

Add a lint/typecheck step if the project adopts one. The current backend auth suite passes 12/12 and the frontend suite passes 39/39, but neither currently covers the confirmed C1 regression.

---

## Recommended next implementation task

**Phase 8.2 — Fix the frontend authentication request routing and lock it down with regression tests.**

Specifically:

1. Update `authInterceptor` so only anonymous auth endpoints skip Bearer injection; ensure `PATCH /api/v1/auth/me` and `POST /api/v1/auth/change-password` send `Authorization: Bearer <access_token>`.
2. Add frontend tests covering those two protected calls, plus the existing login/register/refresh exclusions and the 401 refresh/retry path.
3. Add backend tests for `PATCH /auth/me` and `POST /auth/change-password` through the real Bearer dependency.
4. Verify the Settings page profile and password forms end-to-end.

This is the highest-priority next task because it repairs a confirmed broken user-facing authentication feature without requiring schema changes, new dependencies, or a broad redesign.
