from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.auth_schemas import CVAnalysisHistoryResponse
from app.api.dependencies import get_api_settings, get_matching_application_service
from app.api.errors import APIError
from app.api.schemas import CVAnalysisRequest, CVProfileResponse
from app.api.settings import APISettings
from app.api.uploads import read_document_upload
from app.auth.dependencies import get_auth_context
from app.auth.service import AuthContext
from app.core.user_quota import CV_ANALYSIS, enforce_user_quota
from app.db.session import get_db
from app.services.matching.application import MatchingApplicationService
from app.services.user_records import (
    cv_analysis_to_response,
    delete_cv_history,
    get_cv_analysis,
    latest_cv_analysis,
    list_cv_analyses,
    save_cv_analysis,
)

router = APIRouter(prefix="/cv", tags=["CV analysis"])


def _validate_text_length(text: str, settings: APISettings) -> None:
    if len(text) > settings.max_text_length:
        raise APIError(
            status_code=413,
            code="text_too_large",
            message=f"Résumé text exceeds the {settings.max_text_length} character limit.",
        )


@router.post(
    "/analyze",
    response_model=CVProfileResponse,
    summary="Extract and persist a structured profile from résumé text",
)
def analyze_cv(
    http_request: Request,
    request: CVAnalysisRequest,
    context: AuthContext = Depends(get_auth_context),
    settings: APISettings = Depends(get_api_settings),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> CVProfileResponse:
    _validate_text_length(request.resume_text, settings)
    enforce_user_quota(
        http_request,
        user_id=context.user.id,
        operation=CV_ANALYSIS,
        limit=settings.user_quota_cv_analysis_attempts,
        window_seconds=settings.user_quota_cv_analysis_window_seconds,
        settings=settings,
    )
    result = service.analyze_cv(
        request.resume_text,
        resume_id=request.resume_id,
        category=request.category,
        experience_evidence=request.experience_evidence or None,
    )
    analysis = save_cv_analysis(db, user_id=context.user.id, result=result)
    return cv_analysis_to_response(analysis)


@router.post(
    "/analyze/upload",
    response_model=CVProfileResponse,
    summary="Upload, extract, and persist a TXT, PDF, or DOCX résumé",
)
async def analyze_cv_upload(
    request: Request,
    file: UploadFile = File(...),
    resume_id: str | None = Form(default=None, max_length=120),
    category: str | None = Form(default=None, max_length=120),
    context: AuthContext = Depends(get_auth_context),
    settings: APISettings = Depends(get_api_settings),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> CVProfileResponse:
    document = await read_document_upload(
        file,
        max_bytes=settings.max_upload_bytes,
        max_chars=settings.max_text_length,
        max_pdf_pages=settings.max_pdf_pages,
        max_docx_entries=settings.max_docx_entries,
        max_docx_uncompressed_bytes=settings.max_docx_uncompressed_bytes,
        max_docx_compression_ratio=settings.max_docx_compression_ratio,
        request=request,
        user_id=context.user.id,
    )
    enforce_user_quota(
        request,
        user_id=context.user.id,
        operation=CV_ANALYSIS,
        limit=settings.user_quota_cv_analysis_attempts,
        window_seconds=settings.user_quota_cv_analysis_window_seconds,
        settings=settings,
    )
    result = service.analyze_cv(
        document.text,
        resume_id=resume_id,
        category=category,
    )
    analysis = save_cv_analysis(
        db,
        user_id=context.user.id,
        result=result,
        document=document,
    )
    return cv_analysis_to_response(analysis)


@router.get(
    "/latest",
    response_model=CVProfileResponse,
    summary="Get the authenticated user's latest CV analysis",
)
def get_latest_cv(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> CVProfileResponse:
    analysis = latest_cv_analysis(db, user_id=context.user.id)
    if analysis is None:
        raise APIError(
            status_code=404,
            code="cv_not_found",
            message="No CV analysis was found for this account.",
        )
    return cv_analysis_to_response(analysis)


@router.get(
    "/history",
    response_model=CVAnalysisHistoryResponse,
    summary="List the authenticated user's CV analysis history",
)
def get_cv_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> CVAnalysisHistoryResponse:
    return CVAnalysisHistoryResponse(
        items=list_cv_analyses(
            db,
            user_id=context.user.id,
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    "/history/{analysis_id}",
    response_model=CVProfileResponse,
    summary="Get one authenticated CV analysis",
)
def get_cv_history_item(
    analysis_id: str,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> CVProfileResponse:
    analysis = get_cv_analysis(
        db,
        user_id=context.user.id,
        analysis_id=analysis_id,
    )
    if analysis is None:
        raise APIError(
            status_code=404,
            code="cv_not_found",
            message="CV analysis was not found.",
        )
    return cv_analysis_to_response(analysis)


@router.delete(
    "/history",
    status_code=204,
    summary="Delete all CV analyses and candidate profiles for the authenticated user",
)
def delete_cv_history_route(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> None:
    delete_cv_history(db, user_id=context.user.id)
