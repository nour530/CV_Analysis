import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiError, toApiError } from '../../core/api-error';
import { JobSummary } from '../../core/api.models';
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

  readonly form = this.formBuilder.nonNullable.group({
    q: ['', [Validators.maxLength(200)]],
    category: ['', [Validators.maxLength(120)]],
  });

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
    this.load(0);
  }

  search(): void {
    this.load(0);
  }

  clearFilters(): void {
    this.form.reset({ q: '', category: '' });
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
