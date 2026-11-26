import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { API_BASE_URL } from './api.config';
import { ApiError } from './api-error';
import { AuthResponse, AuthUser, UserSettings } from './api.models';
import { AuthService } from './auth.service';
import { PROFILE_RESPONSE } from './testing/fixtures';
import { WorkspaceStore } from './workspace.store';

const BASE_URL = 'http://api.test';
const CSRF_COOKIE = 'jobmatch_csrf';

const USER: AuthUser = {
  id: 'user-1',
  full_name: 'Test User',
  email: 'test@example.com',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
};

const AUTH_RESPONSE: AuthResponse = {
  access_token: 'access-1',
  token_type: 'bearer',
  expires_in: 900,
  user: USER,
  csrf_token: 'csrf-2',
};

const SETTINGS: UserSettings = {
  recommendation_limit: 5,
  candidate_pool_size: 25,
  preferred_category: 'Data Science',
  allow_remote_analysis: true,
  updated_at: '2026-01-01T00:00:00Z',
};

function setCsrfCookie(value: string): void {
  document.cookie = `${CSRF_COOKIE}=${encodeURIComponent(value)}; path=/`;
}

function clearCsrfCookie(): void {
  document.cookie = `${CSRF_COOKIE}=; Max-Age=0; path=/`;
}

