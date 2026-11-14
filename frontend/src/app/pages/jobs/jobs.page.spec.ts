import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { JobMatchApiService } from '../../core/api.service';
import { JOB_LIST } from '../../core/testing/fixtures';
import { JobsPage } from './jobs.page';

describe('JobsPage', () => {
  let fixture: ComponentFixture<JobsPage>;
  let page: JobsPage;

  const api = {
    listJobs: vi.fn(() => of(JOB_LIST)),
  };

  beforeEach(async () => {
    api.listJobs.mockClear();

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

  it('submits search and category filters to the API', () => {
    page.form.setValue({ q: 'data', category: 'Data Science' });
    page.search();
    fixture.detectChanges();

    expect(api.listJobs).toHaveBeenLastCalledWith(
      expect.objectContaining({
        q: 'data',
        category: 'Data Science',
        offset: 0,
        limit: 12,
      }),
    );
  });

  it('clears filters and reloads the first page', () => {
    page.form.setValue({ q: 'data', category: 'Data Science' });
    page.clearFilters();

    expect(page.form.getRawValue()).toEqual({ q: '', category: '' });
    expect(api.listJobs).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: '', category: '', offset: 0 }),
    );
  });
});
