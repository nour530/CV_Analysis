import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { ApiError, toApiError } from '../../core/api-error';
import { ApplicationsStore } from '../../core/applications.store';
import { ApplicationStatus, JobApplication } from '../../core/resume.models';
import { IconComponent } from '../../shared/components/icon/icon';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header';
import { StatePanelComponent } from '../../shared/components/state-panel/state-panel';

interface Column {
  status: ApplicationStatus;
  label: string;
  hint: string;
}

export const COLUMNS: Column[] = [
  { status: 'saved', label: 'Saved', hint: 'Jobs you want to apply to' },
  { status: 'applied', label: 'Applied', hint: 'Waiting for an answer' },
  { status: 'interview', label: 'Interview', hint: 'Prepare and follow up' },
  { status: 'offer', label: 'Offer', hint: 'Congratulations!' },
  { status: 'rejected', label: 'Closed', hint: 'Not this time' },
];

@Component({
  selector: 'app-applications-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, RouterLink, IconComponent, PageHeaderComponent, StatePanelComponent],
  templateUrl: './applications.page.html',
  styleUrl: './applications.page.css',
})
export class ApplicationsPage {
  private readonly destroyRef = inject(DestroyRef);
  readonly store = inject(ApplicationsStore);
  readonly columns = COLUMNS;

  readonly loading = signal(true);
  readonly error = signal<ApiError | null>(null);
  readonly filter = signal<ApplicationStatus | 'all'>('all');
  readonly editingNotes = signal<string | null>(null);

  readonly grouped = computed(() => {
    const groups = new Map<ApplicationStatus, JobApplication[]>(COLUMNS.map((column) => [column.status, []]));
    for (const item of this.store.items()) {
      groups.get(item.status)?.push(item);
    }
    return groups;
  });
  readonly visibleColumns = computed(() =>
    this.filter() === 'all' ? COLUMNS : COLUMNS.filter((column) => column.status === this.filter()),
  );
  readonly activeCount = computed(() => {
    const counts = this.store.counts();
    return counts.applied + counts.interview + counts.offer;
  });

  constructor() {
    this.store
      .load(true)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => this.loading.set(false),
        error: (error: unknown) => {
          this.error.set(toApiError(error));
          this.loading.set(false);
        },
      });
  }

  setFilter(value: ApplicationStatus | 'all'): void {
    this.filter.set(value);
  }

  move(item: JobApplication, event: Event): void {
    const status = (event.target as HTMLSelectElement).value as ApplicationStatus;
    this.store.update(item, { status }).subscribe({ error: (error: unknown) => this.error.set(toApiError(error)) });
  }

  saveNotes(item: JobApplication, event: Event): void {
    const notes = (event.target as HTMLTextAreaElement).value.slice(0, 2000);
    this.editingNotes.set(null);
    if ((item.notes ?? '') === notes.trim()) {
      return;
    }
    this.store.update(item, { notes }).subscribe({ error: (error: unknown) => this.error.set(toApiError(error)) });
  }

  remove(item: JobApplication): void {
    if (!confirm(`Remove "${item.title}" from your tracker?`)) {
      return;
    }
    this.store.remove(item).subscribe({ error: (error: unknown) => this.error.set(toApiError(error)) });
  }

  percent(score: number | null): string {
    return score === null ? '' : `${Math.round(score * 100)}%`;
  }

  label(status: ApplicationStatus): string {
    return COLUMNS.find((column) => column.status === status)?.label ?? status;
  }
}
