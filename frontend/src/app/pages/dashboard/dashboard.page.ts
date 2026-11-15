import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { catchError, forkJoin, of } from 'rxjs';
import {
  CVAnalysisSummary,
  JobSummary,
  RecommendationRequest,
  RecommendationResponse,
} from '../../core/api.models';
import { JobMatchApiService } from '../../core/api.service';
import { CareerInsights } from '../../core/career.models';
import { ApplicationsStore } from '../../core/applications.store';
import { AuthService } from '../../core/auth.service';
import { postedAgo } from '../../core/formatters';
import { ResumeSummary } from '../../core/resume.models';
import { WorkspaceStore } from '../../core/workspace.store';
import { IconComponent, IconName } from '../../shared/components/icon/icon';
import { ScoreRingComponent } from '../../shared/components/score-ring/score-ring';

export interface JourneyStep {
  id: string;
  title: string;
  text: string;
  done: boolean;
  cta: string;
  link: string;
  icon: IconName;
}

const ATS_TARGET = 80;

@Component({
  selector: 'app-dashboard-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, IconComponent, ScoreRingComponent],
  templateUrl: './dashboard.page.html',
  styleUrl: './dashboard.page.css',
})
export class DashboardPage {
  private readonly api = inject(JobMatchApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly auth = inject(AuthService);
  readonly store = inject(WorkspaceStore);
  readonly applications = inject(ApplicationsStore);

  readonly loading = signal(true);
  readonly history = signal<CVAnalysisSummary[]>([]);
  readonly resumes = signal<ResumeSummary[]>([]);
  readonly recommendations = signal<RecommendationResponse | null>(null);
  readonly totalJobs = signal<number | null>(null);
  readonly career = signal<CareerInsights | null>(null);
  readonly postedAgo = postedAgo;

  readonly firstName = computed(() => {
    const name = this.auth.user()?.full_name?.trim();
    return name ? name.split(/\s+/)[0] : '';
  });
  readonly ats = computed(() => this.store.cvProfile()?.ats ?? null);
  readonly atsTone = computed<'strong' | 'moderate' | 'weak'>(() => {
    const score = this.ats()?.score ?? 0;
    return score >= 75 ? 'strong' : score >= 50 ? 'moderate' : 'weak';
  });
  /** ATS scores of past analyses, oldest first, for the trend line. */
  readonly trend = computed(() =>
    this.history()
      .filter((item) => typeof item.ats_score === 'number')
      .map((item) => item.ats_score as number)
      .reverse()
      .slice(-8),
  );
  readonly trendPoints = computed(() => {
    const values = this.trend();
    if (values.length < 2) {
      return '';
    }
    const step = 100 / (values.length - 1);
    return values.map((value, index) => `${(index * step).toFixed(1)},${(36 - (value / 100) * 32).toFixed(1)}`).join(' ');
  });
  readonly trendDelta = computed(() => {
    const values = this.trend();
    return values.length >= 2 ? values[values.length - 1] - values[0] : null;
  });

  readonly steps = computed<JourneyStep[]>(() => {
    const hasCv = this.store.hasCv();
    const score = this.ats()?.score ?? 0;
    const counts = this.applications.counts();
    return [
      {
        id: 'cv', icon: 'upload', title: 'Add your CV',
        text: 'Upload a PDF or Word file, or paste the text.',
        done: hasCv, cta: 'Add my CV', link: '/cv',
      },
      {
        id: 'ats', icon: 'target', title: `Reach an ATS score of ${ATS_TARGET}+`,
        text: hasCv ? `You are at ${score}. Follow the top fixes below.` : 'Your score appears right after the upload.',
        done: hasCv && score >= ATS_TARGET, cta: 'See my fixes', link: '/cv',
      },
      {
        id: 'builder', icon: 'edit', title: 'Polish it in the CV builder',
        text: 'Edit with a live preview and download an ATS-safe Word or PDF file.',
        done: this.resumes().length > 0, cta: 'Open the builder', link: '/editor',
      },
      {
        id: 'save', icon: 'bookmark', title: 'Save jobs that fit',
        text: 'Browse real openings in Tunisia and bookmark the best matches.',
        done: this.applications.items().length > 0, cta: 'Browse jobs', link: '/jobs',
      },
      {
        id: 'apply', icon: 'arrow-right', title: 'Apply and track',
        text: 'Apply on the employer site, then move the job to "Applied".',
        done: counts.applied + counts.interview + counts.offer + counts.rejected > 0,
        cta: 'Open my tracker', link: '/applications',
      },
    ];
  });
  readonly completed = computed(() => this.steps().filter((step) => step.done).length);
  readonly nextStep = computed(() => this.steps().find((step) => !step.done) ?? null);
  readonly topFixes = computed(() => (this.ats()?.top_fixes ?? []).slice(0, 3));
  readonly bestMatches = computed(() => (this.recommendations()?.recommendations ?? []).slice(0, 5));

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    const profile = this.store.cvProfile();
    const preferences = this.store.preferences();
    let request: RecommendationRequest | null = null;
    if (profile) {
      request = {
        resume_id: profile.profile.resume_id,
        resume_category: profile.profile.category,
        experience_evidence: profile.profile.experience_evidence,
        job_category: preferences.preferredCategory || null,
        limit: 5,
        candidate_pool_size: preferences.candidatePoolSize,
      };
      const text = this.store.resumeText();
      if (text) {
        request.resume_text = text;
      } else {
        request.resume_profile = profile.profile;
      }
    }
    // Each panel degrades on its own: one failing request must not blank the page.
    forkJoin({
      history: profile ? this.api.getCvHistory(10).pipe(catchError(() => of(null))) : of(null),
      resumes: this.api.listResumes().pipe(catchError(() => of(null))),
      recommendations: request
        ? this.api.getRecommendations(request).pipe(catchError(() => of(null)))
        : of(null),
      jobs: this.api.listJobs({ limit: 1 }).pipe(catchError(() => of(null))),
      applications: this.applications.load().pipe(catchError(() => of(null))),
      career: this.api.getCareerInsights().pipe(catchError(() => of(null))),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => {
        this.history.set(result.history?.items ?? []);
        this.resumes.set(result.resumes?.items ?? []);
        this.recommendations.set(result.recommendations);
        this.totalJobs.set(result.jobs?.total ?? null);
        this.career.set(result.career);
        this.loading.set(false);
      });
  }

  percent(value: number): number {
    return Math.round(value * 100);
  }

  tone(value: number): string {
    return value >= 0.75 ? 'strong' : value >= 0.4 ? 'moderate' : 'weak';
  }

  toggleSave(job: JobSummary): void {
    this.applications.toggle(job.job_id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({ error: () => undefined });
  }
}
