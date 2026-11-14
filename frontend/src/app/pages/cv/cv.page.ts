import { ChangeDetectionStrategy, Component, computed, DestroyRef, ElementRef, inject, signal, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiError, toApiError } from '../../core/api-error';
import {
  CVProfileResponse,
  DocumentMetadata,
  DocumentType,
  ProfileSkill,
  SkillCategory,
} from '../../core/api.models';
import { JobMatchApiService } from '../../core/api.service';
import { formatDate, skillCategoryLabel } from '../../core/formatters';
import { WorkspaceStore } from '../../core/workspace.store';
import { AtsReportComponent } from '../../shared/components/ats-report/ats-report';
import { IconComponent } from '../../shared/components/icon/icon';

type InputMode = 'paste' | 'upload';

interface SkillGroup {
  category: SkillCategory;
  label: string;
  skills: ProfileSkill[];
}

const MAX_TEXT_CHARS = 200_000;
const MAX_FILE_BYTES = 5_000_000;
const TEXT_EXTENSIONS = ['.txt', '.text', '.md', '.csv'];
const ACCEPTED_EXTENSIONS = [...TEXT_EXTENSIONS, '.pdf', '.docx'];
const ACCEPT_ATTRIBUTE = [
  ...ACCEPTED_EXTENSIONS,
  'text/plain',
  'text/markdown',
  'text/csv',
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
].join(',');

