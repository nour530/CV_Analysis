import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { of, throwError } from 'rxjs';
import { vi } from 'vitest';
import { API_BASE_URL } from './api.config';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from './auth.service';

const BASE_URL = 'http://api.test';

describe('authInterceptor', () => {
  let http: HttpClient;
  let httpMock: HttpTestingController;
  let auth: {
    accessToken: ReturnType<typeof vi.fn>;
    refreshAccessToken: ReturnType<typeof vi.fn>;
    clearSession: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    auth = {
      accessToken: vi.fn(() => 'access-1'),
      refreshAccessToken: vi.fn(() => of({ access_token: 'access-2' })),
      clearSession: vi.fn(),
    };

    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: BASE_URL },
        { provide: AuthService, useValue: auth },
        { provide: Router, useValue: { navigate: vi.fn() } },
      ],
    });

    http = TestBed.inject(HttpClient);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('adds the bearer token and credentials to protected requests', () => {
    http.get(`${BASE_URL}/api/v1/jobs`).subscribe();

    const request = httpMock.expectOne(`${BASE_URL}/api/v1/jobs`);
    expect(request.request.headers.get('Authorization')).toBe('Bearer access-1');
    expect(request.request.withCredentials).toBe(true);
    request.flush({ jobs: [], total: 0, offset: 0, limit: 12 });
  });

  it('does not attach a bearer token to login requests', () => {
    http.post(`${BASE_URL}/api/v1/auth/login`, {}).subscribe();

    const request = httpMock.expectOne(`${BASE_URL}/api/v1/auth/login`);
    expect(request.request.headers.has('Authorization')).toBe(false);
    expect(request.request.withCredentials).toBe(true);
    request.flush({});
  });

  it('does not attach a bearer token to registration requests', () => {
    http.post(`${BASE_URL}/api/v1/auth/register`, {}).subscribe();

    const request = httpMock.expectOne(`${BASE_URL}/api/v1/auth/register`);
    expect(request.request.headers.has('Authorization')).toBe(false);
    expect(request.request.withCredentials).toBe(true);
    request.flush({});
  });

  it('attaches the bearer token to profile update requests', () => {
    http.patch(`${BASE_URL}/api/v1/auth/me`, { full_name: 'Test User' }).subscribe();

    const request = httpMock.expectOne(`${BASE_URL}/api/v1/auth/me`);
    expect(request.request.headers.getAll('Authorization')).toEqual([
      'Bearer access-1',
    ]);
    expect(request.request.withCredentials).toBe(true);
    request.flush({});
  });

  it('attaches the bearer token to password change requests', () => {
    http
      .post(`${BASE_URL}/api/v1/auth/change-password`, {
        current_password: 'CurrentPassword1!',
        new_password: 'NewPassword2!',
      })
      .subscribe();

    const request = httpMock.expectOne(
      `${BASE_URL}/api/v1/auth/change-password`,
    );
    expect(request.request.headers.getAll('Authorization')).toEqual([
      'Bearer access-1',
    ]);
    expect(request.request.withCredentials).toBe(true);
    request.flush({});
  });

  it('sends protected requests without authentication when no token is available', () => {
    auth.accessToken.mockReturnValue(null);
    let failed = false;

    http.get(`${BASE_URL}/api/v1/cv/latest`).subscribe({
      error: () => (failed = true),
    });

    const request = httpMock.expectOne(`${BASE_URL}/api/v1/cv/latest`);
    expect(request.request.headers.has('Authorization')).toBe(false);
    expect(request.request.withCredentials).toBe(true);
    request.flush(
      { error: { code: 'authentication_required', message: 'Required.', details: [] } },
      { status: 401, statusText: 'Unauthorized' },
    );

    expect(failed).toBe(true);
    expect(auth.refreshAccessToken).not.toHaveBeenCalled();
  });

  it('preserves an existing Authorization header without duplicating it', () => {
    http
      .get(`${BASE_URL}/api/v1/jobs`, {
        headers: { Authorization: 'Bearer caller-token' },
      })
      .subscribe();

    const request = httpMock.expectOne(`${BASE_URL}/api/v1/jobs`);
    expect(request.request.headers.getAll('Authorization')).toEqual([
      'Bearer caller-token',
    ]);
    expect(request.request.withCredentials).toBe(true);
    request.flush({ jobs: [], total: 0, offset: 0, limit: 12 });
  });

  it('refreshes once and retries a request after a 401', () => {
    let body: unknown;
    http.get(`${BASE_URL}/api/v1/cv/latest`).subscribe((value) => (body = value));

    httpMock
      .expectOne(`${BASE_URL}/api/v1/cv/latest`)
      .flush(
        { error: { code: 'expired', message: 'Expired.', details: [] } },
        { status: 401, statusText: 'Unauthorized' },
      );

    expect(auth.refreshAccessToken).toHaveBeenCalledTimes(1);

    const retry = httpMock.expectOne(`${BASE_URL}/api/v1/cv/latest`);
    expect(retry.request.headers.get('Authorization')).toBe('Bearer access-2');
    retry.flush({ message: 'ok' });

    expect(body).toEqual({ message: 'ok' });
  });

  it('clears the session when refresh fails', () => {
    auth.refreshAccessToken.mockReturnValue(
      throwError(() => new Error('refresh failed')),
    );
    let failed = false;

    http.get(`${BASE_URL}/api/v1/cv/latest`).subscribe({
      error: () => (failed = true),
    });

    httpMock
      .expectOne(`${BASE_URL}/api/v1/cv/latest`)
      .flush(
        { error: { code: 'expired', message: 'Expired.', details: [] } },
        { status: 401, statusText: 'Unauthorized' },
      );

    expect(failed).toBe(true);
    expect(auth.clearSession).toHaveBeenCalledTimes(1);
  });
});
