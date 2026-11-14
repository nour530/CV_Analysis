from __future__ import annotations

from time import perf_counter
from typing import Any, Sequence

from app.schemas.common import Uncertainty
from app.schemas.hybrid_match import (
    ComponentRecord,
    ExperienceEvidenceItem,
    HybridMatchResult,
    JobProfile,
    MatchedSkill,
    MissingSkill,
    PreferredSkill,
    ResumeProfile,
    ScoreBreakdown,
    ScoreComponent,
    MatchClassification,
)
from app.services.matching.config import (
    MatchingConfig,
    RequirementImportanceWeights,
)
from app.services.nlp.embeddings import LocalEmbeddingError, LocalTfidfEmbedder
from app.services.nlp.skills import (
    LocalSkillExtractor,
    LocalSkillExtractorError,
    normalize_skill_key,
)


class LocalMatchingError(RuntimeError):
    """Raised when a local profile comparison cannot be completed."""


class LocalMatcher:
    """Deterministic matcher using canonical skills and local TF-IDF similarity.

    The matcher never calls an LLM and never infers skills that are not present in
    either profile. All scores are computed from named, inspectable components.
    """

    def __init__(
        self,
        config: MatchingConfig | None = None,
        *,
        extractor: LocalSkillExtractor | None = None,
        embedder: LocalTfidfEmbedder | None = None,
    ) -> None:
        self.config = config or MatchingConfig()
        self.extractor = extractor or LocalSkillExtractor()
        self.embedder = embedder or LocalTfidfEmbedder()

    @staticmethod
    def _skill_key(skill: str) -> str:
        return normalize_skill_key(skill)

    @staticmethod
    def _classify(score: float, thresholds: Any) -> MatchClassification:
        if score >= thresholds.strong:
            return "strong_match"
        if score >= thresholds.moderate:
            return "moderate_match"
        return "weak_match"

    @staticmethod
    def _importance_weight(
        importance: str, weights: RequirementImportanceWeights
    ) -> float:
        if importance == "required":
            return weights.required
        if importance == "preferred":
            return weights.preferred
        return weights.mentioned

    def _matched_skills(
        self,
        resume: ResumeProfile,
        job: JobProfile,
        resume_by_key: dict[str, Any],
    ) -> tuple[list[MatchedSkill], list[MissingSkill], list[PreferredSkill]]:
        matched: list[MatchedSkill] = []
        missing_required: list[MissingSkill] = []
        preferred_status: list[PreferredSkill] = []

        for requirement in job.requirements:
            key = self._skill_key(requirement.skill)
            candidate_skill = resume_by_key.get(key)
            if candidate_skill is not None:
                confidence = min(requirement.confidence, candidate_skill.confidence)
                quality = "strong" if confidence >= 0.7 else "partial"
                matched.append(
                    MatchedSkill(
                        skill=requirement.skill,
                        category=requirement.category,
                        importance=requirement.importance,
                        match_quality=quality,
                        resume_evidence=candidate_skill.evidence[:10],
                        job_evidence=requirement.evidence[:10],
                        confidence=confidence,
                    )
                )
                if requirement.importance == "preferred":
                    preferred_status.append(
                        PreferredSkill(
                            skill=requirement.skill,
                            category=requirement.category,
                            matched=True,
                            resume_evidence=candidate_skill.evidence[:10],
                            confidence=confidence,
                        )
                    )
            elif requirement.importance == "required":
                missing_required.append(
                    MissingSkill(
                        skill=requirement.skill,
                        category=requirement.category,
                        importance="required",
                        reason=(
                            f"'{requirement.skill}' is listed as a required skill in "
                            "the job profile but no equivalent canonical skill was "
                            "found in the résumé profile."
                        ),
                        confidence=requirement.confidence,
                    )
                )
            elif requirement.importance == "preferred":
                preferred_status.append(
                    PreferredSkill(
                        skill=requirement.skill,
                        category=requirement.category,
                        matched=False,
                        resume_evidence=[],
                        confidence=requirement.confidence,
                    )
                )

        return matched, missing_required, preferred_status

    def _experience_evidence(
        self,
        resume: ResumeProfile,
        job: JobProfile,
        resume_by_key: dict[str, Any],
    ) -> list[ExperienceEvidenceItem]:
        if not resume.experience_evidence or not job.semantic_text:
            return []

        items: list[ExperienceEvidenceItem] = []
        for description in resume.experience_evidence[:30]:
            try:
                similarity = self.embedder.similarity(description, job.semantic_text)
            except (LocalEmbeddingError, ValueError):
                similarity = 0.0
            matched_skills = [
                skill.skill
                for skill in self.extractor.extract_skills(description)
                if self._skill_key(skill.skill) in resume_by_key
            ]
            if (
                similarity < self.config.minimum_experience_similarity
                and not matched_skills
            ):
                continue
            items.append(
                ExperienceEvidenceItem(
                    requirement=job.title or "Relevant job requirements",
                    evidence=description,
                    similarity=similarity,
                    matched_skills=matched_skills[:50],
                    source="resume_experience",
                )
            )

        items.sort(
            key=lambda item: (
                item.similarity if item.similarity is not None else 0.0
            ),
            reverse=True,
        )
        return items[: self.config.max_experience_evidence]

    def _component(
        self,
        name: str,
        raw_value: float | None,
        weight: float,
        detail: str,
    ) -> ScoreComponent:
        if raw_value is None or weight <= 0:
            return ScoreComponent(
                name=name,
                available=False,
                raw_value=raw_value,
                weight=weight,
                normalized_weight=0.0,
                contribution=0.0,
                detail=detail,
            )
        return ScoreComponent(
            name=name,
            available=True,
            raw_value=max(0.0, min(1.0, raw_value)),
            weight=weight,
            normalized_weight=0.0,
            contribution=0.0,
            detail=detail,
        )

    def _recommendations(
        self,
        matched: Sequence[MatchedSkill],
        missing_required: Sequence[MissingSkill],
        preferred: Sequence[PreferredSkill],
        has_requirements: bool,
    ) -> list[str]:
        recommendations: list[str] = []
        if missing_required:
            names = ", ".join(item.skill for item in missing_required[:10])
            recommendations.append(
                "Provide verifiable evidence for required skills that were not "
                f"detected: {names}."
            )
        missing_preferred = [item.skill for item in preferred if not item.matched]
        if missing_preferred:
            recommendations.append(
                "If applicable, highlight preferred skills not currently evidenced: "
                + ", ".join(missing_preferred[:10])
                + "."
            )
        if matched and not missing_required:
            recommendations.append(
                "Emphasize the matched required skills and the specific projects where "
                "they were applied."
            )
        if not has_requirements:
            recommendations.append(
                "Add explicit skill and responsibility text to the job description so "
                "the deterministic matcher can evaluate it."
            )
        if not matched and has_requirements:
            recommendations.append(
                "Review the résumé for explicit skill terminology and relevant "
                "experience before treating this as a substantive gap."
            )
        return recommendations[:20]

    def _uncertainties(
        self,
        resume: ResumeProfile,
        job: JobProfile,
        semantic_available: bool,
        category_available: bool,
    ) -> list[Uncertainty]:
        uncertainties: list[Uncertainty] = []
        if not resume.skills:
            uncertainties.append(
                Uncertainty(
                    field="resume_skills",
                    reason="No canonical skills were extracted from the résumé.",
                    severity="high",
                )
            )
        if not job.requirements:
            uncertainties.append(
                Uncertainty(
                    field="job_requirements",
                    reason="No canonical requirements were extracted from the job.",
                    severity="high",
                )
            )
        if not semantic_available:
            uncertainties.append(
                Uncertainty(
                    field="semantic_similarity",
                    reason="Semantic similarity was unavailable because text was missing.",
                    severity="medium",
                )
            )
        if not category_available and self.config.category_mapping:
            uncertainties.append(
                Uncertainty(
                    field="category_compatibility",
                    reason="Category compatibility was unavailable for this pair.",
                    severity="low",
                )
            )
        if not self.config.category_mapping:
            uncertainties.append(
                Uncertainty(
                    field="category_compatibility",
                    reason=(
                        "Category compatibility is disabled because no validated "
                        "résumé-category to job-category mapping is configured."
                    ),
                    severity="low",
                )
            )
        return uncertainties

    def match(self, resume: ResumeProfile, job: JobProfile) -> HybridMatchResult:
        if not isinstance(resume, ResumeProfile):
            raise LocalMatchingError("resume must be a ResumeProfile instance.")
        if not isinstance(job, JobProfile):
            raise LocalMatchingError("job must be a JobProfile instance.")

        weights = self.config.weights.as_dict()
        resume_by_key = {self._skill_key(item.skill): item for item in resume.skills}
        job_by_key = {self._skill_key(item.skill): item for item in job.requirements}

        matched, missing_required, preferred = self._matched_skills(
            resume, job, resume_by_key
        )

        required_items = [
            item for item in job.requirements if item.importance == "required"
        ]
        preferred_items = [
            item for item in job.requirements if item.importance == "preferred"
        ]

        required_total = sum(
            self._importance_weight(item.importance, self.config.importance_weights)
            for item in required_items
        )
        required_hit = sum(
            self._importance_weight(item.importance, self.config.importance_weights)
            for item in required_items
            if self._skill_key(item.skill) in resume_by_key
        )
        required_value = (
            required_hit / required_total if required_total > 0 else None
        )

        preferred_total = sum(
            self._importance_weight(item.importance, self.config.importance_weights)
            for item in preferred_items
        )
        preferred_hit = sum(
            self._importance_weight(item.importance, self.config.importance_weights)
            for item in preferred_items
            if self._skill_key(item.skill) in resume_by_key
        )
        preferred_value = (
            preferred_hit / preferred_total if preferred_total > 0 else None
        )

        overlap_value = (
            len(set(job_by_key) & set(resume_by_key)) / len(job_by_key)
            if job_by_key
            else None
        )

        semantic_value: float | None = None
        if resume.semantic_text and job.semantic_text:
            try:
                semantic_value = self.embedder.similarity(
                    resume.semantic_text, job.semantic_text
                )
            except (LocalEmbeddingError, ValueError):
                semantic_value = None

        category_result = self.config.category_is_compatible(
            resume.category, job.category
        )
        category_value = (
            None if category_result is None else (1.0 if category_result else 0.0)
        )

        components = [
            self._component(
                "required_skill_coverage",
                required_value,
                weights["required_skill_coverage"],
                f"{len([item for item in required_items if self._skill_key(item.skill) in resume_by_key])}"
                f"/{len(required_items)} required skills matched.",
            ),
            self._component(
                "preferred_skill_coverage",
                preferred_value,
                weights["preferred_skill_coverage"],
                f"{len([item for item in preferred_items if self._skill_key(item.skill) in resume_by_key])}"
                f"/{len(preferred_items)} preferred skills matched.",
            ),
            self._component(
                "skill_overlap",
                overlap_value,
                weights["skill_overlap"],
                f"{len(set(job_by_key) & set(resume_by_key))}/{len(job_by_key)} "
                "job skills are present in the résumé profile.",
            ),
            self._component(
                "semantic_similarity",
                semantic_value,
                weights["semantic_similarity"],
                "Local TF-IDF cosine similarity over word and character n-grams.",
            ),
            self._component(
                "category_compatibility",
                category_value,
                weights["category_compatibility"],
                "Uses only an explicitly configured, validated category mapping.",
            ),
        ]

        available = [item for item in components if item.available]
        total_available_weight = sum(item.weight for item in available)
        fallback_equal = total_available_weight <= 0
        if fallback_equal:
            equal_weight = 1.0 / len(available) if available else 0.0
            total_available_weight = sum(equal_weight for _ in available)

        for component in components:
            if component.available and total_available_weight > 0:
                normalized = (
                    equal_weight
                    if fallback_equal
                    else component.weight / total_available_weight
                )
                component.normalized_weight = normalized
                component.contribution = (component.raw_value or 0.0) * normalized

        final_score = min(1.0, sum(item.contribution for item in components))
        classification = self._classify(final_score, self.config.thresholds)

        experience = self._experience_evidence(resume, job, resume_by_key)
        recommendations = self._recommendations(
            matched, missing_required, preferred, bool(job.requirements)
        )
        uncertainties = self._uncertainties(
            resume,
            job,
            semantic_available=semantic_value is not None,
            category_available=category_value is not None,
        )

        return HybridMatchResult(
            resume_id=resume.resume_id,
            job_id=job.job_id,
            overall_compatibility_score=round(final_score, 6),
            classification=classification,
            matched_skills=matched,
            missing_required_skills=missing_required,
            preferred_skills=preferred,
            relevant_experience_evidence=experience,
            semantic_similarity=(
                round(semantic_value, 6) if semantic_value is not None else None
            ),
            explanation=None,
            recommendations=recommendations,
            recommendation_source="local_deterministic",
            uncertainty_indicators=uncertainties,
            score_breakdown=ScoreBreakdown(
                components=components,
                final_score=round(final_score, 6),
                classification=classification,
                thresholds=self.config.thresholds.as_dict(),
                weights=weights,
            ),
            components=[
                ComponentRecord(
                    name="local_skill_matching",
                    status="success" if job.requirements else "skipped",
                    detail=(
                        f"{len(matched)} matched requirement(s), "
                        f"{len(missing_required)} missing required requirement(s)."
                    ),
                ),
                ComponentRecord(
                    name="local_semantic_similarity",
                    status="success" if semantic_value is not None else "skipped",
                    detail=(
                        f"Local TF-IDF cosine similarity: {semantic_value:.4f}."
                        if semantic_value is not None
                        else "Text was unavailable for local similarity."
                    ),
                ),
                ComponentRecord(
                    name="local_category_compatibility",
                    status="success" if category_value is not None else "skipped",
                    detail=(
                        "Category mapping matched."
                        if category_value == 1.0
                        else "Category mapping did not match."
                        if category_value == 0.0
                        else "No validated category mapping was applied."
                    ),
                ),
            ],
            llm_metadata=None,
            fairness_notice=self.config.fairness_notice,
            disclaimer=self.config.disclaimer,
        )

    def match_texts(
        self,
        resume_text: str,
        job_text: str,
        *,
        resume_id: str | None = None,
        job_id: str | None = None,
        resume_category: str | None = None,
        job_category: str | None = None,
        resume_experience: Sequence[str] | None = None,
        job_title: str | None = None,
    ) -> HybridMatchResult:
        from app.services.matching.profiles import build_job_profile, build_resume_profile

        try:
            resume = build_resume_profile(
                resume_text,
                extractor=self.extractor,
                resume_id=resume_id,
                category=resume_category,
                experience_evidence=resume_experience,
            )
            job = build_job_profile(
                job_text,
                extractor=self.extractor,
                job_id=job_id,
                title=job_title,
                category=job_category,
            )
        except LocalSkillExtractorError as exc:
            raise LocalMatchingError(str(exc)) from exc
        return self.match(resume, job)
