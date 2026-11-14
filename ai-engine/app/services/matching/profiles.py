from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.schemas.hybrid_match import (
    JobProfile,
    JobRequirement,
    ProfileSkill,
    ResumeProfile,
)
from app.schemas.job_analysis import JobAnalysis
from app.schemas.resume_analysis import ResumeAnalysis, SkillCategory
from app.services.nlp.skills import LocalSkillExtractor, normalize_text

SKILL_CATEGORIES = {
    "programming_language",
    "framework_library",
    "database",
    "tool_technology",
    "soft_skill",
    "domain_knowledge",
    "other",
}
IMPORTANCE_RANK = {"required": 0, "preferred": 1, "mentioned": 2}
MAX_SEMANTIC_TEXT = 200_000


def _safe_category(value: Any) -> SkillCategory:
    if isinstance(value, str) and value in SKILL_CATEGORIES:
        return value  # type: ignore[return-value]
    return "other"


def _clean_strings(values: Iterable[Any], *, limit: int = 100) -> list[str]:
    cleaned = [normalize_text(value) for value in values if isinstance(value, str)]
    return [value for value in dict.fromkeys(cleaned) if value][:limit]


def _semantic_text(parts: Sequence[str | None]) -> str | None:
    text = normalize_text(" ".join(part for part in parts if isinstance(part, str) and part))
    if not text:
        return None
    return text[:MAX_SEMANTIC_TEXT]


def _merge_profile_skills(
    skills: Iterable[ProfileSkill], extractor: LocalSkillExtractor
) -> list[ProfileSkill]:
    merged: dict[str, ProfileSkill] = {}
    for skill in skills:
        canonical = extractor.canonicalize_skill(skill.skill)
        key = canonical.casefold()
        evidence = [item for item in skill.evidence if item]
        existing = merged.get(key)
        if existing is None:
            merged[key] = skill.model_copy(
                update={"skill": canonical, "evidence": evidence[:5]}
            )
            continue
        combined_evidence = _clean_strings([*existing.evidence, *evidence], limit=5)
        merged[key] = existing.model_copy(
            update={
                "category": existing.category
                if existing.category != "other"
                else skill.category,
                "evidence": combined_evidence,
                "confidence": max(existing.confidence, skill.confidence),
            }
        )
    return sorted(merged.values(), key=lambda item: item.skill.casefold())


def _merge_requirements(
    requirements: Iterable[JobRequirement], extractor: LocalSkillExtractor
) -> list[JobRequirement]:
    merged: dict[str, JobRequirement] = {}
    for requirement in requirements:
        canonical = extractor.canonicalize_skill(requirement.skill)
        key = canonical.casefold()
        existing = merged.get(key)
        if existing is None:
            merged[key] = requirement.model_copy(
                update={"skill": canonical, "evidence": requirement.evidence[:5]}
            )
            continue
        importance = min(
            (existing.importance, requirement.importance),
            key=lambda value: IMPORTANCE_RANK[value],
        )
        merged[key] = existing.model_copy(
            update={
                "category": existing.category
                if existing.category != "other"
                else requirement.category,
                "importance": importance,
                "evidence": _clean_strings(
                    [*existing.evidence, *requirement.evidence], limit=5
                ),
                "confidence": max(existing.confidence, requirement.confidence),
            }
        )
    return sorted(
        merged.values(),
        key=lambda item: (IMPORTANCE_RANK[item.importance], item.skill.casefold()),
    )


def build_resume_profile(
    text: str,
    *,
    extractor: LocalSkillExtractor | None = None,
    resume_id: str | None = None,
    category: str | None = None,
    semantic_text: str | None = None,
    experience_evidence: Sequence[str] | None = None,
    education_evidence: Sequence[str] | None = None,
) -> ResumeProfile:
    skill_extractor = extractor or LocalSkillExtractor()
    cleaned_text = normalize_text(text)
    return ResumeProfile(
        resume_id=resume_id,
        category=category,
        semantic_text=_semantic_text([semantic_text or cleaned_text]) or cleaned_text,
        skills=skill_extractor.extract_skills(cleaned_text),
        experience_evidence=_clean_strings(experience_evidence or [], limit=100),
        education_evidence=_clean_strings(education_evidence or [], limit=50),
    )


def build_job_profile(
    text: str,
    *,
    extractor: LocalSkillExtractor | None = None,
    job_id: str | None = None,
    title: str | None = None,
    category: str | None = None,
    semantic_text: str | None = None,
    responsibilities: Sequence[str] | None = None,
    qualifications: Sequence[str] | None = None,
) -> JobProfile:
    skill_extractor = extractor or LocalSkillExtractor()
    cleaned_text = normalize_text(text)
    return JobProfile(
        job_id=job_id,
        title=title,
        category=category,
        semantic_text=_semantic_text([semantic_text or cleaned_text]) or cleaned_text,
        requirements=skill_extractor.extract_requirements(cleaned_text),
        responsibilities=_clean_strings(responsibilities or [], limit=100),
        qualifications=_clean_strings(qualifications or [], limit=100),
    )


