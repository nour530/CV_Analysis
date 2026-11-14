"""Fast local match scores for job lists (no LLM, nothing stored)."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.schemas.applications import MatchScore
from app.schemas.hybrid_match import ResumeProfile
from app.services.matching.application import MatchingApplicationService
from app.services.user_records import latest_cv_analysis


def latest_profile(db: Session, user_id: str) -> ResumeProfile | None:
    analysis = latest_cv_analysis(db, user_id=user_id)
    if analysis is None:
        return None
    try:
        return ResumeProfile.model_validate(analysis.profile_json)
    except ValueError:
        return None


def score_jobs(
    service: MatchingApplicationService,
    profile: ResumeProfile,
    job_ids: Iterable[str],
) -> list[MatchScore]:
    scores: list[MatchScore] = []
    for job_id in dict.fromkeys(job_ids):
        job = service.catalog.get(job_id)
        if job is None:
            continue
        result = service.hybrid_service.match_local(
            resume_profile=profile,
            job_profile=service.catalog.profile(job),
            job_id=job.job_id,
            resume_id=profile.resume_id,
        )
        scores.append(
            MatchScore(
                job_id=job.job_id,
                score=round(result.overall_compatibility_score, 4),
                classification=result.classification,
            )
        )
    return scores
