import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { JobMatchApiService } from '../../core/api.service';
import {
  LLM_JOB_MATCH_RESPONSE,
  PROFILE_RESPONSE,
  RECOMMENDATIONS,
} from '../../core/testing/fixtures';
import { WorkspaceStore } from '../../core/workspace.store';
import { MatchesPage } from './matches.page';

describe('MatchesPage', () => {
  let fixture: ComponentFixture<MatchesPage>;
  let page: MatchesPage;
  let store: WorkspaceStore;

  const api = {
    getRecommendations: vi.fn(() => of(RECOMMENDATIONS)),
    matchJob: vi.fn(() => of(LLM_JOB_MATCH_RESPONSE)),
  };

  beforeEach(async () => {
    sessionStorage.clear();
    localStorage.clear();
    api.getRecommendations.mockClear();
    api.matchJob.mockClear();

    await TestBed.configureTestingModule({
      imports: [MatchesPage],
      providers: [
        provideRouter([]),
        { provide: JobMatchApiService, useValue: api },
      ],
    }).compileComponents();

    store = TestBed.inject(WorkspaceStore);
    store.setCv('Python and SQL experience', PROFILE_RESPONSE);

    fixture = TestBed.createComponent(MatchesPage);
    page = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('loads deterministic recommendations for the stored CV', () => {
    expect(api.getRecommendations).toHaveBeenCalledWith(
      expect.objectContaining({
        resume_text: 'Python and SQL experience',
        limit: 10,
        candidate_pool_size: 40,
      }),
    );
    expect(page.rankedItems().length).toBe(1);
    expect(fixture.nativeElement.textContent).toContain('Data Analyst');
    expect(fixture.nativeElement.textContent).toContain('Strong match');
  });

  it('does not request AI explanations without explicit consent', () => {
    page.generateExplanation('job-1');
    expect(api.matchJob).not.toHaveBeenCalled();

    page.setIncludeLlm(true);
    page.generateExplanation('job-1');
    expect(api.matchJob).not.toHaveBeenCalled();
  });

  it('sends both LLM authorization flags after opt-in and consent', () => {
    page.setIncludeLlm(true);
    page.setRemoteConsent(true);
    page.generateExplanation('job-1');
    fixture.detectChanges();

    expect(api.matchJob).toHaveBeenCalledWith(
      expect.objectContaining({
        job_id: 'job-1',
        include_llm: true,
        allow_remote_analysis: true,
        resume_text: 'Python and SQL experience',
      }),
    );
    expect(page.llmState('job-1').requested).toBe(true);
    expect(page.llmState('job-1').authorized).toBe(true);
    expect(page.rankedItems()[0].match.explanation?.headline).toContain(
      'Strong alignment',
    );
  });

  it('turns consent off when AI explanations are disabled', () => {
    page.setIncludeLlm(true);
    page.setRemoteConsent(true);
    expect(page.canUseLlm()).toBe(true);

    page.setIncludeLlm(false);

    expect(page.canUseLlm()).toBe(false);
    expect(page.remoteConsent()).toBe(false);
  });
});
