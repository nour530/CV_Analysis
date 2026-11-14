from __future__ import annotations

from time import perf_counter
from typing import Sequence

from app.schemas.common import Uncertainty
from app.schemas.hybrid_match import (
    ComponentRecord,
    HybridMatchResult,
    JobProfile,
    ResumeProfile,
)
from app.schemas.job_analysis import JobAnalysis
from app.schemas.resume_analysis import ResumeAnalysis
from app.services.llm.base import LLMProvider
from app.services.llm.exceptions import LLMError
from app.services.matching.config import MatchingConfig
from app.services.matching.exceptions import HybridMatchingError
from app.services.matching.local_matcher import LocalMatcher
from app.services.matching.profiles import (
    build_job_profile,
    build_job_profile_from_analysis,
    build_resume_profile,
    build_resume_profile_from_analysis,
)
from app.services.nlp.skills import LocalSkillExtractor


class HybridMatchingService:
    """Combine deterministic local scoring with optional LLM explanations.

    The local score is authoritative. An LLM can add an explanation and
    recommendations, but it cannot change the score, matched skills, missing
    required skills, or classification. Any LLM failure degrades gracefully to the
    deterministic result.
    """

    def __init__(
        self,
        config: MatchingConfig | None = None,
        *,
        matcher: LocalMatcher | None = None,
        extractor: LocalSkillExtractor | None = None,
    ) -> None:
        self.config = config or MatchingConfig()
        self.extractor = extractor or LocalSkillExtractor()
        self.matcher = matcher or LocalMatcher(
            self.config, extractor=self.extractor
        )

    def _build_resume_profile(
        self,
        *,
        resume_text: str | None,
        resume_analysis: ResumeAnalysis | None,
        resume_profile: ResumeProfile | None,
        resume_id: str | None,
        resume_category: str | None,
        experience_evidence: Sequence[str] | None,
    ) -> ResumeProfile:
        if resume_profile is not None:
            return resume_profile
        if resume_analysis is not None:
            return build_resume_profile_from_analysis(
                resume_analysis,
                raw_text=resume_text,
                extractor=self.extractor,
                resume_id=resume_id,
                category=resume_category,
            )
        if not resume_text:
            raise HybridMatchingError(
                "resume_text, resume_analysis, or resume_profile is required."
            )
        return build_resume_profile(
            resume_text,
            extractor=self.extractor,
            resume_id=resume_id,
            category=resume_category,
            experience_evidence=experience_evidence,
        )

    def _build_job_profile(
        self,
        *,
        job_text: str | None,
        job_analysis: JobAnalysis | None,
        job_profile: JobProfile | None,
        job_id: str | None,
        job_title: str | None,
        job_category: str | None,
    ) -> JobProfile:
        if job_profile is not None:
            return job_profile
        if job_analysis is not None:
            return build_job_profile_from_analysis(
                job_analysis,
                raw_text=job_text,
                extractor=self.extractor,
                job_id=job_id,
                category=job_category,
            )
        if not job_text:
            raise HybridMatchingError(
                "job_text, job_analysis, or job_profile is required."
            )
        return build_job_profile(
            job_text,
            extractor=self.extractor,
            job_id=job_id,
            title=job_title,
            category=job_category,
        )

    @staticmethod
    def _append_component(
        result: HybridMatchResult, component: ComponentRecord
    ) -> list[ComponentRecord]:
        return [*result.components, component][:20]

    @staticmethod
    def _dedupe_uncertainties(
        values: Sequence[Uncertainty], limit: int = 50
    ) -> list[Uncertainty]:
        unique: dict[tuple[str, str], Uncertainty] = {}
        for item in values:
            key = (item.field, item.reason)
            unique.setdefault(key, item)
        return list(unique.values())[:limit]

    def match_local(
        self,
        *,
        resume_text: str | None = None,
        job_text: str | None = None,
        resume_analysis: ResumeAnalysis | None = None,
        job_analysis: JobAnalysis | None = None,
        resume_profile: ResumeProfile | None = None,
        job_profile: JobProfile | None = None,
        resume_id: str | None = None,
        job_id: str | None = None,
        resume_category: str | None = None,
        job_category: str | None = None,
        job_title: str | None = None,
        experience_evidence: Sequence[str] | None = None,
    ) -> HybridMatchResult:
        resume = self._build_resume_profile(
            resume_text=resume_text,
            resume_analysis=resume_analysis,
            resume_profile=resume_profile,
            resume_id=resume_id,
            resume_category=resume_category,
            experience_evidence=experience_evidence,
        )
        job = self._build_job_profile(
            job_text=job_text,
            job_analysis=job_analysis,
            job_profile=job_profile,
            job_id=job_id,
            job_title=job_title,
            job_category=job_category,
        )
        return self.matcher.match(resume, job)

    def match_hybrid(
        self,
        *,
        resume_text: str | None = None,
        job_text: str | None = None,
        provider: LLMProvider | None = None,
        resume_analysis: ResumeAnalysis | None = None,
        job_analysis: JobAnalysis | None = None,
        resume_profile: ResumeProfile | None = None,
        job_profile: JobProfile | None = None,
        include_llm: bool = True,
        allow_remote_analysis: bool = False,
        resume_id: str | None = None,
        job_id: str | None = None,
        resume_category: str | None = None,
        job_category: str | None = None,
        job_title: str | None = None,
        experience_evidence: Sequence[str] | None = None,
    ) -> HybridMatchResult:
        local_result = self.match_local(
            resume_text=resume_text,
            job_text=job_text,
            resume_analysis=resume_analysis,
            job_analysis=job_analysis,
            resume_profile=resume_profile,
            job_profile=job_profile,
            resume_id=resume_id,
            job_id=job_id,
            resume_category=resume_category,
            job_category=job_category,
            job_title=job_title,
            experience_evidence=experience_evidence,
        )

        if not include_llm:
            return local_result.model_copy(
                update={
                    "components": self._append_component(
                        local_result,
                        ComponentRecord(
                            name="openai_explanation",
                            status="skipped",
                            detail="LLM explanation was disabled for this request.",
                        ),
                    )
                }
            )

        if provider is None:
            return local_result.model_copy(
                update={
                    "components": self._append_component(
                        local_result,
                        ComponentRecord(
                            name="openai_explanation",
                            status="skipped",
                            detail="No LLM provider was configured.",
                        ),
                    )
                }
            )

        if resume_analysis is None and (not allow_remote_analysis or not resume_text):
            return local_result.model_copy(
                update={
                    "components": self._append_component(
                        local_result,
                        ComponentRecord(
                            name="openai_explanation",
                            status="skipped",
                            detail=(
                                "Structured résumé analysis is required for LLM "
                                "explanation; remote analysis was not authorized."
                            ),
                        ),
                    )
                }
            )
        if job_analysis is None and (not allow_remote_analysis or not job_text):
            return local_result.model_copy(
                update={
                    "components": self._append_component(
                        local_result,
                        ComponentRecord(
                            name="openai_explanation",
                            status="skipped",
                            detail=(
                                "Structured job analysis is required for LLM explanation; "
                                "remote analysis was not authorized."
                            ),
                        ),
                    )
                }
            )

        started = perf_counter()
        try:
            if resume_analysis is None:
                resume_analysis = provider.analyze_resume(resume_text)  # type: ignore[arg-type]
            if job_analysis is None:
                job_analysis = provider.analyze_job(job_text)  # type: ignore[arg-type]

            analysis = provider.explain_match(
                resume_analysis,
                job_analysis,
                verified_results=local_result,
            )
        except LLMError as exc:
            duration = round((perf_counter() - started) * 1000, 2)
            return local_result.model_copy(
                update={
                    "components": self._append_component(
                        local_result,
                        ComponentRecord(
                            name="openai_explanation",
                            status="failed",
                            duration_ms=duration,
                            detail=(
                                "LLM explanation failed; deterministic local results were "
                                f"retained. Error type: {type(exc).__name__}."
                            ),
                        ),
                    ),
                    "uncertainty_indicators": self._dedupe_uncertainties(
                        [
                            *local_result.uncertainty_indicators,
                            Uncertainty(
                                field="openai_explanation",
                                reason=(
                                    "The LLM explanation was unavailable, so only the "
                                    "deterministic local result is reported."
                                ),
                                severity="medium",
                            ),
                        ]
                    ),
                }
            )
        except Exception as exc:
            duration = round((perf_counter() - started) * 1000, 2)
            return local_result.model_copy(
                update={
                    "components": self._append_component(
                        local_result,
                        ComponentRecord(
                            name="openai_explanation",
                            status="failed",
                            duration_ms=duration,
                            detail=(
                                "Unexpected explanation failure; deterministic local "
                                f"results were retained. Error type: {type(exc).__name__}."
                            ),
                        ),
                    ),
                    "uncertainty_indicators": self._dedupe_uncertainties(
                        [
                            *local_result.uncertainty_indicators,
                            Uncertainty(
                                field="openai_explanation",
                                reason=(
                                    "The LLM explanation failed unexpectedly; only the "
                                    "deterministic local result is reported."
                                ),
                                severity="medium",
                            ),
                        ]
                    ),
                }
            )

        duration = round((perf_counter() - started) * 1000, 2)
        recommendations = (
            analysis.recommendations
            if analysis.recommendations
            else local_result.recommendations
        )
        return local_result.model_copy(
            update={
                "explanation": analysis.explanation,
                "recommendations": recommendations[:20],
                "recommendation_source": (
                    "openai" if analysis.recommendations else "local_deterministic"
                ),
                "uncertainty_indicators": self._dedupe_uncertainties(
                    [*local_result.uncertainty_indicators, *analysis.uncertainties]
                ),
                "components": self._append_component(
                    local_result,
                    ComponentRecord(
                        name="openai_explanation",
                        status="success",
                        duration_ms=duration,
                        detail=(
                            f"Explanation generated by {provider.provider_name}/"
                            f"{provider.model_name}; local score was not modified."
                        ),
                    ),
                ),
                "llm_metadata": analysis.metadata,
            }
        )
