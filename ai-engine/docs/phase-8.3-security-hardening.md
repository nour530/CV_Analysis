# Phase 8.3 — Configuration and Security Hardening

## Scope

This phase reassessed the authentication, authorization, and deployment security
findings from Phase 8.1 and implemented the highest-priority confirmed issues:

- Reject placeholder and low-entropy JWT secrets.
- Add an explicit runtime environment with production startup guards.
- Require secure cookies, HSTS, and HTTPS CORS origins in production.
- Disable API documentation by default in production.
- Add `Cache-Control: no-store` to all auth responses, including errors.
- Add regression tests for configuration guards, response caching, user-scoped
  profile updates, and client-supplied user-ID rejection.
- Update `.env.example`, `README.md`, and `ai-engine/docs/api.md`.

Phase 8.2 had already fixed the confirmed Angular interceptor bug for
`/auth/me` and `/auth/change-password`.

## Reassessment

### Confirmed issues implemented

| ID | Priority | Finding | Resolution |
| --- | --- | --- | --- |
| C2 | Medium | Repository JWT placeholders passed the 32-character check | Placeholder values are rejected outside `APP_ENV=test`; production also requires sufficient character diversity |
| C3 | Medium | Insecure cookie/HSTS defaults could reach production | `APP_ENV=production` now requires `COOKIE_SECURE=true` and `ENABLE_HSTS=true` |
| C7 | Low | Auth/account responses lacked consistent no-store caching | Middleware now sets `Cache-Control: no-store` on all `/auth` responses, including errors |
| C8 | Low | `/docs`, `/redoc`, and `/openapi.json` were always exposed | Documentation endpoints are disabled by default in production |

Additional production configuration guard:

- `APP_ENV` accepts only `development`, `test`, or `production`.
- Production `CORS_ORIGINS` must use HTTPS and must not contain localhost.
- Production startup validates auth settings up front instead of waiting for the
  first auth request.
- `ENABLE_API_DOCS=true` remains available as an explicit opt-in for deployments
  that protect the docs externally.

### Confirmed issues intentionally deferred

These remain real issues but are outside the highest-priority configuration and
response-caching scope:

- **C4 — Rate limiting:** still process-local, proxy-unaware, and lacks stale-key
  eviction. Multi-worker deployments multiply the effective limit.
- **C5 — Account enumeration:** registration `409` and disabled-account `403`
  still reveal account state.
- **C6 — Auth audit logging:** login failures, refresh reuse, password changes,
  lockouts, and session revocation are not centrally logged or alerted.
- **C9 — Logout failure:** a failed logout request can leave the server session
  and HttpOnly refresh cookie active.
- **C10 — Password policy:** common/breached passwords such as `Password123!`
  still pass the length and character-class checks.

### Potential risks deferred

- Multi-tab refresh rotation race can trigger reuse detection.
- No JWT key rotation / `kid` overlap support.
- No expired session/refresh-token cleanup job.
- No active-session management, logout-all, password reset, email verification,
  or account deletion.
- Frontend deployment CSP/Trusted Types are still not defined in this repository.
- Refresh endpoint has no dedicated per-session/IP limit beyond the global API
  limit.

## Authorization Reassessment

No authorization bypass was found in the reviewed routes.

- Protected routes obtain the user from `get_auth_context` / `get_current_user`.
- CV, CV history, saved matches, and account settings filter by the authenticated
  `user_id`.
- New regression coverage confirms that a client-supplied `user_id` is rejected
  with `422` rather than trusted.
- New regression coverage confirms that profile updates and password changes only
  affect the authenticated user.

## Implementation

### Runtime environment and JWT hardening

- `ai-engine/app/api/settings.py`
  - Added `APP_ENV` parsing and validation.
  - Added production CORS validation: HTTPS only, no localhost.
  - Added `ENABLE_API_DOCS`, defaulting to `false` in production.
  - Added `enable_hsts` enforcement in production.
- `ai-engine/app/auth/settings.py`
  - Added placeholder-secret rejection for non-test environments.
  - Added production entropy validation.
  - Added production `COOKIE_SECURE=true` enforcement.
  - Added the runtime environment to `AuthSettings`.

### Application startup and response headers

- `ai-engine/app/main.py`
  - Validates auth settings during app creation.
  - Disables `/docs`, `/redoc`, and `/openapi.json` when API docs are disabled.
  - Adds `Cache-Control: no-store` to every `/api/v1/auth` response, including
    validation, authentication, CSRF, and rate-limit errors.

### Tests

- `ai-engine/tests/test_security_settings.py`
  - Placeholder secrets are rejected.
  - Production requires secure cookies and sufficient secret entropy.
  - Production requires HTTPS/non-localhost CORS and HSTS.
  - Invalid `APP_ENV` values are rejected.
  - Production app creation disables documentation endpoints.
- `ai-engine/tests/test_auth.py`
  - Unauthorized and CSRF-failed auth responses are not cached.
  - Profile updates and password changes are user-scoped and not cached.
  - Client-supplied `user_id` values are rejected.

### Documentation and configuration examples

- `.env.example`: documents `APP_ENV`, production requirements, placeholder
  rejection, and API docs behavior.
- `README.md`: adds a safe secret-generation command and production checklist.
- `ai-engine/docs/api.md`: documents authentication, auth endpoints, production
  security requirements, and removes the stale “no authentication” statement.

## Verification

Commands run from the repository:

```bash
cd ai-engine
venv/Scripts/python.exe -m pytest tests -q
```

Result: `121 passed, 1 skipped`.

Focused suite:

```bash
cd ai-engine
venv/Scripts/python.exe -m pytest tests/test_security_settings.py tests/test_auth.py -q
```

Result: `19 passed`.

Frontend regression checks after the Phase 8.2 interceptor change:

```bash
cd frontend
npm test -- --watch=false
npm run build
```

Results:

- `9` test files passed, `44` tests passed.
- Production build completed successfully.

## Residual Production Guidance

- Set `APP_ENV=production` explicitly.
- Generate `JWT_SECRET_KEY` with a cryptographically secure source and keep it
  outside source control.
- Terminate TLS before the API and set `COOKIE_SECURE=true` and
  `ENABLE_HSTS=true`.
- Use explicit HTTPS origins in `CORS_ORIGINS`; localhost and wildcards are
  rejected in production.
- Leave `ENABLE_API_DOCS` unset in production unless an external access-control
  layer protects the documentation endpoints.
- Replace the process-local rate limiter with a shared store before running
  multiple workers or instances.
- Add structured auth audit logging and session/token cleanup in the next
  security phase.
