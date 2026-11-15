import { Injectable, computed, signal } from '@angular/core';

export type ThemeChoice = 'system' | 'light' | 'dark';

export const THEME_STORAGE_KEY = 'jobmatch.theme';

/**
 * Light/dark theme. Until the user picks one, the theme follows the system.
 * The toggle always switches to the opposite of what is currently shown.
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  readonly choice = signal<ThemeChoice>(readChoice());
  private readonly systemDark = signal(prefersDark());

  /** True when the page is currently shown in dark mode. */
  readonly isDark = computed(() =>
    this.choice() === 'system' ? this.systemDark() : this.choice() === 'dark',
  );

  constructor() {
    applyTheme(this.choice());
    try {
      window
        .matchMedia('(prefers-color-scheme: dark)')
        .addEventListener('change', (event) => this.systemDark.set(event.matches));
    } catch {
      // matchMedia is unavailable in some test environments.
    }
  }

  set(choice: ThemeChoice): void {
    this.choice.set(choice);
    applyTheme(choice);
    try {
      if (choice === 'system') {
        localStorage.removeItem(THEME_STORAGE_KEY);
      } else {
        localStorage.setItem(THEME_STORAGE_KEY, choice);
      }
    } catch {
      // Storage can be unavailable (private mode); the theme still applies.
    }
  }

  toggle(): void {
    this.set(this.isDark() ? 'light' : 'dark');
  }

  /** Accessible label describing what the button does. */
  label(): string {
    return this.isDark() ? 'Switch to light mode' : 'Switch to dark mode';
  }
}

function prefersDark(): boolean {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  } catch {
    return false;
  }
}

function readChoice(): ThemeChoice {
  try {
    const value = localStorage.getItem(THEME_STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === 'system') {
    root.removeAttribute('data-theme');
  } else {
    root.setAttribute('data-theme', choice);
  }
}
