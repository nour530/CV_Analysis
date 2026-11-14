from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import StrictSchema

AtsStatus = Literal["pass", "warning", "fail", "info"]
AtsCategoryId = Literal["parsing", "contact", "sections", "content", "keywords"]
AtsGrade = Literal["excellent", "good", "fair", "poor"]


class AtsCheck(StrictSchema):
    id: str = Field(min_length=1, max_length=60)
    category: AtsCategoryId
    status: AtsStatus
    title: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=600)
    fix: str | None = Field(default=None, max_length=600)
    points: float = Field(ge=0.0, le=100.0)
    max_points: float = Field(ge=0.0, le=100.0)


class AtsCategoryScore(StrictSchema):
    id: AtsCategoryId
    label: str = Field(max_length=60)
    score: int = Field(ge=0, le=100)
    points: float = Field(ge=0.0)
    max_points: float = Field(ge=0.0)
    passed: int = Field(ge=0)
    total: int = Field(ge=0)


class AtsSection(StrictSchema):
    id: str = Field(max_length=40)
    label: str = Field(max_length=60)
    heading: str | None = Field(default=None, max_length=160)
    found: bool


class AtsKeywordMatch(StrictSchema):
    job_id: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=300)
    company: str | None = Field(default=None, max_length=200)
    coverage: float = Field(ge=0.0, le=1.0)
    matched: list[str] = Field(default_factory=list, max_length=200)
    missing_required: list[str] = Field(default_factory=list, max_length=200)
    missing_preferred: list[str] = Field(default_factory=list, max_length=200)
    title_in_cv: bool = False


class AtsStats(StrictSchema):
    word_count: int = Field(ge=0)
    line_count: int = Field(ge=0)
    bullet_count: int = Field(ge=0)
    quantified_bullets: int = Field(ge=0)
    action_verb_bullets: int = Field(ge=0)
    skill_count: int = Field(ge=0)
    page_count: int | None = Field(default=None, ge=0)
    document_type: str | None = Field(default=None, max_length=16)


class AtsReport(StrictSchema):
    score: int = Field(ge=0, le=100)
    grade: AtsGrade
    summary: str = Field(max_length=600)
    language: Literal["en", "fr", "mixed"]
    categories: list[AtsCategoryScore] = Field(default_factory=list, max_length=10)
    checks: list[AtsCheck] = Field(default_factory=list, max_length=80)
    top_fixes: list[str] = Field(default_factory=list, max_length=5)
    sections: list[AtsSection] = Field(default_factory=list, max_length=20)
    keyword_match: AtsKeywordMatch | None = None
    stats: AtsStats
    layout_checked: bool
    version: str = Field(default="1.0", max_length=20)
    disclaimer: str = Field(max_length=600)