def build_resume_profile_from_analysis(
    analysis: ResumeAnalysis,
    *,
    raw_text: str | None = None,
    extractor: LocalSkillExtractor | None = None,
    resume_id: str | None = None,
    category: str | None = None,
) -> ResumeProfile:
    skill_extractor = extractor or LocalSkillExtractor()
    skills: list[ProfileSkill] = [
        ProfileSkill(
            skill=item.skill,
            category=item.category,
            evidence=item.evidence,
            confidence=item.confidence,
        )
        for item in analysis.skills
    ]
    skills.extend(
        ProfileSkill(
            skill=skill,
            category="programming_language",
            evidence=["Listed in résumé programming languages."],
            confidence=analysis.confidence,
        )
        for skill in analysis.programming_languages
    )
    skills.extend(
        ProfileSkill(
            skill=skill,
            category="framework_library",
            evidence=["Listed in résumé frameworks."],
            confidence=analysis.confidence,
        )
        for skill in analysis.frameworks
    )
    skills.extend(
        ProfileSkill(
            skill=skill,
            category="other",
            evidence=["Listed in résumé technical skills."],
            confidence=analysis.confidence,
        )
        for skill in analysis.technical_skills
    )

    experience_evidence: list[str] = []
    for entry in analysis.experience:
        if entry.description:
            experience_evidence.append(entry.description)
        elif any([entry.title, entry.organization]):
            experience_evidence.append(
                " - ".join(
                    value for value in [entry.title, entry.organization] if value
                )
            )
        experience_evidence.extend(entry.evidence)

    education_evidence: list[str] = []
    for entry in analysis.education:
        label = ", ".join(
            value
            for value in [entry.degree, entry.field_of_study, entry.institution]
            if value
        )
        if label:
            education_evidence.append(label)
        education_evidence.extend(entry.evidence)

    semantic_parts: list[str | None] = [
        raw_text,
        analysis.summary.overview,
        *analysis.summary.key_points,
        *[item.skill for item in analysis.skills],
        *analysis.programming_languages,
        *analysis.frameworks,
        *analysis.technical_skills,
        *experience_evidence,
    ]
    return ResumeProfile(
        resume_id=resume_id,
        category=category,
        semantic_text=_semantic_text(semantic_parts),
        skills=_merge_profile_skills(skills, skill_extractor),
        experience_evidence=_clean_strings(experience_evidence, limit=100),
        education_evidence=_clean_strings(education_evidence, limit=50),
    )


def build_job_profile_from_analysis(
    analysis: JobAnalysis,
    *,
    raw_text: str | None = None,
    extractor: LocalSkillExtractor | None = None,
    job_id: str | None = None,
    category: str | None = None,
) -> JobProfile:
    skill_extractor = extractor or LocalSkillExtractor()
    requirements: list[JobRequirement] = [
        JobRequirement(
            skill=item.skill,
            category=item.category,
            importance=item.importance,
            evidence=item.evidence,
            confidence=item.confidence,
        )
        for item in analysis.skills
    ]
    requirements.extend(
        JobRequirement(
            skill=skill,
            category="other",
            importance="required",
            evidence=["Listed as a mandatory requirement."],
            confidence=analysis.confidence,
        )
        for skill in analysis.mandatory_requirements
    )
    requirements.extend(
        JobRequirement(
            skill=skill,
            category="other",
            importance="preferred",
            evidence=["Listed as a preferred requirement."],
            confidence=analysis.confidence,
        )
        for skill in analysis.preferred_requirements
    )
    requirements.extend(
        JobRequirement(
            skill=skill,
            category="other",
            importance="mentioned",
            evidence=["Listed in the job's technical skills."],
            confidence=analysis.confidence,
        )
        for skill in analysis.technical_skills
    )

    responsibilities = [item.description for item in analysis.responsibilities]
    semantic_parts: list[str | None] = [
        raw_text,
        analysis.job_title,
        analysis.summary.overview,
        *analysis.summary.key_points,
        *[item.skill for item in analysis.skills],
        *analysis.mandatory_requirements,
        *analysis.preferred_requirements,
        *analysis.technical_skills,
        *responsibilities,
        *analysis.qualifications,
        *analysis.keywords,
    ]
    return JobProfile(
        job_id=job_id,
        title=analysis.job_title,
        category=category,
        semantic_text=_semantic_text(semantic_parts),
        requirements=_merge_requirements(requirements, skill_extractor),
        responsibilities=_clean_strings(responsibilities, limit=100),
        qualifications=_clean_strings(analysis.qualifications, limit=100),
    )
