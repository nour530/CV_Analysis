from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.schemas.ats import AtsReport
from app.api.auth_schemas import (
    CVAnalysisSummary,
    MatchHistoryDetail,
    MatchHistorySummary,
    UserSettingsUpdate,
)
from app.api.schemas import CVProfileResponse, DocumentMetadata
from app.db.models import (
    CandidateProfile,
    CVAnalysis,
    CvDocumentRecord,
    SavedMatch,
    UserSettings,
    utcnow,
)
from app.schemas.hybrid_match import ResumeProfile
from app.services.documents.extraction import ExtractedDocument
from app.services.matching.application import CVProfileResult


def save_cv_analysis(
    db: Session,
    *,
    user_id: str,
    result: CVProfileResult,
    document: ExtractedDocument | None = None,
    resume_text: str | None = None,
    ats: AtsReport | None = None,
) -> CVAnalysis:
    profile_json = result.profile.model_dump(mode="json")
    resume_id = result.profile.resume_id
    candidate: CandidateProfile | None = None
    if resume_id is not None:
        candidate = db.scalar(
            select(CandidateProfile).where(
                CandidateProfile.user_id == user_id,
                CandidateProfile.resume_id == resume_id,
            )
        )
    if candidate is None:
        candidate = CandidateProfile(
            user_id=user_id,
            resume_id=resume_id,
            category=result.profile.category,
            profile_json=profile_json,
        )
        db.add(candidate)
        db.flush()
    else:
        candidate.category = result.profile.category
        candidate.profile_json = profile_json
        candidate.updated_at = utcnow()

    analysis = CVAnalysis(
        user_id=user_id,
        candidate_profile_id=candidate.id,
        filename=document.filename if document is not None else None,
        document_type=document.document_type if document is not None else None,
        page_count=document.page_count if document is not None else None,
        character_count=document.character_count if document is not None else None,
        skill_count=result.skill_count,
        profile_json=profile_json,
        resume_text=resume_text if resume_text is not None else (document.text if document else None),
        layout_json=(
            document.layout.to_dict() if document is not None and document.layout is not None else None
        ),
        ats_json=ats.model_dump(mode="json") if ats is not None else None,
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    return analysis


def cv_analysis_to_response(analysis: CVAnalysis) -> CVProfileResponse:
    profile = ResumeProfile.model_validate(analysis.profile_json)
    skills_by_category: dict[str, int] = {}
    for skill in profile.skills:
        skills_by_category[skill.category] = (
            skills_by_category.get(skill.category, 0) + 1
        )
    document = (
        DocumentMetadata(
            filename=analysis.filename,
            document_type=analysis.document_type,
            page_count=analysis.page_count,
            extraction_status="success",
            character_count=analysis.character_count or 0,
        )
        if analysis.filename and analysis.document_type
        else None
    )
    return CVProfileResponse(
        profile=profile,
        skill_count=analysis.skill_count,
        skills_by_category=skills_by_category,
        experience_evidence_count=len(profile.experience_evidence),
        message=(
            f"Extracted {analysis.skill_count} canonical skill(s) and "
            f"{len(profile.experience_evidence)} experience evidence item(s)."
        ),
        document=document,
        analysis_id=analysis.id,
        candidate_profile_id=analysis.candidate_profile_id,
        ats=_stored_ats(analysis),
    )


def _stored_ats(analysis: CVAnalysis) -> AtsReport | None:
    if not analysis.ats_json:
        return None
    try:
        return AtsReport.model_validate(analysis.ats_json)
    except ValueError:
        # Reports saved by an older checker version are recomputed on demand.
        return None


def latest_cv_analysis(db: Session, *, user_id: str) -> CVAnalysis | None:
    return db.scalar(
        select(CVAnalysis)
        .where(CVAnalysis.user_id == user_id)
        .order_by(CVAnalysis.created_at.desc())
        .limit(1)
    )


def get_cv_analysis(db: Session, *, user_id: str, analysis_id: str) -> CVAnalysis | None:
    return db.scalar(
        select(CVAnalysis).where(
            CVAnalysis.id == analysis_id,
            CVAnalysis.user_id == user_id,
        )
    )


def list_cv_analyses(
    db: Session,
    *,
    user_id: str,
    limit: int,
    offset: int,
) -> list[CVAnalysisSummary]:
    rows = db.scalars(
        select(CVAnalysis)
        .where(CVAnalysis.user_id == user_id)
        .order_by(CVAnalysis.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        CVAnalysisSummary(
            analysis_id=row.id,
            filename=row.filename,
            document_type=row.document_type,
            page_count=row.page_count,
            character_count=row.character_count,
            skill_count=row.skill_count,
            resume_id=row.candidate_profile.resume_id
            if row.candidate_profile is not None
            else None,
            category=row.candidate_profile.category
            if row.candidate_profile is not None
            else None,
            ats_score=(row.ats_json or {}).get("score") if isinstance(row.ats_json, dict) else None,
            created_at=row.created_at,
        )
        for row in rows
    ]


def delete_cv_history(db: Session, *, user_id: str) -> None:
    # Saved CV builder documents are kept, but no longer point at the deleted
    # analyses (SQLite does not enforce ON DELETE SET NULL by default).
    db.execute(
        update(CvDocumentRecord)
        .where(CvDocumentRecord.user_id == user_id)
        .values(source_analysis_id=None)
    )
    db.execute(delete(CVAnalysis).where(CVAnalysis.user_id == user_id))
    db.execute(delete(CandidateProfile).where(CandidateProfile.user_id == user_id))
    db.commit()


def save_match(
    db: Session,
    *,
    user_id: str,
    kind: str,
    job_id: str | None,
    job_title: str | None,
    resume_id: str | None,
    score: float | None,
    payload: dict,
) -> SavedMatch:
    record = SavedMatch(
        user_id=user_id,
        kind=kind,
        job_id=job_id,
        job_title=job_title,
        resume_id=resume_id,
        score=score,
        result_json=payload,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def list_matches(
    db: Session,
    *,
    user_id: str,
    limit: int,
    offset: int,
) -> list[MatchHistorySummary]:
    rows = db.scalars(
        select(SavedMatch)
        .where(SavedMatch.user_id == user_id)
        .order_by(SavedMatch.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        MatchHistorySummary(
            match_id=row.id,
            kind=row.kind,  # type: ignore[arg-type]
            job_id=row.job_id,
            job_title=row.job_title,
            resume_id=row.resume_id,
            score=row.score,
            created_at=row.created_at,
        )
        for row in rows
    ]


def get_match(
    db: Session,
    *,
    user_id: str,
    match_id: str,
) -> MatchHistoryDetail | None:
    row = db.scalar(
        select(SavedMatch).where(
            SavedMatch.id == match_id,
            SavedMatch.user_id == user_id,
        )
    )
    if row is None:
        return None
    return MatchHistoryDetail(
        match_id=row.id,
        kind=row.kind,  # type: ignore[arg-type]
        created_at=row.created_at,
        payload=row.result_json,
    )


def delete_match_history(db: Session, *, user_id: str) -> None:
    db.execute(delete(SavedMatch).where(SavedMatch.user_id == user_id))
    db.commit()


def get_or_create_user_settings(db: Session, *, user_id: str) -> UserSettings:
    settings = db.get(UserSettings, user_id)
    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            settings = db.get(UserSettings, user_id)
            if settings is None:
                raise
            return settings
        db.refresh(settings)
    return settings


def update_user_settings(
    db: Session,
    *,
    user_id: str,
    payload: UserSettingsUpdate,
) -> UserSettings:
    settings = get_or_create_user_settings(db, user_id=user_id)
    settings.recommendation_limit = payload.recommendation_limit
    settings.candidate_pool_size = payload.candidate_pool_size
    settings.preferred_category = payload.preferred_category
    settings.allow_remote_analysis = payload.allow_remote_analysis
    settings.updated_at = utcnow()
    db.commit()
    db.refresh(settings)
    return settings


def count_user_records(db: Session, *, user_id: str) -> dict[str, int]:
    return {
        "cv_analyses": db.scalar(
            select(func.count()).select_from(CVAnalysis).where(CVAnalysis.user_id == user_id)
        )
        or 0,
        "candidate_profiles": db.scalar(
            select(func.count())
            .select_from(CandidateProfile)
            .where(CandidateProfile.user_id == user_id)
        )
        or 0,
        "saved_matches": db.scalar(
            select(func.count()).select_from(SavedMatch).where(SavedMatch.user_id == user_id)
        )
        or 0,
    }
