import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { of } from 'rxjs';
import { vi } from 'vitest';
import { JobMatchApiService } from '../../core/api.service';
import { PROFILE_RESPONSE } from '../../core/testing/fixtures';
import { WorkspaceStore } from '../../core/workspace.store';
import { CvPage } from './cv.page';

describe('CvPage', () => {
  let fixture: ComponentFixture<CvPage>;
  let page: CvPage;
  let store: WorkspaceStore;

  const api = {
    analyzeCv: vi.fn(() => of(PROFILE_RESPONSE)),
    deleteCvHistory: vi.fn(() => of(undefined)),
    analyzeCvUpload: vi.fn(
      (
        _file: File,
        _metadata: { resumeId?: string | null; category?: string | null },
        onProgress?: (percent: number) => void,
      ) => {
        onProgress?.(100);
        return of(PROFILE_RESPONSE);
      },
    ),
  };

  beforeEach(async () => {
    sessionStorage.clear();
    localStorage.clear();
    api.analyzeCv.mockClear();
    api.analyzeCvUpload.mockClear();
    api.deleteCvHistory.mockClear();
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    await TestBed.configureTestingModule({
      imports: [CvPage],
      providers: [
        provideRouter([]),
        { provide: JobMatchApiService, useValue: api },
      ],
    }).compileComponents();

    store = TestBed.inject(WorkspaceStore);
    fixture = TestBed.createComponent(CvPage);
    page = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('analyses pasted CV text and exposes the profile to the workspace', () => {
    page.form.controls.resumeText.setValue('Python and SQL experience');
    page.form.controls.category.setValue('Data Science');
    page.submitPaste();
    fixture.detectChanges();

    expect(api.analyzeCv).toHaveBeenCalledWith(
      expect.objectContaining({
        resume_text: 'Python and SQL experience',
        category: 'Data Science',
      }),
    );
    expect(store.hasCv()).toBe(true);
    expect(store.resumeText()).toBe('Python and SQL experience');
    expect(page.successMessage()).toContain('CV analysed successfully');
    expect(fixture.nativeElement.textContent).toContain('Python');
  });

  it('validates unsupported CV file types before uploading', () => {
    const file = new File(['not a supported cv'], 'cv.rtf', {
      type: 'application/rtf',
    });

    page.onFileSelected({
      target: { files: [file], value: 'cv.rtf' },
    } as unknown as Event);

    expect(page.selectedFile()).toBeNull();
    expect(page.fileError()).toContain('TXT, PDF, or DOCX');
    expect(api.analyzeCvUpload).not.toHaveBeenCalled();
  });

  it('accepts PDF and DOCX files and starts the check right away', () => {
    for (const [name, type] of [
      ['cv.pdf', 'application/pdf'],
      [
        'cv.docx',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      ],
    ]) {
      const file = new File(['document bytes'], name, { type });
      api.analyzeCvUpload.mockClear();

      page.onFileSelected({
        target: { files: [file], value: name },
      } as unknown as Event);

      expect(page.fileError()).toBe('');
      expect(api.analyzeCvUpload).toHaveBeenCalledTimes(1);
      expect((api.analyzeCvUpload.mock.calls[0] as unknown[])[0]).toBe(file);
    }
  });

  it('uploads a text CV and keeps the text for later matching', async () => {
    const file = new File(['Python and SQL experience'], 'cv.txt', {
      type: 'text/plain',
    });
    Object.defineProperty(file, 'text', {
      value: () => Promise.resolve('Python and SQL experience'),
    });

    page.onFileSelected({
      target: { files: [file], value: 'cv.txt' },
    } as unknown as Event);
    expect(page.selectedFile()).toBe(file);

    page.submitUpload();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(api.analyzeCvUpload).toHaveBeenCalledWith(
      file,
      expect.objectContaining({ resumeId: '', category: '' }),
      expect.any(Function),
    );
    expect(store.resumeText()).toBe('Python and SQL experience');
    expect(store.hasCv()).toBe(true);
  });

  it('uploads a PDF without reading it as text and shows extraction metadata', async () => {
    const file = new File(['%PDF-1.7'], 'resume.pdf', {
      type: 'application/pdf',
    });
    const readText = vi.fn(() => Promise.resolve('should not be read'));
    Object.defineProperty(file, 'text', { value: readText });
    api.analyzeCvUpload.mockReturnValueOnce(
      of({
        ...PROFILE_RESPONSE,
        document: {
          filename: 'resume.pdf',
          document_type: 'pdf' as const,
          page_count: 2,
          extraction_status: 'success' as const,
          character_count: 1234,
        },
      }),
    );

    page.onFileSelected({
      target: { files: [file], value: 'resume.pdf' },
    } as unknown as Event);
    page.submitUpload();
    await fixture.whenStable();
    fixture.detectChanges();

    expect(readText).not.toHaveBeenCalled();
    expect(api.analyzeCvUpload).toHaveBeenCalledWith(
      file,
      expect.objectContaining({ resumeId: '', category: '' }),
      expect.any(Function),
    );
    expect(store.resumeText()).toBeNull();
    expect(store.cvProfile()?.document?.document_type).toBe('pdf');
    expect(fixture.nativeElement.textContent).toContain('resume.pdf');
    expect(fixture.nativeElement.textContent).toContain('PDF · 2 pages');
    expect(fixture.nativeElement.textContent).toContain('1,234');
  });

  it('clears the CV from the current workspace', () => {
    store.setCv('Python and SQL experience', PROFILE_RESPONSE);
    fixture.detectChanges();

    page.clearCv();
    fixture.detectChanges();

    expect(api.deleteCvHistory).toHaveBeenCalled();
    expect(store.hasCv()).toBe(false);
    expect(page.successMessage()).toContain('deleted');
  });
});
