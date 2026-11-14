from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import (
    AnalysisMetadata,
    AnalysisSummary,
    EvidenceBasis,
    StrictSchema,
    Uncertainty,
)
from app.schemas.resume_analysis import SkillCategory

RequirementImportance = Literal["required", "preferred", "mentioned"]


class JobSkillRequirement(StrictSchema):
    skill: str = Field(min_length=1, max_length=160)
    category: SkillCategory = "other"
    importance: RequirementImportance
    basis: EvidenceBasis = "explicit"
    evidence: list[str] = Field(min_length=1, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)


class JobResponsibility(StrictSchema):
    description: str = Field(min_length=1, max_length=2000)
    basis: EvidenceBasis = "explicit"
    evidence: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)


class EducationRequirement(StrictSchema):
    degree: str | None = Field(default=None, max_length=200)
    field_of_study: str | None = Field(default=None, max_length=200)
    evidence: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_content(self) -> EducationRequirement:
        if not any([self.degree, self.field_of_study]):
            raise ValueError("Education requirement must include a degree or field_of_study.")
        return self


class ExperienceRequirement(StrictSchema):
    min_years: int | None = Field(default=None, ge=0, le=60)
    max_years: int | None = Field(default=None, ge=0, le=60)
    description: str | None = Field(default=None, max_length=1000)
    evidence: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_range(self) -> ExperienceRequirement:
        if self.min_years is not None and self.max_years is not None:
            if self.min_years > self.max_years:
                raise ValueError("Experience min_years cannot be greater than max_years.")
        if not any([self.min_years is not None, self.description]):
            raise ValueError("Experience requirement must include years or a description.")
        return self


class JobAnalysis(StrictSchema):
    job_title: str | None = Field(default=None, max_length=200)
    mandatory_requirements: list[str] = Field(default_factory=list, max_length=100)
    preferred_requirements: list[str] = Field(default_factory=list, max_length=100)
    technical_skills: list[str] = Field(default_factory=list, max_length=100)
    skills: list[JobSkillRequirement] = Field(default_factory=list, max_length=200)
    responsibilities: list[JobResponsibility] = Field(default_factory=list, max_length=100)
    qualifications: list[str] = Field(default_factory=list, max_length=100)
    education_requirements: list[EducationRequirement] = Field(
        default_factory=list, max_length=20
    )
    experience_requirements: list[ExperienceRequirement] = Field(
        default_factory=list, max_length=20
    )
    keywords: list[str] = Field(default_factory=list, max_length=100)
    summary: AnalysisSummary
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainties: list[Uncertainty] = Field(default_factory=list, max_length=50)
    metadata: AnalysisMetadata | None = None
