import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { JobMatchApiService } from '../../core/api.service';
import { ApplicationsStore } from '../../core/applications.store';
import { JobApplication } from '../../core/resume.models';
import { ApplicationsPage } from './applications.page';

function application(overrides: Partial<JobApplication>): JobApplication {
  return {
    id: 'a1',
    job_id: 'job-1',
    title: 'Data Analyst',
    company: 'ACME',
    location: 'Tunis, Tunisia',
    apply_url: 'https://example.com/apply',
    status: 'saved',
    notes: null,
    match_score: 0.82,
    resume_id: null,
    job_active: true,
    applied_at: null,
    created_at: '2026-11-14T10:00:00',
    updated_at: '2026-11-14T10:00:00',
    ...overrides,
  };
}

describe('ApplicationsPage', () => {
  let fixture: ComponentFixture<ApplicationsPage>;
  let page: ApplicationsPage;
  const api = {
    listApplications: vi.fn(() =>
      of({
        items: [
          application({}),
          application({ id: 'a2', job_id: 'job-2', title: 'QA Engineer', status: 'interview', applied_at: '2026-11-10T09:00:00', job_active: false }),
        ],
        counts: {},
      }),
    ),
    updateApplication: vi.fn((_id: string, request: { status?: string; notes?: string }) =>
      of(application({ ...request, status: (request.status as JobApplication['status']) ?? 'saved', applied_at: '2026-11-14T10:05:00' })),
    ),
    deleteApplication: vi.fn(() => of(undefined)),
  };

  beforeEach(async () => {
    Object.values(api).forEach((mock) => mock.mockClear());
    await TestBed.configureTestingModule({
      imports: [ApplicationsPage],
      providers: [provideRouter([]), { provide: JobMatchApiService, useValue: api }],
    }).compileComponents();
    TestBed.inject(ApplicationsStore).reset();
    fixture = TestBed.createComponent(ApplicationsPage);
    page = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('groups jobs by status with counts and match scores', () => {
    const text: string = fixture.nativeElement.textContent;
    expect(text).toContain('2 tracked');
    expect(text).toContain('1 interviews');
    expect(text).toContain('82%');
    expect(text).toContain('This posting is no longer listed.');
    expect(page.grouped().get('interview')?.map((item) => item.title)).toEqual(['QA Engineer']);
  });

  it('moves a job to another status', () => {
    const select = document.createElement('select');
    const option = document.createElement('option');
    option.value = 'applied';
    select.appendChild(option);
    select.value = 'applied';
    page.move(page.store.items()[0], { target: select } as unknown as Event);
    expect(api.updateApplication).toHaveBeenCalledWith('a1', { status: 'applied' });
    expect(page.grouped().get('applied')?.length).toBe(1);
  });

  it('filters to one status', () => {
    page.setFilter('interview');
    fixture.detectChanges();
    const columns = fixture.nativeElement.querySelectorAll('.tr-column');
    expect(columns.length).toBe(1);
    expect(columns[0].textContent).toContain('QA Engineer');
  });
});
