import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import {
  HealthResponse,
  JobListResponse,
  RecommendationRequest,
  RecommendationResponse,
} from '../../core/api.models';
import { ApiError, toApiError } from '../../core/api-error';
import { JobMatchApiService } from '../../core/api.service';
import {
  classificationLabel,
  classificationTone,
  formatDate,
  formatDateTime,
  formatPercent,
} from '../../core/formatters';
import { RecentActivity, WorkspaceStore } from '../../core/workspace.store';
import { IconComponent } from '../../shared/components/icon/icon';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header';
import { ScoreRingComponent } from '../../shared/components/score-ring/score-ring';
import { StatePanelComponent } from '../../shared/components/state-panel/state-panel';

interface DashboardData {
  health: HealthResponse;
  jobs: JobListResponse;
  recommendations: RecommendationResponse | null;
}

@Component({
  selector: 'app-dashboard-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DecimalPipe,
    RouterLink,
    IconComponent,
    PageHeaderComponent,
    ScoreRingComponent,
    StatePanelComponent,
  ],
  templateUrl: './dashboard.page.html',
  styleUrl: './dashboard.page.css',
})
export class DashboardPage {
  private readonly api = inject(JobMatchApiService);
  private readonly destroyRef = inject(DestroyRef);
  readonly store = inject(WorkspaceStore);

  readonly loading = signal(true);
  readonly error = signal<ApiError | null>(null);
  readonly data = signal<DashboardData | null>(null);

  readonly hasCv = this.store.hasCv;
  readonly cvProfile = this.store.cvProfile;
  readonly rankedMatches = computed(
    () => this.data()?.recommendations?.recommendations ?? [],
  );
  readonly topMatches = computed(() => this.rankedMatches().slice(0, 3));
  readonly rankedCount = computed(() => this.rankedMatches().length);
  readonly evaluatedCount = computed(
    () => this.data()?.recommendations?.evaluated_count ?? 0,
  );
  readonly jobsAvailable = computed(() => this.data()?.jobs.total ?? 0);
  readonly apiStatus = computed(() =>
    this.data()?.health.status === 'ok' ? 'Online' : 'Unknown',
  );
  readonly recentActivity = computed(() => this.store.recentActivity().slice(0, 4));
  readonly analysisDate = computed(() => formatDate(this.store.cvUpdatedAt()));

  readonly classificationLabel = classificationLabel;
  readonly classificationTone = classificationTone;
  readonly formatPercent = formatPercent;
  readonly formatDateTime = formatDateTime;

  constructor() {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);

    const profile = this.store.cvProfile();
    const resumeText = this.store.resumeText();
    const preferences = this.store.preferences();
    let recommendationRequest: RecommendationRequest | null = null;

    if (profile) {
      recommendationRequest = {
        resume_id: profile.profile.resume_id,
        resume_category: profile.profile.category,
        experience_evidence: profile.profile.experience_evidence,
        job_category: preferences.preferredCategory || null,
        limit: preferences.recommendationLimit,
        candidate_pool_size: preferences.candidatePoolSize,
      };

      if (resumeText) {
        recommendationRequest.resume_text = resumeText;
      } else {
        recommendationRequest.resume_profile = profile.profile;
      }
    }

    const recommendations$ = recommendationRequest
      ? this.api.getRecommendations(recommendationRequest)
      : of(null);

    forkJoin({
      health: this.api.health(),
      jobs: this.api.listJobs({ limit: 1 }),
      recommendations: recommendations$,
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (result) => {
          this.data.set(result);
          this.loading.set(false);
        },
        error: (error: unknown) => {
          this.error.set(toApiError(error));
          this.loading.set(false);
        },
      });
  }

  activityIcon(activity: RecentActivity) {
    if (activity.type === 'cv_analysis') {
      return 'cv' as const;
    }
    if (activity.type === 'job_match') {
      return 'matches' as const;
    }
    return 'jobs' as const;
  }
}
