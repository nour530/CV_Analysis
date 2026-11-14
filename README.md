# CV_Analysis (JobMatch AI)

ATS CV checker, CV builder and job matching for job seekers in Tunisia.

- **ATS check:** upload a PDF, Word or text CV (English or French) and get a
  0–100 score with a fix for every problem (`ai-engine/docs/ats-check.md`).
- **CV builder:** edit section by section with a live preview, accept or reject
  improvement suggestions (rule-based, plus optional AI rewrites that flag
  anything not in your CV), download ATS-safe Word or PDF
  (`ai-engine/docs/cv-builder.md`).
- **Real jobs:** current openings published by employers in Tunisia plus remote
  roles, synced every 6 hours, each with a match score for your CV
  (`ai-engine/docs/real-jobs.md`).
- **Application tracker:** save jobs and move them from Saved to Applied,
  Interview and Offer.
- Public landing page, guided dashboard, light and dark themes, responsive
  layout down to phone width.

## Backend API

The hybrid matching engine is exposed through FastAPI. It accepts CV text
directly or uploads as TXT, PDF, or DOCX. User accounts, saved profile data, CV
analysis history, and match history are stored in a database. Raw uploaded CV
files are never persisted. See `ai-engine/docs/api.md` for architecture,
endpoints, authentication, security, and Postman instructions.

Authentication is required for CV analysis, matching, history, and account
settings. Job browsing and health checks remain public.

### 1. Install and configure

```bash
cd ai-engine
venv/Scripts/python.exe -m pip install -r requirements.txt
```

Copy `.env.example` to your environment configuration and set at least a strong
`JWT_SECRET_KEY` (32+ characters). The repository placeholder is rejected at
startup, so generate a real value. FastAPI does not load `.env` automatically;
export variables through your shell, IDE, Docker Compose, or process manager.

PowerShell example:

```powershell
$env:APP_ENV = "development"
$env:JWT_SECRET_KEY = & venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
$env:CORS_ORIGINS = "http://localhost:4200"
```

If `DATABASE_URL` is unset, the API uses SQLite at
`ai-engine/data/jobmatch.db`. PostgreSQL is supported through a SQLAlchemy URL
such as `postgresql+psycopg://user:password@localhost:5432/jobmatch`. Production
requires PostgreSQL and a non-SQLite `DATABASE_URL`. PostgreSQL pool settings are
available through `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS`,
`DB_POOL_RECYCLE_SECONDS`, and `DB_CONNECT_TIMEOUT_SECONDS`.

### 2. Apply database migrations

```bash
venv/Scripts/python.exe -m alembic upgrade head
```

`AUTO_CREATE_TABLES=true` can create tables directly for local experiments, but
Alembic migrations should be used for normal development and production.

### 3. Start the API

```bash
venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Swagger UI: <http://localhost:8000/docs> (disabled by default when `APP_ENV=production`)

Liveness: `GET /health` or `GET /health/live`. Readiness, which checks PostgreSQL
and Redis when configured: `GET /health/ready`. Readiness returns `503` when a
required dependency is unavailable. The same routes are available under
`/api/v1/health` for compatibility.

### 4. Load real jobs

```bash
venv/Scripts/python.exe -m app.cli sync-jobs
```

This fetches current Tunisian job postings from the employer job-board APIs
listed in `ai-engine/data/job_sources.json` (SmartRecruiters, Recruitee, Lever,
Ashby, plus remote jobs from Remotive). Until the first sync, the API falls back
to the research dataset. See `ai-engine/docs/real-jobs.md`.

## Docker Compose deployment

A production-like stack is defined in `docker-compose.yml`: PostgreSQL, Redis, a job-sync worker that refreshes real job postings every 6 hours,
a one-shot migration service, the backend, and nginx serving the Angular build.
PostgreSQL and Redis use persistent volumes, and only the frontend port is
published. Copy `.env.example` to `.env`, set `APP_ENV=production`, replace
`JWT_SECRET_KEY`, `POSTGRES_PASSWORD`, and `CORS_ORIGINS`, then run:

```bash
docker compose build
docker compose up -d
docker compose ps
```

The frontend is available at <http://localhost:8080> by default. Terminate TLS
in front of nginx for production and use the public HTTPS frontend origin in
`CORS_ORIGINS`. See `ai-engine/docs/phase-8.5.5-deployment-hardening.md` for
Redis, readiness, logging, migration, backup, and troubleshooting details.

## Frontend

The Angular workspace is in `frontend/`. Development uses the API base URL in
`frontend/src/environments/environment.ts` (default: `http://localhost:8000`).
Production builds replace it with `environment.production.ts`, where
`apiBaseUrl` is empty so requests are relative and proxied by nginx.
Keep `localhost` rather than `127.0.0.1` so browser cookies remain same-site
with the frontend. Start the backend first, then run:

```bash
cd frontend
npm install
npm start
```

Open <http://localhost:4200>. Register, then sign in to use protected features.
Access tokens stay in memory; refresh sessions use HttpOnly cookies. The backend
CORS configuration allows this origin by default with credentials enabled.

```bash
npm test -- --watch=false
npm run build
```

Use a supported Node LTS release (Node 22 or 24). Angular may emit warnings on
odd-numbered Node versions such as Node 25.

## Production notes

- Set `APP_ENV=production`. Startup fails if production security requirements
  are not met.
- Serve the frontend and API over HTTPS and set `COOKIE_SECURE=true`.
- Set `ENABLE_HSTS=true` only after HTTPS is correctly configured; production
  startup requires it.
- `CORS_ORIGINS` must contain explicit HTTPS origins and must not contain
  localhost or wildcards.
- Use a unique, high-entropy `JWT_SECRET_KEY`; placeholder values are rejected.
  Keep the key out of source control.
- API documentation endpoints are disabled by default in production. Set
  `ENABLE_API_DOCS=true` only when an external access layer protects them.
- If the API is behind a reverse proxy, set `TRUSTED_PROXY_IPS` to the exact
  proxy addresses or CIDR ranges. `X-Forwarded-For` is ignored from untrusted
  peers so clients cannot spoof their rate-limit identity. Leave it empty when
  clients connect directly.
- Run `alembic upgrade head` during deployment before starting API workers.
- Security audit events are structured JSON lines emitted on the
  `jobmatch.audit` logger; alerts use `jobmatch.security.alert`. They contain a
  request ID, outcome, safe user/IP identifiers, and redacted metadata, but never
  passwords, tokens, CV text, or LLM content. Ship these streams to your
  centralized log platform and apply role-based access and retention there.
  `AUDIT_LOG_LEVEL` defaults to `INFO`. See
  `ai-engine/docs/phase-8.5.2-security-observability.md` for the event catalog,
  redaction rules, and retention guidance.
- Run `python -m app.cli cleanup-sessions` from a scheduler to remove expired and
  revoked sessions and stale refresh tokens after the configured retention
  windows. Use `--dry-run` first. Sensitive API responses set
  `Cache-Control: no-store`, request bodies are size-limited, and uploaded PDF
  and DOCX files are subject to page, archive-entry, uncompressed-size, and
  compression-ratio limits. See
  `ai-engine/docs/phase-8.5.3-data-lifecycle-resource-protection.md`. Abuse
  hardening and per-user quotas are described in
  `ai-engine/docs/phase-8.5.4-abuse-hardening.md`.
- Configure `REDIS_URL` and keep `RATE_LIMIT_BACKEND=auto` or `redis` when
  running multiple workers or instances. Redis shares login, refresh, API, and
  per-user quota windows. Production `auto` requires `REDIS_URL`; explicit
  `memory` is process-local and emits a startup warning. Redis failures fail
  closed by default and return `503` with `Retry-After`. Login protection
  combines a per-IP limit with an account-wide failure budget keyed by a
  pseudonymized email, so rotating client IPs does not reset the account
  budget. Successful login resets the account budget.
- Authenticated CV analysis, job matching, recommendations, and remote LLM
  explanations have separate per-user usage quotas. Quota rejections return
  `429 usage_quota_exceeded` with `Retry-After`; repeated matching attempts and
  remote LLM quota rejections are audited. With the Redis backend, quotas are
  shared across workers; they are still usage protection, not distributed
  billing or cost-control accounting.
- Registration and password change reject common passwords using a local
  blocklist. Rejections are audited as `auth.password.common_rejected`; passwords
  are never logged. Set `COMMON_PASSWORD_CHECK_ENABLED=false` only when an
  external password-strength service provides equivalent coverage.
- Registration returns a neutral `202` and does not start a session; clients
  then sign in. This avoids a direct registration account-existence oracle.
  Email verification is not implemented, so combined register/login probing
  remains a residual enumeration channel.
