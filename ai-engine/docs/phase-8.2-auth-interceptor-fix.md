# Phase 8.2 — Angular Authentication Interceptor Fix

**Project:** JobMatch AI  
**Date:** 2026-11-10  
**Scope:** Angular HTTP authentication interceptor, endpoint classification, and regression coverage.

## 1. Root cause

The interceptor treated every request whose URL contained `/api/v1/auth/` as public:

```ts
const AUTH_PATH = '/api/v1/auth/';

if (request.url.includes(AUTH_PATH)) {
  return next(request.clone({ withCredentials: true }));
}
```

That broad exclusion was correct for login, registration, and token refresh, but it also excluded protected endpoints:

| Endpoint | Backend protection | Frontend caller | Result before fix |
|---|---|---|---|
| `POST /api/v1/auth/login` | Public | `AuthService.login` | Correct: no Bearer token |
| `POST /api/v1/auth/register` | Public | `AuthService.register` | Correct: no Bearer token |
| `POST /api/v1/auth/refresh` | Refresh cookie + CSRF, no Bearer | `AuthService.refreshAccessToken` | Correct: no Bearer token |
| `PATCH /api/v1/auth/me` | Bearer required (`get_auth_context`) | `AuthService.updateProfile` | Broken: request sent without Bearer, backend returned 401 |
| `POST /api/v1/auth/change-password` | Bearer required (`get_auth_context`) | `AuthService.changePassword` | Broken: request sent without Bearer, backend returned 401 |
| `POST /api/v1/auth/logout` | Revokes session; frontend sets its own Bearer header | `AuthService.logout` | Worked because the service sets `Authorization` manually |
| `/api/v1/account/*`, `/api/v1/cv/*`, `/api/v1/matches/*` | Bearer required | API services | Correct: interceptor attached Bearer |

The root cause was therefore an overly broad path exclusion, not a backend authorization defect.

## 2. Files modified

| File | Change |
|---|---|
| `frontend/src/app/core/auth.interceptor.ts` | Replaced the broad auth-path exclusion with explicit public-auth endpoint matching and preserved pre-existing Authorization headers. |
| `frontend/src/app/core/auth.interceptor.spec.ts` | Added regression coverage for public and protected auth endpoints, missing tokens, and existing Authorization headers. |

No backend source, database schema, dependency, or unrelated frontend feature was changed.

## 3. Implementation details

### 3.1 Explicit public endpoint classification

`frontend/src/app/core/auth.interceptor.ts:10` now defines only the Bearer-free auth endpoints:

```ts
const PUBLIC_AUTH_PATHS = [
  '/api/v1/auth/login',
  '/api/v1/auth/register',
  '/api/v1/auth/refresh',
] as const;
```

`isPublicAuthRequest` strips query strings/fragments and performs suffix matching against those explicit paths (`auth.interceptor.ts:16`). This keeps the check robust when the API base URL includes a host or path prefix.

### 3.2 Public requests

Public login, registration, and refresh requests still receive `withCredentials: true` so the browser can send/set the HttpOnly refresh cookie and read the CSRF contract, but they do not receive an `Authorization` header (`auth.interceptor.ts:25`).

### 3.3 Protected requests

For all other requests:

```ts
const token = auth.accessToken();
const authorized =
  token && !request.headers.has('Authorization')
    ? request.clone({
        setHeaders: { Authorization: `Bearer ${token}` },
        withCredentials: true,
      })
    : request.clone({ withCredentials: true });
```

Behavior (`auth.interceptor.ts:29`):

- Normal protected requests receive `Authorization: Bearer <access_token>`.
- `PATCH /api/v1/auth/me` and `POST /api/v1/auth/change-password` now receive the token because they no longer match a public path.
- Requests with no token remain unauthenticated and are handled by the existing error path.
- An existing `Authorization` header is preserved rather than overwritten, preventing duplicate headers. This also keeps the manually constructed logout header intact.
- Every request still receives `withCredentials: true`.

