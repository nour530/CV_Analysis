import { HttpEventType } from '@angular/common/http';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { API_BASE_URL } from './api.config';
import { ApiError } from './api-error';
import { JobMatchApiService } from './api.service';
import { JOB_LIST, PROFILE_RESPONSE } from './testing/fixtures';

const BASE_URL = 'http://api.test';

describe('JobMatchApiService', () => {
  let service: JobMatchApiService;
  let httpMock: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: API_BASE_URL, useValue: `${BASE_URL}/` },
      ],
    });

    service = TestBed.inject(JobMatchApiService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    httpMock.verify();
  });

  it('posts pasted CV text to the analysis endpoint', () => {
    service
      .analyzeCv({ resume_text: 'Python and SQL', resume_id: 'resume-1' })
      .subscribe();

    const request = httpMock.expectOne(`${BASE_URL}/api/v1/cv/analyze`);
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({
      resume_text: 'Python and SQL',
      resume_id: 'resume-1',
    });
    request.flush(PROFILE_RESPONSE);
  });

  it('sends job filters as query parameters', () => {
    service
      .listJobs({ q: 'data', category: 'Data Science', offset: 12, limit: 12 })
      .subscribe();

    const request = httpMock.expectOne(
      (candidate) =>
        candidate.url === `${BASE_URL}/api/v1/jobs` &&
        candidate.params.get('q') === 'data' &&
        candidate.params.get('category') === 'Data Science' &&
        candidate.params.get('offset') === '12' &&
        candidate.params.get('limit') === '12',
    );
    expect(request.request.method).toBe('GET');
    request.flush(JOB_LIST);
  });

  it('reports upload progress and returns the analysed profile', () => {
    const file = new File(['Python and SQL'], 'cv.txt', {
      type: 'text/plain',
    });
    const progress: number[] = [];
    let receivedProfile = false;

    service
      .analyzeCvUpload(file, { resumeId: 'resume-1' }, (percent) =>
        progress.push(percent),
      )
      .subscribe((response) => {
        receivedProfile = response.skill_count === 2;
      });

    const request = httpMock.expectOne(
      `${BASE_URL}/api/v1/cv/analyze/upload`,
    );
    expect(request.request.body).toBeInstanceOf(FormData);

    request.event({
      type: HttpEventType.UploadProgress,
      loaded: 50,
      total: 100,
    });
    request.flush(PROFILE_RESPONSE);

    expect(progress).toEqual([50]);
    expect(receivedProfile).toBe(true);
  });

  it('maps structured API errors with request IDs', () => {
    let captured: ApiError | undefined;

    service.getJob('missing').subscribe({
      error: (error: ApiError) => {
        captured = error;
      },
    });

    const request = httpMock.expectOne(`${BASE_URL}/api/v1/jobs/missing`);
    request.flush(
      {
        error: {
          code: 'job_not_found',
          message: 'Job was not found.',
          details: [],
        },
        request_id: 'req-404',
      },
      { status: 404, statusText: 'Not Found' },
    );

    expect(captured).toBeInstanceOf(ApiError);
    expect(captured?.status).toBe(404);
    expect(captured?.code).toBe('job_not_found');
    expect(captured?.message).toBe('Job was not found.');
    expect(captured?.requestId).toBe('req-404');
  });

  it('maps plain validation failures to a readable API error', () => {
    let captured: ApiError | undefined;

    service.matchJob({ job_id: 'job-1', job_text: 'text' }).subscribe({
      error: (error: ApiError) => {
        captured = error;
      },
    });

    const request = httpMock.expectOne(`${BASE_URL}/api/v1/matches/job`);
    request.flush(
      { detail: [{ loc: ['body'], msg: 'Invalid request', type: 'value_error' }] },
      { status: 422, statusText: 'Unprocessable Entity' },
    );

    expect(captured?.status).toBe(422);
    expect(captured?.code).toBe('validation_error');
    expect(captured?.message).toContain('could not be validated');
  });
});
