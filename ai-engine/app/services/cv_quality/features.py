"""Measurable signals of CV quality, used as features by our CV scorer.

Each CV is parsed into sections (app/services/cv_builder/parser.py) and turned
into about 25 numbers: how many bullets show a figure, start with an action
verb, use the first person or a vague opening, how complete the roles are,
and so on. The scorer combines these with the CV's wording (TF-IDF).
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin

from app.schemas.cv_document import CvDocument
from app.services.ats.checker import FIRST_PERSON_PATTERN, NUMBER_PATTERN, WORD_PATTERN
from app.services.cv_builder.parser import parse_cv_text
from app.services.cv_builder.suggestions import BUZZWORDS, FIRST_PERSON_START, WEAK_OPENING, _starts_with_verb
from app.services.nlp.skills import strip_accents

FEATURE_NAMES = (
    "log_words",
    "has_summary",
    "summary_words",
    "roles",
    "bullets",
    "bullets_per_role",
    "bullet_words_avg",
    "long_bullet_share",
    "number_bullet_share",
    "verb_bullet_share",
    "first_person_bullet_share",
    "weak_opening_share",
    "repeated_verb_share",
    "buzzwords_per_100_words",
    "first_person_per_100_words",
    "numbers_per_100_words",
    "role_title_share",
    "role_company_share",
    "role_dates_share",
    "education_entries",
    "education_dates_share",
    "skills",
    "sections",
    "languages",
    "certifications",
)


def _share(count: int, total: int) -> float:
    return count / total if total else 0.0


def features_from_document(doc: CvDocument, text: str) -> list[float]:
    words = WORD_PATTERN.findall(text)
    n_words = max(len(words), 1)
    bullets = [b for e in doc.experience for b in e.bullets] + [b for p in doc.projects for b in p.bullets]
    openings: list[str] = []
    repeated = 0
    for entry in doc.experience:
        seen: set[str] = set()
        for bullet in entry.bullets:
            first = WORD_PATTERN.findall(bullet)[:1]
            verb = strip_accents(first[0]).casefold() if first else ""
            if verb and verb in seen:
                repeated += 1
            seen.add(verb)
            openings.append(verb)
    sections = sum(
        bool(part)
        for part in (doc.summary, doc.experience, doc.education, doc.skills, doc.languages, doc.certifications, doc.projects)
    )
    roles = doc.experience
    values = {
        "log_words": math.log1p(len(words)),
        "has_summary": float(bool(doc.summary)),
        "summary_words": float(min(len((doc.summary or "").split()), 150)),
        "roles": float(min(len(roles), 20)),
        "bullets": float(min(len(bullets), 80)),
        "bullets_per_role": _share(len([b for e in roles for b in e.bullets]), len(roles)),
        "bullet_words_avg": _share(sum(len(b.split()) for b in bullets), len(bullets)),
        "long_bullet_share": _share(sum(len(b.split()) > 35 for b in bullets), len(bullets)),
        "number_bullet_share": _share(sum(bool(NUMBER_PATTERN.search(b)) for b in bullets), len(bullets)),
        "verb_bullet_share": _share(sum(_starts_with_verb(b) for b in bullets), len(bullets)),
        "first_person_bullet_share": _share(sum(bool(FIRST_PERSON_START.match(b)) for b in bullets), len(bullets)),
        "weak_opening_share": _share(sum(bool(WEAK_OPENING.match(b.strip())) for b in bullets), len(bullets)),
        "repeated_verb_share": _share(repeated, len(openings)),
        "buzzwords_per_100_words": 100 * len(BUZZWORDS.findall(text)) / n_words,
        "first_person_per_100_words": 100 * len(FIRST_PERSON_PATTERN.findall(text)) / n_words,
        "numbers_per_100_words": 100 * len(NUMBER_PATTERN.findall(text)) / n_words,
        "role_title_share": _share(sum(bool(e.title) for e in roles), len(roles)),
        "role_company_share": _share(sum(bool(e.company) for e in roles), len(roles)),
        "role_dates_share": _share(sum(bool(e.start) for e in roles), len(roles)),
        "education_entries": float(min(len(doc.education), 10)),
        "education_dates_share": _share(sum(bool(e.start or e.end) for e in doc.education), len(doc.education)),
        "skills": float(min(len(doc.skills), 60)),
        "sections": float(sections),
        "languages": float(min(len(doc.languages), 10)),
        "certifications": float(min(len(doc.certifications), 20)),
    }
    return [values[name] for name in FEATURE_NAMES]


def extract_features(text: str) -> list[float]:
    return features_from_document(parse_cv_text(text), text)


class CvFeatures(BaseEstimator, TransformerMixin):
    """scikit-learn transformer: CV texts -> the numeric features above."""

    def fit(self, texts: Iterable[str], y=None):  # noqa: ANN001 - sklearn API
        return self

    def transform(self, texts: Iterable[str]) -> np.ndarray:
        return np.array([extract_features(text) for text in texts], dtype=float)

    def get_feature_names_out(self, input_features=None) -> np.ndarray:  # noqa: ANN001 - sklearn API
        return np.array(FEATURE_NAMES)