### 3.4 Error handling preserved

The existing 401 logic is unchanged: if a request that had a token receives 401, the interceptor calls `refreshAccessToken()`, retries once with the new token, and clears the session/navigates to `/login` when refresh fails (`auth.interceptor.ts:40`). Requests with no token do not trigger a refresh, matching prior behavior.

## 4. Regression tests added

Tests were added/updated in `frontend/src/app/core/auth.interceptor.spec.ts`:

1. **Login without Authorization** — `auth.interceptor.spec.ts:59`
2. **Registration without Authorization** — `auth.interceptor.spec.ts:68`
3. **Profile update with Bearer** — `auth.interceptor.spec.ts:77`
4. **Password change with Bearer** — `auth.interceptor.spec.ts:88`
5. **Normal protected API request with Bearer** — existing jobs test at `auth.interceptor.spec.ts:50`
6. **Missing token behavior** — no Authorization header, credentials still enabled, 401 handled without refresh retry at `auth.interceptor.spec.ts:106`
7. **Existing Authorization header handling** — exactly one caller-provided header is preserved at `auth.interceptor.spec.ts:126`

The existing refresh-and-retry and refresh-failure tests remain in place (`auth.interceptor.spec.ts:141`, `:161`).

## 5. Test results

All commands below were executed locally.

### Angular frontend tests

```text
npm test -- --watch=false

Test Files  9 passed (9)
Tests       44 passed (44)
Duration    14.27s
```

The previous suite had 39 tests; 5 new interceptor tests were added.

### Angular production build / type-check

```text
npm run build

Application bundle generation complete.
Output location: frontend/dist/frontend
```

Build succeeded. The Angular CLI emitted only the existing Node.js odd-version warning (`Node.js v25.9.0`); it was not a build failure.

### Backend authentication tests

```text
cd ai-engine
venv/Scripts/python.exe -m pytest tests/test_auth.py -q

12 passed in 5.82s
```

## 6. Behavior verification

| Scenario | Verified by |
|---|---|
| Login sends no Authorization header | `auth.interceptor.spec.ts:59` |
| Registration sends no Authorization header | `auth.interceptor.spec.ts:68` |
| Profile update sends `Bearer access-1` | `auth.interceptor.spec.ts:77` |
| Password change sends `Bearer access-1` | `auth.interceptor.spec.ts:88` |
| Jobs/protected API requests send Bearer | `auth.interceptor.spec.ts:50` |
| Missing token sends no Authorization and does not refresh | `auth.interceptor.spec.ts:106` |
| Existing Authorization header is not duplicated | `auth.interceptor.spec.ts:126` |
| 401 refresh/retry still works | `auth.interceptor.spec.ts:141` |
| Refresh failure clears session | `auth.interceptor.spec.ts:161` |
| Backend auth behavior unchanged | `tests/test_auth.py` 12/12 |

No real HTTP call to a running backend or browser-level E2E flow was executed; the verification above is unit/component test coverage plus the backend auth suite and Angular build.

## 7. Remaining issues

The following Phase 8.1 findings are outside this targeted fix and remain:

1. **No browser E2E test** was added for the Settings profile/password forms. Unit interceptor coverage proves the headers are attached, but a Playwright/Cypress flow against a live API would provide end-to-end confirmation.
2. **Missing-token requests do not attempt a refresh.** This matches existing behavior: the interceptor only refreshes when a request had a token and received 401. If desired, this can be changed separately, but it was intentionally left unchanged to avoid altering existing error semantics.
3. **Logout still builds its Authorization header manually** in `AuthService.logout()`. It is now preserved by the interceptor rather than overridden. This is functionally correct and does not create duplicate headers.
4. **Multi-tab refresh rotation race** remains a potential issue from the previous audit.
5. **Other Phase 8.1 items remain open**, including placeholder JWT-secret validation, production cookie/HSTS guards, auth audit logging, password reset/email verification, account deletion, and session management UI.
