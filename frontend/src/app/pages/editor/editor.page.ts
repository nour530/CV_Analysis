import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  HostListener,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Observable, defer, of, shareReplay, switchMap, tap } from 'rxjs';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ApiError, toApiError } from '../../core/api-error';
import { AtsReport } from '../../core/api.models';
import { JobMatchApiService } from '../../core/api.service';
import { saveFile } from '../../core/save-file';
import {
  CvDocument,
  CvEducation,
  CvExperience,
  CvLanguageCode,
  CvProject,
  CvSuggestion,
  CvSuggestionResponse,
  CvTemplate,
  ResumeResponse,
  emptyCv,
  newItemId,
} from '../../core/resume.models';
import { AtsReportComponent } from '../../shared/components/ats-report/ats-report';
import { IconComponent } from '../../shared/components/icon/icon';
import { StatePanelComponent } from '../../shared/components/state-panel/state-panel';

const AUTOSAVE_MS = 1500;

const HEADINGS: Record<CvLanguageCode, Record<string, string>> = {
  en: {
    summary: 'Professional Summary',
    experience: 'Professional Experience',
    education: 'Education',
    skills: 'Skills',
    languages: 'Languages',
    certifications: 'Certifications',
    projects: 'Projects',
    present: 'Present',
  },
  fr: {
    summary: 'Profil',
    experience: 'Expérience professionnelle',
    education: 'Formation',
    skills: 'Compétences',
    languages: 'Langues',
    certifications: 'Certifications',
    projects: 'Projets',
    present: "Aujourd'hui",
  },
};

type SaveState = 'saved' | 'dirty' | 'saving' | 'error';

