# JobMatch AI FastAPI service

The API is a thin FastAPI layer over the existing hybrid matching engine. It adds
HTTP request validation, job-catalog access, structured errors, CORS, and optional
authorized LLM explanations. It does not reimplement or modify local scoring.

## Architecture

```text
app/main.py
  -> request ID middleware + CORS + exception handlers
  -> app/api/router.py
       -> routes/health.py
       -> routes/cv.py
       -> routes/jobs.py
       -> routes/matching.py
            -> MatchingApplicationService
                 -> JobCatalog
                 -> HybridMatchingService
                      -> LocalMatcher + LocalSkillExtractor
                      -> optional LLMProvider (authorized only)
```

Key files:

- `app/main.py`: application factory, middleware, router wiring.
- `app/api/settings.py`: environment-backed API settings.
- `app/api/schemas.py`: request and response models.
- `app/api/errors.py`: `APIError` plus structured validation/HTTP/domain errors.
- `app/api/dependencies.py`: cached settings, catalog, hybrid service, LLM provider.
- `app/api/uploads.py`: upload byte limits plus extraction error mapping.
- `app/services/documents/extraction.py`: TXT, PDF, and DOCX validation and text extraction.
- `app/services/job_catalog.py`: lazy, cached catalog over `job_features.csv`.
- `app/services/matching/application.py`: CV analysis, job matching, ranking.

Business logic stays in the application service. Route handlers only validate
transport concerns and map results to API schemas.

## Run locally

