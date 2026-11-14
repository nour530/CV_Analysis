"""Structured CV document edited in the CV builder."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from app.schemas.common import StrictSchema

CvLanguageCode = Literal["en", "fr"]
CvTemplate = Literal["classic", "modern", "compact"]


def new_item_id() -> str:
    return uuid4().hex[:12]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def _clean_lines(values: list[str]) -> list[str]:
    return [" ".join(value.split()) for value in values if value and value.strip()]


class CvContact(StrictSchema):
    full_name: str | None = Field(default=None, max_length=120)
    headline: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=120)
    linkedin: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=200)

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return _clean(value) if isinstance(value, str) else value


class CvExperience(StrictSchema):
    id: str = Field(default_factory=new_item_id, min_length=1, max_length=40)
    title: str | None = Field(default=None, max_length=160)
    company: str | None = Field(default=None, max_length=160)
    location: str | None = Field(default=None, max_length=120)
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    current: bool = False
    bullets: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("bullets")
    @classmethod
    def _clean_bullets(cls, values: list[str]) -> list[str]:
        return _clean_lines(values)


class CvEducation(StrictSchema):
    id: str = Field(default_factory=new_item_id, min_length=1, max_length=40)
    degree: str | None = Field(default=None, max_length=200)
    school: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=120)
    start: str | None = Field(default=None, max_length=40)
    end: str | None = Field(default=None, max_length=40)
    details: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("details")
    @classmethod
    def _clean_details(cls, values: list[str]) -> list[str]:
        return _clean_lines(values)


class CvLanguage(StrictSchema):
    name: str = Field(min_length=1, max_length=60)
    level: str | None = Field(default=None, max_length=60)


class CvProject(StrictSchema):
    id: str = Field(default_factory=new_item_id, min_length=1, max_length=40)
    name: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=600)
    bullets: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("bullets")
    @classmethod
    def _clean_bullets(cls, values: list[str]) -> list[str]:
        return _clean_lines(values)


class CvDocument(StrictSchema):
    contact: CvContact = Field(default_factory=CvContact)
    summary: str | None = Field(default=None, max_length=1500)
    experience: list[CvExperience] = Field(default_factory=list, max_length=30)
    education: list[CvEducation] = Field(default_factory=list, max_length=15)
    skills: list[str] = Field(default_factory=list, max_length=80)
    languages: list[CvLanguage] = Field(default_factory=list, max_length=12)
    certifications: list[str] = Field(default_factory=list, max_length=30)
    projects: list[CvProject] = Field(default_factory=list, max_length=15)

    @field_validator("skills", "certifications")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in _clean_lines(values):
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                result.append(value[:120])
        return result


class ResumeSummary(StrictSchema):
    id: str
    title: str
    language: CvLanguageCode
    template: CvTemplate
    ats_score: int | None = Field(default=None, ge=0, le=100)
    updated_at: datetime
    created_at: datetime


class ResumeResponse(ResumeSummary):
    content: CvDocument
    source_analysis_id: str | None = None


class ResumeListResponse(StrictSchema):
    items: list[ResumeSummary] = Field(default_factory=list)


class ResumeCreateRequest(StrictSchema):
    title: str | None = Field(default=None, max_length=120)
    from_analysis_id: str | None = Field(default=None, max_length=36)
    blank: bool = False
    language: CvLanguageCode | None = None


class ResumeUpdateRequest(StrictSchema):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    language: CvLanguageCode | None = None
    template: CvTemplate | None = None
    content: CvDocument | None = None


SuggestionField = Literal["summary", "bullet", "headline", "skills", "tip"]
SuggestionSource = Literal["ai", "rules"]


class CvSuggestion(StrictSchema):
    id: str = Field(default_factory=new_item_id)
    field: SuggestionField
    target_id: str | None = Field(default=None, max_length=40)
    index: int | None = Field(default=None, ge=0, le=50)
    original: str | None = Field(default=None, max_length=1500)
    suggestion: str | None = Field(default=None, max_length=1500)
    reason: str = Field(max_length=400)
    warnings: list[str] = Field(default_factory=list, max_length=5)
    add_skills: list[str] = Field(default_factory=list, max_length=20)


class CvSuggestionRequest(StrictSchema):
    scope: Literal["all", "summary", "experience", "skills"] = "all"
    experience_id: str | None = Field(default=None, max_length=40)
    job_id: str | None = Field(default=None, max_length=120)
    allow_remote_analysis: bool = False


class CvSuggestionResponse(StrictSchema):
    source: SuggestionSource
    suggestions: list[CvSuggestion] = Field(default_factory=list, max_length=60)
    notice: str | None = Field(default=None, max_length=400)
    job_id: str | None = None
    job_title: str | None = None


# --- LLM exchange -------------------------------------------------------------------


class CvImprovementItem(StrictSchema):
    ref: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1500)
    reason: str = Field(min_length=1, max_length=300)


class CvImprovementOutput(StrictSchema):
    suggestions: list[CvImprovementItem] = Field(default_factory=list, max_length=40)
