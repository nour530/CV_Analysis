import { TestBed } from '@angular/core/testing';
import { THEME_STORAGE_KEY, ThemeService } from './theme.service';

describe('ThemeService', () => {
  beforeEach(() => {
    localStorage.removeItem(THEME_STORAGE_KEY);
    document.documentElement.removeAttribute('data-theme');
    TestBed.resetTestingModule();
  });

  it('follows the system until the user toggles, then switches between light and dark', () => {
    const theme = TestBed.inject(ThemeService);
    expect(theme.choice()).toBe('system');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
    const startedDark = theme.isDark();

    theme.toggle();
    expect(theme.isDark()).toBe(!startedDark);
    expect(document.documentElement.getAttribute('data-theme')).toBe(startedDark ? 'light' : 'dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe(startedDark ? 'light' : 'dark');
    expect(theme.label()).toBe(startedDark ? 'Switch to dark mode' : 'Switch to light mode');

    theme.toggle();
    expect(theme.isDark()).toBe(startedDark);
    expect(document.documentElement.getAttribute('data-theme')).toBe(startedDark ? 'dark' : 'light');
  });

  it('restores a saved choice', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'dark');
    const theme = TestBed.inject(ThemeService);
    expect(theme.isDark()).toBe(true);
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });
});
