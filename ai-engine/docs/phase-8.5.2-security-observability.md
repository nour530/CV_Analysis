# Phase 8.5.2 - Security Observability and Audit Logging

Date: 2026-11-11

## 1. Architecture and audit-event design

Security audit logging is implemented in `ai-engine/app/core/audit.py` and uses
the Python standard library `logging` hierarchy. It does not introduce a second
logging framework, a new dependency, a background worker, or a database audit
table.

```text
auth route / service
        |
        v
audit_event(...)                     app/core/audit.py
        |
        +--> AuditEvent              normalized, immutable event
        |       |
        |       +--> to_payload()   JSON-safe, redacted payload
        |               |
        |               v
        |       logging.getLogger("jobmatch.audit").info(<json>)
        |
        +--> SecurityAlertDispatcher.observe(event)
                |
                +--> threshold / window / cooldown state (per process)
                        |
                        v
                logging.getLogger("jobmatch.security.alert").warning(<json>)
```

Design properties:

- **Single interface.** Callers use `audit_event(event_type, outcome, ...)`.
  They never build log strings, and they never decide what is safe to log.
- **Structured payloads.** Each event is serialized as one JSON object with
  `component`, `event_type`, `timestamp`, `outcome`, `request_id`, optional
  `user_id` and `client_ip`, and optional `metadata`.
- **Standard logging integration.** Events flow through named loggers
  (`jobmatch.audit` and `jobmatch.security.alert`) so existing handlers,
  formatters, shippers, and log platforms continue to work.
- **Best-effort isolation.** Serialization, logger, and alert-sink failures are
  swallowed. A broken audit sink cannot turn a successful login into a `500`.
- **Bounded metadata.** Payload depth, key count, string length, and collection
  size are capped to prevent log flooding from request-controlled data.
- **Testable alert state.** `SecurityAlertDispatcher` accepts a clock and sink
  list, so thresholds and cooldowns are tested deterministically without
  sleeping or using external services.

`app/core/logging.py` now exposes `configure_security_logging()`. When a
deployment or test harness has not configured any root handler, it attaches one
stream handler to the two security loggers and sets their level from
`AUDIT_LOG_LEVEL` (default `INFO`). When root handlers already exist, it does not
override deployment logging configuration. `create_app()` calls this helper
once at startup.

## 2. Event catalog

Outcomes are `success` or `failure`. Every event includes `event_type`,
`timestamp`, `request_id`, and `outcome`. `user_id` is included only when a
resolved user is available and safe to record.

| Event type | Outcome | Source | Notes |
| --- | --- | --- | --- |
| `auth.registration.requested` | success | `POST /auth/register` | Records `metadata.account_ref`, never the email. |
| `auth.registration.succeeded` | success | `POST /auth/register` | Includes new `user_id` and `account_ref`. |
| `auth.registration.duplicate` | failure | `POST /auth/register` | Neutral response is preserved; no account details leak. |
| `auth.registration.failed` | failure | `POST /auth/register` | Includes `metadata.reason` (validation/service code). |
| `auth.login.succeeded` | success | `POST /auth/login` | Includes `user_id` and `account_ref`. |
| `auth.login.failed` | failure | `POST /auth/login` | Generic `invalid_credentials`; no `user_id`. |
| `auth.login.disabled` | failure | `POST /auth/login` | Includes `user_id` for operators; response stays generic. |
| `auth.rate_limit.rejected` | failure | Auth routes | Includes `metadata.scope` and optional `retry_after_seconds`. |
| `auth.refresh.succeeded` | success | `POST /auth/refresh` | Includes `user_id`. |
| `auth.refresh.failed` | failure | `POST /auth/refresh` | Includes safe `metadata.reason`. |
| `auth.refresh.reuse_detected` | failure | `POST /auth/refresh` | Refresh-token reuse; triggers alert hook. |
| `auth.session.revoked` | success | refresh/logout | Includes `metadata.reason`. |
| `auth.session.mass_revoked` | success | password change | Includes `metadata.revoked_sessions`; triggers alert hook. |
| `auth.logout.succeeded` | success | `POST /auth/logout` | Includes `user_id` when the access token was valid. |
| `auth.logout.failed` | failure | `POST /auth/logout` | Currently emitted for CSRF failure. |
| `auth.csrf.failed` | failure | refresh/logout | Includes `metadata.action`. |
| `auth.password_change.succeeded` | success | `POST /auth/change-password` | Includes `user_id`. |
| `auth.password_change.failed` | failure | `POST /auth/change-password` | Includes safe `metadata.reason`. |

Registration/login response bodies are unchanged. Failed authentication events
never record whether an email belongs to an existing account in a way that is
visible to the caller. Disabled-account events are distinguishable only in
server-side logs.

## 3. Request ID implementation

`app/core/request_context.py` owns request-ID normalization and context
propagation:

- Accepted IDs match `^[A-Za-z0-9._-]{1,120}$`. Whitespace, control characters,
  newlines, slashes, and over-long values are rejected.
- `app/main.py` reads `X-Request-ID`, uses the sanitized value when valid, and
  otherwise generates `uuid.uuid4().hex`.
