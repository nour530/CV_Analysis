from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import (
    AnalysisMetadata,
    AnalysisSummary,
    StrictSchema,
    Uncertainty,
)
from app.schemas.resume_analysis import SkillCategory

MatchStrength = Literal["strong", "partial", "weak"]
MissingRequirementImportance = Literal["required", "preferred", "optional"]


class MatchedRequirement(StrictSchema):
    requirement: str = Field(min_length=1, max_length=240)
    category: SkillCategory = "other"
    resume_evidence: list[str] = Field(min_length=1, max_length=10)
    job_evidence: list[str] = Field(min_length=1, max_length=10)
    strength: MatchStrength
    confidence: float = Field(ge=0.0, le=1.0)


class MissingRequirement(StrictSchema):
    requirement: str = Field(min_length=1, max_length=240)
    category: SkillCategory = "other"
    importance: MissingRequirementImportance
    reason: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0)


class MatchExplanation(StrictSchema):
    headline: str = Field(min_length=1, max_length=240)
    narrative: str = Field(min_length=1, max_length=4000)
    strengths: list[str] = Field(default_factory=list, max_length=20)
    gaps: list[str] = Field(default_factory=list, max_length=20)


class MatchAnalysis(StrictSchema):
    overall_score: float = Field(ge=0.0, le=1.0)
    summary: AnalysisSummary
    explanation: MatchExplanation
    matched_requirements: list[MatchedRequirement] = Field(
        default_factory=list, max_length=100
    )
    missing_requirements: list[MissingRequirement] = Field(
        default_factory=list, max_length=100
    )
    recommendations: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainties: list[Uncertainty] = Field(default_factory=list, max_length=50)
    metadata: AnalysisMetadata | None = None
