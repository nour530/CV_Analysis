from __future__ import annotations

import pytest

from app.schemas.hybrid_match import (
    HybridMatchResult,
    JobProfile,
    JobRequirement,
    ProfileSkill,
    ResumeProfile,
    ScoreBreakdown,
)
from app.services.llm.exceptions import LLMProviderError
from app.services.llm.mock_provider import MockLLMProvider
from app.services.matching.config import (
    MatchingConfig,
    MatchingWeights,
    MatchThresholds,
)
from app.services.matching.evaluation import evaluate_cases, load_reviewed_cases
from app.services.matching.hybrid_service import HybridMatchingService
from app.services.matching.local_matcher import LocalMatcher, LocalMatchingError
from app.services.job_catalog import CatalogJob, JobCatalog
from app.services.nlp.embeddings import LocalEmbeddingError, LocalTfidfEmbedder
from app.services.nlp.skills import LocalSkillExtractor


@pytest.fixture(scope="module")
def extractor() -> LocalSkillExtractor:
    return LocalSkillExtractor()


def test_skill_extractor_handles_canonical_aliases(extractor: LocalSkillExtractor) -> None:
    text = (
        "We require Python, Node.js, and Docker. Kubernetes is preferred. "
        "Experience with C++ and C# is a plus."
    )
    skills = {item.skill for item in extractor.extract_skills(text)}
    assert {"Python", "Node.js", "Docker", "Kubernetes", "C++", "C#"}.issubset(skills)

    requirements = {item.skill: item.importance for item in extractor.extract_requirements(text)}
    assert requirements["Python"] == "required"
    assert requirements["Node.js"] == "required"
    assert requirements["Docker"] == "required"
    assert requirements["Kubernetes"] == "preferred"


def test_short_aliases_require_programming_context(extractor: LocalSkillExtractor) -> None:
    assert exact_skills(extractor, "C level executives met the client.") == set()
    assert "C" in exact_skills(extractor, "Programming languages: C, Python, and Go.")


def exact_skills(extractor: LocalSkillExtractor, text: str) -> set[str]:
    return {item.skill for item in extractor.extract_skills(text)}


def test_local_similarity_is_deterministic_and_bounded() -> None:
    embedder = LocalTfidfEmbedder()
    first = embedder.similarity(
        "Python developer building Docker services",
        "Python engineer deploying containers with Docker",
    )
    second = embedder.similarity(
        "Python developer building Docker services",
        "Python engineer deploying containers with Docker",
    )
    assert first == second
    assert 0.0 <= first <= 1.0
    assert embedder.similarity("Python", "Python") == 1.0
    assert embedder.similarity("", "Python") == 0.0


def test_fitted_embedder_downweights_corpus_boilerplate() -> None:
    boilerplate = "strong communication skills and teamwork in a fast paced environment"
    corpus = [
        f"{boilerplate} building {topic} systems"
        for topic in ("payroll", "billing", "logistics", "inventory", "search", "ads")
    ] + ["kubernetes cluster operations", "terraform infrastructure automation"]
    # Distinct topics drawn from the corpus vocabulary, so only the shared
    # boilerplate makes these two texts look alike.
    shared_boilerplate = (
        f"{boilerplate} building payroll systems",
        f"{boilerplate} building billing systems",
    )
    shared_rare_skill = (
        "kubernetes cluster upgrades",
        "operating kubernetes cluster nodes",
    )

    unfitted = LocalTfidfEmbedder()
    fitted = LocalTfidfEmbedder().fit(corpus)

    assert fitted.is_fitted and not unfitted.is_fitted
    assert fitted.similarity(*shared_boilerplate) < unfitted.similarity(
        *shared_boilerplate
    )
    assert fitted.similarity(*shared_rare_skill) > fitted.similarity(
        *shared_boilerplate
    )
    assert fitted.similarity(*shared_rare_skill) == fitted.similarity(
        *shared_rare_skill
    )


def test_fit_rejects_empty_corpus() -> None:
    with pytest.raises(LocalEmbeddingError):
        LocalTfidfEmbedder().fit(["", "   "])


def test_catalog_reference_corpus_is_category_balanced_and_deterministic() -> None:
    def job(job_id: str, category: str) -> CatalogJob:
        return CatalogJob(
            job_id=job_id,
            title=None,
            category=category,
            description=f"{category} role {job_id}",
            required_skills=(),
            preferred_skills=(),
            other_skills=(),
        )

    jobs = [job(f"d{i}", "Dominant") for i in range(10)] + [
        job("m1", "Minor"),
        job("m2", "Minor"),
    ]
    catalog = JobCatalog(jobs)

    corpus = catalog.reference_corpus(limit=4)

    assert corpus == catalog.reference_corpus(limit=4)
    assert sum("Minor" in text for text in corpus) == 2
    assert len(corpus) == 4
    assert catalog.embedder is catalog.embedder
    assert catalog.embedder.is_fitted


