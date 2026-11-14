import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ApiError, toApiError } from '../../core/api-error';
import { AtsReport, JobDetailResponse } from '../../core/api.models';
import { JobMatchApiService } from '../../core/api.service';
import { postedAgo, workplaceLabel } from '../../core/formatters';
import { ApplicationsStore } from '../../core/applications.store';
import { AtsReportComponent } from '../../shared/components/ats-report/ats-report';
import { IconComponent } from '../../shared/components/icon/icon';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header';
import { StatePanelComponent } from '../../shared/components/state-panel/state-panel';

@Component({
  selector: 'app-job-detail-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    AtsReportComponent,
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
  readonly postedAgo = postedAgo;
  readonly applications = inject(ApplicationsStore);
  private readonly applicationsLoad = this.applications.load().subscribe({ error: () => undefined });

  toggleSave(): void {
    const jobId = this.detail()?.job.job_id;
    if (jobId) {
      this.applications.toggle(jobId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ error: () => undefined });
    }
  }
  readonly atsReport = signal<AtsReport | null>(null);
  readonly atsLoading = signal(false);
  readonly atsError = signal<ApiError | null>(null);

  checkAts(): void {
    const jobId = this.detail()?.job.job_id;
    if (!jobId || this.atsLoading()) {
      return;
    }
    this.atsLoading.set(true);
    this.atsError.set(null);
    this.api
      .checkAts({ job_id: jobId })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (report) => {
          this.atsReport.set(report);
          this.atsLoading.set(false);
        },
        error: (error: unknown) => {
          this.atsError.set(toApiError(error));
          this.atsLoading.set(false);
        },
      });
  }
  readonly workplaceLabel = workplaceLabel;

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
