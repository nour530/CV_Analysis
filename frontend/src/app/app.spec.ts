import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Observable, of, throwError } from 'rxjs';
import { vi } from 'vitest';
import { App } from './app';
import { AuthService } from './core/auth.service';

function authStub(
  authenticated: boolean,
  logout: Observable<void> = of(undefined),
) {
  return {
    isAuthenticated: signal(authenticated),
    displayName: signal(authenticated ? 'Test User' : ''),
    user: signal(
      authenticated
        ? {
            id: 'user-1',
            full_name: 'Test User',
            email: 'test@example.com',
            is_active: true,
            created_at: '2026-01-01T00:00:00Z',
          }
        : null,
    ),
    logout: vi.fn(() => logout),
  };
}

describe('App', () => {
  beforeEach(async () => {
    sessionStorage.clear();
    localStorage.clear();

    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: authStub(true) },
      ],
    }).compileComponents();
  });

  it('creates the authenticated app shell', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const app = fixture.componentInstance;

    expect(app).toBeTruthy();
    expect(fixture.nativeElement.textContent).toContain('JobMatch AI');
    expect(fixture.nativeElement.textContent).toContain('test@example.com');
    expect(fixture.nativeElement.textContent).toContain('Sign out');
  });

  it('renders only the route outlet when signed out', async () => {
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: authStub(false) },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.app-shell')).toBeNull();
    expect(fixture.nativeElement.querySelector('router-outlet')).not.toBeNull();
  });

  it('toggles the responsive navigation drawer', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const app = fixture.componentInstance;

    expect(app.sidebarOpen()).toBe(false);

    const toggle = fixture.nativeElement.querySelector(
      '.mobile-menu',
    ) as HTMLButtonElement;
    toggle.click();
    fixture.detectChanges();

    expect(app.sidebarOpen()).toBe(true);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('shows a recoverable error when sign out fails', async () => {
    TestBed.resetTestingModule();
    const auth = authStub(
      true,
      throwError(() => new Error('logout failed')),
    );
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideRouter([]),
        { provide: AuthService, useValue: auth },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const signOut = fixture.nativeElement.querySelector(
      '.topbar__actions button',
    ) as HTMLButtonElement;

    signOut.click();
    fixture.detectChanges();

    expect(auth.logout).toHaveBeenCalledTimes(1);
    expect(fixture.nativeElement.textContent).toContain(
      'Sign out failed. Your session is still active.',
    );
    expect(fixture.nativeElement.textContent).toContain('Try again');
  });
});
