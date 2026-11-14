"""Saved jobs, application tracking and quick match scores."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_matching_application_service
from app.api.errors import APIError
from app.auth.dependencies import get_auth_context
from app.auth.service import AuthContext
from app.db.models import CvDocumentRecord, JobApplication, utcnow
from app.db.session import get_db
from app.schemas.applications import (
    ApplicationCreateRequest,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationUpdateRequest,
    MatchScoresRequest,
    MatchScoresResponse,
)
from app.services.matching.application import MatchingApplicationService
from app.services.quick_match import latest_profile, score_jobs

router = APIRouter(tags=["Applications"])

MAX_APPLICATIONS_PER_USER = 300
STATUSES = ("saved", "applied", "interview", "offer", "rejected")


def _response(row: JobApplication, service: MatchingApplicationService) -> ApplicationResponse:
    return ApplicationResponse(
        id=row.id,
        job_id=row.job_id,
        title=row.title,
        company=row.company,
        location=row.location,
        apply_url=row.apply_url,
        status=row.status,  # type: ignore[arg-type]
        notes=row.notes,
        match_score=row.match_score,
        resume_id=row.resume_id,
        job_active=service.catalog.get(row.job_id) is not None,
        applied_at=row.applied_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _load(db: Session, user_id: str, application_id: str) -> JobApplication:
    row = db.scalar(
        select(JobApplication).where(
            JobApplication.id == application_id, JobApplication.user_id == user_id
        )
    )
    if row is None:
        raise APIError(status_code=404, code="application_not_found", message="Saved job was not found.")
    return row


@router.get("/applications", response_model=ApplicationListResponse, summary="List saved jobs and applications")
def list_applications(
    context: AuthContext = Depends(get_auth_context),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> ApplicationListResponse:
    rows = db.scalars(
        select(JobApplication)
        .where(JobApplication.user_id == context.user.id)
        .order_by(JobApplication.updated_at.desc())
    ).all()
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return ApplicationListResponse(items=[_response(row, service) for row in rows], counts=counts)


@router.post("/applications", response_model=ApplicationResponse, status_code=201, summary="Save a job to the tracker")
def create_application(
    request: ApplicationCreateRequest,
    context: AuthContext = Depends(get_auth_context),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    existing = db.scalar(
        select(JobApplication).where(
            JobApplication.user_id == context.user.id, JobApplication.job_id == request.job_id
        )
    )
    if existing is not None:
        return _response(existing, service)
    count = db.scalar(
        select(func.count()).select_from(JobApplication).where(JobApplication.user_id == context.user.id)
    )
    if (count or 0) >= MAX_APPLICATIONS_PER_USER:
        raise APIError(
            status_code=409,
            code="application_limit_reached",
            message=f"You can track up to {MAX_APPLICATIONS_PER_USER} jobs. Remove old ones to add more.",
        )
    job = service.get_job(request.job_id)  # unknown IDs map to a 404 handler
    profile = latest_profile(db, context.user.id)
    score = score_jobs(service, profile, [job.job_id]) if profile is not None else []
    row = JobApplication(
        user_id=context.user.id,
        job_id=job.job_id,
        title=(job.title or "Untitled role")[:300],
        company=job.company,
        location=job.location,
        apply_url=job.apply_url,
        status=request.status,
        match_score=score[0].score if score else None,
        applied_at=utcnow() if request.status != "saved" else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _response(row, service)


@router.patch("/applications/{application_id}", response_model=ApplicationResponse, summary="Update status, notes or the CV used")
def update_application(
    request: ApplicationUpdateRequest,
    application_id: str = Path(max_length=36),
    context: AuthContext = Depends(get_auth_context),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    row = _load(db, context.user.id, application_id)
    fields = request.model_fields_set
    if "status" in fields and request.status is not None:
        row.status = request.status
        if request.status != "saved" and row.applied_at is None:
            row.applied_at = utcnow()
    if "notes" in fields:
        row.notes = (request.notes or "").strip() or None
    if "resume_id" in fields:
        if request.resume_id:
            owned = db.scalar(
                select(CvDocumentRecord.id).where(
                    CvDocumentRecord.id == request.resume_id,
                    CvDocumentRecord.user_id == context.user.id,
                )
            )
            if owned is None:
                raise APIError(status_code=404, code="resume_not_found", message="CV was not found.")
        row.resume_id = request.resume_id or None
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return _response(row, service)


@router.delete("/applications/{application_id}", status_code=204, summary="Remove a job from the tracker")
def delete_application(
    application_id: str = Path(max_length=36),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> None:
    db.delete(_load(db, context.user.id, application_id))
    db.commit()


@router.post("/matches/scores", response_model=MatchScoresResponse, summary="Match scores of the latest CV for a list of jobs")
def match_scores(
    request: MatchScoresRequest,
    context: AuthContext = Depends(get_auth_context),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> MatchScoresResponse:
    profile = latest_profile(db, context.user.id)
    if profile is None:
        return MatchScoresResponse(has_cv=False)
    return MatchScoresResponse(has_cv=True, scores=score_jobs(service, profile, request.job_ids))
