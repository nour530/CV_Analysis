"""ESCO occupations, skills and career fields (adapted subset, EN/FR).

This service uses the ESCO classification of the European Commission
(https://ec.europa.eu/esco). Data is imported by
data_analysis/05_import_esco.py into data/esco/.
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.services.nlp.skills import strip_accents

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "esco"


def esco_data_dir() -> Path:
    """ESCO data folder; ESCO_DATA_DIR overrides it (used by tests)."""
    override = os.environ.get("ESCO_DATA_DIR")
    return Path(override) if override else DATA_DIR


@dataclass(frozen=True, slots=True)
class Domain:
    id: str
    en: str
    fr: str
    isco: tuple[str, ...]

    def label(self, language: str = "en") -> str:
        return self.fr if language == "fr" else self.en


@dataclass(frozen=True, slots=True)
class EscoSkill:
    uri: str
    label_en: str
    label_fr: str
    alt_en: tuple[str, ...]
    alt_fr: tuple[str, ...]
    skill_type: str
    reuse_level: str

    def label(self, language: str = "en") -> str:
        return (self.label_fr or self.label_en) if language == "fr" else self.label_en

    @property
    def transversal(self) -> bool:
        return "transversal" in self.reuse_level


@dataclass(frozen=True, slots=True)
class EscoOccupation:
    uri: str
    code: str
    domain_id: str | None
    label_en: str
    label_fr: str
    alt_en: tuple[str, ...]
    alt_fr: tuple[str, ...]
    description_en: str
    description_fr: str
    essential: tuple[str, ...]
    optional: tuple[str, ...] = field(default_factory=tuple)

    def label(self, language: str = "en") -> str:
        return (self.label_fr or self.label_en) if language == "fr" else self.label_en

    def description(self, language: str = "en") -> str:
        return (self.description_fr or self.description_en) if language == "fr" else self.description_en


def _split(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(";") if part.strip())


def normalize_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(text or "").casefold()).strip()


class EscoCatalog:
    def __init__(self, directory: Path | None = None) -> None:
        data_dir = directory or esco_data_dir()
        self.data_dir = data_dir
        payload = json.loads((data_dir / "domains.json").read_text(encoding="utf-8"))
        self.domains: dict[str, Domain] = {
            item["id"]: Domain(item["id"], item["en"], item["fr"], tuple(item["isco"]))
            for item in payload["domains"]
        }
        self._prefixes = sorted(
            ((prefix, domain.id) for domain in self.domains.values() for prefix in domain.isco),
            key=lambda item: -len(item[0]),
        )
        self.skills: dict[str, EscoSkill] = {}
        self.occupations: list[EscoOccupation] = []
        skills_path = data_dir / "esco_skills.csv"
        occupations_path = data_dir / "esco_occupations.csv"
        if skills_path.exists():
            with skills_path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    self.skills[row["uri"]] = EscoSkill(
                        uri=row["uri"],
                        label_en=row["label_en"],
                        label_fr=row["label_fr"],
                        alt_en=_split(row["alt_en"]),
                        alt_fr=_split(row["alt_fr"]),
                        skill_type=row["skill_type"],
                        reuse_level=row["reuse_level"],
                    )
        if occupations_path.exists():
            with occupations_path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    self.occupations.append(
                        EscoOccupation(
                            uri=row["uri"],
                            code=row["code"],
                            domain_id=self.domain_for_code(row["code"]),
                            label_en=row["label_en"],
                            label_fr=row["label_fr"],
                            alt_en=_split(row["alt_en"]),
                            alt_fr=_split(row["alt_fr"]),
                            description_en=row["description_en"],
                            description_fr=row["description_fr"],
                            essential=_split(row["essential_skills"]),
                            optional=_split(row["optional_skills"]),
                        )
                    )
        self.by_uri = {occupation.uri: occupation for occupation in self.occupations}

    @property
    def available(self) -> bool:
        return bool(self.occupations and self.skills)

    def domain_for_code(self, code: str) -> str | None:
        """Map an ESCO occupation code (ISCO-based, e.g. '2221.3') to a field,
        using the most specific matching ISCO prefix."""
        digits = re.sub(r"\D", "", (code or "").split(".")[0])
        for prefix, domain_id in self._prefixes:
            if digits.startswith(prefix):
                return domain_id
        return None

    def occupation_document(self, occupation: EscoOccupation, language: str) -> str:
        """Text describing an occupation: title, synonyms, description, skills."""
        if language == "fr":
            labels = [occupation.label_fr, *occupation.alt_fr[:8]]
            skills = [self.skills[uri].label_fr for uri in occupation.essential if uri in self.skills]
            description = occupation.description_fr
        else:
            labels = [occupation.label_en, *occupation.alt_en[:8]]
            skills = [self.skills[uri].label_en for uri in occupation.essential if uri in self.skills]
            description = occupation.description_en
        return " . ".join(part for part in (" ; ".join(filter(None, labels)), description, " ; ".join(filter(None, skills))) if part)

    def search_occupations(self, query: str, *, domain_id: str | None = None, limit: int = 10) -> list[EscoOccupation]:
        needle = normalize_phrase(query)
        scored: list[tuple[int, str, EscoOccupation]] = []
        for occupation in self.occupations:
            if domain_id and occupation.domain_id != domain_id:
                continue
            names = [occupation.label_en, occupation.label_fr, *occupation.alt_en, *occupation.alt_fr]
            keys = [normalize_phrase(name) for name in names if name]
            if not needle:
                rank = 2
            elif any(key == needle for key in keys):
                rank = 0
            elif any(key.startswith(needle) for key in keys):
                rank = 1
            elif any(needle in key for key in keys):
                rank = 2
            else:
                continue
            scored.append((rank, occupation.label_en, occupation))
        scored.sort(key=lambda item: (item[0], len(item[1]), item[1]))
        return [item[2] for item in scored[:limit]]


@lru_cache(maxsize=1)
def get_esco_catalog() -> EscoCatalog:
    return EscoCatalog()


class SkillMatcher:
    """Finds which ESCO skills (by EN/FR label or synonym) a text mentions.

    ESCO skill labels are often phrases ("coordinate care", "gestion des
    stocks"). Matching is whole-phrase, accent-insensitive; single words need
    at least 4 letters so short ambiguous tokens are ignored.
    """

    def __init__(self, catalog: EscoCatalog) -> None:
        self.catalog = catalog
        self._patterns: dict[str, re.Pattern[str] | None] = {}

    def _pattern(self, uri: str) -> re.Pattern[str] | None:
        if uri in self._patterns:
            return self._patterns[uri]
        skill = self.catalog.skills.get(uri)
        pattern = None
        if skill is not None:
            phrases = {
                normalize_phrase(text)
                for text in (skill.label_en, skill.label_fr, *skill.alt_en[:6], *skill.alt_fr[:6])
                if text
            }
            phrases = {p for p in phrases if len(p) >= 4 and (" " in p or len(p) >= 5)}
            if phrases:
                pattern = re.compile(
                    r"(?<![\w])(?:" + "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True)) + r")(?![\w])"
                )
        self._patterns[uri] = pattern
        return pattern

    def present(self, text: str, uris: tuple[str, ...] | list[str]) -> list[str]:
        normalized = normalize_phrase(text)
        found: list[str] = []
        for uri in uris:
            pattern = self._pattern(uri)
            if pattern is not None and pattern.search(normalized):
                found.append(uri)
        return found