def test_local_matcher_produces_transparent_strong_match() -> None:
    resume = ResumeProfile(
        resume_id="r1",
        semantic_text="Python JavaScript React PostgreSQL Docker Git projects.",
        skills=[
            ProfileSkill(skill=skill, category="other", evidence=[f"Used {skill}."], confidence=0.9)
            for skill in ["Python", "JavaScript", "React", "PostgreSQL", "Docker", "Git"]
        ],
        experience_evidence=["Built Python React services with PostgreSQL and Docker."],
    )
    job = JobProfile(
        job_id="j1",
        title="Software Engineer",
        semantic_text="Build Python JavaScript React services with PostgreSQL and Docker.",
        requirements=[
            JobRequirement(skill=skill, importance="required", evidence=[f"{skill} required."], confidence=0.9)
            for skill in ["Python", "JavaScript", "React"]
        ],
    )
    result = LocalMatcher().match(resume, job)

    assert isinstance(result, HybridMatchResult)
    assert result.classification == "strong_match"
    assert result.overall_compatibility_score > 0.7
    assert not result.missing_required_skills
    assert result.explanation is None
    assert result.recommendation_source == "local_deterministic"
    assert result.components[0].status == "success"
    assert result.score_breakdown.final_score == result.overall_compatibility_score
    available = [item for item in result.score_breakdown.components if item.available]
    assert any(item.name == "required_skill_coverage" for item in available)
    assert pytest.approx(sum(item.normalized_weight for item in available), abs=1e-9) == 1.0
    assert pytest.approx(
        sum(item.contribution for item in result.score_breakdown.components),
        abs=1e-6,
    ) == result.overall_compatibility_score
    assert "hiring" in result.fairness_notice.casefold()
    assert result.disclaimer


def test_local_matcher_distinguishes_missing_required_and_preferred() -> None:
    matcher = LocalMatcher()
    result = matcher.match_texts(
        "Data analyst with Python, pandas, and Microsoft Excel experience.",
        (
            "Required skills are Python, pandas, Microsoft Excel, Tableau, and "
            "Microsoft SQL Server. Docker is preferred."
        ),
        resume_id="r2",
        job_id="j2",
    )
    missing = {item.skill for item in result.missing_required_skills}
    preferred = {item.skill: item.matched for item in result.preferred_skills}
    assert {"Tableau", "Microsoft SQL Server"} == missing
    assert preferred["Docker"] is False
    assert result.classification in {"moderate_match", "strong_match"}


def test_category_component_is_disabled_without_validated_mapping() -> None:
    matcher = LocalMatcher()
    result = matcher.match_texts(
        "Python developer with Docker experience.",
        "Requires Python and Docker.",
        resume_category="Software Development",
        job_category="Software Development",
    )
    category = next(
        item for item in result.score_breakdown.components if item.name == "category_compatibility"
    )
    assert category.available is False
    assert category.normalized_weight == 0.0


def test_category_component_uses_explicit_mapping_only() -> None:
    config = MatchingConfig(
        category_mapping={"software development": ["software development"]},
        weights=MatchingWeights(
            required_skill_coverage=0.4,
            preferred_skill_coverage=0.1,
            skill_overlap=0.1,
            semantic_similarity=0.2,
            category_compatibility=0.2,
        ),
    )
    result = LocalMatcher(config).match_texts(
        "Python developer with Docker experience.",
        "Requires Python and Docker.",
        resume_category="Software Development",
        job_category="Software Development",
    )
    category = next(
        item for item in result.score_breakdown.components if item.name == "category_compatibility"
    )
    assert category.available is True
    assert category.raw_value == 1.0


def test_hybrid_service_uses_mock_explanation_without_overriding_score() -> None:
    service = HybridMatchingService()
    local = service.match_local(
        resume_text="Python developer with Docker and PostgreSQL experience.",
        job_text="Requires Python, Docker, and PostgreSQL.",
    )
    result = service.match_hybrid(
        resume_text="Python developer with Docker and PostgreSQL experience.",
        job_text="Requires Python, Docker, and PostgreSQL.",
        provider=MockLLMProvider(),
        allow_remote_analysis=True,
    )

    assert result.overall_compatibility_score == local.overall_compatibility_score
    assert result.classification == local.classification
    assert result.explanation is not None
    assert result.components[-1].name == "openai_explanation"
    assert result.components[-1].status == "success"
    assert result.llm_metadata is not None


def test_hybrid_service_skips_llm_without_remote_authorization() -> None:
    result = HybridMatchingService().match_hybrid(
        resume_text="Python developer.",
        job_text="Requires Python.",
        provider=MockLLMProvider(),
        allow_remote_analysis=False,
    )
    assert result.explanation is None
    assert result.components[-1].status == "skipped"
    assert "not authorized" in result.components[-1].detail.casefold()


def test_hybrid_service_degrades_to_local_when_llm_fails() -> None:
    class FailingProvider(MockLLMProvider):
        def explain_match(self, *args, **kwargs):
            raise LLMProviderError("synthetic provider failure")

    service = HybridMatchingService()
    result = service.match_hybrid(
        resume_text="Python developer with Docker experience.",
        job_text="Requires Python and Docker.",
        provider=FailingProvider(),
        allow_remote_analysis=True,
    )
    assert result.explanation is None
    assert result.recommendation_source == "local_deterministic"
    assert result.components[-1].status == "failed"
    assert any(
        item.field == "openai_explanation" for item in result.uncertainty_indicators
    )
    assert result.overall_compatibility_score > 0


def test_hybrid_service_validates_required_profile_inputs() -> None:
    with pytest.raises(Exception):
        HybridMatchingService().match_local()


def test_reviewed_evaluation_set_runs_and_exposes_metrics() -> None:
    cases = load_reviewed_cases()
    assert len(cases) >= 20
    metrics = evaluate_cases(cases)
    assert metrics["sample_count"] == len(cases)
    assert 0.0 <= metrics["accuracy"] <= 1.0
    assert 0.0 <= metrics["macro_f1"] <= 1.0
    assert metrics["per_class"]
    assert metrics["confusion_matrix"]["labels"]
    assert metrics["predictions"]


def test_score_breakdown_rejects_unknown_fields() -> None:
    with pytest.raises(Exception):
        ScoreBreakdown(
            components=[],
            final_score=0.5,
            classification="moderate_match",
            thresholds={"strong": 0.75, "moderate": 0.4},
            weights={},
            unexpected="value",
        )