@Component({
  selector: 'app-cv-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ReactiveFormsModule,
    RouterLink,
    AtsReportComponent,
    IconComponent,
  ],
  templateUrl: './cv.page.html',
  styleUrl: './cv.page.css',
})
export class CvPage {
  private readonly api = inject(JobMatchApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly formBuilder = inject(FormBuilder);
  readonly store = inject(WorkspaceStore);

  readonly form = this.formBuilder.nonNullable.group({
    resumeText: ['', [Validators.maxLength(MAX_TEXT_CHARS)]],
    resumeId: ['', [Validators.maxLength(120)]],
    category: ['', [Validators.maxLength(120)]],
  });

  readonly mode = signal<InputMode>('upload');
  readonly showUploader = signal(false);
  readonly dragging = signal(false);
  readonly selectedFile = signal<File | null>(null);
  readonly fileError = signal('');
  readonly loading = signal(false);
  readonly clearingCv = signal(false);
  readonly uploadProgress = signal<number | null>(null);
  readonly apiError = signal<ApiError | null>(null);
  readonly successMessage = signal('');
  readonly acceptedExtensions = ACCEPT_ATTRIBUTE;
  readonly maxFileLabel = '5 MB';
  readonly maxTextChars = MAX_TEXT_CHARS;
  readonly uploadStatus = computed(() => {
    if (!this.loading() || this.mode() !== 'upload') {
      return '';
    }
    const percent = this.uploadProgress();
    if (percent === null) {
      return 'Extracting and analysing your CV…';
    }
    return percent < 100
      ? `Uploading ${percent}%`
      : 'Extracting text and analysing…';
  });
  readonly skillGroups = computed<SkillGroup[]>(() =>
    groupSkills(this.store.cvProfile()?.profile.skills ?? []),
  );
  readonly cvProfile = this.store.cvProfile;
  readonly hasCv = this.store.hasCv;
  readonly updatedAt = computed(() => formatDate(this.store.cvUpdatedAt()));
  readonly skillCategoryLabel = skillCategoryLabel;

  @ViewChild('fileInput')
  private fileInput?: ElementRef<HTMLInputElement>;

  switchMode(mode: InputMode): void {
    this.mode.set(mode);
    this.apiError.set(null);
    this.successMessage.set('');
    this.fileError.set('');
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    this.apiError.set(null);
    this.successMessage.set('');

    if (!file) {
      this.selectedFile.set(null);
      this.fileError.set('');
      return;
    }

    const validationError = validateFile(file);
    if (validationError) {
      this.selectedFile.set(null);
      this.fileError.set(validationError);
      input.value = '';
      return;
    }

    this.fileError.set('');
    this.selectedFile.set(file);
    // One step: choosing or dropping a file starts the check right away.
    this.submitUpload();
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    if (!this.loading()) {
      this.dragging.set(true);
    }
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragging.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (!file || this.loading()) {
      return;
    }
    this.apiError.set(null);
    this.successMessage.set('');
    const validationError = validateFile(file);
    if (validationError) {
      this.selectedFile.set(null);
      this.fileError.set(validationError);
      return;
    }
    this.fileError.set('');
    this.selectedFile.set(file);
    this.submitUpload();
  }

  submitPaste(): void {
    this.form.controls.resumeText.markAsTouched();
    this.apiError.set(null);
    this.successMessage.set('');

    const resumeText = this.form.controls.resumeText.value.trim();
    if (!resumeText) {
      return;
    }
    if (resumeText.length > MAX_TEXT_CHARS) {
      return;
    }

    this.loading.set(true);
    this.api
      .analyzeCv({
        resume_text: resumeText,
        resume_id: this.form.controls.resumeId.value.trim() || null,
        category: this.form.controls.category.value.trim() || null,
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => this.onAnalysisSuccess(resumeText, response),
        error: (error: unknown) => this.onAnalysisError(error),
      });
  }

  submitUpload(): void {
    const file = this.selectedFile();
    this.apiError.set(null);
    this.successMessage.set('');

    if (!file) {
      this.fileError.set('Choose a PDF, Word or text file to continue.');
      return;
    }

    const validationError = validateFile(file);
    if (validationError) {
      this.fileError.set(validationError);
      return;
    }

    this.loading.set(true);
    this.uploadProgress.set(0);

    if (isTextFile(file)) {
      file
        .text()
        .then((text) => {
          if (text.length > MAX_TEXT_CHARS) {
            this.loading.set(false);
            this.uploadProgress.set(null);
            this.fileError.set(
              `The CV must be ${MAX_TEXT_CHARS.toLocaleString('en-US')} characters or fewer.`,
            );
            return;
          }
          this.uploadDocument(file, text);
        })
        .catch(() => {
          this.loading.set(false);
          this.uploadProgress.set(null);
          this.fileError.set(
            'The selected text file could not be read. Try another TXT file.',
          );
        });
      return;
    }

    this.uploadDocument(file, null);
  }

  clearCv(): void {
    if (!window.confirm('Delete all saved CV analyses from your account?')) {
      return;
    }

    this.clearingCv.set(true);
    this.apiError.set(null);
    this.api
      .deleteCvHistory()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          this.store.clearCv();
          this.form.reset({ resumeText: '', resumeId: '', category: '' });
          this.clearFileSelection();
          this.successMessage.set('Saved CV analyses were deleted.');
          this.clearingCv.set(false);
        },
        error: (error: unknown) => {
          this.apiError.set(toApiError(error));
          this.clearingCv.set(false);
        },
      });
  }

  textError(): string {
    const control = this.form.controls.resumeText;
    if (!control.touched) {
      return '';
    }
    if (!control.value.trim()) {
      return 'Paste your CV text to continue.';
    }
    if (control.hasError('maxlength')) {
      return `CV text must be ${MAX_TEXT_CHARS.toLocaleString('en-US')} characters or fewer.`;
    }
    return '';
  }

  fileSize(file: File): string {
    if (file.size < 1024) {
      return `${file.size} B`;
    }
    if (file.size < 1024 * 1024) {
      return `${(file.size / 1024).toFixed(1)} KB`;
    }
    return `${(file.size / (1024 * 1024)).toFixed(1)} MB`;
  }

  skillEvidence(skill: ProfileSkill): string {
    return skill.evidence.length
      ? `Evidence: ${skill.evidence.join(' · ')}`
      : 'No evidence snippet recorded';
  }

  confidence(skill: ProfileSkill): string {
    return `${Math.round(skill.confidence * 100)}% confidence`;
  }

  documentTypeLabel(type: DocumentType): string {
    return type === 'pdf' ? 'PDF' : type === 'docx' ? 'DOCX' : 'Text';
  }

  documentFormat(document: DocumentMetadata): string {
    return document.page_count
      ? `${this.documentTypeLabel(document.document_type)} · ${document.page_count} page${document.page_count === 1 ? '' : 's'}`
      : this.documentTypeLabel(document.document_type);
  }

  private uploadDocument(file: File, resumeText: string | null): void {
    this.api
      .analyzeCvUpload(
        file,
        {
          resumeId: this.form.controls.resumeId.value,
          category: this.form.controls.category.value,
        },
        (percent) => this.uploadProgress.set(percent),
      )
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.uploadProgress.set(null);
          this.onAnalysisSuccess(resumeText, response);
          this.clearFileSelection();
        },
        error: (error: unknown) => {
          this.uploadProgress.set(null);
          this.onAnalysisError(error);
        },
      });
  }

  private onAnalysisSuccess(
    resumeText: string | null,
    response: CVProfileResponse,
  ): void {
    this.store.setCv(resumeText, response);
    this.store.recordActivity({
      type: 'cv_analysis',
      title: 'CV analysed',
      detail: `${response.skill_count} skills extracted · ${response.profile.category || 'category not set'}`,
    });
    this.loading.set(false);
    this.showUploader.set(false);
    this.successMessage.set(
      response.ats
        ? `Done! Your ATS score is ${response.ats.score}/100. See what to fix below.`
        : response.message,
    );
  }

  private onAnalysisError(error: unknown): void {
    this.loading.set(false);
    this.apiError.set(toApiError(error));
  }

  private clearFileSelection(): void {
    this.selectedFile.set(null);
    this.fileError.set('');
    this.uploadProgress.set(null);
    if (this.fileInput) {
      this.fileInput.nativeElement.value = '';
    }
  }
}

function validateFile(file: File): string {
  const lowerName = file.name.toLowerCase();
  const extensionAccepted = ACCEPTED_EXTENSIONS.some((extension) =>
    lowerName.endsWith(extension),
  );

  if (!extensionAccepted) {
    return 'Please use a PDF, Word (.docx) or text file (TXT, PDF, or DOCX).';
  }

  if (file.size > MAX_FILE_BYTES) {
    return `The file is too large. The maximum size is ${MAX_FILE_BYTES / 1_000_000} MB.`;
  }

  if (file.size === 0) {
    return 'The selected file is empty.';
  }

  return '';
}

function isTextFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return TEXT_EXTENSIONS.some((extension) => name.endsWith(extension));
}

function groupSkills(skills: ProfileSkill[]): SkillGroup[] {
  const groups = new Map<SkillCategory, ProfileSkill[]>();

  for (const skill of skills) {
    const current = groups.get(skill.category) ?? [];
    current.push(skill);
    groups.set(skill.category, current);
  }

  return [...groups.entries()]
    .map(([category, items]) => ({
      category,
      label: skillCategoryLabel(category),
      skills: items.sort((left, right) => left.skill.localeCompare(right.skill)),
    }))
    .sort((left, right) => left.label.localeCompare(right.label));
}
