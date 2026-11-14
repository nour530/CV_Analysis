from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import AnalysisMetadata, StrictSchema, Uncertainty
from app.schemas.match_analysis import MatchExplanation, MatchStrength, MissingRequirementImportance
from app.schemas.resume_analysis import SkillCategory

MatchClassification = Literal["strong_match", "moderate_match", "weak_match"]
RequirementImportance = Literal["required", "preferred", "mentioned"]
ComponentStatus = Literal["success", "skipped", "failed"]
RecommendationSource = Literal["openai", "local_deterministic"]


class ProfileSkill(StrictSchema):
    skill: str = Field(min_length=1, max_length=160)
    category: SkillCategory = "other"
    evidence: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ResumeProfile(StrictSchema):
    resume_id: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    semantic_text: str | None = Field(default=None, max_length=200_000)
    skills: list[ProfileSkill] = Field(default_factory=list, max_length=500)
    experience_evidence: list[str] = Field(default_factory=list, max_length=100)
    education_evidence: list[str] = Field(default_factory=list, max_length=50)


class JobRequirement(StrictSchema):
    skill: str = Field(min_length=1, max_length=160)
    category: SkillCategory = "other"
    importance: RequirementImportance = "required"
    evidence: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class JobProfile(StrictSchema):
    job_id: str | None = Field(default=None, max_length=120)
    title: str | None = Field(default=None, max_length=240)
    category: str | None = Field(default=None, max_length=120)
    semantic_text: str | None = Field(default=None, max_length=200_000)
    requirements: list[JobRequirement] = Field(default_factory=list, max_length=500)
    responsibilities: list[str] = Field(default_factory=list, max_length=100)
    qualifications: list[str] = Field(default_factory=list, max_length=100)


class MatchedSkill(StrictSchema):
    skill: str = Field(min_length=1, max_length=160)
    category: SkillCategory = "other"
    importance: RequirementImportance = "required"
    match_quality: MatchStrength = "partial"
    resume_evidence: list[str] = Field(default_factory=list, max_length=10)
    job_evidence: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)


class MissingSkill(StrictSchema):
    skill: str = Field(min_length=1, max_length=160)
    category: SkillCategory = "other"
    importance: MissingRequirementImportance = "required"
    reason: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class PreferredSkill(StrictSchema):
    skill: str = Field(min_length=1, max_length=160)
    category: SkillCategory = "other"
    matched: bool
    resume_evidence: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExperienceEvidenceItem(StrictSchema):
    requirement: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(min_length=1, max_length=4000)
    similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    matched_skills: list[str] = Field(default_factory=list, max_length=50)
    source: Literal["resume_experience", "resume_skill_evidence", "explicit"] = (
        "resume_experience"
    )


class ScoreComponent(StrictSchema):
    name: str = Field(min_length=1, max_length=80)
    available: bool
    raw_value: float | None = Field(default=None, ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0)
    normalized_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    contribution: float = Field(default=0.0, ge=0.0, le=1.0)
    detail: str = Field(default="", max_length=1000)


class ScoreBreakdown(StrictSchema):
    components: list[ScoreComponent] = Field(default_factory=list, max_length=20)
    final_score: float = Field(ge=0.0, le=1.0)
    classification: MatchClassification
    thresholds: dict[str, float] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)


class ComponentRecord(StrictSchema):
    name: str = Field(min_length=1, max_length=80)
    status: ComponentStatus
    duration_ms: float | None = Field(default=None, ge=0.0)
    detail: str = Field(default="", max_length=1000)


class HybridMatchResult(StrictSchema):
    resume_id: str | None = Field(default=None, max_length=120)
    job_id: str | None = Field(default=None, max_length=120)
    overall_compatibility_score: float = Field(ge=0.0, le=1.0)
    classification: MatchClassification
    matched_skills: list[MatchedSkill] = Field(default_factory=list, max_length=500)
    missing_required_skills: list[MissingSkill] = Field(default_factory=list, max_length=500)
    preferred_skills: list[PreferredSkill] = Field(default_factory=list, max_length=500)
    relevant_experience_evidence: list[ExperienceEvidenceItem] = Field(
        default_factory=list, max_length=50
    )
    semantic_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    explanation: MatchExplanation | None = None
    recommendations: list[str] = Field(default_factory=list, max_length=20)
    recommendation_source: RecommendationSource = "local_deterministic"
    uncertainty_indicators: list[Uncertainty] = Field(default_factory=list, max_length=50)
    score_breakdown: ScoreBreakdown
    components: list[ComponentRecord] = Field(default_factory=list, max_length=20)
    llm_metadata: AnalysisMetadata | None = None
    fairness_notice: str = Field(min_length=1, max_length=2000)
    disclaimer: str = Field(min_length=1, max_length=2000)
