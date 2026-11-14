import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { ApiError, toApiError } from '../../core/api-error';
import { JobMatchApiService } from '../../core/api.service';
import { ResumeSummary } from '../../core/resume.models';
import { WorkspaceStore } from '../../core/workspace.store';
import { IconComponent } from '../../shared/components/icon/icon';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header';
import { StatePanelComponent } from '../../shared/components/state-panel/state-panel';

@Component({
  selector: 'app-resumes-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DatePipe, RouterLink, IconComponent, PageHeaderComponent, StatePanelComponent],
  template: `
    <div class="page">
      <app-page-header
        eyebrow="CV builder"
        title="My CVs"
        description="Edit your CV section by section, check it against ATS rules, improve it with AI and download it as Word or PDF."
      >
        <button type="button" class="btn btn--secondary" [disabled]="creating()" (click)="create(true)">
          Start from scratch
        </button>
        <button type="button" class="btn btn--primary" [disabled]="creating() || !hasAnalysedCv()" (click)="create(false)"
                [attr.title]="hasAnalysedCv() ? null : 'Analyse a CV on the My CV page first'">
          <app-icon name="sparkle" [size]="16" />
          {{ creating() ? 'Creating…' : 'Create from my CV' }}
        </button>
      </app-page-header>

      @if (jobId) {
        <div class="alert alert--info" role="status">
          <app-icon name="info" [size]="18" />
          <p>Pick a CV to tailor: suggestions will use the skills from the job you were viewing.</p>
        </div>
      }

      @if (error(); as problem) {
        <div class="alert alert--error" role="alert">
          <app-icon name="alert" [size]="18" />
          <p>{{ problem.message }}</p>
        </div>
      }

      @if (loading()) {
        <app-state-panel variant="loading" title="Loading your CVs" message="Fetching saved CVs." />
      } @else if (!items().length) {
        <app-state-panel
          variant="empty"
          title="No CV in the builder yet"
          [message]="hasAnalysedCv()
            ? 'Create one from the CV you analysed: sections, roles and skills are filled in for you.'
            : 'Upload or paste your CV on the My CV page first, or start from scratch.'"
        />
      } @else {
        <section class="grid grid--jobs" aria-label="Saved CVs">
          @for (item of items(); track item.id) {
            <article class="card card--padded card--interactive resume-card">
              <div class="row row--between">
                <span class="tag tag--neutral">{{ item.language === 'fr' ? 'Français' : 'English' }} · {{ item.template }}</span>
                @if (item.ats_score !== null) {
                  <span class="tag" [class.tag--success]="item.ats_score >= 75" [class.tag--warning]="item.ats_score < 75">
                    ATS {{ item.ats_score }}
                  </span>
                }
              </div>
              <h2><a [routerLink]="['/editor', item.id]" [queryParams]="jobId ? { jobId } : {}">{{ item.title }}</a></h2>
              <p class="muted small">Edited {{ item.updated_at + 'Z' | date: 'mediumDate' }}</p>
              <div class="card__footer row row--between">
                <a class="btn btn--primary btn--sm" [routerLink]="['/editor', item.id]" [queryParams]="jobId ? { jobId } : {}">
                  {{ jobId ? 'Tailor this CV' : 'Edit' }}
                </a>
                <button type="button" class="btn btn--ghost btn--sm" (click)="remove(item)">Delete</button>
              </div>
            </article>
          }
        </section>
      }
    </div>
  `,
  styles: `
    .resume-card { display: flex; flex-direction: column; gap: 0.6rem; }
    .resume-card h2 { margin: 0; font-size: 1rem; }
    .resume-card h2 a { color: var(--color-heading); text-decoration: none; }
    .resume-card h2 a:hover { color: var(--color-primary); }
    .resume-card p { margin: 0; }
    .card__footer { margin-top: auto; }
  `,
})
export class ResumesPage {
  private readonly api = inject(JobMatchApiService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private readonly store = inject(WorkspaceStore);
  /** Set when arriving from a job page: suggestions are tailored to it. */
  readonly jobId = inject(ActivatedRoute).snapshot.queryParamMap.get('jobId');

  readonly items = signal<ResumeSummary[]>([]);
  readonly loading = signal(true);
  readonly creating = signal(false);
  readonly error = signal<ApiError | null>(null);
  readonly hasAnalysedCv = () => this.store.cvProfile() !== null;

  constructor() {
    this.api
      .listResumes()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.items.set(response.items);
          this.loading.set(false);
        },
        error: (error: unknown) => {
          this.error.set(toApiError(error));
          this.loading.set(false);
        },
      });
  }

  create(blank: boolean): void {
    this.creating.set(true);
    this.error.set(null);
    this.api
      .createResume({ blank, from_analysis_id: blank ? null : (this.store.cvProfile()?.analysis_id ?? null) })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (resume) =>
          void this.router.navigate(['/editor', resume.id], {
            queryParams: this.jobId ? { jobId: this.jobId } : {},
          }),
        error: (error: unknown) => {
          this.error.set(toApiError(error));
          this.creating.set(false);
        },
      });
  }

  remove(item: ResumeSummary): void {
    if (!confirm(`Delete "${item.title}"? This cannot be undone.`)) {
      return;
    }
    this.api
      .deleteResume(item.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => this.items.update((items) => items.filter((entry) => entry.id !== item.id)),
        error: (error: unknown) => this.error.set(toApiError(error)),
      });
  }
}
