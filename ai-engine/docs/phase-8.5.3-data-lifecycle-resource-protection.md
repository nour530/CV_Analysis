# Phase 8.5.3 — Data Lifecycle & Resource Protection

## Scope

This phase adds operational controls around stored session data and untrusted
request resources without changing authentication, matching, or LLM behavior:

- Explicit cleanup for expired/revoked sessions and stale refresh tokens.
- A global HTTP request-body byte limit.
- PDF page and DOCX archive/decompression limits for CV uploads.
- `Cache-Control: no-store` on sensitive API responses.
- Audit events for resource rejections and cleanup outcomes.

Phase 8.5.4 was not started.

## Configuration

New `APISettings` fields and environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAX_REQUEST_BODY_BYTES` | `8000000` | Maximum HTTP request body size |
| `MAX_PDF_PAGES` | `100` | Maximum pages extracted from a PDF |
| `MAX_DOCX_ENTRIES` | `2000` | Maximum entries in a DOCX archive |
| `MAX_DOCX_UNCOMPRESSED_BYTES` | `50000000` | Maximum declared uncompressed DOCX size |
| `MAX_DOCX_COMPRESSION_RATIO` | `200` | Maximum DOCX compression ratio |
| `SESSION_CLEANUP_RETENTION_SECONDS` | `2592000` | Session/token retention window |
| `REFRESH_TOKEN_CLEANUP_RETENTION_SECONDS` | `2592000` | Refresh-token retention window |

All values are bounded and invalid values fail startup with
`APIConfigurationError`.

## Session and token cleanup

`app/services/session_cleanup.py` exposes `cleanup_sessions()` and
`SessionCleanupResult`.

Eligibility rules:

- A session with `revoked_at` is eligible once `revoked_at` is older than the
  session retention window.
- An unrevoked session is eligible once `expires_at` is older than the session
  retention window.
- Refresh tokens are deleted when they belong to an eligible session, or when
  `expires_at`, `used_at`, or `revoked_at` is older than the refresh-token
  retention window.
- Recently revoked sessions are preserved even if they expired earlier, so
  recent revocation evidence remains available for investigation.

Refresh tokens are deleted before sessions explicitly, so cleanup does not rely
on database foreign-key cascade behavior (SQLite does not guarantee cascades are
enabled). The operation commits only after both deletes succeed, is idempotent,
and supports `dry_run=True`.

Run from a scheduler:

```bash
python -m app.cli cleanup-sessions --dry-run
python -m app.cli cleanup-sessions
```

`--session-retention-seconds` and `--refresh-token-retention-seconds` override
the configured windows for one run. Cleanup failures are rolled back, audited as
`security.cleanup.failed`, and re-raised so scheduler failures are visible.
Successful runs emit `security.cleanup.succeeded` with counts and cutoffs.

## Request-body limit

`app/core/body_limit.py` provides a pure-ASGI `BodySizeLimitMiddleware`. It:

- Rejects a valid `Content-Length` above the limit before route handling.
- Counts streamed bytes for requests that omit or misstate `Content-Length`.
- Replaces the application response with a JSON `413 request_too_large` response
  without raising through Starlette's exception middleware.
- Preserves CORS, request-ID, and security-header middleware behavior because it
  is installed inside those layers.
- Emits `security.request_body.rejected` with reason and limit metadata.

## Upload resource limits

`DocumentExtractionService` now accepts optional limits:

- PDF: page count is checked before page text extraction.
- DOCX: archive entry count, total declared uncompressed size, and per-entry and
  total compression ratios are checked before parsing.

New error codes map to HTTP `413`:

- `pdf_page_limit_exceeded`
- `docx_entry_limit_exceeded`
- `docx_uncompressed_limit_exceeded`
- `docx_compression_ratio_exceeded`

`read_document_upload()` now reads uploads in 64 KiB chunks into a
`SpooledTemporaryFile`, enforcing the byte limit while reading and closing the
spool on both success and rejection. Upload rejections emit
`security.upload.rejected` plus `security.upload.pdf_page_limit_exceeded` or
`security.upload.docx_limit_exceeded` where applicable. Audit metadata contains
reason codes and limits only; filenames, document contents, and tokens are not
logged.

## Cache headers

`security_headers_middleware` now sets `Cache-Control: no-store` for responses
under:

- `/api/v1/auth`
- `/api/v1/account`
- `/api/v1/cv`
- `/api/v1/matches`

Public `/health` and `/jobs` responses are unchanged.

## Tests

New coverage:

- `tests/test_session_cleanup.py`: retention boundaries, recent revocations,
  token cleanup, idempotency, dry-run behavior, and audited failures.
- `tests/test_body_limits.py`: declared-length rejection, streamed/chunked
  rejection, misleading `Content-Length`, accepted bodies, and non-HTTP scopes.
- `tests/test_upload_limits.py`: PDF page limits, DOCX entry/uncompressed/ratio
  limits, audit events, and temporary-spool closure on rejection.
- `tests/test_cache_headers.py`: sensitive endpoints set `no-store`; health does
  not.
- `tests/test_security_settings.py`: new configuration parsing and bounds.

## Verification

- Backend: `180 passed, 1 skipped`.
- Frontend: `49 passed` (9 files).
- Frontend production build: successful.

## Unverified

PostgreSQL was not available in this environment. The cleanup queries use
portable SQLAlchemy constructs and explicit token-before-session deletes, but
PostgreSQL-specific behavior (including any production FK/cascade configuration)
was not exercised. Run the cleanup job against a staging PostgreSQL database
before production rollout.
