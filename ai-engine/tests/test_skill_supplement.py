from __future__ import annotations

import pytest

from app.schemas.hybrid_match import JobProfile, JobRequirement, ProfileSkill, ResumeProfile
from app.services.matching.local_matcher import LocalMatcher
from app.services.nlp.skills import LocalSkillExtractor, normalize_skill_key


@pytest.fixture(scope="module")
def extractor() -> LocalSkillExtractor:
    return LocalSkillExtractor()


def skills(extractor: LocalSkillExtractor, text: str) -> set[str]:
    return {item.skill for item in extractor.extract_skills(text)}


def test_supplement_adds_common_skills_missing_from_onet(extractor) -> None:
    found = skills(extractor, "Skills: Java, AWS, Agile, Scrum, SAP FICO, AutoCAD.")
    assert {"Java", "Amazon Web Services", "Agile", "Scrum", "SAP FICO", "AutoCAD"} <= found


def test_french_aliases_with_and_without_accents(extractor) -> None:
    accented = skills(extractor, "Profil : maîtrise de la comptabilité générale et contrôle de gestion.")
    plain = skills(extractor, "Profil : maitrise de la comptabilite generale et controle de gestion.")
    assert {"Accounting", "Management control"} <= accented
    assert {"Accounting", "Management control"} <= plain
    assert normalize_skill_key("Comptabilité") == normalize_skill_key("comptabilite")


def test_quality_and_language_skills(extractor) -> None:
    found = skills(extractor, "Expérience en AMDEC, 8D et SPC. Anglais courant, français.")
    assert {"FMEA", "8D problem solving", "Statistical process control", "English", "French"} <= found


def test_everyday_words_do_not_become_tools(extractor) -> None:
    found = skills(extractor, "Le chef de projet R&D est rapide, en route vers le succès.")
    assert not {"Chef", "R", "Rapide", "Route"} & found
    assert "Chef" in skills(extractor, "We manage servers with Chef and Puppet for configuration management.")


def test_languages_need_skill_context(extractor) -> None:
    assert "French" not in skills(extractor, "We cooked French toast.")
    assert {"English", "French"} <= skills(extractor, "I speak French and English fluently.")


def _requirement(skill: str, category: str) -> JobRequirement:
    return JobRequirement(
        skill=skill, category=category, importance="required", evidence=[skill], confidence=0.8
    )


def test_generic_only_jobs_cannot_reach_full_skill_coverage() -> None:
    resume = ResumeProfile(
        resume_id="r",
        semantic_text="Python developer who speaks English and French.",
        skills=[
            ProfileSkill(skill=name, category=cat, evidence=[name], confidence=0.9)
            for name, cat in (("Python", "programming_language"), ("English", "other"), ("French", "other"))
        ],
    )
    generic_job = JobProfile(
        job_id="g",
        semantic_text="Bilingual service desk analyst, English and French.",
        requirements=[_requirement("English", "other"), _requirement("French", "other")],
    )
    technical_job = JobProfile(
        job_id="t",
        semantic_text="Python developer, English required.",
        requirements=[_requirement("Python", "programming_language"), _requirement("English", "other")],
    )
    matcher = LocalMatcher()
    generic = matcher.match(resume, generic_job)
    technical = matcher.match(resume, technical_job)
    required = {
        item.name: item.raw_value for item in generic.score_breakdown.components
    }["required_skill_coverage"]
    assert required == pytest.approx(0.5)
    assert technical.overall_compatibility_score > generic.overall_compatibility_score
