import { ChangeDetectionStrategy, Component, DestroyRef, HostListener, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  NavigationEnd,
  Router,
  RouterLink,
  RouterLinkActive,
  RouterOutlet,
} from '@angular/router';
import { filter } from 'rxjs';
import { toApiError } from './core/api-error';
import { AuthService } from './core/auth.service';
import { ThemeService } from './core/theme.service';
import { ApplicationsStore } from './core/applications.store';
import { WorkspaceStore } from './core/workspace.store';
import { AssistantChatComponent } from './shared/components/assistant-chat/assistant-chat';
import { IconComponent, IconName } from './shared/components/icon/icon';

interface NavItem {
  label: string;
  /** Label under the icon in the phone tab bar. */
  short: string;
  path: string;
  icon: IconName;
  exact: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Home', short: 'Home', path: '/dashboard', icon: 'dashboard', exact: true },
  { label: 'My CV', short: 'My CV', path: '/cv', icon: 'cv', exact: true },
  { label: 'CV Builder', short: 'Builder', path: '/editor', icon: 'edit', exact: false },
  { label: 'Jobs', short: 'Jobs', path: '/jobs', icon: 'jobs', exact: false },
  { label: 'Applications', short: 'Applied', path: '/applications', icon: 'bookmark', exact: true },
];

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    IconComponent,
    AssistantChatComponent,
  ],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  readonly auth = inject(AuthService);
  readonly store = inject(WorkspaceStore);
  readonly theme = inject(ThemeService);
  private readonly applications = inject(ApplicationsStore);

  readonly navItems = NAV_ITEMS;
  readonly menuOpen = signal(false);
  readonly firstName = computed(() => {
    const name = this.auth.user()?.full_name?.trim();
    return name ? name.split(/\s+/)[0] : 'Account';
  });
  readonly initials = computed(() => {
    const name = this.auth.user()?.full_name?.trim() || this.auth.user()?.email || '?';
    return name
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? '')
      .join('');
  });
  readonly pageTitle = signal('Dashboard');
  readonly logoutSaving = signal(false);
  readonly logoutError = signal<string | null>(null);

  constructor() {
    this.router.events
      .pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((event) => {
        this.pageTitle.set(titleForUrl(event.urlAfterRedirects));
        this.menuOpen.set(false);
      });
  }

  toggleMenu(event: Event): void {
    event.stopPropagation();
    this.menuOpen.update((open) => !open);
  }

  closeMenu(): void {
    this.menuOpen.set(false);
  }

  @HostListener('document:click')
  @HostListener('document:keydown.escape')
  onOutsideInteraction(): void {
    this.menuOpen.set(false);
  }

  logout(): void {
    if (this.logoutSaving()) {
      return;
    }
    this.logoutSaving.set(true);
    this.logoutError.set(null);
    this.auth.logout().subscribe({
      next: () => {
        this.applications.reset();
        this.logoutSaving.set(false);
        void this.router.navigate(['/login']);
      },
      error: (error: unknown) => {
        this.logoutSaving.set(false);
        this.logoutError.set(
          `Sign out failed. Your session is still active. ${toApiError(error).message}`,
        );
      },
    });
  }
}

function titleForUrl(url: string): string {
  const path = url.split('?')[0].split('#')[0];

  if (path === '/dashboard' || path === '/') {
    return 'Home';
  }
  if (path === '/applications') {
    return 'My applications';
  }
  if (path === '/editor' || path.startsWith('/editor/')) {
    return 'CV Builder';
  }
  if (path === '/cv') {
    return 'My CV & ATS score';
  }
  if (path === '/jobs') {
    return 'Jobs in Tunisia';
  }
  if (path.startsWith('/jobs/')) {
    return 'Job details';
  }
  if (path === '/matches') {
    return 'Match analysis';
  }
  if (path === '/settings') {
    return 'Settings';
  }
  return 'JobMatch AI';
}
