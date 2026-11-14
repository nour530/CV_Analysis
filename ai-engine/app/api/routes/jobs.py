from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.api.dependencies import get_matching_application_service
from app.api.presenters import job_summary
from app.api.schemas import JobDetailResponse, JobListResponse
from app.services.matching.application import MatchingApplicationService

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.get(
    "",
    response_model=JobListResponse,
    summary="List or search catalog jobs",
)
def list_jobs(
    category: str | None = Query(default=None, max_length=120),
    q: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=50, ge=1, le=200),
    service: MatchingApplicationService = Depends(get_matching_application_service),
) -> JobListResponse:
    jobs, total = service.list_jobs(
        category=category,
        query=q,
        offset=offset,
        limit=limit,
    )
    return JobListResponse(
        jobs=[job_summary(job) for job in jobs],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{job_id}",
    response_model=JobDetailResponse,
    summary="Get a catalog job by ID",
)
def get_job(
    job_id: str = Path(min_length=1, max_length=120),
    service: MatchingApplicationService = Depends(get_matching_application_service),
) -> JobDetailResponse:
    job = service.get_job(job_id)
    return JobDetailResponse(
        job=job_summary(job),
        description=job.description,
        responsibilities=list(job.responsibilities),
        qualifications=list(job.qualifications),
        other_skills=list(job.other_skills[:200]),
    )
