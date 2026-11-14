import { ComponentFixture, TestBed } from '@angular/core/testing';
import { AtsReport } from '../../../core/api.models';
import { AtsReportComponent } from './ats-report';

const REPORT: AtsReport = {
  score: 62,
  grade: 'fair',
  summary: 'Some parts of your CV may be misread or overlooked by ATS software. 1 critical issue to fix first.',
  language: 'en',
  categories: [
    { id: 'parsing', label: 'Readability by ATS', score: 100, points: 16, max_points: 16, passed: 2, total: 2 },
    { id: 'contact', label: 'Contact details', score: 40, points: 6, max_points: 15, passed: 1, total: 4 },
    { id: 'keywords', label: 'Job keywords', score: 50, points: 7.5, max_points: 15, passed: 1, total: 2 },
  ],
  checks: [
    { id: 'text_layer', category: 'parsing', status: 'pass', title: 'Text can be read by software', message: 'All text was extracted cleanly.', fix: null, points: 12, max_points: 12 },
    { id: 'email', category: 'contact', status: 'pass', title: 'Email address', message: 'An email address was found.', fix: null, points: 6, max_points: 6 },
    { id: 'phone', category: 'contact', status: 'fail', title: 'Phone number', message: 'No phone number was found.', fix: 'Add a phone number with the country code.', points: 0, max_points: 4 },
  ],
  top_fixes: ['Add a phone number with the country code.'],
  sections: [
    { id: 'experience', label: 'Work experience', heading: 'EXPERIENCE', found: true },
    { id: 'summary', label: 'Summary / profile', heading: null, found: false },
  ],
  keyword_match: {
    job_id: 'job-1',
    job_title: 'Data Analyst',
    company: 'ACME',
    coverage: 0.5,
    matched: ['Python'],
    missing_required: ['Apache Spark'],
    missing_preferred: ['Tableau'],
    title_in_cv: true,
  },
  stats: {
    word_count: 420,
    line_count: 40,
    bullet_count: 8,
    quantified_bullets: 3,
    action_verb_bullets: 6,
    skill_count: 12,
    page_count: 1,
    document_type: 'pdf',
  },
  layout_checked: false,
  version: '1.0',
  disclaimer: 'Applicant tracking systems differ from one employer to another.',
};

describe('AtsReportComponent', () => {
  let fixture: ComponentFixture<AtsReportComponent>;

  async function render(compact = false): Promise<HTMLElement> {
    await TestBed.configureTestingModule({ imports: [AtsReportComponent] }).compileComponents();
    fixture = TestBed.createComponent(AtsReportComponent);
    fixture.componentRef.setInput('report', REPORT);
    fixture.componentRef.setInput('compact', compact);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows the score, grade, top fixes and keyword gaps', async () => {
    const element = await render();
    const text = element.textContent ?? '';
    expect(text).toContain('62%');
    expect(text).toContain('Needs work');
    expect(text).toContain('Fix these first');
    expect(text).toContain('Add a phone number with the country code.');
    expect(text).toContain('Keywords for Data Analyst');
    expect(text).toContain('Apache Spark');
    expect(text).toContain('covers 50%');
    expect(text).toContain('Layout (tables, columns, text boxes) is only checked');
  });

  it('lists checks with problems first and opens groups that need work', async () => {
    const element = await render();
    const groups = Array.from(element.querySelectorAll<HTMLDetailsElement>('details.ats__group'));
    const contact = groups.find((group) => group.textContent?.includes('Contact details'));
    const parsing = groups.find((group) => group.textContent?.includes('Readability by ATS'));
    expect(contact?.open).toBe(true);
    expect(parsing?.open).toBe(false);
    const titles = Array.from(contact!.querySelectorAll('.ats__check-title')).map((node) => node.textContent);
    expect(titles[0]).toBe('Phone number');
  });

  it('hides the detailed checklist in compact mode', async () => {
    const element = await render(true);
    expect(element.querySelector('details.ats__group')).toBeNull();
    expect(element.textContent).toContain('Apache Spark');
  });
});
