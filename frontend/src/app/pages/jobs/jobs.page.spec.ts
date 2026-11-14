import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { JobFacetsResponse, JobListResponse } from '../../core/api.models';
import { JobMatchApiService } from '../../core/api.service';
import { JOB, JOB_LIST } from '../../core/testing/fixtures';
import { JobsPage } from './jobs.page';

const FACETS: JobFacetsResponse = {
  total: 2,
  live: true,
  categories: [{ value: 'Engineering', count: 2 }],
  cities: [
    { value: 'Tunis', count: 1 },
    { value: 'Sousse', count: 1 },
  ],
  companies: [{ value: 'ACME', count: 2 }],
  workplaces: [
    { value: 'onsite', count: 1 },
    { value: 'remote', count: 1 },
    { value: 'unknown', count: 1 },
  ],
};

const LIVE_JOBS: JobListResponse = {
  ...JOB_LIST,
  jobs: [
    {
      ...JOB,
      job_id: 'smartrecruiters-acme-1',
      title: 'Développeur Python',
      company: 'ACME',
      location: 'Tunis, Tunisia',
      city: 'Tunis',
      workplace: 'hybrid',
      employment_type: 'Full-time',
      apply_url: 'https://jobs.smartrecruiters.com/ACME/1',
      posted_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
      source: 'smartrecruiters',
      source_label: 'SmartRecruiters',
    },
  ],
};

describe('JobsPage', () => {
  let fixture: ComponentFixture<JobsPage>;
  let page: JobsPage;

  const api = {
    listJobs: vi.fn(() => of(JOB_LIST)),
    getJobFacets: vi.fn(() => of(FACETS)),
    listApplications: vi.fn(() => of({ items: [], counts: {} })),
    saveApplication: vi.fn((jobId: string) =>
      of({ id: 'a1', job_id: jobId, title: 'Data Analyst', company: null, location: null, apply_url: null,
           status: 'saved', notes: null, match_score: null, resume_id: null, job_active: true,
           applied_at: null, created_at: '2026-11-14T10:00:00', updated_at: '2026-11-14T10:00:00' }),
    ),
    deleteApplication: vi.fn(() => of(undefined)),
    matchScores: vi.fn(() => of({ has_cv: true, scores: [{ job_id: 'job-1', score: 0.82, classification: 'strong_match' }] })),
  };

  beforeEach(async () => {
    api.listJobs.mockReset();
    api.listJobs.mockImplementation(() => of(JOB_LIST));
    api.getJobFacets.mockClear();

    await TestBed.configureTestingModule({
      imports: [JobsPage],
      providers: [
        provideRouter([]),
        { provide: JobMatchApiService, useValue: api },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(JobsPage);
    page = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('loads and renders jobs from the backend', () => {
    expect(api.listJobs).toHaveBeenCalledWith(
      expect.objectContaining({ offset: 0, limit: 12 }),
    );
    expect(fixture.nativeElement.textContent).toContain('Data Analyst');
    expect(page.total()).toBe(1);
    expect(page.categoryOptions()).toContain('Data Science');
  });

  it('loads city and work-mode filters from facets', () => {
    expect(api.getJobFacets).toHaveBeenCalled();
    expect(page.liveCatalog()).toBe(true);
    expect(page.cityOptions().map((item) => item.value)).toEqual(['Tunis', 'Sousse']);
    expect(page.workplaceOptions().map((item) => item.value)).toEqual(['onsite', 'remote']);
    expect(page.categoryOptions()).toContain('Engineering');
    expect(fixture.nativeElement.textContent).toContain('Jobs in Tunisia');
  });

  it('submits search, category, city and work-mode filters to the API', () => {
    page.form.setValue({
      q: 'data',
      category: 'Data Science',
      location: 'Tunis',
      workplace: 'remote',
    });
    page.search();
    fixture.detectChanges();

    expect(api.listJobs).toHaveBeenLastCalledWith(
      expect.objectContaining({
        q: 'data',
        category: 'Data Science',
        location: 'Tunis',
        workplace: 'remote',
        offset: 0,
        limit: 12,
      }),
    );
  });

  it('clears filters and reloads the first page', () => {
    page.form.setValue({ q: 'data', category: 'Data Science', location: 'Tunis', workplace: 'remote' });
    page.clearFilters();

    expect(page.form.getRawValue()).toEqual({ q: '', category: '', location: '', workplace: '' });
    expect(api.listJobs).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: '', category: '', location: '', workplace: '', offset: 0 }),
    );
  });

  it('saves a job to the tracker with the bookmark button', () => {
    const button: HTMLButtonElement = fixture.nativeElement.querySelector('.save-btn');
    expect(button.getAttribute('aria-pressed')).toBe('false');
    button.click();
    fixture.detectChanges();
    expect(api.saveApplication).toHaveBeenCalledWith('job-1');
    expect(button.getAttribute('aria-pressed')).toBe('true');
  });

  it('shows company, location, date and an external apply link for real jobs', () => {
    api.listJobs.mockImplementation(() => of(LIVE_JOBS));
    page.search();
    fixture.detectChanges();

    const text: string = fixture.nativeElement.textContent;
    expect(text).toContain('ACME');
    expect(text).toContain('Tunis');
    expect(text).toContain('Hybrid');
    expect(text).toContain('3 days ago');

    const apply: HTMLAnchorElement | null = fixture.nativeElement.querySelector(
      'a[href="https://jobs.smartrecruiters.com/ACME/1"]',
    );
    expect(apply).not.toBeNull();
    expect(apply?.target).toBe('_blank');
    expect(apply?.rel).toContain('noopener');
  });
});