```bash
cd ai-engine
venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Swagger UI: <http://127.0.0.1:8000/docs>
ReDoc: <http://127.0.0.1:8000/redoc>
OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>

The first catalog request loads and canonicalizes `job_features.csv` once per
process (about 2 seconds for the current 10,461-row table). Later requests use
the cached catalog.

## Configuration

Copy the repository `.env.example` to `.env` or export the values directly.
FastAPI does not load `.env` automatically; use your shell, IDE, Docker Compose,
or a process manager.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `development` | Runtime mode: `development`, `test`, or `production` |
| `API_TITLE` | `JobMatch AI API` | OpenAPI title |
| `API_VERSION` | `1.0.0` | OpenAPI version |
| `API_PREFIX` | `/api/v1` | Versioned route prefix |
| `JOBS_FEATURES_PATH` | `data/features/job_features.csv` | Job catalog source |
| `CORS_ORIGINS` | `http://localhost:4200` | Comma-separated allowed origins |
| `MAX_TEXT_LENGTH` | `200000` | Text limit; schemas also cap at `200000` |
| `MAX_UPLOAD_BYTES` | `5000000` | Upload byte limit |
| `MAX_REQUEST_BODY_BYTES` | `8000000` | Maximum HTTP request body bytes; larger bodies return `413` |
| `MAX_PDF_PAGES` | `100` | Maximum pages extracted from an uploaded PDF |
| `MAX_DOCX_ENTRIES` | `2000` | Maximum entries in an uploaded DOCX archive |
| `MAX_DOCX_UNCOMPRESSED_BYTES` | `50000000` | Maximum declared uncompressed DOCX size |
| `MAX_DOCX_COMPRESSION_RATIO` | `200` | Maximum DOCX compressed-to-uncompressed ratio |
| `SESSION_CLEANUP_RETENTION_SECONDS` | `2592000` | Retention for expired/revoked sessions |
| `REFRESH_TOKEN_CLEANUP_RETENTION_SECONDS` | `2592000` | Retention for stale refresh tokens |
| `DEFAULT_RECOMMENDATION_LIMIT` | `10` | Default ranked jobs returned |
| `MAX_RECOMMENDATION_LIMIT` | `50` | Maximum ranked jobs allowed |
| `DEFAULT_CANDIDATE_POOL_SIZE` | `30` | Default jobs evaluated fully |
| `MAX_CANDIDATE_POOL_SIZE` | `200` | Maximum candidate pool |
| `API_RATE_LIMIT_REQUESTS` | `120` | Global API requests allowed per IP/window; `0` disables |
| `API_RATE_LIMIT_WINDOW_SECONDS` | `60` | Global API rate-limit window |
| `LOGIN_RATE_LIMIT_ATTEMPTS` | `5` | Login attempts per account/window; per-IP limit is 4x |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `300` | Login rate-limit window |
| `USER_QUOTA_CV_ANALYSIS_ATTEMPTS` | `30` | Per-user CV analysis/upload attempts; `0` disables |
| `USER_QUOTA_CV_ANALYSIS_WINDOW_SECONDS` | `3600` | CV analysis quota window |
| `USER_QUOTA_JOB_MATCH_ATTEMPTS` | `60` | Per-user job-match attempts; `0` disables |
| `USER_QUOTA_JOB_MATCH_WINDOW_SECONDS` | `3600` | Job-match quota window |
| `USER_QUOTA_RECOMMENDATIONS_ATTEMPTS` | `30` | Per-user recommendation attempts; `0` disables |
| `USER_QUOTA_RECOMMENDATIONS_WINDOW_SECONDS` | `3600` | Recommendation quota window |
| `USER_QUOTA_REMOTE_LLM_ATTEMPTS` | `20` | Per-user remote LLM attempts; `0` disables |
| `USER_QUOTA_REMOTE_LLM_WINDOW_SECONDS` | `3600` | Remote LLM quota window |
| `COMMON_PASSWORD_CHECK_ENABLED` | `true` | Reject local common/blocklisted passwords |
| `REGISTRATION_RATE_LIMIT_ATTEMPTS` | `5` | Registration attempts per IP and per email/IP/window |
| `REGISTRATION_RATE_LIMIT_WINDOW_SECONDS` | `3600` | Registration rate-limit window |
| `REFRESH_RATE_LIMIT_ATTEMPTS` | `30` | Refresh attempts per token/window; IP limit is 4x |
| `REFRESH_RATE_LIMIT_WINDOW_SECONDS` | `60` | Refresh rate-limit window |
| `PASSWORD_CHANGE_RATE_LIMIT_ATTEMPTS` | `5` | Password-change attempts per user/window; IP limit is 4x |
| `PASSWORD_CHANGE_RATE_LIMIT_WINDOW_SECONDS` | `300` | Password-change rate-limit window |
| `RATE_LIMIT_MAX_KEYS` | `20000` | Maximum in-memory rate-limit buckets per process |
| `RATE_LIMIT_BACKEND` | `auto` | `auto`, `memory`, or `redis` shared limiter backend |
| `REDIS_URL` | unset | Redis URL; required in production when backend is `auto` |
| `REDIS_KEY_PREFIX` | `jobmatch:rate_limit:` | Prefix for shared Redis keys |
| `RATE_LIMIT_REDIS_FAILURE_MODE` | `closed` | Fail closed (`503`) or open when Redis is unavailable; production requires `closed` |
| `TRUSTED_PROXY_IPS` | empty | Comma-separated proxy IPs/CIDRs allowed to supply `X-Forwarded-For` |
| `ALLOW_REMOTE_LLM_ANALYSIS` | `false` | Server-level OpenAI consent gate |
| `JWT_SECRET_KEY` | required | Access/refresh signing key; 32+ chars, placeholders rejected |
| `COOKIE_SECURE` | `false` | Must be `true` when `APP_ENV=production` |
| `ENABLE_HSTS` | `false` | Must be `true` when `APP_ENV=production` |
| `ENABLE_API_DOCS` | `true` in development/test, `false` in production | Expose `/docs`, `/redoc`, and `/openapi.json` |

Existing LLM variables (`LLM_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_MODEL`,
`OPENAI_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`) are reused.

Production startup fails if `COOKIE_SECURE=false`, `ENABLE_HSTS=false`, a CORS
origin is not HTTPS or is localhost, or `JWT_SECRET_KEY` is a documented
placeholder or has insufficient entropy.

## Authentication and security

Access tokens are short-lived bearer tokens. Refresh tokens are stored in an
HttpOnly, SameSite cookie and rotated on every refresh; reuse revokes the
session. Refresh and logout requests require the CSRF header and cookie pair;
password change is protected by the bearer token. Protected account, CV, match,
and settings routes derive the user from the authenticated token and never
accept a client-supplied user ID.

