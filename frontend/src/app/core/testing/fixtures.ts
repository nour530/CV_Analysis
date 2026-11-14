import {
  CVProfileResponse,
  HybridMatchResult,
  JobDetailResponse,
  JobListResponse,
  JobMatchResponse,
  JobSummary,
  RecommendationResponse,
} from '../api.models';

export const PROFILE_RESPONSE: CVProfileResponse = {
  profile: {
    resume_id: 'resume-1',
    category: 'Data Science',
    semantic_text: 'Python and SQL experience with analytics projects.',
    skills: [
      {
        skill: 'Python',
        category: 'programming_language',
        evidence: ['Built Python data pipelines'],
        confidence: 0.94,
      },
      {
        skill: 'SQL',
        category: 'database',
        evidence: ['Wrote analytical SQL queries'],
        confidence: 0.9,
      },
    ],
    experience_evidence: ['Built Python data pipelines for reporting.'],
    education_evidence: ['BSc in Statistics'],
  },
  skill_count: 2,
  skills_by_category: {
    programming_language: 1,
    database: 1,
  },
  experience_evidence_count: 1,
  message: 'CV analysed successfully.',
  document: {
    filename: 'cv.txt',
    document_type: 'text',
    page_count: null,
    extraction_status: 'success',
    character_count: 50,
  },
};

export const JOB: JobSummary = {
  job_id: 'job-1',
  title: 'Data Analyst',
  category: 'Data Science',
  description_excerpt: 'Analyse product data and build reporting pipelines.',
  required_skills: ['Python', 'SQL'],
  preferred_skills: ['Tableau'],
};

export const JOB_DETAIL: JobDetailResponse = {
  job: JOB,
  description:
    'Join the analytics team to build reliable reporting and insight products.',
  responsibilities: ['Build dashboards', 'Maintain data quality checks'],
  qualifications: ['Experience with SQL and Python'],
  other_skills: ['Communication'],
};

export const JOB_LIST: JobListResponse = {
  jobs: [JOB],
  total: 1,
  offset: 0,
  limit: 12,
};

export const MATCH: HybridMatchResult = {
  resume_id: 'resume-1',
  job_id: 'job-1',
  overall_compatibility_score: 0.82,
  classification: 'strong_match',
  matched_skills: [
    {
      skill: 'Python',
      category: 'programming_language',
      importance: 'required',
      match_quality: 'strong',
      resume_evidence: ['Built Python data pipelines'],
      job_evidence: ['Python required'],
      confidence: 0.94,
    },
  ],
  missing_required_skills: [],
  preferred_skills: [
    {
      skill: 'Tableau',
      category: 'tool_technology',
      matched: false,
      resume_evidence: [],
      confidence: 0.6,
    },
  ],
  relevant_experience_evidence: [
    {
      requirement: 'Data pipelines',
      evidence: 'Built Python data pipelines for reporting.',
      similarity: 0.8,
      matched_skills: ['Python'],
      source: 'resume_experience',
    },
  ],
  semantic_similarity: 0.8,
  explanation: null,
  recommendations: ['Highlight your Python reporting work.'],
  recommendation_source: 'local_deterministic',
  uncertainty_indicators: [],
  score_breakdown: {
    components: [
      {
        name: 'required_skill_coverage',
        available: true,
        raw_value: 1,
        weight: 0.5,
        normalized_weight: 0.5,
        contribution: 0.5,
        detail: 'All required skills are evidenced.',
      },
    ],
    final_score: 0.82,
    classification: 'strong_match',
    thresholds: { strong_match: 0.75, moderate_match: 0.5 },
    weights: { required_skill_coverage: 0.5 },
  },
  components: [
    {
      name: 'local_skill_matching',
      status: 'success',
      duration_ms: 4,
      detail: 'Deterministic skill match completed.',
    },
  ],
  llm_metadata: null,
  fairness_notice: 'Scores are based only on the evidence provided.',
  disclaimer: 'This is a decision-support tool, not an automated hiring decision.',
};

export const MATCH_WITH_EXPLANATION: HybridMatchResult = {
  ...MATCH,
  explanation: {
    headline: 'Strong alignment with the core requirements',
    narrative: 'Your Python and SQL evidence aligns with the main requirements.',
    strengths: ['Python evidence', 'SQL reporting experience'],
    gaps: ['No Tableau evidence'],
  },
  recommendation_source: 'openai',
  llm_metadata: {
    provider: 'openai',
    model: 'test-model',
    prompt_version: 'v1',
    generated_at: '2026-01-01T00:00:00Z',
  },
};

export const RECOMMENDATIONS: RecommendationResponse = {
  recommendations: [
    {
      rank: 1,
      job: JOB,
      match: MATCH,
    },
  ],
  candidate_count: 100,
  evaluated_count: 40,
  limit: 10,
  candidate_pool_size: 40,
  job_category: null,
  resume_id: 'resume-1',
  resume_skill_count: 2,
  warnings: [],
};

export const JOB_MATCH_RESPONSE: JobMatchResponse = {
  job: JOB,
  match: MATCH,
  llm_requested: false,
  llm_authorized: false,
};

export const LLM_JOB_MATCH_RESPONSE: JobMatchResponse = {
  job: JOB,
  match: MATCH_WITH_EXPLANATION,
  llm_requested: true,
  llm_authorized: true,
};
