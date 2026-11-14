"""Saved jobs and application tracking."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import StrictSchema

ApplicationStatus = Literal["saved", "applied", "interview", "offer", "rejected"]


class ApplicationResponse(StrictSchema):
    id: str
    job_id: str
    title: str
    company: str | None = None
    location: str | None = None
    apply_url: str | None = None
    status: ApplicationStatus
    notes: str | None = None
    match_score: float | None = Field(default=None, ge=0.0, le=1.0)
    resume_id: str | None = None
    job_active: bool = True
    applied_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ApplicationListResponse(StrictSchema):
    items: list[ApplicationResponse] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class ApplicationCreateRequest(StrictSchema):
    job_id: str = Field(min_length=1, max_length=120)
    status: ApplicationStatus = "saved"


class ApplicationUpdateRequest(StrictSchema):
    status: ApplicationStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)
    resume_id: str | None = Field(default=None, max_length=36)


class MatchScoresRequest(StrictSchema):
    job_ids: list[str] = Field(min_length=1, max_length=50)


class MatchScore(StrictSchema):
    job_id: str
    score: float = Field(ge=0.0, le=1.0)
    classification: Literal["strong_match", "moderate_match", "weak_match"]


class MatchScoresResponse(StrictSchema):
    has_cv: bool
    scores: list[MatchScore] = Field(default_factory=list)