Registration returns `202` with a neutral message and does not create a session;
sign in afterward to obtain tokens. Login returns the same
`401 invalid_credentials` response for unknown emails, wrong passwords, and
disabled accounts. Sensitive auth endpoints enforce sliding-window limits per
IP and per account, token, or user, and return `429` with `Retry-After` when
exceeded. The login account budget is keyed by a pseudonymized email and is
independent of client IP, so rotating IPs does not reset it. A successful login
resets the account budget. `X-Forwarded-For` is trusted only when the direct peer
is listed in `TRUSTED_PROXY_IPS`; the limiter walks the header from right to left
and uses the first address outside the trusted proxy networks.

Registration and password change reject passwords from a local common-password
blocklist. Rejections return `422 password_too_common` and are audited as
`auth.password.common_rejected`; the password is never logged. Disable this only
when an external password-strength service provides equivalent coverage.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/register` | Public | Request account creation (neutral response) |
| `POST` | `/api/v1/auth/login` | Public | Authenticate and set refresh/CSRF cookies |
| `POST` | `/api/v1/auth/refresh` | Cookie + CSRF | Rotate refresh token and issue access token |
| `POST` | `/api/v1/auth/logout` | Bearer + CSRF | Revoke the session and clear cookies |
| `GET` | `/api/v1/auth/me` | Bearer | Read the current profile |
| `PATCH` | `/api/v1/auth/me` | Bearer | Update the current profile |
| `POST` | `/api/v1/auth/change-password` | Bearer | Change password and revoke other sessions |
| `GET` | `/api/v1/account/settings` | Bearer | Read user settings |
| `PUT` | `/api/v1/account/settings` | Bearer | Update user settings |

### Usage quotas

Authenticated resource-heavy operations have separate per-user sliding-window
quotas:

- CV text analysis and CV upload share the CV analysis quota.
- Job matching and recommendations have separate quotas.
- Remote LLM analysis consumes an additional remote LLM quota when a provider is
  available and both the request and server opt-ins are enabled.
- History, settings, and job-catalog reads are not quota-limited.

Quota rejections return `429 usage_quota_exceeded` with `Retry-After`. Invalid
request bodies and validation failures are rejected before the quota check, so
they do not consume quota. Quota counters use the configured rate-limit backend;
with Redis they are shared across workers. They remain usage protection, not
distributed billing or cost accounting. `0` disables an individual quota.

| Condition | Status | Error code |
| --- | --- | --- |
| Per-user CV, matching, recommendation, or remote LLM quota exceeded | `429` | `usage_quota_exceeded` |
| Password is on the local common-password blocklist | `422` | `password_too_common` |

### Security audit events

Authentication and session operations emit structured JSON audit events on the
`jobmatch.audit` logger. Each event includes `event_type`, `timestamp`,
`request_id`, `outcome`, optional `user_id` and `client_ip`, and redacted
`metadata`. Registration, login, refresh, logout, CSRF failures, rate-limit
rejections, password changes, refresh-token reuse, and session revocation are
covered.

Audit payloads never include passwords, access or refresh tokens, JWT secrets,
CSRF tokens, full CV or resume text, or LLM prompts and responses. Emails are
replaced by a pseudonymized `account_ref`. Alert hooks on
`jobmatch.security.alert` cover repeated login failures, refresh-token reuse,
repeated CSRF failures, excessive rate-limit rejections, and mass session
revocation, with per-process thresholds and cooldowns. Configure log shipping
and retention in the deployment environment; the API does not store audit
events in the database.

Phase 8.5.3 adds `security.request_body.rejected`,
`security.upload.rejected`, `security.upload.pdf_page_limit_exceeded`,
`security.upload.docx_limit_exceeded`, `security.cleanup.failed`, and
`security.cleanup.succeeded` events. Metadata contains reason codes, limits, and
counts, never document contents or tokens.

Phase 8.5.4 adds `auth.password.common_rejected`,
`security.quota.rejected`, `security.matching.repeated`, and
`security.llm.quota.rejected`. Account-wide login rejections continue to use
`auth.rate_limit.rejected` with scope `login-account`. Quota event metadata
contains the operation and retry delay only; it does not include passwords, CV
content, prompts, responses, tokens, or quota limits.

Phase 8.5.5 adds `security.quota.unavailable` when shared quota storage fails
closed, plus operational `dependency_unavailable`, `request_completed`,
`request_failed`, `service_starting`, and `service_stopping` events on the
`jobmatch.ops` logger. Readiness failures identify the dependency without
exposing connection details.

### Data lifecycle and caching

Responses under `/api/v1/auth`, `/api/v1/account`, `/api/v1/cv`, and
`/api/v1/matches` include `Cache-Control: no-store`. Public health and job
catalog responses are not marked `no-store`.

Expired and revoked sessions, plus their refresh tokens, are retained for
investigation and then removed by an explicit cleanup job:

```bash
python -m app.cli cleanup-sessions --dry-run
python -m app.cli cleanup-sessions
```

The job is idempotent and safe to run from a scheduler. It deletes refresh
tokens before sessions so cleanup does not rely on database foreign-key cascade
behavior. `--session-retention-seconds` and
`--refresh-token-retention-seconds` override the configured windows for one run.
Cleanup failures are audited and re-raised so scheduler failures are visible.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness check |
| `GET` | `/health/live` | Liveness check |
| `GET` | `/health/ready` | Readiness check for PostgreSQL and Redis |
| `GET` | `/api/v1/health` | Versioned liveness check |
| `GET` | `/api/v1/health/ready` | Versioned readiness check |
| `POST` | `/api/v1/cv/analyze` | Extract a structured profile from résumé text |
| `POST` | `/api/v1/cv/analyze/upload` | Extract a profile from a TXT, PDF, or DOCX upload |
| `GET` | `/api/v1/jobs` | List/search catalog jobs |
| `GET` | `/api/v1/jobs/{job_id}` | Get one catalog job |
| `POST` | `/api/v1/matches/job` | Match against a catalog job or free-text job |
| `POST` | `/api/v1/matches/recommendations` | Rank catalog jobs for a résumé |

The CV, matching, history, and account endpoints require
`Authorization: Bearer <access_token>`. Register, then log in and reuse the
returned access token. The curl examples below use a `TOKEN` shell variable set
to that value.

### Analyze résumé text

```bash
curl -X POST http://127.0.0.1:8000/api/v1/cv/analyze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"resume_text":"Python developer using Docker, PostgreSQL, and React.","resume_id":"resume-1"}'
```

### Upload a résumé

```bash
curl -X POST http://127.0.0.1:8000/api/v1/cv/analyze/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@resume.pdf" \
  -F "resume_id=resume-1"
