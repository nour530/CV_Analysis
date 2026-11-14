export type SkillCategory =
  | 'programming_language'
  | 'framework_library'
  | 'database'
  | 'tool_technology'
  | 'soft_skill'
  | 'domain_knowledge'
  | 'other';

export type MatchClassification =
  | 'strong_match'
  | 'moderate_match'
  | 'weak_match';

export type RequirementImportance = 'required' | 'preferred' | 'mentioned';
export type MissingImportance = 'required' | 'preferred' | 'optional';
export type MatchStrength = 'strong' | 'partial' | 'weak';
export type ComponentStatus = 'success' | 'skipped' | 'failed';
export type RecommendationSource = 'openai' | 'local_deterministic';

export interface HealthResponse {
  status: 'ok';
  service: string;
  version: string;
}

export interface AuthUser {
  id: string;
  full_name: string;
  email: string;
  is_active: boolean;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  user: AuthUser;
  csrf_token: string;
}

export interface RegisterRequest {
  full_name: string;
  email: string;
  password: string;
}

export interface RegistrationResponse {
  message: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface UserSettings {
  recommendation_limit: number;
  candidate_pool_size: number;
  preferred_category: string | null;
  allow_remote_analysis: boolean;
  updated_at: string | null;
}

export interface UserSettingsUpdate {
  recommendation_limit: number;
  candidate_pool_size: number;
  preferred_category: string | null;
  allow_remote_analysis: boolean;
}

export interface ProfileSkill {
  skill: string;
  category: SkillCategory;
  evidence: string[];
  confidence: number;
}

export interface ResumeProfile {
  resume_id: string | null;
  category: string | null;
  semantic_text: string | null;
  skills: ProfileSkill[];
  experience_evidence: string[];
  education_evidence: string[];
}

export interface CVAnalysisRequest {
  resume_text: string;
  resume_id?: string | null;
  category?: string | null;
  experience_evidence?: string[];
}

export type DocumentType = 'text' | 'pdf' | 'docx';

export interface DocumentMetadata {
  filename: string;
  document_type: DocumentType;
  page_count: number | null;
  extraction_status: 'success';
  character_count: number;
}

export interface CVProfileResponse {
  profile: ResumeProfile;
  skill_count: number;
  skills_by_category: Record<string, number>;
  experience_evidence_count: number;
  message: string;
  document: DocumentMetadata | null;
  analysis_id?: string | null;
  candidate_profile_id?: string | null;
  ats?: AtsReport | null;
}

export type AtsStatus = 'pass' | 'warning' | 'fail' | 'info';
export type AtsCategoryId = 'parsing' | 'contact' | 'sections' | 'content' | 'keywords';
export type AtsGrade = 'excellent' | 'good' | 'fair' | 'poor';

export interface AtsCheck {
  id: string;
  category: AtsCategoryId;
  status: AtsStatus;
  title: string;
  message: string;
  fix: string | null;
  points: number;
  max_points: number;
}

export interface AtsCategoryScore {
  id: AtsCategoryId;
  label: string;
  score: number;
  points: number;
  max_points: number;
  passed: number;
  total: number;
}

export interface AtsSection {
  id: string;
  label: string;
  heading: string | null;
  found: boolean;
}

export interface AtsKeywordMatch {
  job_id: string | null;
  job_title: string | null;
  company: string | null;
  coverage: number;
  matched: string[];
  missing_required: string[];
  missing_preferred: string[];
  title_in_cv: boolean;
}

export interface AtsStats {
  word_count: number;
  line_count: number;
  bullet_count: number;
  quantified_bullets: number;
  action_verb_bullets: number;
  skill_count: number;
  page_count: number | null;
  document_type: string | null;
}

export interface AtsReport {
  score: number;
  grade: AtsGrade;
  summary: string;
  language: 'en' | 'fr' | 'mixed';
  categories: AtsCategoryScore[];
  checks: AtsCheck[];
  top_fixes: string[];
  sections: AtsSection[];
  keyword_match: AtsKeywordMatch | null;
  stats: AtsStats;
  layout_checked: boolean;
  version: string;
  disclaimer: string;
}

export interface AtsCheckRequest {
  analysis_id?: string | null;
  job_id?: string | null;
  job_text?: string | null;
  job_title?: string | null;
}

export interface CVAnalysisSummary {
  analysis_id: string;
  filename: string | null;
  document_type: string | null;
  page_count: number | null;
  character_count: number | null;
  skill_count: number;
  resume_id: string | null;
  category: string | null;
  ats_score?: number | null;
  created_at: string;
}

export interface CVAnalysisHistoryResponse {
  items: CVAnalysisSummary[];
}

export interface JobSummary {
  job_id: string;
  title: string | null;
  category: string | null;
  description_excerpt: string;
  required_skills: string[];
  preferred_skills: string[];
  company?: string | null;
  company_logo_url?: string | null;
  location?: string | null;
  city?: string | null;
  country_code?: string | null;
  workplace?: JobWorkplace | null;
  employment_type?: string | null;
  experience_level?: string | null;
  salary?: string | null;
  apply_url?: string | null;
  posted_at?: string | null;
  source?: string;
  source_label?: string | null;
}

export type JobWorkplace = 'onsite' | 'hybrid' | 'remote' | 'unknown';

export interface FacetValue {
  value: string;
  count: number;
}

export interface JobFacetsResponse {
  total: number;
  live: boolean;
  categories: FacetValue[];
  cities: FacetValue[];
  companies: FacetValue[];
  workplaces: FacetValue[];
}

export interface JobDetailResponse {
  job: JobSummary;
  description: string;
  responsibilities: string[];
  qualifications: string[];
  other_skills: string[];
}

export interface JobListResponse {
  jobs: JobSummary[];
  total: number;
  offset: number;
  limit: number;
}

export interface JobListParams {
  category?: string | null;
  q?: string | null;
  workplace?: JobWorkplace | '' | null;
  location?: string | null;
  company?: string | null;
  offset?: number;
  limit?: number;
}

export interface MatchedSkill {
  skill: string;
  category: SkillCategory;
  importance: RequirementImportance;
  match_quality: MatchStrength;
  resume_evidence: string[];
  job_evidence: string[];
  confidence: number;
}

export interface MissingSkill {
  skill: string;
  category: SkillCategory;
  importance: MissingImportance;
  reason: string;
  confidence: number;
}

export interface PreferredSkill {
  skill: string;
  category: SkillCategory;
  matched: boolean;
  resume_evidence: string[];
  confidence: number;
}

export interface ExperienceEvidenceItem {
  requirement: string;
  evidence: string;
  similarity: number | null;
  matched_skills: string[];
  source: 'resume_experience' | 'resume_skill_evidence' | 'explicit';
}

export interface MatchExplanation {
  headline: string;
  narrative: string;
  strengths: string[];
  gaps: string[];
}

export interface ScoreComponent {
  name: string;
  available: boolean;
  raw_value: number | null;
  weight: number;
  normalized_weight: number;
  contribution: number;
  detail: string;
}

export interface ScoreBreakdown {
  components: ScoreComponent[];
  final_score: number;
  classification: MatchClassification;
  thresholds: Record<string, number>;
  weights: Record<string, number>;
}

export interface ComponentRecord {
  name: string;
  status: ComponentStatus;
  duration_ms: number | null;
  detail: string;
}

export interface Uncertainty {
  field: string;
  reason: string;
  severity: 'low' | 'medium' | 'high';
}

export interface AnalysisMetadata {
  provider: string;
  model: string;
  prompt_version: string;
  generated_at: string;
}

export interface HybridMatchResult {
  resume_id: string | null;
  job_id: string | null;
  overall_compatibility_score: number;
  classification: MatchClassification;
  matched_skills: MatchedSkill[];
  missing_required_skills: MissingSkill[];
  preferred_skills: PreferredSkill[];
  relevant_experience_evidence: ExperienceEvidenceItem[];
  semantic_similarity: number | null;
  explanation: MatchExplanation | null;
  recommendations: string[];
  recommendation_source: RecommendationSource;
  uncertainty_indicators: Uncertainty[];
  score_breakdown: ScoreBreakdown;
  components: ComponentRecord[];
  llm_metadata: AnalysisMetadata | null;
  fairness_notice: string;
  disclaimer: string;
}

export interface JobMatchRequest {
  resume_text?: string | null;
  resume_profile?: ResumeProfile | null;
  job_id?: string | null;
  job_text?: string | null;
  job_title?: string | null;
  job_category?: string | null;
  resume_id?: string | null;
  resume_category?: string | null;
  experience_evidence?: string[];
  include_llm?: boolean;
  allow_remote_analysis?: boolean;
}

export interface JobMatchResponse {
  job: JobSummary;
  match: HybridMatchResult;
  llm_requested: boolean;
  llm_authorized: boolean;
  match_id?: string | null;
}

export interface RecommendationRequest {
  resume_text?: string | null;
  resume_profile?: ResumeProfile | null;
  resume_id?: string | null;
  resume_category?: string | null;
  experience_evidence?: string[];
  job_category?: string | null;
  limit?: number | null;
  candidate_pool_size?: number | null;
}

export interface RankedJobRecommendation {
  rank: number;
  job: JobSummary;
  match: HybridMatchResult;
}

export interface RecommendationResponse {
  recommendations: RankedJobRecommendation[];
  candidate_count: number;
  evaluated_count: number;
  limit: number;
  candidate_pool_size: number;
  job_category: string | null;
  resume_id: string | null;
  resume_skill_count: number;
  warnings: string[];
  match_id?: string | null;
}

export interface MatchHistorySummary {
  match_id: string;
  kind: 'job' | 'recommendations';
  job_id: string | null;
  job_title: string | null;
  resume_id: string | null;
  score: number | null;
  created_at: string;
}

export interface MatchHistoryResponse {
  items: MatchHistorySummary[];
}

export interface ApiErrorDetail {
  loc: (string | number)[];
  message: string;
  type: string;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details: ApiErrorDetail[];
}

export interface ApiErrorResponse {
  error: ApiErrorBody;
  request_id: string | null;
}
