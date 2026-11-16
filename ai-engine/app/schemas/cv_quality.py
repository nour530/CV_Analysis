"""API schemas for CV evaluation (our scorer) and full AI rewrites (Claude)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import StrictSchema
from app.schemas.cv_document import CvDocument

SectionId = Literal["contact", "summary", "experience", "education", "skills", "languages", "certifications", "projects"]


class CriterionScore(StrictSchema):
    id: str
    label: str
    score: float | None = Field(default=None, description="0-10, from our trained model; null until it is trained")
    evidence: list[str] = Field(default_factory=list, description="Facts measured in the CV")


class FactCheckItem(StrictSchema):
    id: str
    label: str
    ok: bool


class CvEvaluationResponse(StrictSchema):
    available: bool = Field(description="False until the CV scorer has been trained")
    overall: int | None = Field(default=None, description="0-100")
    criteria: list[CriterionScore]
    checks: list[FactCheckItem]
    model: dict = Field(default_factory=dict, description="Model type, training size and date")
    notice: str | None = None


class CvRewriteRequest(StrictSchema):
    job_id: str | None = Field(default=None, max_length=80)


class SectionChange(StrictSchema):
    id: SectionId
    changed: bool
    note: str = ""
    warnings: list[str] = Field(default_factory=list)


class CvRewriteResponse(StrictSchema):
    improved: CvDocument
    sections: list[SectionChange]
    score_before: int | None = None
    score_after: int | None = None
    model: str
    job_title: str | None = None
