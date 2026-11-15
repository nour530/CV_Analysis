"""Career paths: occupations that fit a CV and the skills missing for them."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from app.services.career.esco import (
    EscoCatalog,
    EscoOccupation,
    SkillMatcher,
    get_esco_catalog,
    normalize_phrase,
)


MIN_SKILLS = 8


@dataclass(slots=True)
class OccupationFit:
    occupation: EscoOccupation
    similarity: float
    coverage: float
    score: float
    have: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


class CareerAdvisor:
    def __init__(self, catalog: EscoCatalog) -> None:
        self.catalog = catalog
        self.matcher = SkillMatcher(catalog)
        documents = [
            catalog.occupation_document(occupation, "en") + " . " + catalog.occupation_document(occupation, "fr")
            for occupation in catalog.occupations
        ]
        self.vectorizer = TfidfVectorizer(
            preprocessor=normalize_phrase, ngram_range=(1, 2), sublinear_tf=True, min_df=2, max_features=150_000
        )
        self.matrix = self.vectorizer.fit_transform(documents) if documents else None

    def find_occupation(self, title: str) -> EscoOccupation | None:
        matches = self.catalog.search_occupations(title, limit=1)
        return matches[0] if matches else None

    def skill_gap(self, occupation: EscoOccupation, cv_text: str, *, language: str = "en", limit: int = 12) -> OccupationFit:
        present = set(self.matcher.present(cv_text, occupation.essential))
        essential = [uri for uri in occupation.essential if uri in self.catalog.skills]
        # Count against at least MIN_SKILLS so occupations that list only one
        # or two essential skills cannot reach full coverage too easily.
        coverage = len(present) / max(len(essential), MIN_SKILLS) if essential else 0.0

        def priority(uri: str) -> tuple[int, int]:
            skill = self.catalog.skills[uri]
            # Specific skills first, transversal (soft) ones last; knowledge
            # items before broad activity phrases.
            return (1 if skill.transversal else 0, 0 if skill.skill_type == "knowledge" else 1)

        have = [self.catalog.skills[uri].label(language) for uri in essential if uri in present]
        missing = [
            self.catalog.skills[uri].label(language)
            for uri in sorted((u for u in essential if u not in present), key=priority)
        ]
        return OccupationFit(occupation, 0.0, round(coverage, 3), round(coverage, 3), have[:limit], missing[:limit])

    def suggest(
        self,
        cv_text: str,
        *,
        domain_id: str | None = None,
        language: str = "en",
        limit: int = 5,
    ) -> list[OccupationFit]:
        if self.matrix is None or not cv_text.strip():
            return []
        similarities = linear_kernel(self.vectorizer.transform([cv_text]), self.matrix)[0]
        order = np.argsort(-similarities)
        candidates: list[OccupationFit] = []
        for index in order[:200]:
            occupation = self.catalog.occupations[int(index)]
            if domain_id and occupation.domain_id != domain_id:
                continue
            fit = self.skill_gap(occupation, cv_text, language=language)
            fit.similarity = round(float(similarities[index]), 4)
            candidates.append(fit)
            if len(candidates) >= 30:
                break
        top_similarity = max((c.similarity for c in candidates), default=0.0) or 1.0
        for fit in candidates:
            fit.score = round(0.55 * (fit.similarity / top_similarity) + 0.45 * fit.coverage, 4)
        candidates.sort(key=lambda fit: -fit.score)
        return candidates[:limit]


_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _advisor() -> CareerAdvisor | None:
    catalog = get_esco_catalog()
    return CareerAdvisor(catalog) if catalog.available else None


def get_career_advisor() -> CareerAdvisor | None:
    with _LOCK:
        return _advisor()
