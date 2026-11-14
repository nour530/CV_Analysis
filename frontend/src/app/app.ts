import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
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
import { WorkspaceStore } from './core/workspace.store';
import { IconComponent, IconName } from './shared/components/icon/icon';

interface NavItem {
  label: string;
  path: string;
  icon: IconName;
  exact: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { label: 'Dashboard', path: '/dashboard', icon: 'dashboard', exact: true },
  { label: 'My CV', path: '/cv', icon: 'cv', exact: true },
  { label: 'Job Explorer', path: '/jobs', icon: 'jobs', exact: false },
  { label: 'My Matches', path: '/matches', icon: 'matches', exact: true },
  { label: 'Settings', path: '/settings', icon: 'settings', exact: true },
];

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    IconComponent,
  ],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  readonly auth = inject(AuthService);
  readonly store = inject(WorkspaceStore);

  readonly navItems = NAV_ITEMS;
  readonly sidebarOpen = signal(false);
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
        this.sidebarOpen.set(false);
      });
  }

  toggleSidebar(): void {
    this.sidebarOpen.update((open) => !open);
  }

  closeSidebar(): void {
    this.sidebarOpen.set(false);
  }

  logout(): void {
    if (this.logoutSaving()) {
      return;
    }
    this.logoutSaving.set(true);
    this.logoutError.set(null);
    this.auth.logout().subscribe({
      next: () => {
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
    return 'Dashboard';
  }
  if (path === '/cv') {
    return 'My CV';
  }
  if (path === '/jobs') {
    return 'Job Explorer';
  }
  if (path.startsWith('/jobs/')) {
    return 'Job details';
  }
  if (path === '/matches') {
    return 'My Matches';
  }
  if (path === '/settings') {
    return 'Settings';
  }
  return 'JobMatch AI';
}