@Component({
  selector: 'app-editor-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, AtsReportComponent, IconComponent, StatePanelComponent],
  templateUrl: './editor.page.html',
  styleUrl: './editor.page.css',
})
export class EditorPage {
  private readonly api = inject(JobMatchApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private saveTimer: ReturnType<typeof setTimeout> | null = null;

  readonly record = signal<ResumeResponse | null>(null);
  readonly doc = signal<CvDocument>(emptyCv());
  readonly title = signal('');
  readonly template = signal<CvTemplate>('classic');
  readonly language = signal<CvLanguageCode>('en');
  readonly loading = signal(true);
  readonly loadError = signal<ApiError | null>(null);
  readonly saveState = signal<SaveState>('saved');
  readonly saveError = signal<string>('');
  readonly atsScore = signal<number | null>(null);

  readonly panel = signal<'none' | 'suggestions' | 'ats'>('none');
  readonly useAi = signal(false);
  readonly aiConsent = signal(false);
  readonly suggestions = signal<CvSuggestionResponse | null>(null);
  readonly suggestionsLoading = signal(false);
  readonly suggestionsError = signal<string>('');
  readonly applyMessage = signal<string>('');
  readonly atsReport = signal<AtsReport | null>(null);
  readonly atsLoading = signal(false);
  readonly jobId = signal<string | null>(null);
  readonly downloading = signal<'pdf' | 'docx' | null>(null);
  readonly downloadMessage = signal('');
  readonly skillDraft = signal('');

  readonly headings = computed(() => HEADINGS[this.language()]);
  readonly contactLine = computed(() => {
    const c = this.doc().contact;
    return [c.location, c.phone, c.email, c.linkedin, c.website].filter((part) => !!part).join(' | ');
  });
  readonly pendingSuggestions = computed(() => this.suggestions()?.suggestions ?? []);
  readonly saveLabel = computed(() => {
    switch (this.saveState()) {
      case 'saving':
        return 'Saving…';
      case 'dirty':
        return 'Unsaved changes';
      case 'error':
        return 'Not saved';
      default:
        return 'All changes saved';
    }
  });

  constructor() {
    const id = this.route.snapshot.paramMap.get('id') ?? '';
    this.jobId.set(this.route.snapshot.queryParamMap.get('jobId'));
    this.api
      .getResume(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (record) => {
          this.applyRecord(record);
          this.loading.set(false);
        },
        error: (error: unknown) => {
          this.loadError.set(toApiError(error));
          this.loading.set(false);
        },
      });
    this.api
      .getUserSettings()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (settings) => {
          this.aiConsent.set(settings.allow_remote_analysis);
          this.useAi.set(settings.allow_remote_analysis);
        },
        error: () => undefined,
      });
    this.destroyRef.onDestroy(() => {
      if (this.saveTimer) {
        clearTimeout(this.saveTimer);
        if (this.saveState() === 'dirty') {
          this.save();
        }
      }
    });
  }

  private applyRecord(record: ResumeResponse): void {
    this.record.set(record);
    this.doc.set(record.content);
    this.title.set(record.title);
    this.template.set(record.template);
    this.language.set(record.language);
    this.atsScore.set(record.ats_score);
  }

  // -- editing --------------------------------------------------------------------

  /** Apply a change to a copy of the document and schedule an autosave. */
  edit(change: (draft: CvDocument) => void): void {
    const draft = structuredClone(this.doc());
    change(draft);
    this.doc.set(draft);
    this.markDirty();
  }

  markDirty(): void {
    this.saveState.set('dirty');
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
    }
    this.saveTimer = setTimeout(() => this.save(), AUTOSAVE_MS);
  }

  value(event: Event): string {
    return (event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement).value;
  }

  text(event: Event): string | null {
    const value = this.value(event).trim();
    return value ? value : null;
  }

  setContact(field: keyof CvDocument['contact'], event: Event): void {
    const value = this.text(event);
    this.edit((draft) => {
      draft.contact[field] = value;
    });
  }

  setTitle(event: Event): void {
    this.title.set(this.value(event).slice(0, 120));
    this.markDirty();
  }

  setTemplate(event: Event): void {
    this.template.set(this.value(event) as CvTemplate);
    this.markDirty();
  }

  setLanguage(event: Event): void {
    this.language.set(this.value(event) as CvLanguageCode);
    this.markDirty();
  }

  lines(event: Event): string[] {
    return this.value(event)
      .split('\n')
      .map((line) => line.replace(/^\s*[-•*]\s*/, '').trim())
      .filter((line) => line.length > 0);
  }

  // Experience
  addExperience(): void {
    this.edit((draft) => {
      draft.experience.unshift({
        id: newItemId(),
        title: null,
        company: null,
        location: null,
        start: null,
        end: null,
        current: false,
        bullets: [],
      });
    });
  }

  setExperience(index: number, field: keyof Omit<CvExperience, 'id' | 'bullets' | 'current'>, event: Event): void {
    const value = this.text(event);
    this.edit((draft) => {
      draft.experience[index][field] = value;
    });
  }

  setCurrent(index: number, event: Event): void {
    const checked = (event.target as HTMLInputElement).checked;
    this.edit((draft) => {
      draft.experience[index].current = checked;
      if (checked) {
        draft.experience[index].end = null;
      }
    });
  }

  setBullets(index: number, event: Event): void {
    const bullets = this.lines(event);
    this.edit((draft) => {
      draft.experience[index].bullets = bullets.slice(0, 20);
    });
  }

  // Education
  addEducation(): void {
    this.edit((draft) => {
      draft.education.unshift({ id: newItemId(), degree: null, school: null, location: null, start: null, end: null, details: [] });
    });
  }

  setEducation(index: number, field: keyof Omit<CvEducation, 'id' | 'details'>, event: Event): void {
    const value = this.text(event);
    this.edit((draft) => {
      draft.education[index][field] = value;
    });
  }

  setEducationDetails(index: number, event: Event): void {
    const details = this.lines(event);
    this.edit((draft) => {
      draft.education[index].details = details.slice(0, 10);
    });
  }

  // Projects
  addProject(): void {
    this.edit((draft) => {
      draft.projects.push({ id: newItemId(), name: null, description: null, bullets: [] });
    });
  }

  setProject(index: number, field: keyof Omit<CvProject, 'id' | 'bullets'>, event: Event): void {
    const value = this.text(event);
    this.edit((draft) => {
      draft.projects[index][field] = value;
    });
  }

  setProjectBullets(index: number, event: Event): void {
    const bullets = this.lines(event);
    this.edit((draft) => {
      draft.projects[index].bullets = bullets.slice(0, 10);
    });
  }

  // Lists shared by sections
  move(section: 'experience' | 'education' | 'projects', index: number, delta: -1 | 1): void {
    this.edit((draft) => {
      const list = draft[section] as unknown[];
      const target = index + delta;
      if (target < 0 || target >= list.length) {
        return;
      }
      [list[index], list[target]] = [list[target], list[index]];
    });
  }

  remove(section: 'experience' | 'education' | 'projects' | 'languages', index: number): void {
    this.edit((draft) => {
      (draft[section] as unknown[]).splice(index, 1);
    });
  }

  // Skills, languages, certifications
  setSkillDraft(event: Event): void {
    this.skillDraft.set(this.value(event));
  }

  addSkillsFromDraft(event?: Event): void {
    event?.preventDefault();
    const values = this.skillDraft()
      .split(/[,;\n]/)
      .map((value) => value.trim())
      .filter((value) => value.length > 0);
    if (values.length) {
      this.addSkills(values);
    }
    this.skillDraft.set('');
  }

  addSkills(values: string[]): void {
    this.edit((draft) => {
      const known = new Set(draft.skills.map((skill) => skill.toLocaleLowerCase()));
      for (const value of values) {
        if (!known.has(value.toLocaleLowerCase()) && draft.skills.length < 80) {
          draft.skills.push(value.slice(0, 120));
          known.add(value.toLocaleLowerCase());
        }
      }
    });
  }

  removeSkill(index: number): void {
    this.edit((draft) => {
      draft.skills.splice(index, 1);
    });
  }

  addLanguage(): void {
    this.edit((draft) => {
      draft.languages.push({ name: this.language() === 'fr' ? 'Langue' : 'Language', level: null });
    });
  }

  setLanguageItem(index: number, field: 'name' | 'level', event: Event): void {
    const value = this.text(event);
    this.edit((draft) => {
      if (field === 'name') {
        draft.languages[index].name = value ?? '';
      } else {
        draft.languages[index].level = value;
      }
    });
  }

  setCertifications(event: Event): void {
    const values = this.lines(event);
    this.edit((draft) => {
      draft.certifications = values.slice(0, 30);
    });
  }

  setSummary(event: Event): void {
    const value = this.text(event);
    this.edit((draft) => {
      draft.summary = value;
    });
  }

  joinLines(values: string[]): string {
    return values.join('\n');
  }

  dateRange(start: string | null, end: string | null, current = false): string {
    const finish = current ? this.headings()['present'] : end;
    return start && finish ? `${start} – ${finish}` : (start ?? finish ?? '');
  }

  // -- saving, checking, exporting ----------------------------------------------------

  save(): void {
    this.saveRequest().subscribe({ error: () => undefined });
  }

  /** Completes once the CV on the server matches the editor. */
  private pendingSave(): Observable<unknown> {
    return this.saveState() === 'saved' ? of(null) : this.saveRequest();
  }

  private saveRequest(): Observable<ResumeResponse | null> {
    const record = this.record();
    if (!record) {
      return of(null);
    }
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    // Languages need a name to be valid; drop empty rows rather than failing.
    const content = structuredClone(this.doc());
    content.languages = content.languages.filter((item) => item.name.trim().length > 0);
    this.saveState.set('saving');
    const request = this.api
      .updateResume(record.id, {
        title: this.title().trim() || 'My CV',
        template: this.template(),
        language: this.language(),
        content,
      })
      .pipe(
        tap({
          next: (saved) => {
            this.record.set(saved);
            this.atsScore.set(saved.ats_score);
            if (this.saveState() === 'saving') {
              this.saveState.set('saved');
            }
            this.saveError.set('');
          },
          error: (error: unknown) => {
            this.saveState.set('error');
            this.saveError.set(toApiError(error).message);
          },
        }),
        shareReplay(1),
      );
    return request;
  }

  openAts(): void {
    const record = this.record();
    if (!record) {
      return;
    }
    this.panel.set('ats');
    this.atsLoading.set(true);
    this.pendingSave()
      .pipe(
        switchMap(() => this.api.checkResumeAts(record.id, this.jobId())),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (report) => {
          this.atsReport.set(report);
          this.atsLoading.set(false);
        },
        error: () => this.atsLoading.set(false),
      });
  }

  /** Save the CV as an ATS-friendly PDF or Word file (built by the server
   * from the saved CV, so unsaved edits are saved first). */
  async download(kind: 'pdf' | 'docx'): Promise<void> {
    const record = this.record();
    if (!record || this.downloading()) {
      return;
    }
    this.downloading.set(kind);
    this.downloadMessage.set('');
    const name = (this.doc().contact.full_name || this.title() || 'CV').replace(/[^\p{L}\p{N}-]+/gu, '_');
    const source = defer(() => this.pendingSave()).pipe(
      switchMap(() => (kind === 'pdf' ? this.api.exportResumePdf(record.id) : this.api.exportResumeDocx(record.id))),
    );
    try {
      const result = await saveFile(source, `${name}_CV.${kind}`, kind);
      if (result !== 'cancelled') {
        this.downloadMessage.set(`${kind === 'pdf' ? 'PDF' : 'Word file'} ${result === 'saved' ? 'saved' : 'downloaded'}.`);
      }
    } catch (error) {
      this.downloadMessage.set(toApiError(error).message || 'The download failed. Please try again.');
    } finally {
      this.downloading.set(null);
    }
  }

  // -- suggestions ----------------------------------------------------------------------

  toggleAi(event: Event): void {
    this.useAi.set((event.target as HTMLInputElement).checked);
  }

  loadSuggestions(): void {
    const record = this.record();
    if (!record) {
      return;
    }
    if (this.saveState() !== 'saved') {
      this.save();
    }
    this.panel.set('suggestions');
    this.suggestionsLoading.set(true);
    this.suggestionsError.set('');
    this.applyMessage.set('');
    this.api
      .suggestResume(record.id, {
        scope: 'all',
        job_id: this.jobId(),
        allow_remote_analysis: this.useAi() && this.aiConsent(),
      })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (response) => {
          this.suggestions.set(response);
          this.suggestionsLoading.set(false);
        },
        error: (error: unknown) => {
          this.suggestionsError.set(toApiError(error).message);
          this.suggestionsLoading.set(false);
        },
      });
  }

  targetLabel(item: CvSuggestion): string {
    if (item.field === 'summary') {
      return this.headings()['summary'];
    }
    if (item.field === 'skills') {
      return this.headings()['skills'];
    }
    const experience = this.doc().experience.find((entry) => entry.id === item.target_id);
    if (experience) {
      return [experience.title, experience.company].filter(Boolean).join(' · ') || this.headings()['experience'];
    }
    const project = this.doc().projects.find((entry) => entry.id === item.target_id);
    return project?.name || this.headings()['projects'];
  }

  accept(item: CvSuggestion, skill?: string): void {
    let applied = true;
    if (item.field === 'skills') {
      this.addSkills(skill ? [skill] : item.add_skills);
      if (skill && item.add_skills.length > 1) {
        this.replaceSuggestion({ ...item, add_skills: item.add_skills.filter((value) => value !== skill) });
        return;
      }
    } else if (item.field === 'summary' && item.suggestion) {
      const suggestion = item.suggestion;
      this.edit((draft) => {
        draft.summary = suggestion;
      });
    } else if (item.field === 'bullet' && item.suggestion) {
      applied = this.replaceBullet(item);
    }
    if (applied) {
      this.dismiss(item);
    } else {
      this.applyMessage.set('That bullet point has changed since the suggestion was made, so it was not replaced.');
    }
  }

  private replaceBullet(item: CvSuggestion): boolean {
    const suggestion = item.suggestion ?? '';
    let done = false;
    this.edit((draft) => {
      const owner =
        draft.experience.find((entry) => entry.id === item.target_id) ??
        draft.projects.find((entry) => entry.id === item.target_id);
      if (!owner) {
        return;
      }
      let index = item.index ?? -1;
      if (owner.bullets[index] !== item.original) {
        index = owner.bullets.findIndex((bullet) => bullet === item.original);
      }
      if (index >= 0) {
        owner.bullets[index] = suggestion;
        done = true;
      }
    });
    return done;
  }

  private replaceSuggestion(updated: CvSuggestion): void {
    this.suggestions.update((current) =>
      current
        ? { ...current, suggestions: current.suggestions.map((item) => (item.id === updated.id ? updated : item)) }
        : current,
    );
  }

  dismiss(item: CvSuggestion): void {
    this.suggestions.update((current) =>
      current ? { ...current, suggestions: current.suggestions.filter((entry) => entry.id !== item.id) } : current,
    );
  }

  @HostListener('document:keydown.escape')
  closePanel(): void {
    this.panel.set('none');
  }
}
