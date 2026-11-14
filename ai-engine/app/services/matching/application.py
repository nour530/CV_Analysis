from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.schemas.hybrid_match import HybridMatchResult, JobProfile, ResumeProfile
from app.services.job_catalog import CatalogJob, JobCatalog
from app.services.llm.base import LLMProvider
from app.services.matching.exceptions import (
    HybridMatchingError,
    InvalidMatchingInputError,
    JobNotFoundError,
)
from app.services.matching.hybrid_service import HybridMatchingService
from app.services.matching.profiles import build_resume_profile
from app.services.nlp.skills import LocalSkillExtractorError, normalize_skill_key


@dataclass(frozen=True, slots=True)
class CVProfileResult:
    profile: ResumeProfile
    skill_count: int
    skills_by_category: dict[str, int]
    experience_evidence_count: int
    message: str


@dataclass(frozen=True, slots=True)
class RankedJob:
    job: CatalogJob
    match: HybridMatchResult


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    recommendations: list[RankedJob]
    candidate_count: int
    evaluated_count: int
    resume_skill_count: int
    warnings: list[str]


class MatchingApplicationService:
    """Application-level workflows over the existing hybrid engine."""

    def __init__(
        self,
        catalog: JobCatalog,
        hybrid_service: HybridMatchingService | None = None,
    ) -> None:
        self.catalog = catalog
        self.hybrid_service = hybrid_service or HybridMatchingService()

    @staticmethod
    def _require_text(value: str | None, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise InvalidMatchingInputError(f"{field_name} must not be empty.")
        return value

    def _profile_from_text(
        self,
        resume_text: str,
        *,
        resume_id: str | None,
        resume_category: str | None,
        experience_evidence: Sequence[str] | None,
    ) -> ResumeProfile:
        checked = self._require_text(resume_text, "resume_text")
        try:
            return build_resume_profile(
                checked,
                extractor=self.hybrid_service.extractor,
                resume_id=resume_id,
                category=resume_category,
                experience_evidence=experience_evidence,
            )
        except LocalSkillExtractorError as exc:
            raise InvalidMatchingInputError(str(exc)) from exc

    def analyze_cv(
        self,
        resume_text: str,
        *,
        resume_id: str | None = None,
        category: str | None = None,
        experience_evidence: Sequence[str] | None = None,
    ) -> CVProfileResult:
        profile = self._profile_from_text(
            resume_text,
            resume_id=resume_id,
            resume_category=category,
            experience_evidence=experience_evidence,
        )
        skills_by_category: dict[str, int] = {}
        for skill in profile.skills:
            skills_by_category[skill.category] = (
                skills_by_category.get(skill.category, 0) + 1
            )
        message = (
            f"Extracted {len(profile.skills)} canonical skill(s) and "
            f"{len(profile.experience_evidence)} experience evidence item(s)."
        )
        return CVProfileResult(
            profile=profile,
            skill_count=len(profile.skills),
            skills_by_category=skills_by_category,
            experience_evidence_count=len(profile.experience_evidence),
            message=message,
        )

    def _resolve_resume(
        self,
        *,
        resume_text: str | None,
        resume_profile: ResumeProfile | None,
        resume_id: str | None,
        resume_category: str | None,
        experience_evidence: Sequence[str] | None,
    ) -> ResumeProfile:
        if resume_profile is not None:
            return resume_profile
        if resume_text is None:
            raise InvalidMatchingInputError(
                "resume_text or resume_profile is required."
            )
        return self._profile_from_text(
            resume_text,
            resume_id=resume_id,
            resume_category=resume_category,
            experience_evidence=experience_evidence,
        )

    def _resolve_catalog_job(self, job_id: str) -> CatalogJob:
        job = self.catalog.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Job '{job_id}' was not found in the catalog.")
        return job

    def match_job(
        self,
        *,
        resume_text: str | None,
        resume_profile: ResumeProfile | None,
        job_id: str | None,
        job_text: str | None,
        job_title: str | None,
        job_category: str | None,
        resume_id: str | None,
        resume_category: str | None,
        experience_evidence: Sequence[str] | None,
        include_llm: bool,
        allow_remote_analysis: bool,
        provider: LLMProvider | None,
    ) -> tuple[CatalogJob | None, HybridMatchResult]:
        if bool(job_id) == bool(job_text):
            raise InvalidMatchingInputError(
                "Provide exactly one of job_id or job_text."
            )

        resume = self._resolve_resume(
            resume_text=resume_text,
            resume_profile=resume_profile,
            resume_id=resume_id,
            resume_category=resume_category,
            experience_evidence=experience_evidence,
        )

        catalog_job: CatalogJob | None = None
        job_profile: JobProfile | None = None
        checked_job_text: str | None = None
        if job_id is not None:
            catalog_job = self._resolve_catalog_job(job_id)
            job_profile = self.catalog.profile(catalog_job)
        else:
            checked_job_text = self._require_text(job_text, "job_text")

        remote_resume_text = resume_text if resume_profile is None else None
        remote_job_text = checked_job_text or (
            catalog_job.description if catalog_job else None
        )
        result = self.hybrid_service.match_hybrid(
            resume_text=remote_resume_text,
            resume_profile=resume,
            job_profile=job_profile,
            job_text=remote_job_text,
            job_id=job_id or (catalog_job.job_id if catalog_job else None),
            job_title=job_title or (catalog_job.title if catalog_job else None),
            job_category=job_category or (
                catalog_job.category if catalog_job else None
            ),
            resume_id=resume.resume_id,
            resume_category=resume.category,
            include_llm=include_llm,
            allow_remote_analysis=allow_remote_analysis,
            provider=provider,
        )
        return catalog_job, result

    def recommend_jobs(
        self,
        *,
        resume_text: str | None,
        resume_profile: ResumeProfile | None,
        resume_id: str | None,
        resume_category: str | None,
        experience_evidence: Sequence[str] | None,
        job_category: str | None,
        limit: int,
        candidate_pool_size: int,
    ) -> RecommendationResult:
        if limit < 1 or limit > candidate_pool_size:
            raise InvalidMatchingInputError(
                "limit must be at least 1 and cannot exceed candidate_pool_size."
            )
        resume = self._resolve_resume(
            resume_text=resume_text,
            resume_profile=resume_profile,
            resume_id=resume_id,
            resume_category=resume_category,
            experience_evidence=experience_evidence,
        )

        category_key = job_category.strip().casefold() if job_category else None
        candidate_jobs = [
            job
            for job in self.catalog.all_jobs()
            if category_key is None
            or (job.category or "").casefold() == category_key
        ]
        if not candidate_jobs:
            return RecommendationResult(
                recommendations=[],
                candidate_count=0,
                evaluated_count=0,
                resume_skill_count=len(resume.skills),
                warnings=["No catalog jobs matched the requested category filter."],
            )

        resume_keys = frozenset(
            normalize_skill_key(skill.skill) for skill in resume.skills
        )
        config = self.hybrid_service.config
        key_weights = {
            normalize_skill_key(skill.skill): config.skill_weight(skill.category, skill.skill)
            for skill in resume.skills
        }

        def overlap(keys: frozenset[str]) -> float:
            return round(sum(key_weights[key] for key in resume_keys & keys), 6)

        indexed_jobs = list(enumerate(candidate_jobs))
        if resume_keys:
            # Rank by weighted overlap so generic skills (languages, soft
            # skills) do not crowd specialised jobs out of the candidate pool.
            indexed_jobs.sort(
                key=lambda item: (
                    overlap(item[1].required_keys),
                    overlap(item[1].preferred_keys),
                    overlap(item[1].skill_keys),
                    -item[0],
                ),
                reverse=True,
            )
        selected = [job for _, job in indexed_jobs[:candidate_pool_size]]

        matches: list[RankedJob] = []
        for job in selected:
            match = self.hybrid_service.match_local(
                resume_profile=resume,
                job_profile=self.catalog.profile(job),
                job_id=job.job_id,
                resume_id=resume.resume_id,
                resume_category=resume.category,
            )
            matches.append(RankedJob(job=job, match=match))

        matches.sort(
            key=lambda item: (
                -item.match.overall_compatibility_score,
                item.job.job_id,
            )
        )
        matches = matches[:limit]

        warnings: list[str] = []
        if not resume.skills:
            warnings.append(
                "No canonical skills were extracted from the résumé, so candidate "
                "retrieval used catalog order and only the configured candidate pool "
                "was evaluated. Treat the ranking as low-confidence."
            )
        elif not any(
            resume_keys & (job.required_keys | job.preferred_keys | job.skill_keys)
            for job in selected
        ):
            warnings.append(
                "No skill overlap was detected in the evaluated job pool; treat the "
                "ranking as low-confidence."
            )

        return RecommendationResult(
            recommendations=matches,
            candidate_count=len(candidate_jobs),
            evaluated_count=len(selected),
            resume_skill_count=len(resume.skills),
            warnings=warnings,
        )

    def get_job(self, job_id: str) -> CatalogJob:
        return self._resolve_catalog_job(job_id)

    def list_jobs(
        self,
        *,
        category: str | None,
        query: str | None,
        offset: int,
        limit: int,
        workplace: str | None = None,
        location: str | None = None,
        company: str | None = None,
    ) -> tuple[list[CatalogJob], int]:
        return self.catalog.list_jobs(
            category=category,
            query=query,
            offset=offset,
            limit=limit,
            workplace=workplace,
            location=location,
            company=company,
        )
