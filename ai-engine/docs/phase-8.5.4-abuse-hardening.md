# Phase 8.5.4 — Abuse Hardening & Usage Quotas

## Scope

This phase closes the production-readiness findings F3, F12, and F17 without
changing authentication semantics, CV parsing, matching algorithms, or the LLM
architecture:

- Account-wide login failure budget independent of client IP.
- Per-user usage quotas for CPU-heavy CV and matching operations.
- A separate per-user quota for remote LLM analysis.
- Local common-password screening at registration and password change.
- Audit events for account-wide rate-limit rejections, quota rejections,
  repeated matching attempts, remote LLM quota rejections, and common-password
  rejections.

Email verification, password reset, account deletion, and distributed quota
storage were not added.

## Configuration

New `APISettings` fields and environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `USER_QUOTA_CV_ANALYSIS_ATTEMPTS` | `30` | CV analysis/upload attempts per user/window; `0` disables |
| `USER_QUOTA_CV_ANALYSIS_WINDOW_SECONDS` | `3600` | CV analysis quota window |
| `USER_QUOTA_JOB_MATCH_ATTEMPTS` | `60` | Job-match attempts per user/window; `0` disables |
| `USER_QUOTA_JOB_MATCH_WINDOW_SECONDS` | `3600` | Job-match quota window |
| `USER_QUOTA_RECOMMENDATIONS_ATTEMPTS` | `30` | Recommendation attempts per user/window; `0` disables |
| `USER_QUOTA_RECOMMENDATIONS_WINDOW_SECONDS` | `3600` | Recommendation quota window |
| `USER_QUOTA_REMOTE_LLM_ATTEMPTS` | `20` | Remote LLM attempts per user/window; `0` disables |
| `USER_QUOTA_REMOTE_LLM_WINDOW_SECONDS` | `3600` | Remote LLM quota window |
| `COMMON_PASSWORD_CHECK_ENABLED` | `true` | Enable the local common-password blocklist |

Existing `LOGIN_RATE_LIMIT_ATTEMPTS` and `LOGIN_RATE_LIMIT_WINDOW_SECONDS` now
govern the account-wide login budget. The per-IP login limit remains 4x the
account limit. All new numeric values are bounded and invalid values fail
startup with `APIConfigurationError`.

## Account-wide login budget

Before this phase, the login account key was `email:IP`. The account budget is
now keyed by the pseudonymized normalized email only, using
`pseudonymize_identifier()`. The existing per-IP limit is still applied first.

- Both limits are sliding-window limits.
- A successful login resets the account budget.
- The account budget is shared across all source IPs, so IP rotation does not
  reset it.
- Unknown emails, wrong passwords, and disabled accounts continue to return the
  same `401 invalid_credentials` response.
- Account-wide rejections use the existing `auth.rate_limit.rejected` event with
  scope `login-account`.
- Registration account keys now also use the pseudonymized email plus IP,
  preserving the existing registration behavior without storing the raw email in
  the rate-limit store.

The account budget is time-window based, not a permanent lock. It cannot be used
to lock an account permanently through failed login attempts.

## Per-user usage quotas

`app/core/user_quota.py` provides `enforce_user_quota()`, which uses the existing
`SlidingWindowRateLimiter` with keys of the form
`quota:<operation>:<pseudonymized-user-id>`.

Quotas are applied to:

- `POST /api/v1/cv/analyze` and `POST /api/v1/cv/analyze/upload`:
  `cv_analysis`.
- `POST /api/v1/matches/job`: `job_match`.
- `POST /api/v1/matches/recommendations`: `recommendations`.
- Remote LLM analysis within `POST /api/v1/matches/job`: `remote_llm`, applied
  only when a provider is available and both the request and server opt-ins are
  enabled.

Quota checks run after authentication and request validation, so invalid input
does not consume quota. CV upload validation and extraction happen before the
quota check, so unsupported or corrupt uploads are rejected without consuming
analysis quota. History, settings, and job-catalog reads are not quota-limited.

Rejections return `429 usage_quota_exceeded` with `Retry-After`. The limiter
check is atomic under a process lock, so concurrent requests cannot trivially
exceed a single-process limit.

Audit events:

- `security.quota.rejected` for every quota rejection.
- `security.matching.repeated` for job-match and recommendation quota
  rejections.
- `security.llm.quota.rejected` for remote LLM quota rejections.

Quota audit metadata contains only the operation and retry delay. It does not
include quota limits, CV content, prompts, responses, tokens, or provider keys.

## Common-password screening

`app/auth/common_passwords.py` contains a local blocklist of common passwords and
common password fragments. `is_common_password()` compares the case-folded
password, a non-alphanumeric-stripped form, and a small leetspeak-normalized
form. This is a lightweight local control, not a full strength estimator or
breach-corpus lookup.

Registration and password change call the check through `AuthService`, after
structural length/character-class validation. A rejected password returns
`422 password_too_common` with the message `Choose a less common password.`
Rejections emit `auth.password.common_rejected` with action `registration` or
`password_change`. Passwords are never logged.

`COMMON_PASSWORD_CHECK_ENABLED=false` disables the check. This should only be
used when an external password-strength service provides equivalent coverage.

## Tests

New coverage in `tests/test_abuse_hardening.py`:

- Login account limit is enforced across rotating client IPs.
- Successful login resets the account budget.
- Common passwords are rejected at registration and password change and produce
  audit events without leaking the password.
- Common-password checking can be disabled.
- CV, job-match, recommendation, and remote LLM quotas return `429` with
  `Retry-After` and emit the expected audit events.
- Validation failures do not consume quota.
- Quotas are isolated per user.
- A quota limit of `0` disables that quota.
- The sliding-window limiter enforces its limit under concurrent access.
- New quota and common-password settings are bounded.
- `password_strength_errors()` includes common-password screening while
  `password_format_errors()` remains the structural schema check.

## Verification

- Backend: `196 passed, 1 skipped`.
- Frontend: `49 passed` (9 files).
- Frontend production build: successful.

## Limitations and unverified behavior

- Rate limits and quotas are process-local and in-memory. Multiple workers or
  instances have separate counters, so the effective account budget and quotas
  are multiplied across processes. Do not describe this as distributed quota
  enforcement. Use Redis or another shared store before relying on these limits
  across a multi-worker deployment. The limiter caps its key count and evicts
  least-recently-used buckets under memory pressure, which can also drop old
  counters.
- PostgreSQL was not available in this environment. The quota implementation uses
  no database tables, so database-specific behavior is limited to the existing
  auth/session paths, which were not re-verified against PostgreSQL here.
- The common-password blocklist is intentionally small and local. It does not
  detect all breached passwords and does not replace a dedicated password
  strength service.
- The frontend still displays the generic API error message for quota and
  rate-limit responses; it does not yet surface `Retry-After` as a countdown or
  dedicated UX state.
