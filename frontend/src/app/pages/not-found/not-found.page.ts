import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/components/icon/icon';

@Component({
  selector: 'app-not-found-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, IconComponent],
  template: `
    <div class="not-found card card--padded">
      <span class="not-found__icon"><app-icon name="search" [size]="30" /></span>
      <p class="eyebrow">404</p>
      <h1>Page not found</h1>
      <p class="muted">
        The page you requested does not exist in this workspace. Return to the
        dashboard or explore the job catalog.
      </p>
      <div class="row">
        <a class="btn btn--primary" routerLink="/dashboard">Dashboard</a>
        <a class="btn btn--secondary" routerLink="/jobs">Job Explorer</a>
      </div>
    </div>
  `,
  styleUrl: './not-found.page.css',
})
export class NotFoundPage {}
