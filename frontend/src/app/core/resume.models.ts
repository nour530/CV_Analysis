export type CvLanguageCode = 'en' | 'fr';
export type CvTemplate = 'classic' | 'modern' | 'compact';

export interface CvContact {
  full_name: string | null;
  headline: string | null;
  email: string | null;
  phone: string | null;
  location: string | null;
  linkedin: string | null;
  website: string | null;
}

export interface CvExperience {
  id: string;
  title: string | null;
  company: string | null;
  location: string | null;
  start: string | null;
  end: string | null;
  current: boolean;
  bullets: string[];
}

export interface CvEducation {
  id: string;
  degree: string | null;
  school: string | null;
  location: string | null;
  start: string | null;
  end: string | null;
  details: string[];
}

export interface CvLanguageItem {
  name: string;
  level: string | null;
}

export interface CvProject {
  id: string;
  name: string | null;
  description: string | null;
  bullets: string[];
}

export interface CvDocument {
  contact: CvContact;
  summary: string | null;
  experience: CvExperience[];
  education: CvEducation[];
  skills: string[];
  languages: CvLanguageItem[];
  certifications: string[];
  projects: CvProject[];
}

export interface ResumeSummary {
  id: string;
  title: string;
  language: CvLanguageCode;
  template: CvTemplate;
  ats_score: number | null;
  updated_at: string;
  created_at: string;
}

export interface ResumeResponse extends ResumeSummary {
  content: CvDocument;
  source_analysis_id: string | null;
}

export interface ResumeListResponse {
  items: ResumeSummary[];
}

export interface ResumeCreateRequest {
  title?: string | null;
  from_analysis_id?: string | null;
  blank?: boolean;
  language?: CvLanguageCode | null;
}

export interface ResumeUpdateRequest {
  title?: string;
  language?: CvLanguageCode;
  template?: CvTemplate;
  content?: CvDocument;
}

export type SuggestionField = 'summary' | 'bullet' | 'headline' | 'skills' | 'tip';

export interface CvSuggestion {
  id: string;
  field: SuggestionField;
  target_id: string | null;
  index: number | null;
  original: string | null;
  suggestion: string | null;
  reason: string;
  warnings: string[];
  add_skills: string[];
}

export interface CvSuggestionRequest {
  scope?: 'all' | 'summary' | 'experience' | 'skills';
  experience_id?: string | null;
  job_id?: string | null;
  allow_remote_analysis?: boolean;
}

export interface CvSuggestionResponse {
  source: 'ai' | 'rules';
  suggestions: CvSuggestion[];
  notice: string | null;
  job_id: string | null;
  job_title: string | null;
}

export function emptyCv(): CvDocument {
  return {
    contact: {
      full_name: null,
      headline: null,
      email: null,
      phone: null,
      location: null,
      linkedin: null,
      website: null,
    },
    summary: null,
    experience: [],
    education: [],
    skills: [],
    languages: [],
    certifications: [],
    projects: [],
  };
}

export function newItemId(): string {
  const bytes = new Uint8Array(6);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
}

export type ApplicationStatus = 'saved' | 'applied' | 'interview' | 'offer' | 'rejected';

export interface JobApplication {
  id: string;
  job_id: string;
  title: string;
  company: string | null;
  location: string | null;
  apply_url: string | null;
  status: ApplicationStatus;
  notes: string | null;
  match_score: number | null;
  resume_id: string | null;
  job_active: boolean;
  applied_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApplicationListResponse {
  items: JobApplication[];
  counts: Partial<Record<ApplicationStatus, number>>;
}

export interface ApplicationUpdateRequest {
  status?: ApplicationStatus;
  notes?: string | null;
  resume_id?: string | null;
}

export interface MatchScore {
  job_id: string;
  score: number;
  classification: 'strong_match' | 'moderate_match' | 'weak_match';
}

export interface MatchScoresResponse {
  has_cv: boolean;
  scores: MatchScore[];
}
