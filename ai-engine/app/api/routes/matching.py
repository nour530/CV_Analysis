from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.auth_schemas import (
    MatchHistoryDetail,
    MatchHistoryResponse,
)

from app.api.dependencies import (
    get_api_settings,
    get_llm_provider,
    get_matching_application_service,
)
from app.api.errors import APIError
from app.api.presenters import custom_job_summary, job_summary
from app.api.schemas import (
    JobMatchRequest,
    JobMatchResponse,
    RankedJobRecommendation,
    RecommendationRequest,
    RecommendationResponse,
)
from app.api.settings import APISettings
from app.auth.dependencies import get_auth_context
from app.auth.service import AuthContext
from app.core.user_quota import (
    JOB_MATCH,
    RECOMMENDATIONS,
    REMOTE_LLM,
    enforce_user_quota,
)
from app.db.session import get_db
from app.services.matching.application import MatchingApplicationService
from app.services.user_records import (
    delete_match_history,
    get_match,
    list_matches,
    save_match,
)

router = APIRouter(prefix="/matches", tags=["Matching"])


def _check_text_limit(value: str | None, field_name: str, settings: APISettings) -> None:
    if value is not None and len(value) > settings.max_text_length:
        raise APIError(
            status_code=413,
            code="text_too_large",
            message=(
                f"{field_name} exceeds the {settings.max_text_length} character limit."
            ),
        )


def _validate_text_limits(
    request: JobMatchRequest | RecommendationRequest,
    settings: APISettings,
) -> None:
    _check_text_limit(getattr(request, "resume_text", None), "resume_text", settings)
    _check_text_limit(getattr(request, "job_text", None), "job_text", settings)
    profile = getattr(request, "resume_profile", None)
    if profile is not None:
        _check_text_limit(profile.semantic_text, "resume_profile.semantic_text", settings)


