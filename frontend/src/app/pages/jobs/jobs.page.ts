import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiError, toApiError } from '../../core/api-error';
import { FacetValue, JobSummary, JobWorkplace } from '../../core/api.models';
import { postedAgo, workplaceLabel } from '../../core/formatters';
import { ApplicationsStore } from '../../core/applications.store';
import { WorkspaceStore } from '../../core/workspace.store';
import { JobMatchApiService } from '../../core/api.service';
import { IconComponent } from '../../shared/components/icon/icon';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header';
import { StatePanelComponent } from '../../shared/components/state-panel/state-panel';

const PAGE_SIZE = 12;

@Component({
  selector: 'app-jobs-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DecimalPipe,
    ReactiveFormsModule,
    RouterLink,
    IconComponent,
    PageHeaderComponent,
    StatePanelComponent,
  ],
  templateUrl: './jobs.page.html',
  styleUrl: './jobs.page.css',
})
export class JobsPage {
  private readonly api = inject(JobMatchApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly formBuilder = inject(FormBuilder);
  readonly applications = inject(ApplicationsStore);
  private readonly workspace = inject(WorkspaceStore);
  /** Match score (0–1) of the user's latest CV for each listed job. */
  readonly scores = signal<ReadonlyMap<string, number>>(new Map());
  readonly hasCv = this.workspace.hasCv;

  readonly form = this.formBuilder.nonNullable.group({
    q: ['', [Validators.maxLength(200)]],
    category: ['', [Validators.maxLength(120)]],
    location: ['', [Validators.maxLength(120)]],
    workplace: ['' as JobWorkplace | ''],
  });

  readonly cityOptions = signal<FacetValue[]>([]);
  readonly workplaceOptions = signal<FacetValue[]>([]);
  readonly liveCatalog = signal(false);
  readonly postedAgo = postedAgo;
  readonly liveDescription =
    'Current openings from real employers, refreshed every few hours. Filter by city or work mode, then match your CV or apply directly.';
  readonly datasetDescription =
    'Search the role catalog, filter by category, and inspect the requirements behind each match.';
  readonly workplaceLabel = workplaceLabel;

  readonly jobs = signal<JobSummary[]>([]);
  readonly total = signal(0);
  readonly offset = signal(0);
  readonly limit = PAGE_SIZE;
  readonly loading = signal(true);
  readonly error = signal<ApiError | null>(null);
  readonly categoryOptions = signal<string[]>([]);

  readonly pageNumber = computed(() => Math.floor(this.offset() / this.limit) + 1);
  readonly totalPages = computed(() =>
    Math.max(1, Math.ceil(this.total() / this.limit)),
  );
  readonly rangeStart = computed(() =>
    this.total() === 0 ? 0 : this.offset() + 1,
  );
  readonly rangeEnd = computed(() =>
    Math.min(this.offset() + this.limit, this.total()),
  );
  readonly hasPrevious = computed(() => this.offset() > 0);
  readonly hasNext = computed(
    () => this.offset() + this.limit < this.total(),
  );

  constructor() {
    this.loadFacets();
    this.load(0);
    this.applications.load().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ error: () => undefined });
  }

  matchPercent(jobId: string): number | null {
    const score = this.scores().get(jobId);
    return score === undefined ? null : Math.round(score * 100);
  }

  matchTone(percent: number): string {
    return percent >= 75 ? 'strong' : percent >= 40 ? 'moderate' : 'weak';
  }

  toggleSave(job: JobSummary): void {
    this.applications
      .toggle(job.job_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ error: (error: unknown) => this.error.set(toApiError(error)) });
  }

  private loadScores(jobs: JobSummary[]): void {
    if (!this.workspace.hasCv() || !jobs.length) {
      return;
    }
    this.api
      .matchScores(jobs.map((job) => job.job_id))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          const next = new Map(this.scores());
          for (const item of response.scores) {
            next.set(item.job_id, item.score);
          }
          this.scores.set(next);
        },
        // Scores are an enhancement; the list works without them.
        error: () => undefined,
      });
  }

  private loadFacets(): void {
    this.api
      .getJobFacets()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (facets) => {
          this.liveCatalog.set(facets.live);
          this.cityOptions.set(facets.cities.slice(0, 30));
          this.workplaceOptions.set(
            facets.workplaces.filter((item) => item.value !== 'unknown'),
          );
          const categories = facets.categories.map((item) => item.value);
          if (categories.length) {
            this.categoryOptions.set(
              [...new Set([...this.categoryOptions(), ...categories])].sort((a, b) =>
                a.localeCompare(b),
              ),
            );
          }
        },
        // Filters are optional; the list still works without facets.
        error: () => undefined,
      });
  }

  search(): void {
    this.load(0);
  }

  clearFilters(): void {
    this.form.reset({ q: '', category: '', location: '', workplace: '' });
    this.load(0);
  }

  previousPage(): void {
    this.load(Math.max(0, this.offset() - this.limit));
  }

  nextPage(): void {
    this.load(this.offset() + this.limit);
  }

  retry(): void {
    this.load(this.offset());
  }

  load(offset: number): void {
    this.loading.set(true);
    this.error.set(null);
    this.offset.set(Math.max(0, offset));

    this.api
      .listJobs({
        q: this.form.controls.q.value,
        category: this.form.controls.category.value,
        location: this.form.controls.location.value,
        workplace: this.form.controls.workplace.value,
        offset: this.offset(),
        limit: this.limit,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.jobs.set(response.jobs);
          this.total.set(response.total);
          this.offset.set(response.offset);
          this.captureCategories(response.jobs);
          this.loading.set(false);
          this.loadScores(response.jobs);
        },
        error: (error: unknown) => {
          this.error.set(toApiError(error));
          this.loading.set(false);
        },
      });
  }

  private captureCategories(jobs: JobSummary[]): void {
    const existing = new Set(this.categoryOptions());
    let changed = false;

    for (const job of jobs) {
      if (job.category && !existing.has(job.category)) {
        existing.add(job.category);
        changed = true;
      }
    }

    if (changed) {
      this.categoryOptions.set(
        [...existing].sort((left, right) => left.localeCompare(right)),
      );
    }
  }
}
