import { Routes } from '@angular/router';
import { authGuard, guestGuard } from './core/auth.guard';

export const routes: Routes = [
  {
    path: 'login',
    title: 'Sign in | JobMatch AI',
    canActivate: [guestGuard],
    loadComponent: () =>
      import('./pages/login/login.page').then((module) => module.LoginPage),
  },
  {
    path: 'register',
    title: 'Create account | JobMatch AI',
    canActivate: [guestGuard],
    loadComponent: () =>
      import('./pages/register/register.page').then(
        (module) => module.RegisterPage,
      ),
  },
  {
    path: '',
    pathMatch: 'full',
    redirectTo: 'dashboard',
  },
  {
    path: 'dashboard',
    title: 'Dashboard | JobMatch AI',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/dashboard/dashboard.page').then(
        (module) => module.DashboardPage,
      ),
  },
  {
    path: 'cv',
    title: 'My CV | JobMatch AI',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/cv/cv.page').then((module) => module.CvPage),
  },
  {
    path: 'jobs',
    title: 'Job Explorer | JobMatch AI',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/jobs/jobs.page').then((module) => module.JobsPage),
  },
  {
    path: 'jobs/:jobId',
    title: 'Job details | JobMatch AI',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/job-detail/job-detail.page').then(
        (module) => module.JobDetailPage,
      ),
  },
  {
    path: 'matches',
    title: 'My Matches | JobMatch AI',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/matches/matches.page').then(
        (module) => module.MatchesPage,
      ),
  },
  {
    path: 'settings',
    title: 'Settings | JobMatch AI',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/settings/settings.page').then(
        (module) => module.SettingsPage,
      ),
  },
  {
    path: '**',
    title: 'Page not found | JobMatch AI',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/not-found/not-found.page').then(
        (module) => module.NotFoundPage,
      ),
  },
];