@router.post(
    "/job",
    response_model=JobMatchResponse,
    summary="Match a résumé against a catalog job or free-text job description",
)
def match_job(
    http_request: Request,
    request: JobMatchRequest,
    context: AuthContext = Depends(get_auth_context),
    settings: APISettings = Depends(get_api_settings),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> JobMatchResponse:
    _validate_text_limits(request, settings)
    enforce_user_quota(
        http_request,
        user_id=context.user.id,
        operation=JOB_MATCH,
        limit=settings.user_quota_job_match_attempts,
        window_seconds=settings.user_quota_job_match_window_seconds,
        settings=settings,
    )
    llm_authorized = (
        request.include_llm
        and request.allow_remote_analysis
        and settings.allow_remote_llm_analysis
    )
    provider = get_llm_provider() if llm_authorized else None
    if llm_authorized and provider is not None:
        enforce_user_quota(
            http_request,
            user_id=context.user.id,
            operation=REMOTE_LLM,
            limit=settings.user_quota_remote_llm_attempts,
            window_seconds=settings.user_quota_remote_llm_window_seconds,
            settings=settings,
        )
    catalog_job, match = service.match_job(
        resume_text=request.resume_text,
        resume_profile=request.resume_profile,
        job_id=request.job_id,
        job_text=request.job_text,
        job_title=request.job_title,
        job_category=request.job_category,
        resume_id=request.resume_id,
        resume_category=request.resume_category,
        experience_evidence=request.experience_evidence or None,
        include_llm=request.include_llm,
        allow_remote_analysis=llm_authorized,
        provider=provider,
    )
    job = (
        job_summary(catalog_job)
        if catalog_job is not None
        else custom_job_summary(
            title=request.job_title,
            category=request.job_category,
            description=request.job_text or "",
        )
    )
    response = JobMatchResponse(
        job=job,
        match=match,
        llm_requested=request.include_llm,
        llm_authorized=llm_authorized,
    )
    resume_id = request.resume_id
    if resume_id is None and request.resume_profile is not None:
        resume_id = request.resume_profile.resume_id
    saved = save_match(
        db,
        user_id=context.user.id,
        kind="job",
        job_id=request.job_id,
        job_title=job.title,
        resume_id=resume_id,
        score=match.overall_compatibility_score,
        payload=response.model_dump(mode="json"),
    )
    response.match_id = saved.id
    return response


@router.post(
    "/recommendations",
    response_model=RecommendationResponse,
    summary="Rank catalog jobs for a résumé using local hybrid matching",
)
def recommend_jobs(
    http_request: Request,
    request: RecommendationRequest,
    context: AuthContext = Depends(get_auth_context),
    settings: APISettings = Depends(get_api_settings),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    limit = request.limit or settings.default_recommendation_limit
    if limit > settings.max_recommendation_limit:
        raise APIError(
            status_code=422,
            code="recommendation_limit_exceeded",
            message=(
                "limit exceeds MAX_RECOMMENDATION_LIMIT "
                f"({settings.max_recommendation_limit})."
            ),
        )

    pool_size = request.candidate_pool_size or settings.default_candidate_pool_size
    pool_size = max(pool_size, limit)
    if pool_size > settings.max_candidate_pool_size:
        raise APIError(
            status_code=422,
            code="candidate_pool_too_large",
            message=(
                "candidate_pool_size exceeds MAX_CANDIDATE_POOL_SIZE "
                f"({settings.max_candidate_pool_size})."
            ),
        )

    enforce_user_quota(
        http_request,
        user_id=context.user.id,
        operation=RECOMMENDATIONS,
        limit=settings.user_quota_recommendations_attempts,
        window_seconds=settings.user_quota_recommendations_window_seconds,
        settings=settings,
    )
    result = service.recommend_jobs(
        resume_text=request.resume_text,
        resume_profile=request.resume_profile,
        resume_id=request.resume_id,
        resume_category=request.resume_category,
        experience_evidence=request.experience_evidence or None,
        job_category=request.job_category,
        limit=limit,
        candidate_pool_size=pool_size,
    )
    resume_id = request.resume_id
    if resume_id is None and request.resume_profile is not None:
        resume_id = request.resume_profile.resume_id

    response = RecommendationResponse(
        recommendations=[
            RankedJobRecommendation(
                rank=index,
                job=job_summary(item.job),
                match=item.match,
            )
            for index, item in enumerate(result.recommendations, start=1)
        ],
        candidate_count=result.candidate_count,
        evaluated_count=result.evaluated_count,
        limit=limit,
        candidate_pool_size=pool_size,
        job_category=request.job_category,
        resume_id=resume_id,
        resume_skill_count=result.resume_skill_count,
        warnings=result.warnings,
    )
    saved = save_match(
        db,
        user_id=context.user.id,
        kind="recommendations",
        job_id=None,
        job_title=None,
        resume_id=resume_id,
        score=(
            response.recommendations[0].match.overall_compatibility_score
            if response.recommendations
            else None
        ),
        payload=response.model_dump(mode="json"),
    )
    response.match_id = saved.id
    return response


@router.get(
    "/history",
    response_model=MatchHistoryResponse,
    summary="List the authenticated user's saved match results",
)
def get_match_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> MatchHistoryResponse:
    return MatchHistoryResponse(
        items=list_matches(
            db,
            user_id=context.user.id,
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    "/history/{match_id}",
    response_model=MatchHistoryDetail,
    summary="Get one saved match result",
)
def get_match_history_item(
    match_id: str,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> MatchHistoryDetail:
    record = get_match(db, user_id=context.user.id, match_id=match_id)
    if record is None:
        raise APIError(
            status_code=404,
            code="match_not_found",
            message="Saved match was not found.",
        )
    return record


@router.delete(
    "/history",
    status_code=204,
    summary="Delete all saved match results for the authenticated user",
)
def delete_match_history_route(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> None:
    delete_match_history(db, user_id=context.user.id)
