import {
  HttpClient,
  HttpEventType,
  HttpParams,
  HttpResponse,
} from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { catchError, Observable, filter, map, tap, throwError, timeout } from 'rxjs';
import { API_BASE_URL } from './api.config';
import {
  AssistantStatus,
  CareerDomainList,
  CareerInsights,
  ChatHistory,
  ChatReply,
  OccupationList,
} from './career.models';
import {
  ApplicationListResponse,
  ApplicationStatus,
  ApplicationUpdateRequest,
  CvEvaluation,
  CvRewriteResponse,
  CvSuggestionRequest,
  JobApplication,
  MatchScoresResponse,
  CvSuggestionResponse,
  ResumeCreateRequest,
  ResumeListResponse,
  ResumeResponse,
  ResumeUpdateRequest,
} from './resume.models';
import { toApiError } from './api-error';
import {
  AtsCheckRequest,
  AtsReport,
  CVAnalysisHistoryResponse,
  CVAnalysisRequest,
  CVProfileResponse,
  HealthResponse,
  JobDetailResponse,
  JobFacetsResponse,
  JobListParams,
  JobListResponse,
  JobMatchRequest,
  JobMatchResponse,
  MatchHistoryResponse,
  RecommendationRequest,
  RecommendationResponse,
  UserSettings,
  UserSettingsUpdate,
} from './api.models';

const API_PREFIX = '/api/v1';
const REQUEST_TIMEOUT_MS = 60_000;