```

TXT, PDF, and DOCX are supported. Legacy `.text`, `.md`, and `.csv` text files are
also accepted. The upload response includes extraction metadata:

```json
{
  "document": {
    "filename": "resume.pdf",
    "document_type": "pdf",
    "page_count": 2,
    "extraction_status": "success",
    "character_count": 1834
  }
}
```

Uploads are streamed into a temporary spool and are not stored permanently. The
default byte limit is 5,000,000 and the extracted-text limit is 200,000
characters. Request bodies larger than `MAX_REQUEST_BODY_BYTES` are rejected
before route handling. Text is normalized and then passed to the existing CV
analysis and matching pipeline. Scanned/image-only PDFs are not supported because
OCR is unavailable. Password-protected or corrupt documents are rejected.
Relevant status codes:

| Condition | Status | Error code |
| --- | --- | --- |
| Empty, corrupt, binary text, or encrypted file | `400` | `empty_document`, `invalid_document`, `encrypted_document` |
| Unsupported extension or mismatched MIME type | `415` | `unsupported_document_type`, `mime_type_mismatch` |
| Upload bytes, extracted text, or request body over the limit | `413` | `upload_too_large`, `text_too_large`, `request_too_large` |
| PDF page or DOCX archive resource limit exceeded | `413` | `pdf_page_limit_exceeded`, `docx_entry_limit_exceeded`, `docx_uncompressed_limit_exceeded`, `docx_compression_ratio_exceeded` |
| No usable text (for example, a scanned PDF) | `422` | `no_usable_text` |

PDF and DOCX parsing use `pypdf` and `python-docx`. PDFs are capped by page
count. DOCX archives are capped by entry count, declared uncompressed size, and
compression ratio to limit archive and decompression abuse. Extraction does not
alter the local matching or scoring logic. Uploaded content and filenames are
never logged. Rejections emit `security.upload.*` audit events with limits and
reason codes only.

### Match a catalog job

```bash
curl -X POST http://127.0.0.1:8000/api/v1/matches/job \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"resume_text":"Python developer using Docker.","job_id":"JOB_00000000"}'
```

### Match a free-text job

```bash
curl -X POST http://127.0.0.1:8000/api/v1/matches/job \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"resume_text":"Python developer.","job_text":"Python and Docker required.","job_title":"Backend Engineer","job_category":"Software Development"}'
```

### Recommendations

```bash
curl -X POST http://127.0.0.1:8000/api/v1/matches/recommendations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"resume_text":"Python developer using Docker and React.","limit":5,"candidate_pool_size":30}'
```

Recommendations first retrieve candidates by required/preferred/all skill overlap,
then run the real local matcher on the candidate pool and sort by actual
`overall_compatibility_score`. They never use LLM output.

## LLM explanation flow

Local matching always runs first and remains authoritative. An OpenAI
explanation is attempted only when all three conditions are true:

1. Request body has `"include_llm": true`.
2. Request body has `"allow_remote_analysis": true`.
3. Server has `ALLOW_REMOTE_LLM_ANALYSIS=true`.

When authorized, résumé text and job text are sent to the configured provider for
structured analysis and explanation. For catalog jobs, the catalog description is
sent as job text. If the request supplies a pre-built `resume_profile` instead of
`resume_text`, remote résumé analysis is skipped because no raw text is available.

Provider failure never fails the request. The response keeps the local score,
matched skills, missing skills, and classification, sets `explanation` to `null`,
and records an `openai_explanation` component with `failed` plus an uncertainty
indicator. `llm_requested` and `llm_authorized` show what was attempted.

Keep the server flag `false` unless your organization has explicitly approved
sending résumé/personal data to a remote provider.

## Error format

```json
{
  "error": {
    "code": "job_not_found",
    "message": "Job 'NOPE' was not found in the catalog.",
    "details": []
  },
  "request_id": "4fe20dcffdb44a4dbc945a9310bbacd5"
}
```

Validation errors list only `loc`, `msg`, and `type`; request input is not echoed.
`X-Request-ID` is accepted when it matches `[A-Za-z0-9._-]{1,120}` and is otherwise
generated. Every response includes it.

## Swagger and Postman

Use Swagger UI at `/docs` for interactive requests. Documentation endpoints are
disabled by default when `APP_ENV=production`; set `ENABLE_API_DOCS=true` only
behind an external access-control layer. To import into Postman:

1. Open Postman and choose **Import**.
2. Select **Link** and enter `http://127.0.0.1:8000/openapi.json`.
3. Run **GET /health** first to confirm the service is up.
4. Run **POST /api/v1/cv/analyze**, then copy `profile` if you want to send a
   pre-built `resume_profile` to matching endpoints.
