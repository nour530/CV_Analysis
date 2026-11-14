import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of, throwError } from 'rxjs';
import { vi } from 'vitest';
import { JobMatchApiService } from '../../core/api.service';
import { LandingPage } from './landing.page';

const LIVE = {
  total: 361,
  live: true,
  categories: [],
  cities: [{ value: 'Tunis', count: 140 }, { value: 'Sousse', count: 50 }],
  companies: [{ value: 'LEONI', count: 150 }, { value: 'Devoteam', count: 59 }],
  workplaces: [],
};

async function render(facets: () => unknown) {
  await TestBed.configureTestingModule({
    imports: [LandingPage],
    providers: [provideRouter([]), { provide: JobMatchApiService, useValue: { getJobFacets: vi.fn(facets) } }],
  }).compileComponents();
  const fixture = TestBed.createComponent(LandingPage);
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('LandingPage', () => {
  beforeEach(() => TestBed.resetTestingModule());

  it('shows live job numbers and employers', async () => {
    const element = await render(() => of(LIVE));
    const text = element.textContent ?? '';
    expect(text).toContain('361 open roles from 2 employers right now, in Tunis, Sousse and more');
    expect(text).toContain('LEONI');
    expect(element.querySelector('a[href="/register"]')).not.toBeNull();
  });

  it('still renders when job numbers are unavailable', async () => {
    const element = await render(() => throwError(() => new Error('offline')));
    expect(element.textContent).toContain('Get your CV past the robots');
    expect(element.textContent).toContain('Free · English and French CVs');
  });
});
