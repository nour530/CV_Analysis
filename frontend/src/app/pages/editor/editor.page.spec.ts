import { ComponentFixture, TestBed } from '@angular/core/testing';
import { ActivatedRoute, convertToParamMap, provideRouter } from '@angular/router';
import { NEVER, of } from 'rxjs';
import { vi } from 'vitest';
import { JobMatchApiService } from '../../core/api.service';
import { CvSuggestion, CvSuggestionResponse, ResumeResponse, emptyCv } from '../../core/resume.models';
import { EditorPage } from './editor.page';

function record(): ResumeResponse {
  const content = emptyCv();
  content.contact.full_name = 'Amira Ben Salah';
  content.contact.email = 'amira@example.com';
  content.summary = 'Data analyst.';
  content.experience = [
    {
      id: 'exp1',
      title: 'Data Analyst',
      company: 'Ooredoo',
      location: 'Tunis',
      start: 'Mar 2022',
      end: null,
      current: true,
      bullets: ['Made dashboards', 'Wrote SQL'],
    },
  ];
  content.skills = ['Python'];
  return {
    id: 'r1',
    title: 'My CV',
    language: 'en',
    template: 'classic',
    ats_score: 72,
    updated_at: '2026-11-14T10:00:00',
    created_at: '2026-11-14T10:00:00',
    content,
    source_analysis_id: null,
  };
}

function suggestion(overrides: Partial<CvSuggestion>): CvSuggestion {
  return {
    id: 's1',
    field: 'bullet',
    target_id: 'exp1',
    index: 0,
    original: 'Made dashboards',
    suggestion: 'Built 12 Power BI dashboards for [N] managers',
    reason: 'Stronger verb',
    warnings: ['Replace [N] with your real figure, or remove it.'],
    add_skills: [],
    ...overrides,
  };
}