- The resolved ID is stored on `request.state.request_id` and in a
  `ContextVar`, so route handlers, services, and audit events in the same
  request all see the same value.
- The ID is returned on every response as `X-Request-ID`; the CORS configuration
  exposes that header to browser clients.
- Error payloads also include `request_id`.
- `install_request_id_log_attribute()` attaches `record.request_id` to every
  `LogRecord`, so ordinary application logs can be correlated with audit
  events.
- Only one ID is generated per request. Audit events read the request state or
  context value and never create a new ID.

## 4. Redaction strategy

Redaction is centralized in `app/core/audit.py` and applied to every audit
payload before serialization. It has three layers:

1. **Key-based redaction.** Any metadata key containing one of the sensitive
   fragments is replaced with `[REDACTED]`. Fragments include password,
   credential, secret, token, jwt, authorization, cookie, csrf, api_key,
   session_id, prompt, response, resume, cv_content, cv_text, email, phone, and
   ssn.
2. **Value-based redaction.** Remaining strings have JWT-like values, bearer
   values, email addresses, and long opaque tokens replaced with
   `[REDACTED_TOKEN]` or `[REDACTED_EMAIL]`. Long strings are truncated.
3. **Structural limits.** Nested mappings and collections are capped, depth is
   capped, and unsupported values become `[UNSUPPORTED]`.

Identifiers are treated differently from free text. `request_id`, `user_id`,
and `client_ip` are length-limited and stripped of non-printable characters but
are not passed through token heuristics. Email addresses are never logged
directly; routes use `pseudonymize_identifier(normalized_email)`, a truncated
SHA-256 reference, as `metadata.account_ref`.

The following are never written to audit logs:

- Passwords or password hashes.
- Access tokens, refresh tokens, JWT secrets, or CSRF tokens.
- Full CV contents, resume text, or uploaded document bytes.
- LLM prompts or responses.
- Password-reset or email-verification tokens (those flows do not exist yet).

Regression tests assert that these values do not appear in captured log output.

## 5. Alert architecture

`SecurityAlertDispatcher` is an in-process, extensible hook layer. It is not an
external monitoring integration and sends no email or third-party
notifications.

| Category | Trigger event | Default threshold | Window | Cooldown |
| --- | --- | --- | --- | --- |
| `login_failures` | `auth.login.failed`, `auth.login.disabled` | 5 | 300 s | 300 s |
| `refresh_token_reuse` | `auth.refresh.reuse_detected` | 1 | 300 s | 300 s |
| `csrf_failures` | `auth.csrf.failed` | 5 | 300 s | 300 s |
| `rate_limit_rejections` | `auth.rate_limit.rejected` | 10 | 60 s | 120 s |
| `mass_session_revocation` | `auth.session.mass_revoked` | 1 | 300 s | 300 s |

Behavior:

- Each category keeps a sliding window of timestamps.
- An alert is emitted only when the threshold is reached.
- After an alert, the category enters a cooldown and the window is cleared, so a
  sustained attack does not produce one alert per event.
- Alert payloads are passed through the same sanitizer as audit events.
- Default sinks log a `WARNING` JSON object on `jobmatch.security.alert`.
- Operators can add a future sink with
  `SECURITY_AUDIT.dispatcher.register_sink(callable)` without changing the
  authentication code.
- Sink exceptions are isolated and cannot break authentication.
- State is per process, consistent with the existing per-process rate limiter.

## 6. Retention and access policy

### Storage

Events are written as JSON lines to the standard logging stream of the API
process (`stdout`/`stderr` by default). There is no audit database table. The
existing architecture already emits operational logs this way, and a database
table would add write contention, migration surface, and retention obligations
without a current operational requirement. Deployments are expected to ship
the stream to their normal log platform (for example, container log collection
to a SIEM or centralized log store).

### Operational logs vs permanent audit records

- **Operational logs** are the process output used for debugging and day-to-day
  monitoring. They may be rotated and discarded.
- **Permanent audit records** are the security-event subset copied to a
  tamper-resistant, access-controlled store (for example, a SIEM with immutable
  retention). This phase provides the structured source; it does not provide the
  permanent store. Deployments that require compliance evidence must configure
  shipping and immutable retention outside the application.

### Recommended retention

| Record class | Suggested retention | Notes |
| --- | --- | --- |
| Application/operational logs | 30 days hot, up to 90 days cold | Debugging and capacity analysis. |
| Security audit events | 180 days | Adjust to organizational policy or regulation. |
| Authentication failure/reuse alerts | 1 year | Useful for incident review and abuse trends. |
| Permanent incident evidence | Per legal/compliance policy | Export from the centralized store, not the live process. |

The application does not delete log records itself. Retention is enforced by the
log platform or the file rotation policy. A future cleanup job for database
sessions/tokens is separate and is not part of this phase.

### Access

- Audit output must be readable only by authorized operators through the log
  platform's role-based access control.
- The API process should not expose log files over HTTP. No log-read endpoint
  exists.
- Support staff should use `request_id`, `user_id`, `account_ref`, event type,
  and timestamp to investigate. They should not need email addresses or tokens.
