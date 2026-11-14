import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ApiError, toApiError } from '../../core/api-error';
import { JobDetailResponse } from '../../core/api.models';
import { JobMatchApiService } from '../../core/api.service';
import { IconComponent } from '../../shared/components/icon/icon';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header';
import { StatePanelComponent } from '../../shared/components/state-panel/state-panel';

@Component({
  selector: 'app-job-detail-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    RouterLink,
    IconComponent,
    PageHeaderComponent,
    StatePanelComponent,
  ],
  templateUrl: './job-detail.page.html',
  styleUrl: './job-detail.page.css',
})
export class JobDetailPage {
  private readonly api = inject(JobMatchApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);

  readonly jobId = signal('');
  readonly detail = signal<JobDetailResponse | null>(null);
  readonly loading = signal(true);
  readonly error = signal<ApiError | null>(null);

  constructor() {
    this.route.paramMap
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((params) => {
        const jobId = params.get('jobId') ?? '';
        this.jobId.set(jobId);
        if (jobId) {
          this.load(jobId);
        }
      });
  }

  retry(): void {
    if (this.jobId()) {
      this.load(this.jobId());
    }
  }

  private load(jobId: string): void {
    this.loading.set(true);
    this.error.set(null);
    this.detail.set(null);

    this.api
      .getJob(jobId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.detail.set(response);
          this.loading.set(false);
        },
        error: (error: unknown) => {
          this.error.set(toApiError(error));
          this.loading.set(false);
        },
      });
  }
}
