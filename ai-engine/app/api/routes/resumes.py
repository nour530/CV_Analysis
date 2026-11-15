"""CV builder: create, edit, check, improve and export structured CVs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_api_settings, get_llm_provider, get_matching_application_service
from app.api.errors import APIError
from app.api.settings import APISettings
from app.auth.dependencies import get_auth_context
from app.auth.service import AuthContext
from app.core.user_quota import REMOTE_LLM, enforce_user_quota
from app.db.models import CVAnalysis, CvDocumentRecord, utcnow
from app.db.session import get_db
from app.schemas.ats import AtsReport
from app.schemas.cv_document import (
    CvDocument,
    CvSuggestionRequest,
    CvSuggestionResponse,
    ResumeCreateRequest,
    ResumeListResponse,
    ResumeResponse,
    ResumeSummary,
    ResumeUpdateRequest,
)
from app.services.ats import AtsChecker
from app.services.cv_builder.parser import document_language, parse_cv_text
from app.services.cv_builder.pdf import render_pdf
from app.services.cv_builder.render import export_filename, render_docx, render_text
from app.services.cv_builder.suggestions import SuggestionService, merge_with_rules
from app.services.documents.layout import LayoutSignals
from app.services.llm.base import BaseLLMProvider
from app.services.llm.exceptions import LLMError
from app.services.matching.application import MatchingApplicationService
from app.services.user_records import get_or_create_user_settings

router = APIRouter(prefix="/resumes", tags=["CV builder"])

MAX_RESUMES_PER_USER = 20
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
AI_NOTICES = {
    "not_requested": "Showing rule-based tips. Turn on AI suggestions to get rewrites.",
    "server_disabled": "AI rewrites are turned off on this server, so these are rule-based tips.",
    "user_disabled": "AI rewrites need your consent: enable remote AI analysis in Settings. These are rule-based tips.",
    "no_provider": "No AI provider is configured on this server, so these are rule-based tips.",
    "failed": "The AI service did not respond, so these are rule-based tips. Try again later.",
}


def _load(db: Session, user_id: str, resume_id: str) -> CvDocumentRecord:
    record = db.scalar(
        select(CvDocumentRecord).where(
            CvDocumentRecord.id == resume_id, CvDocumentRecord.user_id == user_id
        )
    )
    if record is None:
        raise APIError(status_code=404, code="resume_not_found", message="CV was not found.")
    return record


def _summary(record: CvDocumentRecord) -> ResumeSummary:
    return ResumeSummary(
        id=record.id,
        title=record.title,
        language=record.language,  # type: ignore[arg-type]
        template=record.template,  # type: ignore[arg-type]
        ats_score=record.ats_score,
        updated_at=record.updated_at,
        created_at=record.created_at,
    )


def _response(record: CvDocumentRecord) -> ResumeResponse:
    return ResumeResponse(
        **_summary(record).model_dump(),
        content=CvDocument.model_validate(record.content_json),
        source_analysis_id=record.source_analysis_id,
    )


def _ats(
    service: MatchingApplicationService,
    record: CvDocumentRecord,
    *,
    job_id: str | None = None,
) -> AtsReport:
    content = CvDocument.model_validate(record.content_json)
    text = render_text(content, record.language)
    requirements = job_title = company = None
    if job_id:
        job = service.get_job(job_id)
        requirements = service.catalog.profile(job).requirements
        job_title, company = job.title, job.company
    # The exported DOCX is single-column with no tables, text boxes, images or
    # header contact, so the layout checks reflect what the user downloads.
    return AtsChecker(extractor=service.hybrid_service.extractor).check(
        text,
        layout=LayoutSignals(document_type="docx"),
        document_type="docx",
        job_requirements=requirements,
        job_id=job_id,
        job_title=job_title,
        company=company,
    )


@router.get("", response_model=ResumeListResponse, summary="List the user's CVs")
def list_resumes(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ResumeListResponse:
    rows = db.scalars(
        select(CvDocumentRecord)
        .where(CvDocumentRecord.user_id == context.user.id)
        .order_by(CvDocumentRecord.updated_at.desc())
    ).all()
    return ResumeListResponse(items=[_summary(row) for row in rows])


@router.post("", response_model=ResumeResponse, status_code=201, summary="Create a CV, by default from the latest analysed CV")
def create_resume(
    request: ResumeCreateRequest,
    context: AuthContext = Depends(get_auth_context),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> ResumeResponse:
    count = db.scalar(
        select(func.count()).select_from(CvDocumentRecord).where(CvDocumentRecord.user_id == context.user.id)
    )
    if (count or 0) >= MAX_RESUMES_PER_USER:
        raise APIError(
            status_code=409,
            code="resume_limit_reached",
            message=f"You can keep up to {MAX_RESUMES_PER_USER} CVs. Delete one to create another.",
        )

    analysis: CVAnalysis | None = None
    if not request.blank:
        query = select(CVAnalysis).where(CVAnalysis.user_id == context.user.id)
        if request.from_analysis_id:
            query = query.where(CVAnalysis.id == request.from_analysis_id)
        analysis = db.scalar(query.order_by(CVAnalysis.created_at.desc()).limit(1))
        if analysis is None and request.from_analysis_id:
            raise APIError(status_code=404, code="cv_not_found", message="CV analysis was not found.")

    if analysis is not None:
        text = analysis.resume_text or str((analysis.profile_json or {}).get("semantic_text") or "")
        content = parse_cv_text(text, extractor=service.hybrid_service.extractor)
        language = request.language or document_language(text)
        default_title = content.contact.full_name or analysis.filename or "My CV"
    else:
        content = CvDocument()
        language = request.language or "en"
        default_title = "New CV"

    record = CvDocumentRecord(
        user_id=context.user.id,
        title=(request.title or default_title)[:120],
        language=language,
        template="classic",
        content_json=content.model_dump(mode="json"),
        source_analysis_id=analysis.id if analysis is not None else None,
    )
    db.add(record)
    db.flush()
    record.ats_score = _ats(service, record).score
    db.commit()
    db.refresh(record)
    return _response(record)


@router.get("/{resume_id}", response_model=ResumeResponse, summary="Get one CV")
def get_resume(
    resume_id: str = Path(max_length=36),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> ResumeResponse:
    return _response(_load(db, context.user.id, resume_id))


@router.put("/{resume_id}", response_model=ResumeResponse, summary="Save changes to a CV")
def update_resume(
    request: ResumeUpdateRequest,
    resume_id: str = Path(max_length=36),
    context: AuthContext = Depends(get_auth_context),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> ResumeResponse:
    record = _load(db, context.user.id, resume_id)
    if request.title is not None:
        record.title = request.title
    if request.language is not None:
        record.language = request.language
    if request.template is not None:
        record.template = request.template
    if request.content is not None:
        record.content_json = request.content.model_dump(mode="json")
    record.ats_score = _ats(service, record).score
    record.updated_at = utcnow()
    db.commit()
    db.refresh(record)
    return _response(record)


@router.delete("/{resume_id}", status_code=204, summary="Delete a CV")
def delete_resume(
    resume_id: str = Path(max_length=36),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> None:
    db.delete(_load(db, context.user.id, resume_id))
    db.commit()


@router.post("/{resume_id}/ats", response_model=AtsReport, summary="ATS check of a CV as it will be exported")
def check_resume_ats(
    request: CvSuggestionRequest | None = None,
    resume_id: str = Path(max_length=36),
    context: AuthContext = Depends(get_auth_context),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> AtsReport:
    record = _load(db, context.user.id, resume_id)
    return _ats(service, record, job_id=request.job_id if request else None)


@router.get("/{resume_id}/export.docx", summary="Download the CV as an ATS-friendly Word file")
def export_resume_docx(
    resume_id: str = Path(max_length=36),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> Response:
    record = _load(db, context.user.id, resume_id)
    content = CvDocument.model_validate(record.content_json)
    payload = render_docx(content, language=record.language, template=record.template)
    filename = export_filename(content, record.title, "docx")
    return Response(
        content=payload,
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{resume_id}/export.pdf", summary="Download the CV as an ATS-friendly PDF")
def export_resume_pdf(
    resume_id: str = Path(max_length=36),
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> Response:
    record = _load(db, context.user.id, resume_id)
    content = CvDocument.model_validate(record.content_json)
    payload = render_pdf(content, language=record.language, template=record.template)
    filename = export_filename(content, record.title, "pdf")
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{resume_id}/suggestions", response_model=CvSuggestionResponse, summary="Suggest improvements (rules, plus AI rewrites when allowed)")
def suggest_improvements(
    http_request: Request,
    request: CvSuggestionRequest,
    resume_id: str = Path(max_length=36),
    context: AuthContext = Depends(get_auth_context),
    settings: APISettings = Depends(get_api_settings),
    service: MatchingApplicationService = Depends(get_matching_application_service),
    db: Session = Depends(get_db),
) -> CvSuggestionResponse:
    record = _load(db, context.user.id, resume_id)
    content = CvDocument.model_validate(record.content_json)
    language = record.language
    requirements = job_title = None
    if request.job_id:
        job = service.get_job(request.job_id)
        requirements = service.catalog.profile(job).requirements
        job_title = job.title

    helper = SuggestionService(extractor=service.hybrid_service.extractor)
    rules = helper.rules(
        content,
        language=language,
        scope=request.scope,
        experience_id=request.experience_id,
        job_requirements=requirements,
    )

    reason = "not_requested"
    provider = None
    if request.allow_remote_analysis:
        if not settings.allow_remote_llm_analysis:
            reason = "server_disabled"
        elif not get_or_create_user_settings(db, user_id=context.user.id).allow_remote_analysis:
            reason = "user_disabled"
        else:
            provider = get_llm_provider()
            reason = "no_provider" if not isinstance(provider, BaseLLMProvider) else ""

    if isinstance(provider, BaseLLMProvider) and not reason:
        items_text, items = helper.build_ai_input(
            content,
            scope=request.scope,
            experience_id=request.experience_id,
            job_requirements=requirements,
            job_title=job_title,
        )
        if items:
            enforce_user_quota(
                http_request,
                user_id=context.user.id,
                operation=REMOTE_LLM,
                limit=settings.user_quota_remote_llm_attempts,
                window_seconds=settings.user_quota_remote_llm_window_seconds,
                settings=settings,
            )
            try:
                output = provider.improve_cv(items_text, language=language)
            except LLMError:
                reason = "failed"
            else:
                ai = helper.validate_ai(
                    content,
                    [(item.ref, item.text, item.reason) for item in output.suggestions],
                    items,
                    language=language,
                )
                return CvSuggestionResponse(
                    source="ai",
                    suggestions=merge_with_rules(ai, rules),
                    notice=None if ai else "The AI found nothing to improve; showing rule-based tips.",
                    job_id=request.job_id,
                    job_title=job_title,
                )
        else:
            reason = "not_requested"

    return CvSuggestionResponse(
        source="rules",
        suggestions=rules[:60],
        notice=AI_NOTICES.get(reason) if request.allow_remote_analysis or reason == "not_requested" else None,
        job_id=request.job_id,
        job_title=job_title,
    )