describe('AuthService', () => {
  let service: AuthService;
  let store: WorkspaceStore;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    clearCsrfCookie();

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: `${BASE_URL}/` },
      ],
    });

    service = TestBed.inject(AuthService);
    store = TestBed.inject(WorkspaceStore);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
    clearCsrfCookie();
  });

  it('stays signed out and clears stale storage without a CSRF cookie', () => {
    store.setCv('stale CV', PROFILE_RESPONSE);
    let result: boolean | undefined;

    service.initialize().subscribe((value) => (result = value));

    expect(result).toBe(false);
    expect(service.isAuthenticated()).toBe(false);
    expect(store.hasCv()).toBe(false);
    httpMock.expectNone(`${BASE_URL}/api/v1/auth/refresh`);
  });

  it('restores a session and hydrates server-owned workspace data', () => {
    setCsrfCookie('csrf-1');
    let result: boolean | undefined;

    service.initialize().subscribe((value) => (result = value));

    const refresh = httpMock.expectOne(`${BASE_URL}/api/v1/auth/refresh`);
    expect(refresh.request.method).toBe('POST');
    expect(refresh.request.withCredentials).toBe(true);
    expect(refresh.request.headers.get('X-CSRF-Token')).toBe('csrf-1');
    refresh.flush(AUTH_RESPONSE);

    const settings = httpMock.expectOne(
      `${BASE_URL}/api/v1/account/settings`,
    );
    const latestCv = httpMock.expectOne(`${BASE_URL}/api/v1/cv/latest`);
    settings.flush(SETTINGS);
    latestCv.flush(PROFILE_RESPONSE);

    expect(result).toBe(true);
    expect(service.user()).toEqual(USER);
    expect(service.accessToken()).toBe('access-1');
    expect(store.preferences().recommendationLimit).toBe(5);
    expect(store.preferences().allowRemoteAnalysis).toBe(true);
    expect(store.cvProfile()?.profile.category).toBe('Data Science');
  });

  it('submits registration without creating an authenticated session', () => {
    let completed = false;

    service
      .register({
        full_name: USER.full_name,
        email: USER.email,
        password: 'CorrectPassword1!',
      })
      .subscribe(() => (completed = true));

    const request = httpMock.expectOne(
      `${BASE_URL}/api/v1/auth/register`,
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.withCredentials).toBe(true);
    request.flush({
      message: 'If this email is available, the account can now sign in.',
    });

    expect(completed).toBe(true);
    expect(service.isAuthenticated()).toBe(false);
    expect(service.user()).toBeNull();
    httpMock.expectNone(`${BASE_URL}/api/v1/account/settings`);
  });

  it('clears a previous user workspace after login and hydrates the new one', () => {
    store.setCv('previous user CV', PROFILE_RESPONSE);
    store.updatePreferences({ recommendationLimit: 33 });
    let authenticatedAs: string | undefined;

    service
      .login({ email: USER.email, password: 'CorrectPassword1!' })
      .subscribe((user) => (authenticatedAs = user.id));

    const login = httpMock.expectOne(`${BASE_URL}/api/v1/auth/login`);
    expect(login.request.withCredentials).toBe(true);
    login.flush(AUTH_RESPONSE);

    expect(store.hasCv()).toBe(false);
    expect(store.preferences().recommendationLimit).toBe(10);

    httpMock
      .expectOne(`${BASE_URL}/api/v1/account/settings`)
      .flush(SETTINGS);
    httpMock.expectOne(`${BASE_URL}/api/v1/cv/latest`).flush({
      ...PROFILE_RESPONSE,
      analysis_id: 'analysis-2',
    });

    expect(authenticatedAs).toBe(USER.id);
    expect(store.preferences().recommendationLimit).toBe(5);
    expect(store.cvProfile()?.analysis_id).toBe('analysis-2');
  });

  it('deletes the account with the password, then clears the local session', () => {
    store.setCv('my CV', PROFILE_RESPONSE);
    let done = false;
    service.deleteAccount('CorrectPassword1!').subscribe(() => (done = true));

    const request = httpMock.expectOne(`${BASE_URL}/api/v1/auth/me`);
    expect(request.request.method).toBe('DELETE');
    expect(request.request.body).toEqual({ current_password: 'CorrectPassword1!' });
    expect(request.request.withCredentials).toBe(true);
    request.flush(null, { status: 204, statusText: 'No Content' });

    expect(done).toBe(true);
    expect(service.accessToken()).toBeNull();
    expect(store.hasCv()).toBe(false);
  });

  it('keeps the session when account deletion is refused', () => {
    let failed = '';
    service.deleteAccount('wrong').subscribe({ error: (e) => (failed = e.message) });
    httpMock
      .expectOne(`${BASE_URL}/api/v1/auth/me`)
      .flush({ error: { code: 'invalid_current_password', message: 'Current password is incorrect.' } }, { status: 401, statusText: 'Unauthorized' });
    expect(failed).not.toBe('');
  });

  it('clears stale CV data when the authenticated user has no saved CV', () => {
    store.setCv('stale CV', PROFILE_RESPONSE);

    service
      .login({ email: USER.email, password: 'CorrectPassword1!' })
      .subscribe();

    httpMock.expectOne(`${BASE_URL}/api/v1/auth/login`).flush(AUTH_RESPONSE);
    httpMock
      .expectOne(`${BASE_URL}/api/v1/account/settings`)
      .flush(SETTINGS);
    httpMock
      .expectOne(`${BASE_URL}/api/v1/cv/latest`)
      .flush(
        { error: { code: 'not_found', message: 'No CV.', details: [] } },
        { status: 404, statusText: 'Not Found' },
      );

    expect(store.hasCv()).toBe(false);
  });

  it('shares a single refresh request across concurrent callers', () => {
    setCsrfCookie('csrf-1');
    const tokens: string[] = [];

    service
      .refreshAccessToken()
      .subscribe((response) => tokens.push(response.access_token));
    service
      .refreshAccessToken()
      .subscribe((response) => tokens.push(response.access_token));

    const refresh = httpMock.expectOne(`${BASE_URL}/api/v1/auth/refresh`);
    refresh.flush(AUTH_RESPONSE);

    expect(tokens).toEqual(['access-1', 'access-1']);
    httpMock.expectNone(`${BASE_URL}/api/v1/auth/refresh`);
  });

  it('clears the session only after a successful logout', () => {
    setCsrfCookie('csrf-1');
    store.setCv('saved CV', PROFILE_RESPONSE);
    service.refreshAccessToken().subscribe();
    httpMock.expectOne(`${BASE_URL}/api/v1/auth/refresh`).flush(AUTH_RESPONSE);
    let completed = false;

    service.logout().subscribe(() => (completed = true));

    const logout = httpMock.expectOne(`${BASE_URL}/api/v1/auth/logout`);
    expect(logout.request.withCredentials).toBe(true);
    expect(logout.request.headers.get('Authorization')).toBe('Bearer access-1');
    expect(logout.request.headers.get('X-CSRF-Token')).toBe('csrf-1');
    logout.flush(null, { status: 204, statusText: 'No Content' });

    expect(completed).toBe(true);
    expect(service.isAuthenticated()).toBe(false);
    expect(service.user()).toBeNull();
    expect(store.hasCv()).toBe(false);
  });

  it('keeps the session active when the server rejects logout', () => {
    setCsrfCookie('csrf-1');
    store.setCv('saved CV', PROFILE_RESPONSE);
    service.refreshAccessToken().subscribe();
    httpMock.expectOne(`${BASE_URL}/api/v1/auth/refresh`).flush(AUTH_RESPONSE);
    let failure: ApiError | undefined;

    service.logout().subscribe({ error: (error) => (failure = error) });

    httpMock
      .expectOne(`${BASE_URL}/api/v1/auth/logout`)
      .flush(
        { error: { code: 'internal_error', message: 'Failed.', details: [] } },
        { status: 500, statusText: 'Server Error' },
      );

    expect(failure?.status).toBe(500);
    expect(service.isAuthenticated()).toBe(true);
    expect(service.user()).toEqual(USER);
    expect(store.hasCv()).toBe(true);
  });

  it('keeps the session active after a logout network failure', () => {
    setCsrfCookie('csrf-1');
    service.refreshAccessToken().subscribe();
    httpMock.expectOne(`${BASE_URL}/api/v1/auth/refresh`).flush(AUTH_RESPONSE);
    let failure: ApiError | undefined;

    service.logout().subscribe({ error: (error) => (failure = error) });

    httpMock
      .expectOne(`${BASE_URL}/api/v1/auth/logout`)
      .error(new ProgressEvent('error'));

    expect(failure?.code).toBe('network_error');
    expect(service.isAuthenticated()).toBe(true);
    expect(service.user()).toEqual(USER);
  });

  it('keeps the session active when logout fails CSRF verification', () => {
    setCsrfCookie('csrf-1');
    service.refreshAccessToken().subscribe();
    httpMock.expectOne(`${BASE_URL}/api/v1/auth/refresh`).flush(AUTH_RESPONSE);
    let failure: ApiError | undefined;

    service.logout().subscribe({ error: (error) => (failure = error) });

    httpMock.expectOne(`${BASE_URL}/api/v1/auth/logout`).flush(
      {
        error: {
          code: 'csrf_failed',
          message: 'The request could not be verified.',
          details: [],
        },
      },
      { status: 403, statusText: 'Forbidden' },
    );

    expect(failure?.code).toBe('csrf_failed');
    expect(service.isAuthenticated()).toBe(true);
    expect(service.user()).toEqual(USER);
  });
});
