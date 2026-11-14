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
    const text: string = fixture.nativeElement.textContent;
    expect(text).toContain('JobMatch');
    expect(text).toContain('Test');
    expect(fixture.nativeElement.querySelectorAll('.topnav__link').length).toBe(5);
    expect(fixture.nativeElement.querySelectorAll('.tabbar__item').length).toBe(5);
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

    expect(fixture.nativeElement.querySelector('.shell')).toBeNull();
    expect(fixture.nativeElement.querySelector('router-outlet')).not.toBeNull();
  });

  it('opens the account menu with settings and sign out', () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    const app = fixture.componentInstance;
    const button = fixture.nativeElement.querySelector('.account__button') as HTMLButtonElement;

    expect(app.menuOpen()).toBe(false);
    button.click();
    fixture.detectChanges();

    expect(app.menuOpen()).toBe(true);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    const menu: HTMLElement = fixture.nativeElement.querySelector('.account__menu');
    expect(menu.textContent).toContain('test@example.com');
    expect(menu.textContent).toContain('Sign out');

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    fixture.detectChanges();
    expect(app.menuOpen()).toBe(false);
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
    (fixture.nativeElement.querySelector('.account__button') as HTMLButtonElement).click();
    fixture.detectChanges();
    const signOut = Array.from(
      fixture.nativeElement.querySelectorAll('.account__menu button') as NodeListOf<HTMLButtonElement>,
    ).find((button) => button.textContent?.includes('Sign out')) as HTMLButtonElement;

    signOut.click();
    fixture.detectChanges();

    expect(auth.logout).toHaveBeenCalledTimes(1);
    expect(fixture.nativeElement.textContent).toContain(
      'Sign out failed. Your session is still active.',
    );
    expect(fixture.nativeElement.textContent).toContain('Try again');
  });
});
