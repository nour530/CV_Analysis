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

SkillCategory = Literal[
    "programming_language",
    "framework_library",
    "database",
    "tool_technology",
    "soft_skill",
    "domain_knowledge",
    "other",
]


class SkillEvidence(StrictSchema):
    skill: str = Field(min_length=1, max_length=160)
    category: SkillCategory = "other"
    basis: EvidenceBasis = "explicit"
    evidence: list[str] = Field(min_length=1, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)


class EducationEntry(StrictSchema):
    degree: str | None = Field(default=None, max_length=200)
    field_of_study: str | None = Field(default=None, max_length=200)
    institution: str | None = Field(default=None, max_length=240)
    start_year: int | None = Field(default=None, ge=1900, le=2200)
    end_year: int | None = Field(default=None, ge=1900, le=2200)
    basis: EvidenceBasis = "explicit"
    evidence: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_content(self) -> EducationEntry:
        if not any([self.degree, self.field_of_study, self.institution]):
            raise ValueError("Education entry must include a degree, field, or institution.")
        if self.start_year and self.end_year and self.start_year > self.end_year:
            raise ValueError("Education start_year cannot be after end_year.")
        return self


class ExperienceEntry(StrictSchema):
    title: str | None = Field(default=None, max_length=200)
    organization: str | None = Field(default=None, max_length=240)
    start_date: str | None = Field(default=None, max_length=40)
    end_date: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=4000)
    skills: list[str] = Field(default_factory=list, max_length=50)
    basis: EvidenceBasis = "explicit"
    evidence: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_content(self) -> ExperienceEntry:
        if not any([self.title, self.organization, self.description]):
            raise ValueError("Experience entry must include a title, organization, or description.")
        return self


class ResumeAnalysis(StrictSchema):
    technical_skills: list[str] = Field(default_factory=list, max_length=100)
    programming_languages: list[str] = Field(default_factory=list, max_length=50)
    frameworks: list[str] = Field(default_factory=list, max_length=50)
    skills: list[SkillEvidence] = Field(default_factory=list, max_length=200)
    education: list[EducationEntry] = Field(default_factory=list, max_length=50)
    experience: list[ExperienceEntry] = Field(default_factory=list, max_length=100)
    certifications: list[str] = Field(default_factory=list, max_length=100)
    summary: AnalysisSummary
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainties: list[Uncertainty] = Field(default_factory=list, max_length=50)
    metadata: AnalysisMetadata | None = None
