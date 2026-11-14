import { TestBed } from '@angular/core/testing';
import {
  CanActivateFn,
  provideRouter,
  Router,
  RouterStateSnapshot,
  UrlTree,
} from '@angular/router';
import { firstValueFrom, Observable, of } from 'rxjs';
import { vi } from 'vitest';
import { authGuard, guestGuard } from './auth.guard';
import { AuthService } from './auth.service';

const PROTECTED_STATE = { url: '/dashboard' } as RouterStateSnapshot;

function runGuard(
  guard: CanActivateFn,
  auth: { isAuthenticated: ReturnType<typeof vi.fn>; initialize: ReturnType<typeof vi.fn> },
) {
  TestBed.configureTestingModule({
    providers: [
      provideRouter([]),
      { provide: AuthService, useValue: auth },
    ],
  });
  return TestBed.runInInjectionContext(() =>
    guard({} as never, PROTECTED_STATE),
  );
}

function router(): Router {
  return TestBed.inject(Router);
}

describe('auth guards', () => {
  it('allows an authenticated user through the auth guard', () => {
    const auth = {
      isAuthenticated: vi.fn(() => true),
      initialize: vi.fn(() => of(true)),
    };

    const result = runGuard(authGuard, auth);

    expect(result).toBe(true);
    expect(auth.initialize).not.toHaveBeenCalled();
  });

  it('redirects an unauthenticated user to login with a return URL', async () => {
    const auth = {
      isAuthenticated: vi.fn(() => false),
      initialize: vi.fn(() => of(false)),
    };

    const result = await firstValueFrom(
      runGuard(authGuard, auth) as Observable<boolean | UrlTree>,
    );

    expect(result).toBeInstanceOf(UrlTree);
    const url = router().serializeUrl(result as UrlTree);
    expect(url).toContain('/login');
    expect(url).toContain('returnUrl=%2Fdashboard');
  });

  it('redirects an authenticated guest away from login', () => {
    const auth = {
      isAuthenticated: vi.fn(() => true),
      initialize: vi.fn(() => of(true)),
    };

    const result = runGuard(guestGuard, auth);

    expect(result).toBeInstanceOf(UrlTree);
    expect(router().serializeUrl(result as UrlTree)).toBe('/dashboard');
  });

  it('allows an unauthenticated guest to see login', async () => {
    const auth = {
      isAuthenticated: vi.fn(() => false),
      initialize: vi.fn(() => of(false)),
    };

    const result = await firstValueFrom(
      runGuard(guestGuard, auth) as Observable<boolean | UrlTree>,
    );

    expect(result).toBe(true);
  });
});
