# Phase 8.4 — Authentication Abuse Protection

## Objective

Harden authentication abuse protection without weakening authorization or
introducing a distributed-systems dependency:

- Add configurable limits to sensitive auth endpoints that previously had only
  the global API limit.
- Trust forwarded client IPs only when the direct peer is a configured proxy.
- Reduce account-enumeration signals.
- Bound in-memory rate-limit growth.
- Preserve legitimate registration, login, refresh, logout, profile, and
  password-change workflows.
- Cover the changes with backend and frontend regression tests.

## What changed

### Sensitive endpoint limits

All auth limits use the existing sliding-window limiter and return HTTP `429`
with `Retry-After` through the existing error contract.

| Endpoint | Limits |
| --- | --- |
| `POST /auth/register` | Per-IP and per-email/IP |
| `POST /auth/login` | Per-IP (4x) and per-email/IP |
| `POST /auth/refresh` | Per-IP (4x) and per-refresh-token hash |
| `POST /auth/change-password` | Per-IP (4x) and per-user |

A successful login resets the account/IP login bucket. A successful refresh or
password change resets the corresponding token/user bucket. Registration limits
are intentionally not reset after success, which keeps account-creation spam
bounded.

New settings:

- `REFRESH_RATE_LIMIT_ATTEMPTS` / `REFRESH_RATE_LIMIT_WINDOW_SECONDS`
- `PASSWORD_CHANGE_RATE_LIMIT_ATTEMPTS` /
  `PASSWORD_CHANGE_RATE_LIMIT_WINDOW_SECONDS`
- `RATE_LIMIT_MAX_KEYS`
- `TRUSTED_PROXY_IPS`

Existing login and registration limit settings remain compatible.

### Proxy-safe client IP handling

`TRUSTED_PROXY_IPS` accepts comma-separated exact IP addresses and CIDR ranges.
No new dependency is required; parsing uses the standard-library `ipaddress`
module.

- If the direct peer is not trusted, `X-Forwarded-For` is ignored.
- If the direct peer is trusted, the header is parsed from right to left and the
  first address outside the trusted proxy networks is selected.
- Invalid forwarded entries are skipped.
- IPv4-mapped IPv6 addresses are normalized.
- Empty configuration means use the direct socket peer only.

This prevents arbitrary clients from choosing their own rate-limit identity by
sending a forged `X-Forwarded-For` header.

### Limiter memory bounds

The in-memory limiter now stores buckets in an ordered map with:

- Periodic pruning of buckets whose last event is older than their window.
- A configurable `RATE_LIMIT_MAX_KEYS` cap.
- Least-recently-used eviction when the cap is exceeded.

This is still a per-process limiter. It is not distributed and does not share
state across workers or instances.

### Reduced account enumeration

- Login now returns the same `401 invalid_credentials` response for:
  - an unknown email,
  - a wrong password,
  - a disabled account.
- The previous disabled-account `403 account_disabled` response was removed.
- Password verification still runs for disabled accounts to avoid an obvious
  timing shortcut.
- Registration now always returns `202` with the same neutral message and never
  auto-creates a session. It no longer returns `409` for an existing email and
  does not overwrite an existing account.
- Registration keeps useful `422` validation feedback for malformed emails and
  weak passwords.

Because registration no longer returns tokens, the frontend redirects to login
with a neutral notice and prefills the submitted email. The login page displays
an "if this email was available" message rather than confirming that an account
was created.

### Password reset

There is still no password-reset endpoint. When one is added, it should use the
same neutral response pattern, per-IP and per-account limits, and generic
responses for unknown accounts.

## Residual risks and deployment notes

- Combined registration-then-login probing can still reveal account existence
  to someone willing to submit login attempts. Full mitigation requires email
  verification or delayed account activation, which is not implemented.
- Registration still hashes the password before checking for an existing
  account, but duplicate requests skip database writes, so a timing side channel
  may remain. The neutral status and response body remove the direct oracle.
- The limiter remains per-process. A multi-worker or multi-instance deployment
  needs a shared store such as Redis for consistent enforcement across workers.
- `TRUSTED_PROXY_IPS` must match the actual reverse-proxy peers. Never trust
  arbitrary internet-facing proxies. Leaving it empty is correct when clients
  connect directly.
- Disabled accounts deliberately fail with the same login response as invalid
  credentials; operational support should use server-side account state, not
  login responses, to diagnose disabled users.
- No audit logging, JWT signing-key rotation, expired-token cleanup, active
  session management, or email verification was added in this phase.

## Verification

Backend full suite:

```text
venv/Scripts/python.exe -m pytest tests -q
133 passed, 1 skipped
```

Focused auth and rate-limit suite:

```text
venv/Scripts/python.exe -m pytest tests/test_rate_limits.py tests/test_auth.py -q
26 passed
```

Frontend:

```text
npm test -- --watch=false
9 test files passed, 45 tests passed

npm run build
Application bundle generation complete
```

The Node runtime emitted the existing warning that Node 25 is not an LTS
release; it did not fail the tests or build.

## Files modified

- `.env.example`
- `README.md`
- `ai-engine/app/api/auth_schemas.py`
- `ai-engine/app/api/dependencies.py`
- `ai-engine/app/api/routes/auth.py`
- `ai-engine/app/api/settings.py`
- `ai-engine/app/auth/service.py`
- `ai-engine/app/core/rate_limit.py`
- `ai-engine/app/main.py`
- `ai-engine/docs/api.md`
- `ai-engine/tests/conftest.py`
- `ai-engine/tests/test_auth.py`
- `ai-engine/tests/test_rate_limits.py` (new)
- `frontend/src/app/core/api.models.ts`
- `frontend/src/app/core/auth.service.ts`
- `frontend/src/app/core/auth.service.spec.ts`
- `frontend/src/app/pages/login/login.page.ts`
- `frontend/src/app/pages/login/login.page.html`
- `frontend/src/app/pages/register/register.page.ts`
- `ai-engine/docs/phase-8.4-auth-abuse-protection.md` (new)
