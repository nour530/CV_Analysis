import { Injectable, signal } from '@angular/core';

export type ThemeChoice = 'system' | 'light' | 'dark';

export const THEME_STORAGE_KEY = 'jobmatch.theme';
const ORDER: ThemeChoice[] = ['system', 'light', 'dark'];

/** Light/dark theme. "system" follows the operating system setting. */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  readonly choice = signal<ThemeChoice>(readChoice());

  constructor() {
    applyTheme(this.choice());
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

  cycle(): void {
    const next = ORDER[(ORDER.indexOf(this.choice()) + 1) % ORDER.length];
    this.set(next);
  }

  label(): string {
    return { system: 'Theme: system', light: 'Theme: light', dark: 'Theme: dark' }[this.choice()];
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
