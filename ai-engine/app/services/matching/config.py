from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.schemas.common import StrictSchema

ComponentName = Literal[
    "required_skill_coverage",
    "preferred_skill_coverage",
    "skill_overlap",
    "semantic_similarity",
    "category_compatibility",
]

DEFAULT_FAIRNESS_NOTICE = (
    "This matching output is an informational decision-support aid. It must not be "
    "used as the sole basis for hiring, rejection, ranking, or any other employment "
    "decision. It does not use or infer protected characteristics such as age, gender, "
    "race, ethnicity, religion, disability, or nationality. A human reviewer must "
    "verify all results."
)

DEFAULT_DISCLAIMER = (
    "Local scores are lexical and dictionary based, not a validated prediction of job "
    "performance. Missing skills may reflect extraction limitations rather than a "
    "candidate gap. Review the supporting evidence before drawing conclusions."
)


class MatchingWeights(StrictSchema):
    """Weights for the deterministic local matcher.

    Defaults are provisional starting points and must be tuned against a reviewed
    evaluation set. Weights are renormalised over available components for each
    comparison, so disabling a component (for example semantic similarity when text
    is missing) does not penalise the result.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    required_skill_coverage: float = Field(default=0.45, ge=0.0, le=1.0)
    preferred_skill_coverage: float = Field(default=0.15, ge=0.0, le=1.0)
    skill_overlap: float = Field(default=0.15, ge=0.0, le=1.0)
    semantic_similarity: float = Field(default=0.25, ge=0.0, le=1.0)
    category_compatibility: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_total_weight(self) -> MatchingWeights:
        if sum(self.as_dict().values()) <= 0:
            raise ValueError("At least one matching weight must be greater than zero.")
        return self

    def as_dict(self) -> dict[str, float]:
        return {
            "required_skill_coverage": self.required_skill_coverage,
            "preferred_skill_coverage": self.preferred_skill_coverage,
            "skill_overlap": self.skill_overlap,
            "semantic_similarity": self.semantic_similarity,
            "category_compatibility": self.category_compatibility,
        }


class RequirementImportanceWeights(StrictSchema):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    required: float = Field(default=1.0, ge=0.0, le=1.0)
    preferred: float = Field(default=0.6, ge=0.0, le=1.0)
    mentioned: float = Field(default=0.3, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_values(self) -> RequirementImportanceWeights:
        if self.required <= 0:
            raise ValueError("The required importance weight must be greater than zero.")
        if self.required < self.preferred:
            raise ValueError("Required weight cannot be lower than preferred weight.")
        if self.preferred < self.mentioned:
            raise ValueError("Preferred weight cannot be lower than mentioned weight.")
        return self

    def as_dict(self) -> dict[str, float]:
        return {
            "required": self.required,
            "preferred": self.preferred,
            "mentioned": self.mentioned,
        }


class MatchThresholds(StrictSchema):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    strong: float = Field(default=0.75, ge=0.0, le=1.0)
    moderate: float = Field(default=0.40, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> MatchThresholds:
        if self.strong <= self.moderate:
            raise ValueError("Strong threshold must be greater than moderate threshold.")
        return self

    def as_dict(self) -> dict[str, float]:
        return {"strong": self.strong, "moderate": self.moderate}


class MatchingConfig(StrictSchema):
    """Full configuration for local and hybrid matching.

    `category_mapping` must contain an explicitly validated resume-category to
    job-category mapping. When it is empty, category compatibility is unavailable and
    receives zero effective weight. No category relationship is inferred.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    weights: MatchingWeights = Field(default_factory=MatchingWeights)
    importance_weights: RequirementImportanceWeights = Field(
        default_factory=RequirementImportanceWeights
    )
    thresholds: MatchThresholds = Field(default_factory=MatchThresholds)
    category_mapping: dict[str, list[str]] = Field(default_factory=dict)
    # Generic skills appear in most CVs and job ads, so they carry less weight
    # than technical or domain skills. Spoken languages are listed by skill key
    # because their category ("other") is shared with unclassified skills.
    skill_category_weights: dict[str, float] = Field(
        default_factory=lambda: {"soft_skill": 0.25}
    )
    generic_skill_weights: dict[str, float] = Field(
        default_factory=lambda: {
            key: 0.5
            for key in ("english", "french", "arabic", "german", "italian", "spanish")
        }
    )
    minimum_experience_similarity: float = Field(default=0.15, ge=0.0, le=1.0)
    max_experience_evidence: int = Field(default=3, ge=0, le=20)
    fairness_notice: str = Field(default=DEFAULT_FAIRNESS_NOTICE, min_length=1, max_length=2000)
    disclaimer: str = Field(default=DEFAULT_DISCLAIMER, min_length=1, max_length=2000)

    def skill_weight(self, category: str | None, skill: str | None = None) -> float:
        if skill:
            key = "".join(ch for ch in skill.casefold() if ch.isalnum())
            if key in self.generic_skill_weights:
                return min(1.0, max(0.0, float(self.generic_skill_weights[key])))
        value = self.skill_category_weights.get(category or "", 1.0)
        return min(1.0, max(0.0, float(value)))

    def category_is_compatible(
        self, resume_category: str | None, job_category: str | None
    ) -> bool | None:
        if not resume_category or not job_category or not self.category_mapping:
            return None
        key = resume_category.strip().casefold()
        allowed = {
            value.strip().casefold()
            for source, targets in self.category_mapping.items()
            if source.strip().casefold() == key
            for value in targets
        }
        if not allowed:
            return None
        return job_category.strip().casefold() in allowed