5. Run **POST /api/v1/matches/recommendations** with `resume_text` for the
   simplest end-to-end flow.

## Tests

```bash
cd ai-engine
venv/Scripts/python.exe -m pytest tests -q
```

API tests use a small temporary catalog and dependency overrides, so they do not
load the full 10,461-row table or call OpenAI. LLM tests use `MockLLMProvider`.

## Fairness and limitations

The API inherits the hybrid engine's fairness notice and disclaimer. It must not
be used as the sole basis for hiring, rejection, ranking, or other employment
decisions. Skill extraction is dictionary based, TF-IDF similarity is lexical,
and the evaluation set is small and synthetic. Human review of evidence is
required.

Recommendation retrieval first uses skill overlap to limit the candidate pool,
then runs the real matcher on that pool. If no canonical skills are extracted,
candidate retrieval falls back to catalog order and evaluates only the configured
pool; the response includes a low-confidence warning. The catalog is cached in
memory per worker, so a multi-worker deployment loads one copy per process.
Authentication, authorization, CSRF protection, security headers, and rate
limits and usage quotas are implemented. Set `REDIS_URL` and keep
`RATE_LIMIT_BACKEND=auto` or `redis` for multi-worker or multi-instance
deployments. The memory backend prunes stale keys and caps bucket count but is
process-local. Redis failures fail closed by default and return `503` with
`Retry-After`; see `phase-8.5.5-deployment-hardening.md`.