@Injectable({ providedIn: 'root' })
export class JobMatchApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = normalizeBaseUrl(inject(API_BASE_URL));

  health(): Observable<HealthResponse> {
    return this.request(
      this.http.get<HealthResponse>(`${this.baseUrl}/health`),
    );
  }

  analyzeCv(request: CVAnalysisRequest): Observable<CVProfileResponse> {
    return this.request(
      this.http.post<CVProfileResponse>(
        `${this.baseUrl}${API_PREFIX}/cv/analyze`,
        request,
      ),
    );
  }

  analyzeCvUpload(
    file: File,
    metadata: { resumeId?: string | null; category?: string | null } = {},
    onProgress?: (percent: number) => void,
  ): Observable<CVProfileResponse> {
    const form = new FormData();
    form.append('file', file, file.name);

    if (metadata.resumeId?.trim()) {
      form.append('resume_id', metadata.resumeId.trim());
    }
    if (metadata.category?.trim()) {
      form.append('category', metadata.category.trim());
    }

    return this.request(
      this.http
        .post<CVProfileResponse>(
          `${this.baseUrl}${API_PREFIX}/cv/analyze/upload`,
          form,
          { observe: 'events', reportProgress: true },
        )
        .pipe(
          tap((event) => {
            if (
              event.type === HttpEventType.UploadProgress &&
              event.total &&
              onProgress
            ) {
              onProgress(Math.round((event.loaded / event.total) * 100));
            }
          }),
          filter(
            (event): event is HttpResponse<CVProfileResponse> =>
              event.type === HttpEventType.Response,
          ),
          map((event) => event.body as CVProfileResponse),
        ),
    );
  }

  getLatestCv(): Observable<CVProfileResponse> {
    return this.request(
      this.http.get<CVProfileResponse>(`${this.baseUrl}${API_PREFIX}/cv/latest`),
    );
  }

  getCvHistory(limit = 20): Observable<CVAnalysisHistoryResponse> {
    return this.request(
      this.http.get<CVAnalysisHistoryResponse>(
        `${this.baseUrl}${API_PREFIX}/cv/history`,
        { params: new HttpParams().set('limit', String(limit)) },
      ),
    );
  }

  deleteCvHistory(): Observable<void> {
    return this.request(
      this.http.delete<void>(`${this.baseUrl}${API_PREFIX}/cv/history`),
    );
  }

  getUserSettings(): Observable<UserSettings> {
    return this.request(
      this.http.get<UserSettings>(`${this.baseUrl}${API_PREFIX}/account/settings`),
    );
  }

  updateUserSettings(settings: UserSettingsUpdate): Observable<UserSettings> {
    return this.request(
      this.http.put<UserSettings>(
        `${this.baseUrl}${API_PREFIX}/account/settings`,
        settings,
      ),
    );
  }

  getMatchHistory(limit = 20): Observable<MatchHistoryResponse> {
    return this.request(
      this.http.get<MatchHistoryResponse>(
        `${this.baseUrl}${API_PREFIX}/matches/history`,
        { params: new HttpParams().set('limit', String(limit)) },
      ),
    );
  }

  deleteMatchHistory(): Observable<void> {
    return this.request(
      this.http.delete<void>(`${this.baseUrl}${API_PREFIX}/matches/history`),
    );
  }

  listJobs(params: JobListParams = {}): Observable<JobListResponse> {
    let httpParams = new HttpParams();

    if (params.category?.trim()) {
      httpParams = httpParams.set('category', params.category.trim());
    }
    if (params.q?.trim()) {
      httpParams = httpParams.set('q', params.q.trim());
    }
    if (params.workplace) {
      httpParams = httpParams.set('workplace', params.workplace);
    }
    if (params.location?.trim()) {
      httpParams = httpParams.set('location', params.location.trim());
    }
    if (params.company?.trim()) {
      httpParams = httpParams.set('company', params.company.trim());
    }
    if (params.offset !== undefined) {
      httpParams = httpParams.set('offset', String(params.offset));
    }
    if (params.limit !== undefined) {
      httpParams = httpParams.set('limit', String(params.limit));
    }

    return this.request(
      this.http.get<JobListResponse>(`${this.baseUrl}${API_PREFIX}/jobs`, {
        params: httpParams,
      }),
    );
  }

  checkAts(request: AtsCheckRequest = {}): Observable<AtsReport> {
    return this.request(
      this.http.post<AtsReport>(`${this.baseUrl}${API_PREFIX}/cv/ats`, request),
    );
  }

  listApplications(): Observable<ApplicationListResponse> {
    return this.request(
      this.http.get<ApplicationListResponse>(`${this.baseUrl}${API_PREFIX}/applications`),
    );
  }

  saveApplication(jobId: string, status: ApplicationStatus = 'saved'): Observable<JobApplication> {
    return this.request(
      this.http.post<JobApplication>(`${this.baseUrl}${API_PREFIX}/applications`, {
        job_id: jobId,
        status,
      }),
    );
  }

  updateApplication(id: string, request: ApplicationUpdateRequest): Observable<JobApplication> {
    return this.request(
      this.http.patch<JobApplication>(
        `${this.baseUrl}${API_PREFIX}/applications/${encodeURIComponent(id)}`,
        request,
      ),
    );
  }

  deleteApplication(id: string): Observable<void> {
    return this.request(
      this.http.delete<void>(`${this.baseUrl}${API_PREFIX}/applications/${encodeURIComponent(id)}`),
    );
  }

  matchScores(jobIds: string[]): Observable<MatchScoresResponse> {
    return this.request(
      this.http.post<MatchScoresResponse>(`${this.baseUrl}${API_PREFIX}/matches/scores`, {
        job_ids: jobIds.slice(0, 50),
      }),
    );
  }

  getCareerDomains(lang: 'en' | 'fr' = 'en'): Observable<CareerDomainList> {
    return this.request(
      this.http.get<CareerDomainList>(`${this.baseUrl}${API_PREFIX}/career/domains`, {
        params: new HttpParams().set('lang', lang),
      }),
    );
  }

  searchOccupations(query: string, domain: string | null = null, lang: 'en' | 'fr' = 'en'): Observable<OccupationList> {
    let params = new HttpParams().set('q', query.trim()).set('lang', lang).set('limit', '8');
    if (domain) {
      params = params.set('domain', domain);
    }
    return this.request(
      this.http.get<OccupationList>(`${this.baseUrl}${API_PREFIX}/career/occupations`, { params }),
    );
  }

  getCareerInsights(): Observable<CareerInsights> {
    return this.request(this.http.get<CareerInsights>(`${this.baseUrl}${API_PREFIX}/career/insights`));
  }

  getAssistantStatus(): Observable<AssistantStatus> {
    return this.request(this.http.get<AssistantStatus>(`${this.baseUrl}${API_PREFIX}/assistant/status`));
  }

  getAssistantHistory(): Observable<ChatHistory> {
    return this.request(this.http.get<ChatHistory>(`${this.baseUrl}${API_PREFIX}/assistant/messages`));
  }

  sendAssistantMessage(message: string): Observable<ChatReply> {
    return this.request(
      this.http.post<ChatReply>(`${this.baseUrl}${API_PREFIX}/assistant/messages`, { message }),
      120_000,
    );
  }

  clearAssistantHistory(): Observable<void> {
    return this.request(this.http.delete<void>(`${this.baseUrl}${API_PREFIX}/assistant/messages`));
  }

  listResumes(): Observable<ResumeListResponse> {
    return this.request(this.http.get<ResumeListResponse>(`${this.baseUrl}${API_PREFIX}/resumes`));
  }

  createResume(request: ResumeCreateRequest = {}): Observable<ResumeResponse> {
    return this.request(
      this.http.post<ResumeResponse>(`${this.baseUrl}${API_PREFIX}/resumes`, request),
    );
  }

  getResume(id: string): Observable<ResumeResponse> {
    return this.request(
      this.http.get<ResumeResponse>(`${this.baseUrl}${API_PREFIX}/resumes/${encodeURIComponent(id)}`),
    );
  }

  updateResume(id: string, request: ResumeUpdateRequest): Observable<ResumeResponse> {
    return this.request(
      this.http.put<ResumeResponse>(
        `${this.baseUrl}${API_PREFIX}/resumes/${encodeURIComponent(id)}`,
        request,
      ),
    );
  }

  deleteResume(id: string): Observable<void> {
    return this.request(
      this.http.delete<void>(`${this.baseUrl}${API_PREFIX}/resumes/${encodeURIComponent(id)}`),
    );
  }

  checkResumeAts(id: string, jobId: string | null = null): Observable<AtsReport> {
    return this.request(
      this.http.post<AtsReport>(
        `${this.baseUrl}${API_PREFIX}/resumes/${encodeURIComponent(id)}/ats`,
        { job_id: jobId },
      ),
    );
  }

  /** Scores from our own trained CV model (runs on the server, no AI provider). */
  evaluateResume(id: string): Observable<CvEvaluation> {
    return this.request(
      this.http.post<CvEvaluation>(`${this.baseUrl}${API_PREFIX}/resumes/${encodeURIComponent(id)}/evaluation`, {}),
    );
  }

  /** Full AI rebuild and rewrite of the CV (Claude); can take up to a few minutes. */
  rewriteResume(id: string, jobId: string | null = null): Observable<CvRewriteResponse> {
    return this.request(
      this.http.post<CvRewriteResponse>(`${this.baseUrl}${API_PREFIX}/resumes/${encodeURIComponent(id)}/ai-rewrite`, {
        job_id: jobId,
      }),
      240_000,
    );
  }

  suggestResume(id: string, request: CvSuggestionRequest): Observable<CvSuggestionResponse> {
    return this.request(
      this.http.post<CvSuggestionResponse>(
        `${this.baseUrl}${API_PREFIX}/resumes/${encodeURIComponent(id)}/suggestions`,
        request,
      ),
    );
  }

  exportResumeDocx(id: string): Observable<Blob> {
    return this.request(
      this.http.get(`${this.baseUrl}${API_PREFIX}/resumes/${encodeURIComponent(id)}/export.docx`, {
        responseType: 'blob',
      }),
    );
  }

  /** Everything stored about the signed-in user, as a JSON file. */
  exportAccountData(): Observable<Blob> {
    return this.request(
      this.http.get(`${this.baseUrl}${API_PREFIX}/account/export`, { responseType: 'blob' }),
    );
  }

  exportResumePdf(id: string): Observable<Blob> {
    return this.request(
      this.http.get(`${this.baseUrl}${API_PREFIX}/resumes/${encodeURIComponent(id)}/export.pdf`, {
        responseType: 'blob',
      }),
    );
  }

  getJobFacets(): Observable<JobFacetsResponse> {
    return this.request(
      this.http.get<JobFacetsResponse>(`${this.baseUrl}${API_PREFIX}/jobs/facets`),
    );
  }

  getJob(jobId: string): Observable<JobDetailResponse> {
    return this.request(
      this.http.get<JobDetailResponse>(
        `${this.baseUrl}${API_PREFIX}/jobs/${encodeURIComponent(jobId)}`,
      ),
    );
  }

  matchJob(request: JobMatchRequest): Observable<JobMatchResponse> {
    return this.request(
      this.http.post<JobMatchResponse>(
        `${this.baseUrl}${API_PREFIX}/matches/job`,
        request,
      ),
    );
  }

  getRecommendations(
    request: RecommendationRequest,
  ): Observable<RecommendationResponse> {
    return this.request(
      this.http.post<RecommendationResponse>(
        `${this.baseUrl}${API_PREFIX}/matches/recommendations`,
        request,
      ),
    );
  }

  private request<T>(source: Observable<T>, timeoutMs: number = REQUEST_TIMEOUT_MS): Observable<T> {
    return source.pipe(
      timeout(timeoutMs),
      catchError((error: unknown) => throwError(() => toApiError(error))),
    );
  }
}

function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, '');
}
