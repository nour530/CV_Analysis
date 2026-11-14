import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, finalize, map, of, shareReplay, tap } from 'rxjs';
import { JobMatchApiService } from './api.service';
import { ApplicationStatus, ApplicationUpdateRequest, JobApplication } from './resume.models';

/** Saved jobs and applications, shared by job cards, job details and the tracker. */
@Injectable({ providedIn: 'root' })
export class ApplicationsStore {
  private readonly api = inject(JobMatchApiService);
  private readonly itemsState = signal<JobApplication[]>([]);
  private loading$: Observable<JobApplication[]> | null = null;

  readonly items = this.itemsState.asReadonly();
  readonly loaded = signal(false);
  readonly pending = signal<ReadonlySet<string>>(new Set());
  readonly byJobId = computed(() => new Map(this.itemsState().map((item) => [item.job_id, item])));
  readonly counts = computed(() => {
    const counts: Record<ApplicationStatus, number> = { saved: 0, applied: 0, interview: 0, offer: 0, rejected: 0 };
    for (const item of this.itemsState()) {
      counts[item.status] += 1;
    }
    return counts;
  });

  load(force = false): Observable<JobApplication[]> {
    if (this.loaded() && !force) {
      return of(this.itemsState());
    }
    if (!this.loading$ || force) {
      this.loading$ = this.api.listApplications().pipe(
        map((response) => response.items),
        tap((items) => {
          this.itemsState.set(items);
          this.loaded.set(true);
        }),
        finalize(() => (this.loading$ = null)),
        shareReplay(1),
      );
    }
    return this.loading$;
  }

  isSaved(jobId: string): boolean {
    return this.byJobId().has(jobId);
  }

  isPending(jobId: string): boolean {
    return this.pending().has(jobId);
  }

  /** Save a job, or remove it when it is already saved. */
  toggle(jobId: string): Observable<JobApplication | null> {
    const existing = this.byJobId().get(jobId);
    this.setPending(jobId, true);
    const request$: Observable<JobApplication | null> = existing
      ? this.api.deleteApplication(existing.id).pipe(
          tap(() => this.itemsState.update((items) => items.filter((item) => item.id !== existing.id))),
          map(() => null),
        )
      : this.api.saveApplication(jobId).pipe(tap((saved) => this.upsert(saved)));
    return request$.pipe(finalize(() => this.setPending(jobId, false)));
  }

  update(item: JobApplication, request: ApplicationUpdateRequest): Observable<JobApplication> {
    return this.api.updateApplication(item.id, request).pipe(tap((saved) => this.upsert(saved)));
  }

  remove(item: JobApplication): Observable<void> {
    return this.api
      .deleteApplication(item.id)
      .pipe(tap(() => this.itemsState.update((items) => items.filter((entry) => entry.id !== item.id))));
  }

  reset(): void {
    this.itemsState.set([]);
    this.loaded.set(false);
  }

  private upsert(saved: JobApplication): void {
    this.itemsState.update((items) => [saved, ...items.filter((item) => item.id !== saved.id)]);
  }

  private setPending(jobId: string, on: boolean): void {
    this.pending.update((current) => {
      const next = new Set(current);
      if (on) {
        next.add(jobId);
      } else {
        next.delete(jobId);
      }
      return next;
    });
  }
}
