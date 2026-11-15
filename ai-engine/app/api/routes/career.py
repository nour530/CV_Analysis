"""Career fields, occupations and personalised career insights (ESCO-based)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_auth_context
from app.auth.service import AuthContext
from app.db.session import get_db
from app.schemas.common import StrictSchema
from app.services.ats.checker import detect_language
from app.services.career.classifier import get_domain_classifier
from app.services.career.esco import EscoOccupation, get_esco_catalog
from app.services.career.paths import OccupationFit, get_career_advisor
from app.services.user_records import get_or_create_user_settings, latest_cv_analysis

router = APIRouter(prefix="/career", tags=["Career"])

ATTRIBUTION = "This service uses the ESCO classification of the European Commission."


class DomainItem(StrictSchema):
    id: str
    label: str
    label_en: str
    label_fr: str
    occupations: int = Field(ge=0)


class DomainListResponse(StrictSchema):
    domains: list[DomainItem]
    attribution: str = ATTRIBUTION


class OccupationItem(StrictSchema):
    uri: str
    title: str
    domain_id: str | None = None
    description: str | None = None


class OccupationListResponse(StrictSchema):
    occupations: list[OccupationItem]
    attribution: str = ATTRIBUTION


class DomainScore(StrictSchema):
    id: str
    label: str
    probability: float = Field(ge=0.0, le=1.0)


class RoleFit(StrictSchema):
    uri: str
    title: str
    domain_id: str | None = None
    domain_label: str | None = None
    fit: float = Field(ge=0.0, le=1.0)
    skills_you_have: list[str] = Field(default_factory=list)
    skills_to_build: list[str] = Field(default_factory=list)


class CareerInsightsResponse(StrictSchema):
    has_cv: bool
    language: Literal["en", "fr"] = "en"
    detected_domains: list[DomainScore] = Field(default_factory=list)
    target_domain: DomainScore | None = None
    target_role: str | None = None
    target_role_fit: RoleFit | None = None
    suggested_roles: list[RoleFit] = Field(default_factory=list)
    available: bool = True
    attribution: str = ATTRIBUTION


def _language(value: str | None) -> Literal["en", "fr"]:
    return "fr" if value == "fr" else "en"


def _occupation(occupation: EscoOccupation, language: str, *, describe: bool = False) -> OccupationItem:
    return OccupationItem(
        uri=occupation.uri,
        title=occupation.label(language),
        domain_id=occupation.domain_id,
        description=occupation.description(language)[:300] if describe else None,
    )


def _role(fit: OccupationFit, language: str) -> RoleFit:
    catalog = get_esco_catalog()
    domain = catalog.domains.get(fit.occupation.domain_id or "")
    return RoleFit(
        uri=fit.occupation.uri,
        title=fit.occupation.label(language),
        domain_id=fit.occupation.domain_id,
        domain_label=domain.label(language) if domain else None,
        fit=min(1.0, max(0.0, fit.score)),
        skills_you_have=fit.have[:8],
        skills_to_build=fit.missing[:8],
    )


@router.get("/domains", response_model=DomainListResponse, summary="Career fields")
def list_domains(lang: str = Query(default="en", pattern="^(en|fr)$")) -> DomainListResponse:
    catalog = get_esco_catalog()
    counts: dict[str, int] = {}
    for occupation in catalog.occupations:
        if occupation.domain_id:
            counts[occupation.domain_id] = counts.get(occupation.domain_id, 0) + 1
    return DomainListResponse(
        domains=[
            DomainItem(
                id=domain.id,
                label=domain.label(_language(lang)),
                label_en=domain.en,
                label_fr=domain.fr,
                occupations=counts.get(domain.id, 0),
            )
            for domain in catalog.domains.values()
        ]
    )


@router.get("/occupations", response_model=OccupationListResponse, summary="Search occupations (EN/FR)")
def search_occupations(
    q: str = Query(default="", max_length=100),
    domain: str | None = Query(default=None, max_length=40),
    lang: str = Query(default="en", pattern="^(en|fr)$"),
    limit: int = Query(default=10, ge=1, le=30),
) -> OccupationListResponse:
    catalog = get_esco_catalog()
    language = _language(lang)
    return OccupationListResponse(
        occupations=[
            _occupation(occupation, language, describe=True)
            for occupation in catalog.search_occupations(q, domain_id=domain, limit=limit)
        ]
    )


def build_insights(db: Session, user_id: str) -> CareerInsightsResponse:
    """Personal career insights; shared by the API and the career assistant."""
    catalog = get_esco_catalog()
    settings = get_or_create_user_settings(db, user_id=user_id)
    analysis = latest_cv_analysis(db, user_id=user_id)
    text = (analysis.resume_text or (analysis.profile_json or {}).get("semantic_text") or "") if analysis else ""
    language = _language(detect_language(text)) if text else "en"
    target = catalog.domains.get(settings.target_domain or "")
    response = CareerInsightsResponse(
        has_cv=bool(text),
        language=language,
        target_role=settings.target_role,
        target_domain=DomainScore(id=target.id, label=target.label(language), probability=1.0) if target else None,
        available=catalog.available,
    )
    if not catalog.available or not text:
        return response

    classifier = get_domain_classifier()
    if classifier is not None:
        response.detected_domains = [
            DomainScore(
                id=prediction.domain_id,
                label=catalog.domains[prediction.domain_id].label(language),
                probability=prediction.probability,
            )
            for prediction in classifier.predict(text, top=3)
            if prediction.domain_id in catalog.domains
        ]

    advisor = get_career_advisor()
    if advisor is not None:
        if settings.target_role:
            occupation = advisor.find_occupation(settings.target_role)
            if occupation is not None:
                response.target_role_fit = _role(advisor.skill_gap(occupation, text, language=language), language)
        domain_id = target.id if target else None
        response.suggested_roles = [
            _role(fit, language) for fit in advisor.suggest(text, domain_id=domain_id, language=language, limit=5)
        ]
    return response


@router.get("/insights", response_model=CareerInsightsResponse, summary="Personal career insights from your CV and goal")
def career_insights(
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> CareerInsightsResponse:
    return build_insights(db, context.user.id)