- `user_id` and `client_ip` are present in audit events and are personal data.
  Apply the same access controls and data-handling rules as other operational
  telemetry.

### Export for incident investigation

Export the JSON lines for the required time range and filter fields from the
central log store. Example queries use `component = "security_audit"` and
`event_type` values from the catalog. Alerts use
`component = "security_alert"`. If a deployment has no central store yet, it can
ship container output with its existing logging driver; the application does not
add an export endpoint or file writer.

## 7. Modified files

Backend:

- `ai-engine/app/core/audit.py` (new) - audit event model, redaction, alert
  dispatcher, logger integration.
- `ai-engine/app/core/request_context.py` (new) - request-ID normalization and
  context storage.
- `ai-engine/app/core/logging.py` - request-ID log attribute and security
  logger configuration.
- `ai-engine/app/main.py` - request-ID middleware, response header, security
  logging startup.
- `ai-engine/app/api/routes/auth.py` - audit emission for registration, login,
  refresh, logout, CSRF, rate limits, password changes, and session revocation.
- `ai-engine/app/auth/service.py` - safe audit metadata on authentication and
  refresh errors.
- `ai-engine/tests/test_audit.py` (new) - event, redaction, request-ID, alert,
  and isolation tests.
- `ai-engine/tests/conftest.py` - resets alert state between tests.

Documentation and configuration:

- `ai-engine/docs/phase-8.5.2-security-observability.md` (new).
- `ai-engine/docs/api.md` - request correlation and audit notes.
- `README.md` - operational logging and retention notes.
- `.env.example` - `AUDIT_LOG_LEVEL`.

No frontend source change was required. The browser already receives and
exposes `X-Request-ID` through CORS.

## 8. Test results

Focused audit suite:

```text
venv/Scripts/python.exe -m pytest tests/test_audit.py -q
13 passed
```

Full backend suite:

```text
venv/Scripts/python.exe -m pytest -q
151 passed, 1 skipped
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

- every authentication event type and its success/failure outcome;
- duplicate registration and disabled-account login behavior;
- refresh success, refresh-token reuse, session revocation, logout success and
  CSRF failure;
- password change success/failure and mass session revocation;
- request-ID validation, propagation into logs, response header, and rejection
  of unsafe values;
- sensitive-key and sensitive-value redaction, including passwords, tokens,
  JWTs, emails, resume text, and LLM prompt/response content;
- rate-limit rejection events;
- alert thresholds, windows, cooldowns, category coverage, and sink isolation;
- logger failure and dispatcher failure not breaking authentication;
- non-destructive security logging configuration.

## 9. Remaining risks

- **Best-effort delivery.** If the logging sink is unavailable, audit events are
  dropped. Authentication stays available by design. High-assurance
  environments should ship logs to a durable external collector.
- **Per-process alert state.** Thresholds and cooldowns are not shared across
  workers or instances, consistent with the current per-process rate limiter.
  A multi-worker deployment can under-count or duplicate alerts.
- **Pseudonymization is unsalted.** `account_ref` is a truncated SHA-256 of the
  normalized email. An attacker with the logs and a candidate email list could
  test guesses. Use a deployment-specific HMAC key if pseudonymized account
  correlation must resist offline guessing.
- **No tamper-evident storage.** JSON log lines are not signed or hash-chained.
  Immutable retention and access control depend on the external log platform.
- **Retention is policy, not enforcement.** The application does not rotate,
  delete, or archive logs.
- **Global alert buckets.** Login-failure and CSRF alerts aggregate all clients
  in one process bucket. This detects broad abuse but does not distinguish a
  single targeted account from background noise.
- **Request IDs are caller-controlled.** Values are sanitized and length-capped,
  but a caller can choose a predictable ID. Treat the ID as a correlation aid,
  not as proof of origin.
- **PII in telemetry.** `user_id` and `client_ip` are deliberately included for
  investigation. They must be protected like other personal data.
- **No live PostgreSQL/multi-worker verification.** The audit code is
  process-local and dialect-independent; deployment-scale log shipping and
  alert behavior were not load tested.

## 10. Recommendations for Phase 8.5.3

1. Add scheduled cleanup and a documented retention boundary for expired and
   revoked sessions and refresh tokens.
2. Add `Cache-Control: no-store` to authenticated account, CV, and match
   responses.
3. Add a normalized-email login budget independent of source IP, while keeping
   the current IP anti-DoS controls.
4. Move rate limiting and alert deduplication to a shared store when deploying
   more than one worker.
5. Add request-body and decompression limits before expensive parsing.
6. Add JWT signing-key rotation with `kid` overlap and a documented emergency
   re-issuance procedure.
7. Integrate the alert sink interface with the organization's monitoring or SIEM
   platform and add metrics for rejected authentication, refresh reuse, and
   rate-limit events.
8. Consider a deployment-specific HMAC key for `account_ref` and an immutable
   audit store if compliance requirements demand tamper evidence.
9. Add PostgreSQL-backed concurrency tests and multi-worker log/alert behavior
   tests.
10. Then proceed to the deferred account-lifecycle work (email verification,
    password reset, account deletion, active-session management).
