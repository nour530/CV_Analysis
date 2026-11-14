import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { ApiError, toApiError } from '../../core/api-error';
import {
  JobMatchRequest,
  JobMatchResponse,
  RecommendationRequest,
  RecommendationResponse,
} from '../../core/api.models';
import { JobMatchApiService } from '../../core/api.service';
import {
  classificationLabel,
  classificationTone,
  formatPercent,
} from '../../core/formatters';
import { WorkspaceStore } from '../../core/workspace.store';
import { IconComponent } from '../../shared/components/icon/icon';
import { MatchDetailsComponent } from '../../shared/components/match-details/match-details';
import { PageHeaderComponent } from '../../shared/components/page-header/page-header';
import { ScoreRingComponent } from '../../shared/components/score-ring/score-ring';
import { StatePanelComponent } from '../../shared/components/state-panel/state-panel';

interface LlmUiState {
  requested: boolean;
  authorized: boolean;
  loading: boolean;
  error: string | null;
}

const EMPTY_LLM_STATE: LlmUiState = {
  requested: false,
  authorized: false,
  loading: false,
  error: null,
};

@Component({
  selector: 'app-matches-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    DecimalPipe,
    RouterLink,
    IconComponent,
    MatchDetailsComponent,
    PageHeaderComponent,
    ScoreRingComponent,
    StatePanelComponent,
  ],
  templateUrl: './matches.page.html',
  styleUrl: './matches.page.css',
})
export class MatchesPage {
  private readonly api = inject(JobMatchApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  readonly store = inject(WorkspaceStore);

  readonly recommendations = signal<RecommendationResponse | null>(null);
  readonly loading = signal(false);
  readonly error = signal<ApiError | null>(null);
  readonly featured = signal<JobMatchResponse | null>(null);
  readonly featuredJobId = signal<string | null>(null);
  readonly featuredLoading = signal(false);
  readonly featuredError = signal<ApiError | null>(null);
  readonly includeLlm = signal(false);
  readonly remoteConsent = signal(this.store.preferences().allowRemoteAnalysis);
  readonly llmStates = signal<Record<string, LlmUiState>>({});

  readonly hasCv = this.store.hasCv;
  readonly canUseLlm = computed(() => this.includeLlm() && this.remoteConsent());
  readonly rankedItems = computed(
    () => this.recommendations()?.recommendations ?? [],
  );
  readonly warnings = computed(() => this.recommendations()?.warnings ?? []);
  readonly evaluatedCount = computed(
    () => this.recommendations()?.evaluated_count ?? 0,
  );
  readonly candidateCount = computed(
    () => this.recommendations()?.candidate_count ?? 0,
  );

  readonly classificationLabel = classificationLabel;
  readonly classificationTone = classificationTone;
  readonly formatPercent = formatPercent;

  constructor() {
    this.loadRecommendations();

    this.route.queryParamMap
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((params) => {
        const jobId = params.get('jobId');
        this.featuredJobId.set(jobId);
        if (!jobId) {
          this.featured.set(null);
          this.featuredError.set(null);
          this.featuredLoading.set(false);
          return;
        }

        if (this.hasCv()) {
          this.loadFeatured(jobId);
        }
      });
  }

  loadRecommendations(): void {
    const request = this.recommendationRequest();
    if (!request) {
      this.loading.set(false);
      this.recommendations.set(null);
      return;
    }

    this.loading.set(true);
    this.error.set(null);

    this.api
      .getRecommendations(request)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.recommendations.set(response);
          this.store.recordActivity({
            type: 'recommendations',
            title: 'Recommendations refreshed',
            detail: `${response.recommendations.length} ranked roles · ${response.evaluated_count.toLocaleString('en-US')} evaluated`,
          });
          this.loading.set(false);
        },
        error: (error: unknown) => {
          this.error.set(toApiError(error));
          this.loading.set(false);
        },
      });
  }

  loadFeatured(jobId: string): void {
    const request = this.jobMatchRequest(jobId, false);
    if (!request) {
      return;
    }

    this.featuredLoading.set(true);
    this.featuredError.set(null);

    this.api
      .matchJob(request)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.featured.set(response);
          this.featuredLoading.set(false);
          this.recordMatchActivity(response);
        },
        error: (error: unknown) => {
          this.featuredError.set(toApiError(error));
          this.featuredLoading.set(false);
        },
      });
  }

  generateExplanation(jobId: string): void {
    if (!this.canUseLlm()) {
      this.updateLlmState(jobId, {
        error: 'Enable optional AI explanations and confirm external processing first.',
      });
      return;
    }

    const request = this.jobMatchRequest(jobId, true);
    if (!request) {
      this.updateLlmState(jobId, {
        error: 'Add a CV before requesting an AI explanation.',
      });
      return;
    }

    this.updateLlmState(jobId, { loading: true, error: null });

    this.api
      .matchJob(request)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.updateMatch(jobId, response);
          this.updateLlmState(jobId, {
            requested: true,
            authorized: response.llm_authorized,
            loading: false,
            error: null,
          });
          this.recordMatchActivity(response);
        },
        error: (error: unknown) => {
          const apiError = toApiError(error);
          this.updateLlmState(jobId, {
            loading: false,
            error: apiError.message,
          });
        },
      });
  }

  setIncludeLlm(enabled: boolean): void {
    this.includeLlm.set(enabled);
    if (!enabled) {
      this.remoteConsent.set(false);
    }
  }

  setRemoteConsent(enabled: boolean): void {
    this.remoteConsent.set(enabled);
  }

  setFeaturedJob(jobId: string): void {
    void this.router.navigate(['/matches'], { queryParams: { jobId } });
  }

  goToCv(): void {
    void this.router.navigateByUrl('/cv');
  }

  goToSettings(): void {
    void this.router.navigateByUrl('/settings');
  }

  retryFeatured(): void {
    const jobId = this.featuredJobId();
    if (jobId) {
      this.loadFeatured(jobId);
    }
  }

  llmState(jobId: string): LlmUiState {
    return this.llmStates()[jobId] ?? EMPTY_LLM_STATE;
  }

  private recommendationRequest(): RecommendationRequest | null {
    const resume = this.resumeFields();
    if (!resume) {
      return null;
    }

    const preferences = this.store.preferences();
    return {
      ...resume,
      job_category: preferences.preferredCategory || null,
      limit: preferences.recommendationLimit,
      candidate_pool_size: preferences.candidatePoolSize,
    };
  }

  private jobMatchRequest(
    jobId: string,
    includeExplanation: boolean,
  ): JobMatchRequest | null {
    const resume = this.resumeFields();
    if (!resume) {
      return null;
    }

    return {
      ...resume,
      job_id: jobId,
      include_llm: includeExplanation,
      allow_remote_analysis: includeExplanation,
    };
  }

  private resumeFields(): Pick<
    JobMatchRequest,
    'resume_text' | 'resume_profile' | 'resume_id' | 'resume_category' | 'experience_evidence'
  > | null {
    const profileResponse = this.store.cvProfile();
    if (!profileResponse) {
      return null;
    }

    const resumeText = this.store.resumeText();
    const profile = profileResponse.profile;

    return {
      ...(resumeText
        ? { resume_text: resumeText }
        : { resume_profile: profile }),
      resume_id: profile.resume_id,
      resume_category: profile.category,
      experience_evidence: profile.experience_evidence,
    };
  }

  private updateMatch(jobId: string, response: JobMatchResponse): void {
    const recommendations = this.recommendations();
    if (recommendations) {
      this.recommendations.set({
        ...recommendations,
        recommendations: recommendations.recommendations.map((item) =>
          item.job.job_id === jobId
            ? { ...item, match: response.match }
            : item,
        ),
      });
    }

    const featured = this.featured();
    if (featured?.job.job_id === jobId) {
      this.featured.set({
        ...featured,
        match: response.match,
        llm_requested: response.llm_requested,
        llm_authorized: response.llm_authorized,
      });
    }
  }

  private updateLlmState(jobId: string, patch: Partial<LlmUiState>): void {
    this.llmStates.update((states) => ({
      ...states,
      [jobId]: {
        ...(states[jobId] ?? EMPTY_LLM_STATE),
        ...patch,
      },
    }));
  }

  private recordMatchActivity(response: JobMatchResponse): void {
    this.store.recordActivity({
      type: 'job_match',
      title: `Matched ${response.job.title || 'role'}`,
      detail: `${formatPercent(response.match.overall_compatibility_score)} compatibility · ${classificationLabel(response.match.classification)}`,
      score: response.match.overall_compatibility_score,
    });
  }
}