describe('EditorPage', () => {
  let fixture: ComponentFixture<EditorPage>;
  let page: EditorPage;
  const api = {
    getResume: vi.fn(() => of(record())),
    getUserSettings: vi.fn(() => of({ allow_remote_analysis: true })),
    updateResume: vi.fn((_id: string, request: { content: ResumeResponse['content'] }) =>
      of({ ...record(), content: request.content, ats_score: 81 }),
    ),
    suggestResume: vi.fn(() => of<CvSuggestionResponse>({ source: 'ai', suggestions: [], notice: null, job_id: 'job-9', job_title: 'Data Analyst' })),
    checkResumeAts: vi.fn(() => NEVER),
    exportResumeDocx: vi.fn(() => of(new Blob(['docx']))),
    exportResumePdf: vi.fn(() => of(new Blob(['%PDF']))),
    evaluateResume: vi.fn(() =>
      of({
        available: true,
        overall: 64,
        criteria: [{ id: 'achievements', label: 'Achievements and impact', score: 5.5, evidence: ['1 of 2 bullet points show a figure.'] }],
        checks: [{ id: 'email', label: 'Email address', ok: true }],
        model: { kind: 'ridge', cvs: 1500 },
        notice: null,
      }),
    ),
    rewriteResume: vi.fn(() => {
      const improved = record().content;
      improved.summary = 'Data Analyst with 4 years of experience building Power BI dashboards.';
      improved.experience[0].bullets = ['Built 12 Power BI dashboards for 150 managers', 'Wrote SQL pipelines cutting reporting time by [X%]'];
      return of({
        improved,
        sections: [
          { id: 'contact', changed: false, note: '', warnings: [] },
          { id: 'summary', changed: true, note: 'Adds title and years.', warnings: [] },
          { id: 'experience', changed: true, note: 'Results first.', warnings: ['Replace [X%] with your real figures, or remove them.'] },
        ],
        score_before: 64,
        score_after: 81,
        model: 'claude-opus-5-5',
        job_title: null,
      });
    }),
  };

  beforeEach(async () => {
    vi.useFakeTimers();
    Object.values(api).forEach((mock) => mock.mockClear());
    await TestBed.configureTestingModule({
      imports: [EditorPage],
      providers: [
        provideRouter([]),
        { provide: JobMatchApiService, useValue: api },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: {
              paramMap: convertToParamMap({ id: 'r1' }),
              queryParamMap: convertToParamMap({ jobId: 'job-9' }),
            },
          },
        },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(EditorPage);
    page = fixture.componentInstance;
    fixture.detectChanges();
  });

  afterEach(() => vi.useRealTimers());

  it('loads the CV and renders the live preview', () => {
    const preview: HTMLElement = fixture.nativeElement.querySelector('.cv-page');
    expect(preview.textContent).toContain('Amira Ben Salah');
    expect(preview.textContent).toContain('Mar 2022 – Present');
    expect(preview.textContent).toContain('Made dashboards');
    expect(page.atsScore()).toBe(72);
    expect(page.useAi()).toBe(true);
  });

  it('updates the preview immediately and autosaves after a pause', () => {
    page.edit((draft) => {
      draft.contact.headline = 'Senior Data Analyst';
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.cv-headline').textContent).toContain('Senior Data Analyst');
    expect(page.saveState()).toBe('dirty');
    expect(api.updateResume).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1600);
    expect(api.updateResume).toHaveBeenCalledTimes(1);
    const [id, body] = api.updateResume.mock.calls[0] as unknown as [string, { content: { contact: { headline: string } } }];
    expect(id).toBe('r1');
    expect(body.content.contact.headline).toBe('Senior Data Analyst');
    expect(page.saveState()).toBe('saved');
    expect(page.atsScore()).toBe(81);
  });

  it('requests suggestions tailored to the job with AI consent', () => {
    page.loadSuggestions();
    expect(api.suggestResume).toHaveBeenCalledWith('r1', {
      scope: 'all',
      job_id: 'job-9',
      allow_remote_analysis: true,
    });
  });

  it('accepts a bullet rewrite, adds suggested skills and refuses stale suggestions', () => {
    page.suggestions.set({
      source: 'ai',
      notice: null,
      job_id: null,
      job_title: null,
      suggestions: [
        suggestion({}),
        suggestion({ id: 's2', index: 1, original: 'Old text that changed', suggestion: 'Wrote 30 SQL queries' }),
        suggestion({ id: 's3', field: 'skills', target_id: null, index: null, original: null, suggestion: null, add_skills: ['SQL', 'Tableau'] }),
      ],
    });

    page.accept(page.pendingSuggestions()[0]);
    expect(page.doc().experience[0].bullets[0]).toBe('Built 12 Power BI dashboards for [N] managers');

    page.accept(page.pendingSuggestions()[0]);
    expect(page.doc().experience[0].bullets[1]).toBe('Wrote SQL');
    expect(page.applyMessage()).toContain('has changed');

    const skills = page.pendingSuggestions().find((item) => item.field === 'skills')!;
    page.accept(skills, 'Tableau');
    expect(page.doc().skills).toEqual(['Python', 'Tableau']);
    expect(page.pendingSuggestions().find((item) => item.field === 'skills')?.add_skills).toEqual(['SQL']);
  });

  it('splits bullet text into lines and drops list markers', () => {
    const textarea = document.createElement('textarea');
    textarea.value = '- Built dashboards\n\n• Reduced costs by 20%\n';
    page.setBullets(0, { target: textarea } as unknown as Event);
    expect(page.doc().experience[0].bullets).toEqual(['Built dashboards', 'Reduced costs by 20%']);
  });

  it('saves pending edits, then downloads the PDF file directly', async () => {
    const createObjectURL = vi.fn(() => 'blob:cv');
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    page.edit((draft) => {
      draft.summary = 'Changed';
    });
    await page.download('pdf');
    expect(api.updateResume).toHaveBeenCalledTimes(1);
    expect(api.exportResumePdf).toHaveBeenCalledWith('r1');
    expect(click).toHaveBeenCalledTimes(1);
    expect(page.downloadMessage()).toBe('PDF downloaded.');
    expect(page.downloading()).toBeNull();
    click.mockRestore();
  });

  it('opens the ATS check above the header and closes it with the button, backdrop or Escape', () => {
    page.openAts();
    fixture.detectChanges();
    expect(api.checkResumeAts).toHaveBeenCalledWith('r1', 'job-9');
    const close: HTMLButtonElement = fixture.nativeElement.querySelector('.ed-panel__close');
    expect(close.textContent).toContain('Close');
    close.click();
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector('.ed-panel')).toBeNull();

    page.openAts();
    fixture.detectChanges();
    fixture.nativeElement.querySelector('.ed-backdrop').click();
    expect(page.panel()).toBe('none');

    page.openAts();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(page.panel()).toBe('none');
  });

  it('checks the saved version: unsaved edits are saved before the ATS check', () => {
    page.edit((draft) => {
      draft.summary = 'Changed';
    });
    page.openAts();
    expect(api.updateResume).toHaveBeenCalledTimes(1);
    expect(api.updateResume.mock.invocationCallOrder[0]).toBeLessThan(api.checkResumeAts.mock.invocationCallOrder[0]);
  });

  it('shows the model scores and evidence in the AI review', () => {
    page.openReview();
    fixture.detectChanges();
    expect(api.evaluateResume).toHaveBeenCalledWith('r1');
    const text = fixture.nativeElement.querySelector('.ed-panel').textContent;
    expect(text).toContain('trained on 1500 CVs');
    expect(text).toContain('5.5/10');
    expect(text).toContain('1 of 2 bullet points show a figure.');
  });

  it('rewrites the CV with AI and applies only the accepted sections', () => {
    page.openReview();
    page.runRewrite();
    fixture.detectChanges();
    expect(api.rewriteResume).toHaveBeenCalledWith('r1', 'job-9');
    expect(page.pendingSections().map((s) => s.id)).toEqual(['summary', 'experience']);
    const text = fixture.nativeElement.querySelector('.ed-panel').textContent;
    expect(text).toContain('64 → 81');
    expect(text).toContain('Replace [X%]');

    page.keepSection('summary');
    page.acceptSection('experience');
    expect(page.doc().summary).toBe('Data analyst.');
    expect(page.doc().experience[0].bullets[0]).toBe('Built 12 Power BI dashboards for 150 managers');
    expect(page.pendingSections()).toEqual([]);
    expect(page.acceptedCount()).toBe(1);
  });
});
