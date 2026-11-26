import { HttpClient, HttpHeaders } from '@angular/common/http';
import { computed, inject, Injectable, signal } from '@angular/core';
import {
  catchError,
  finalize,
  forkJoin,
  map,
  Observable,
  of,
  shareReplay,
  switchMap,
  tap,
  throwError,
  timeout,
} from 'rxjs';
import { API_BASE_URL } from './api.config';
import { ApiError, toApiError } from './api-error';
import {
  AuthResponse,
  AuthUser,
  LoginRequest,
  RegisterRequest,
  RegistrationResponse,
} from './api.models';
import { JobMatchApiService } from './api.service';
import { WorkspaceStore } from './workspace.store';

const API_PREFIX = '/api/v1';
const CSRF_COOKIE = 'jobmatch_csrf';
const CSRF_HEADER = 'X-CSRF-Token';
const AUTH_TIMEOUT_MS = 15_000;

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly api = inject(JobMatchApiService);
  private readonly store = inject(WorkspaceStore);
  private readonly baseUrl = inject(API_BASE_URL).replace(/\/+$/, '');

  private readonly userState = signal<AuthUser | null>(null);
  private readonly accessTokenState = signal<string | null>(null);
  private readonly csrfTokenState = signal<string | null>(null);
  private readonly initializedState = signal(false);
  private refreshRequest: Observable<AuthResponse> | null = null;

  readonly user = this.userState.asReadonly();
  readonly initialized = this.initializedState.asReadonly();
  readonly isAuthenticated = computed(
    () => this.userState() !== null && this.accessTokenState() !== null,
  );
  readonly displayName = computed(
    () => this.userState()?.full_name || this.userState()?.email || '',
  );

  accessToken(): string | null {
    return this.accessTokenState();
  }

  initialize(): Observable<boolean> {
    if (this.initializedState()) {
      return of(this.isAuthenticated());
    }
    if (!this.readCsrfCookie()) {
      this.clearSession();
      return of(false);
    }
    this.store.clearWorkspace();
    return this.refreshAccessToken().pipe(
      switchMap(() => this.hydrateWorkspace()),
      map(() => this.isAuthenticated()),
      catchError(() => {
        this.clearSession();
        return of(false);
      }),
    );
  }

  register(request: RegisterRequest): Observable<void> {
    return this.http
      .post<RegistrationResponse>(
        `${this.baseUrl}${API_PREFIX}/auth/register`,
        request,
        { withCredentials: true },
      )
      .pipe(
        timeout(AUTH_TIMEOUT_MS),
        map(() => undefined),
        catchError((error: unknown) => throwError(() => toApiError(error))),
      );
  }

  login(request: LoginRequest): Observable<AuthUser> {
    return this.http
      .post<AuthResponse>(`${this.baseUrl}${API_PREFIX}/auth/login`, request, {
        withCredentials: true,
      })
      .pipe(
        timeout(AUTH_TIMEOUT_MS),
        tap((response) => {
          this.store.clearWorkspace();
          this.applyAuth(response);
        }),
        switchMap((response) =>
          this.hydrateWorkspace().pipe(map(() => response.user)),
        ),
        catchError((error: unknown) => throwError(() => toApiError(error))),
      );
  }

  refreshAccessToken(): Observable<AuthResponse> {
    if (this.refreshRequest) {
      return this.refreshRequest;
    }

    const csrfToken = this.readCsrfCookie() ?? this.csrfTokenState();
    if (!csrfToken) {
      return throwError(
        () =>
          new ApiError({
            message: 'Your session has expired. Sign in again.',
            status: 401,
            code: 'authentication_required',
          }),
      );
    }

    const headers = new HttpHeaders().set(CSRF_HEADER, csrfToken);
    this.refreshRequest = this.http
      .post<AuthResponse>(
        `${this.baseUrl}${API_PREFIX}/auth/refresh`,
        {},
        { headers, withCredentials: true },
      )
      .pipe(
        timeout(AUTH_TIMEOUT_MS),
        tap((response) => this.applyAuth(response)),
        catchError((error: unknown) => {
          const apiError = toApiError(error);
          if (apiError.status === 401 || apiError.status === 403) {
            this.clearSession();
          }
          return throwError(() => apiError);
        }),
        finalize(() => {
          this.refreshRequest = null;
        }),
        shareReplay({ bufferSize: 1, refCount: false }),
      );
    return this.refreshRequest;
  }

  logout(): Observable<void> {
    const token = this.accessTokenState();
    const csrfToken = this.readCsrfCookie() ?? this.csrfTokenState();
    const headers: Record<string, string> = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    if (csrfToken) {
      headers[CSRF_HEADER] = csrfToken;
    }

    return this.http
      .post<void>(
        `${this.baseUrl}${API_PREFIX}/auth/logout`,
        {},
        { headers, withCredentials: true },
      )
      .pipe(
        timeout(AUTH_TIMEOUT_MS),
        map(() => undefined),
        tap(() => this.clearSession()),
        catchError((error: unknown) => throwError(() => toApiError(error))),
      );
  }

  updateProfile(fullName: string): Observable<AuthUser> {
    return this.http
      .patch<AuthUser>(
        `${this.baseUrl}${API_PREFIX}/auth/me`,
        { full_name: fullName },
      )
      .pipe(
        tap((user) => this.userState.set(user)),
        catchError((error: unknown) => throwError(() => toApiError(error))),
      );
  }

  changePassword(
    currentPassword: string,
    newPassword: string,
  ): Observable<void> {
    return this.http
      .post<void>(`${this.baseUrl}${API_PREFIX}/auth/change-password`, {
        current_password: currentPassword,
        new_password: newPassword,
      })
      .pipe(
        catchError((error: unknown) => throwError(() => toApiError(error))),
      );
  }

  /** Permanently delete the account and all its data, then sign out here. */
  deleteAccount(currentPassword: string): Observable<void> {
    return this.http
      .delete<void>(`${this.baseUrl}${API_PREFIX}/auth/me`, {
        body: { current_password: currentPassword },
        withCredentials: true,
      })
      .pipe(
        map(() => undefined),
        tap(() => this.clearSession()),
        catchError((error: unknown) => throwError(() => toApiError(error))),
      );
  }

  clearSession(): void {
    this.userState.set(null);
    this.accessTokenState.set(null);
    this.csrfTokenState.set(null);
    this.initializedState.set(true);
    this.store.clearWorkspace();
  }

  private applyAuth(response: AuthResponse): void {
    this.userState.set(response.user);
    this.accessTokenState.set(response.access_token);
    this.csrfTokenState.set(response.csrf_token);
    this.initializedState.set(true);
  }

  private hydrateWorkspace(): Observable<void> {
    return forkJoin({
      settings: this.api.getUserSettings().pipe(
        catchError(() => of(null)),
      ),
      cv: this.api.getLatestCv().pipe(
        catchError((error: unknown) => {
          if (toApiError(error).status === 404) {
            this.store.clearCv();
          }
          return of(null);
        }),
      ),
    }).pipe(
      tap(({ settings, cv }) => {
        if (settings) {
          this.store.applyServerSettings(settings);
        }
        if (cv) {
          this.store.setServerCv(cv);
        }
      }),
      map(() => undefined),
    );
  }

  private readCsrfCookie(): string | null {
    if (typeof document === 'undefined') {
      return null;
    }
    const prefix = `${CSRF_COOKIE}=`;
    const entry = document.cookie
      .split(';')
      .map((part) => part.trim())
      .find((part) => part.startsWith(prefix));
    if (!entry) {
      return null;
    }
    try {
      return decodeURIComponent(entry.slice(prefix.length));
    } catch {
      return null;
    }
  }
}
