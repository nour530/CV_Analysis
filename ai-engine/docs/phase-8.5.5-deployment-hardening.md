# Phase 8.5.5: Deployment Hardening

## Scope

Phase 8.5.5 moves JobMatch AI from a single-process development setup to a
deployable, multi-worker production shape. It adds shared Redis-backed rate
limiting and quotas, production configuration validation, PostgreSQL pool
settings, liveness/readiness probes, structured operational logging, container
images, a production-like Compose stack, and CI. It does not add Phase 8.6
features.

## Shared rate limiting and quotas

`RATE_LIMIT_BACKEND` accepts `auto`, `memory`, or `redis`.

| Value | Behavior |
| --- | --- |
| `auto` | Uses Redis when `REDIS_URL` is set. Outside production, falls back to memory when unset. Production fails startup when unset. |
| `redis` | Requires `REDIS_URL`. |
| `memory` | Explicit process-local backend. Intended for local development, tests, or a deliberate single-worker deployment. |

The Redis backend uses a Lua sliding-window sorted set. Redis server `TIME` is
used for the window, every request has a unique member, and keys expire
automatically. Login, refresh, password-change, API, and per-user quota checks
therefore share state across workers and instances.

Redis failures are controlled by `RATE_LIMIT_REDIS_FAILURE_MODE`:

- `closed` (default, required in production): fail protected requests with
  `503 rate_limit_unavailable` or `503 usage_quota_unavailable` and
  `Retry-After: 5`.
- `open`: allow requests when Redis is unavailable. This is rejected in
  production and should only be used for explicitly accepted availability
  trade-offs outside production.

Identity values in rate-limit keys are pseudonymized with the same helper used
by audit logging. Raw emails, tokens, and user IDs are not used as Redis keys.

## Production configuration

Production startup fails unless all of the following are true:

- `DATABASE_URL` is set and uses PostgreSQL.
- `JWT_SECRET_KEY` is a strong, non-placeholder secret of at least 32 characters.
- `COOKIE_SECURE=true`; `COOKIE_SAMESITE=none` also requires secure cookies.
- `ENABLE_HSTS=true`.
- `AUTO_CREATE_TABLES=false`.
- `APP_DEBUG=false`.
- `RATE_LIMIT_REDIS_FAILURE_MODE=closed`.
- `RATE_LIMIT_BACKEND=auto` includes `REDIS_URL`, or the backend is explicitly
  `redis`.
- `CORS_ORIGINS` contains explicit HTTPS origins without localhost, wildcards,
  paths, queries, fragments, or embedded credentials.

Production defaults `LOG_JSON=true` and disables API documentation unless
`ENABLE_API_DOCS=true`. Explicit `RATE_LIMIT_BACKEND=memory` is allowed for a
deliberate single-process deployment and emits a startup configuration warning.

## Database

`DATABASE_URL` accepts SQLite outside production and PostgreSQL in production.
PostgreSQL engines use configurable pooling:

- `DB_POOL_SIZE` (default `5`)
- `DB_MAX_OVERFLOW` (default `10`)
- `DB_POOL_TIMEOUT_SECONDS` (default `30`)
- `DB_POOL_RECYCLE_SECONDS` (default `1800`)
- `DB_CONNECT_TIMEOUT_SECONDS` (default `5`)

Migrations must run before API workers start. The Compose `migrate` service
runs `alembic upgrade head` once and must complete successfully before the
backend starts. API workers never create production tables automatically.

## Health and readiness

- `GET /health` and `GET /health/live` are liveness probes. They do not contact
  external dependencies.
- `GET /health/ready` checks PostgreSQL with `SELECT 1` and Redis with `PING`.
  It returns `200` when dependencies are available and `503` otherwise. The
  memory backend reports Redis as `disabled`.
- The same routes are available under `/api/v1/health` for compatibility.

Readiness responses contain only status and latency metadata. Connection
strings, credentials, and exception text are not exposed.

## Logging

Application and operational events are emitted through standard logging.
`LOG_LEVEL` accepts `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG`;
`LOG_JSON` enables structured JSON. When no root handler is configured, the
application installs a safe stream handler. Startup, shutdown, dependency
failures, configuration warnings, and request completion/failure events use the
`jobmatch.ops` logger. Audit and alert events continue to use
`jobmatch.audit` and `jobmatch.security.alert`.

Operational logs include request ID, method, path, status, duration, dependency
name, and error type. They do not include request bodies, CV text, prompts,
tokens, passwords, emails, or response content.

## Containers

Backend image (`ai-engine/Dockerfile`):

- Python 3.12 slim, non-root `jobmatch` user.
- Installs `requirements.txt`, includes `app`, `migrations`, `alembic.ini`, and
  the required job catalog, skills dictionary, and skill alias artifacts.
- Exposes port `8000`, uses an internal liveness healthcheck, and supports
  `UVICORN_WORKERS`.

Frontend image (`frontend/Dockerfile`):

- Multi-stage Node 22 build and nginx runtime.
- Production Angular build uses `environment.production.ts`, so API requests
  are relative and proxied through nginx.
- nginx proxies `/api/`, `/health`, and `/health/` to the backend and serves
  the SPA with fallback routing. Only the frontend port is published.

`docker-compose.yml` runs PostgreSQL, Redis, a one-shot migration service, the
backend, and nginx. PostgreSQL and Redis use persistent named volumes, all
services have healthchecks where applicable, and backend/Redis/PostgreSQL ports
are not published to the host. The backend trusts only the Compose network CIDR
for forwarded client IPs.

## Deployment steps

```bash
cp .env.example .env
# Set APP_ENV=production, JWT_SECRET_KEY, POSTGRES_PASSWORD, CORS_ORIGINS, and
# any provider keys. Use a strong URL-safe database password, or override
# DATABASE_URL separately.
docker compose build
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:8080/healthz
```

The public frontend origin must terminate TLS before production traffic. Set
`CORS_ORIGINS` to that HTTPS origin and keep `COOKIE_SECURE=true`. For a local
HTTP-only smoke test, explicitly use development settings such as
`APP_ENV=development`, `CORS_ORIGINS=http://localhost:8080`,
`COOKIE_SECURE=false`, and `ENABLE_HSTS=false`.

## Operations

- Scale API workers with `UVICORN_WORKERS`; Redis keeps limits shared.
- Use `docker compose logs -f backend` to inspect JSON operational logs.
- Readiness failures identify `database` or `redis` without exposing secrets.
- Back up the PostgreSQL volume with `pg_dump`; Redis contains only ephemeral
  rate-limit keys and may be recreated. Persistence is enabled for graceful
  restarts, not as a source of business records.
- Rotate `JWT_SECRET_KEY` with a planned session invalidation, and rotate
  database/Redis credentials through the deployment environment.
- Run `python -m app.cli cleanup-sessions` on a scheduler for session hygiene.

## Verification

CI installs `requirements-dev.txt`, runs backend compilation and pytest, runs
frontend tests and a production build, and builds both container images.
Deployment tests cover production configuration rejection, Redis auto-selection,
shared sliding windows, fail-closed/fail-open behavior, readiness responses,
database configuration, and pseudonymized keys.

The test suite uses SQLite and `fakeredis`; it does not require a live Redis or
PostgreSQL service.

Local verification on 2026-11-12:

- Backend: `python -m compileall -q app` passed.
- Backend: `217 passed, 1 skipped`.
- Frontend: `49 passed`.
- Frontend production build completed successfully.
- `docker compose config` validated with the required environment variables.
- Container image builds were not executed because the local Docker daemon was
  not running.
