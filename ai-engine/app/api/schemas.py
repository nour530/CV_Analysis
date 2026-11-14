from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import StrictSchema
from app.schemas.hybrid_match import HybridMatchResult, ResumeProfile


class ErrorDetail(StrictSchema):
    loc: list[str | int] = Field(default_factory=list, max_length=20)
    message: str = Field(min_length=1, max_length=1000)
    type: str = Field(default="validation_error", min_length=1, max_length=120)


class ErrorPayload(StrictSchema):
    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=2000)
    details: list[ErrorDetail] = Field(default_factory=list, max_length=50)


class ErrorResponse(StrictSchema):
    error: ErrorPayload
    request_id: str | None = Field(default=None, max_length=120)


class HealthResponse(StrictSchema):
    status: Literal["ok"] = "ok"
    service: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)


class DependencyStatus(StrictSchema):
    status: Literal["ok", "unavailable", "disabled"]
    latency_ms: int | None = Field(default=None, ge=0, le=60_000)


class ReadinessResponse(StrictSchema):
    status: Literal["ok", "unavailable"]
    service: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    checks: dict[str, DependencyStatus] = Field(default_factory=dict)


class CVAnalysisRequest(StrictSchema):
    resume_text: str = Field(min_length=1, max_length=200_000)
    resume_id: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    experience_evidence: list[str] = Field(default_factory=list, max_length=100)


class DocumentMetadata(StrictSchema):
    filename: str = Field(min_length=1, max_length=255)
    document_type: Literal["text", "pdf", "docx"]
    page_count: int | None = Field(default=None, ge=1)
    extraction_status: Literal["success"] = "success"
    character_count: int = Field(ge=0)


class CVProfileResponse(StrictSchema):
    profile: ResumeProfile
    skill_count: int = Field(ge=0)
    skills_by_category: dict[str, int] = Field(default_factory=dict)
    experience_evidence_count: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=1000)
    document: DocumentMetadata | None = None
    analysis_id: str | None = Field(default=None, max_length=36)
    candidate_profile_id: str | None = Field(default=None, max_length=36)


class JobSummary(StrictSchema):
    job_id: str = Field(min_length=1, max_length=120)
    title: str | None = Field(default=None, max_length=240)
    category: str | None = Field(default=None, max_length=120)
    description_excerpt: str = Field(default="", max_length=600)
    required_skills: list[str] = Field(default_factory=list, max_length=200)
    preferred_skills: list[str] = Field(default_factory=list, max_length=200)


class JobDetailResponse(StrictSchema):
    job: JobSummary
    description: str = Field(min_length=1, max_length=200_000)
    responsibilities: list[str] = Field(default_factory=list, max_length=100)
    qualifications: list[str] = Field(default_factory=list, max_length=100)
    other_skills: list[str] = Field(default_factory=list, max_length=200)


class JobListResponse(StrictSchema):
    jobs: list[JobSummary] = Field(default_factory=list, max_length=200)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)


class JobMatchRequest(StrictSchema):
    resume_text: str | None = Field(default=None, min_length=1, max_length=200_000)
    resume_profile: ResumeProfile | None = None
    job_id: str | None = Field(default=None, min_length=1, max_length=120)
    job_text: str | None = Field(default=None, min_length=1, max_length=200_000)
    job_title: str | None = Field(default=None, max_length=240)
    job_category: str | None = Field(default=None, max_length=120)
    resume_id: str | None = Field(default=None, max_length=120)
    resume_category: str | None = Field(default=None, max_length=120)
    experience_evidence: list[str] = Field(default_factory=list, max_length=100)
    include_llm: bool = False
    allow_remote_analysis: bool = False

    @model_validator(mode="after")
    def validate_sources(self) -> JobMatchRequest:
        if bool(self.resume_text) == bool(self.resume_profile):
            raise ValueError(
                "Provide exactly one of resume_text or resume_profile."
            )
        if bool(self.job_id) == bool(self.job_text):
            raise ValueError("Provide exactly one of job_id or job_text.")
        return self


class JobMatchResponse(StrictSchema):
    job: JobSummary
    match: HybridMatchResult
    llm_requested: bool
    llm_authorized: bool
    match_id: str | None = Field(default=None, max_length=36)


class RecommendationRequest(StrictSchema):
    resume_text: str | None = Field(default=None, min_length=1, max_length=200_000)
    resume_profile: ResumeProfile | None = None
    resume_id: str | None = Field(default=None, max_length=120)
    resume_category: str | None = Field(default=None, max_length=120)
    experience_evidence: list[str] = Field(default_factory=list, max_length=100)
    job_category: str | None = Field(default=None, max_length=120)
    limit: int | None = Field(default=None, ge=1, le=200)
    candidate_pool_size: int | None = Field(default=None, ge=1, le=500)

    @model_validator(mode="after")
    def validate_resume_source(self) -> RecommendationRequest:
        if bool(self.resume_text) == bool(self.resume_profile):
            raise ValueError(
                "Provide exactly one of resume_text or resume_profile."
            )
        return self


class RankedJobRecommendation(StrictSchema):
    rank: int = Field(ge=1)
    job: JobSummary
    match: HybridMatchResult


class RecommendationResponse(StrictSchema):
    recommendations: list[RankedJobRecommendation] = Field(
        default_factory=list, max_length=200
    )
    candidate_count: int = Field(ge=0)
    evaluated_count: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    candidate_pool_size: int = Field(ge=1, le=500)
    job_category: str | None = Field(default=None, max_length=120)
    resume_id: str | None = Field(default=None, max_length=120)
    resume_skill_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    match_id: str | None = Field(default=None, max_length=36)
