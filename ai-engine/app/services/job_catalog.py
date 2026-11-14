from __future__ import annotations

import csv
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from app.schemas.hybrid_match import JobProfile, JobRequirement
from app.services.nlp.embeddings import LocalTfidfEmbedder
from app.services.nlp.skills import LocalSkillExtractor, normalize_skill_key, strip_accents

if TYPE_CHECKING:
    from app.db.models import JobPosting

DEFAULT_RESPONSIBILITY_LIMIT = 100
DEFAULT_SKILL_LIMIT = 500
# Number of catalog descriptions used to fit TF-IDF weights. A category-balanced
# sample this size gives stable IDF estimates while keeping the one-off fit to a
# few seconds; fitting on all ~10k descriptions takes roughly five times longer.
DEFAULT_EMBEDDING_CORPUS_SIZE = 2000
EMPTY_VALUES = frozenset({"", "nan", "none", "null", "n/a", "na"})
WHITESPACE_PATTERN = re.compile(r"\s+")


class JobCatalogError(RuntimeError):
    """Raised when the job catalog cannot be loaded."""


def _clean(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = WHITESPACE_PATTERN.sub(" ", value.replace("\u00a0", " ")).strip()
    return "" if cleaned.casefold() in EMPTY_VALUES else cleaned


def _split_semicolon(value: object) -> tuple[str, ...]:
    text = _clean(value)
    if not text:
        return ()
    return tuple(
        item.strip()
        for item in text.split(";")
        if item.strip() and item.strip().casefold() not in EMPTY_VALUES
    )


def _split_pipe(value: object) -> tuple[str, ...]:
    text = _clean(value)
    if not text:
        return ()
    return tuple(
        item.strip()
        for item in text.split("|")
        if item.strip() and item.strip().casefold() not in EMPTY_VALUES
    )


def _excerpt(text: str, limit: int = 500) -> str:
    cleaned = WHITESPACE_PATTERN.sub(" ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


@dataclass(slots=True)
class CatalogJob:
    job_id: str
    title: str | None
    category: str | None
    description: str
    required_skills: tuple[str, ...]
    preferred_skills: tuple[str, ...]
    other_skills: tuple[str, ...]
    responsibilities: tuple[str, ...] = ()
    qualifications: tuple[str, ...] = ()
    skill_keys: frozenset[str] = field(default_factory=frozenset)
    required_keys: frozenset[str] = field(default_factory=frozenset)
    preferred_keys: frozenset[str] = field(default_factory=frozenset)
    search_text: str = field(default="", repr=False)
    company: str | None = None
    company_logo_url: str | None = None
    location: str | None = None
    city: str | None = None
    country_code: str | None = None
    workplace: str | None = None
    employment_type: str | None = None
    experience_level: str | None = None
    salary_text: str | None = None
    apply_url: str | None = None
    posted_at: datetime | None = None
    source: str = "dataset"

    @property
    def description_excerpt(self) -> str:
        return _excerpt(self.description)


class JobCatalog:
    """In-memory job catalog backed by the existing job feature table."""

    def __init__(
        self,
        jobs: Iterable[CatalogJob],
        *,
        extractor: LocalSkillExtractor | None = None,
    ) -> None:
        self.extractor = extractor or LocalSkillExtractor()
        self._jobs = {job.job_id: job for job in jobs}
        self._all_jobs = tuple(self._jobs.values())
        self._profile_cache: dict[str, JobProfile] = {}
        self._embedder: LocalTfidfEmbedder | None = None
        self._embedder_lock = threading.Lock()

    def reference_corpus(
        self, limit: int = DEFAULT_EMBEDDING_CORPUS_SIZE
    ) -> list[str]:
        """Return a deterministic, category-balanced sample of descriptions.

        Jobs are taken round-robin across categories in catalog order so the
        dominant category does not dictate which terms look common.
        """
        by_category: dict[str, list[str]] = {}
        for job in self._all_jobs:
            if job.description:
                by_category.setdefault(job.category or "", []).append(job.description)
        queues = [list(reversed(texts)) for _, texts in sorted(by_category.items())]
        corpus: list[str] = []
        while queues and len(corpus) < limit:
            for queue in queues:
                if queue and len(corpus) < limit:
                    corpus.append(queue.pop())
            queues = [queue for queue in queues if queue]
        return corpus

    @property
    def embedder(self) -> LocalTfidfEmbedder:
        """Return a TF-IDF embedder fitted on this catalog (built on first use)."""
        if self._embedder is None:
            with self._embedder_lock:
                if self._embedder is None:
                    corpus = self.reference_corpus()
                    embedder = LocalTfidfEmbedder()
                    self._embedder = embedder.fit(corpus) if corpus else embedder
        return self._embedder

    @classmethod
    def from_postings(
        cls,
        postings: Iterable[JobPosting],
        *,
        extractor: LocalSkillExtractor | None = None,
    ) -> JobCatalog:
        """Build a catalog from synchronised real job postings."""
        skill_extractor = extractor or LocalSkillExtractor()
        jobs: list[CatalogJob] = []
        for row in postings:
            required = _canonical_skill_list(row.required_skills or [], skill_extractor)
            preferred = _canonical_skill_list(row.preferred_skills or [], skill_extractor)
            other = _canonical_skill_list(row.other_skills or [], skill_extractor)
            search_text = WHITESPACE_PATTERN.sub(
                " ",
                " ".join(
                    part
                    for part in (
                        row.title, row.company, row.location, row.category,
                        row.description, " ".join(required + preferred + other),
                    )
                    if part
                ),
            ).casefold()
            jobs.append(
                CatalogJob(
                    job_id=row.id,
                    title=row.title,
                    category=row.category,
                    description=row.description,
                    required_skills=required,
                    preferred_skills=preferred,
                    other_skills=other,
                    skill_keys=frozenset(
                        normalize_skill_key(skill) for skill in required + preferred + other
                    ),
                    required_keys=frozenset(normalize_skill_key(s) for s in required),
                    preferred_keys=frozenset(normalize_skill_key(s) for s in preferred),
                    search_text=search_text,
                    company=row.company,
                    company_logo_url=row.company_logo_url,
                    location=tidy_location(row.location),
                    city=row.city,
                    country_code=row.country_code,
                    workplace=row.workplace,
                    employment_type=row.employment_type,
                    experience_level=row.experience_level,
                    salary_text=row.salary_text,
                    apply_url=row.apply_url,
                    posted_at=row.posted_at,
                    source=row.source,
                )
            )
        return cls(jobs, extractor=skill_extractor)

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        *,
        extractor: LocalSkillExtractor | None = None,
    ) -> JobCatalog:
        source = Path(path)
        if not source.exists():
            raise JobCatalogError(
                f"Job catalog file was not found at {source}. Set JOBS_FEATURES_PATH."
            )

        skill_extractor = extractor or LocalSkillExtractor()
        last_error: Exception | None = None
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with source.open("r", encoding=encoding, newline="") as handle:
                    jobs = cls._read_jobs(csv.DictReader(handle), skill_extractor)
                if not jobs:
                    raise JobCatalogError(
                        "Job catalog contains no valid job rows. Verify the feature "
                        "table columns and contents."
                    )
                return cls(jobs, extractor=skill_extractor)
            except UnicodeDecodeError as exc:
                last_error = exc
            except OSError as exc:
                raise JobCatalogError(f"Could not read job catalog: {exc}") from exc
        raise JobCatalogError(f"Could not decode job catalog: {last_error}")

    @staticmethod
    def _read_jobs(
        rows: Iterable[dict[str, str]],
        extractor: LocalSkillExtractor,
    ) -> list[CatalogJob]:
        jobs: list[CatalogJob] = []
        for row in rows:
            job_id = _clean(row.get("job_id"))
            description = _clean(row.get("clean_description")) or _clean(
                row.get("jobdescription")
            )
            if not job_id or not description:
                continue

            category = _clean(row.get("label_clean")) or _clean(row.get("label")) or None
            title = _clean(row.get("job_title")) or category

            required_skills = _canonical_skill_list(
                _split_semicolon(row.get("required_skills")), extractor
            )
            preferred_skills = _canonical_skill_list(
                _split_semicolon(row.get("preferred_skills")), extractor
            )
            other_skills = _canonical_skill_list(
                _split_semicolon(row.get("other_skills")), extractor
            )
            if not any([required_skills, preferred_skills, other_skills]):
                other_skills = _canonical_skill_list(
                    _split_semicolon(row.get("all_extracted_skills")), extractor
                )

            all_skill_keys = frozenset(
                normalize_skill_key(skill)
                for skill in [*required_skills, *preferred_skills, *other_skills]
            )
            search_text = " ".join(
                part for part in [title, category, description] if part
            ).casefold()
            jobs.append(
                CatalogJob(
                    job_id=job_id,
                    title=title,
                    category=category,
                    description=description,
                    required_skills=required_skills,
                    preferred_skills=preferred_skills,
                    other_skills=other_skills,
                    responsibilities=_split_pipe(row.get("responsibilities"))[
                        :DEFAULT_RESPONSIBILITY_LIMIT
                    ],
                    qualifications=_split_pipe(row.get("qualifications"))[
                        :DEFAULT_RESPONSIBILITY_LIMIT
                    ],
                    skill_keys=all_skill_keys,
                    required_keys=frozenset(
                        normalize_skill_key(skill) for skill in required_skills
                    ),
                    preferred_keys=frozenset(
                        normalize_skill_key(skill) for skill in preferred_skills
                    ),
                    search_text=search_text,
                )
            )
        return jobs

    def __len__(self) -> int:
        return len(self._jobs)

    def get(self, job_id: str) -> CatalogJob | None:
        return self._jobs.get(job_id.strip())

    def all_jobs(self) -> tuple[CatalogJob, ...]:
        return self._all_jobs

    def profile(self, job: CatalogJob) -> JobProfile:
        cached = self._profile_cache.get(job.job_id)
        if cached is not None:
            return cached
        profile = JobProfile(
            job_id=job.job_id,
            title=job.title,
            category=job.category,
            semantic_text=job.description,
            requirements=self._requirements(job),
            responsibilities=list(job.responsibilities),
            qualifications=list(job.qualifications),
        )
        self._profile_cache[job.job_id] = profile
        return profile

    def _requirements(self, job: CatalogJob) -> list[JobRequirement]:
        requirements: list[JobRequirement] = []
        seen: set[str] = set()
        for skills, importance in (
            (job.required_skills, "required"),
            (job.preferred_skills, "preferred"),
            (job.other_skills, "mentioned"),
        ):
            for skill in skills:
                key = normalize_skill_key(skill)
                if key in seen:
                    continue
                seen.add(key)
                requirements.append(
                    JobRequirement(
                        skill=skill,
                        category=self.extractor.category_for_skill(skill),
                        importance=importance,
                        evidence=[self._skill_evidence(job.description, skill)],
                        confidence=0.8 if importance == "required" else 0.65,
                    )
                )
        return requirements[:DEFAULT_SKILL_LIMIT]

    @staticmethod
    def _skill_evidence(description: str, skill: str) -> str:
        lowered = description.casefold()
        index = lowered.find(skill.casefold())
        if index < 0:
            return f"{skill} was extracted from the job description."
        start = max(0, index - 80)
        end = min(len(description), index + len(skill) + 140)
        return _excerpt(description[start:end], limit=260)

    def categories(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    job.category
                    for job in self._jobs.values()
                    if job.category is not None
                }
            )
        )

    def list_jobs(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
        workplace: str | None = None,
        location: str | None = None,
        company: str | None = None,
    ) -> tuple[list[CatalogJob], int]:
        category_key = category.strip().casefold() if category else None
        query_terms = query.strip().casefold().split() if query else []
        workplace_key = workplace.strip().casefold() if workplace else None
        location_pattern = (
            re.compile(rf"(?<!\w){re.escape(location.strip().casefold())}(?!\w)")
            if location and location.strip()
            else None
        )
        company_key = company.strip().casefold() if company else None
        filtered = [
            job
            for job in self._jobs.values()
            if (category_key is None or (job.category or "").casefold() == category_key)
            and all(term in job.search_text for term in query_terms)
            and (workplace_key is None or (job.workplace or "").casefold() == workplace_key)
            and (
                location_pattern is None
                or location_pattern.search((job.location or "").casefold()) is not None
                or location_pattern.search((job.city or "").casefold()) is not None
            )
            and (company_key is None or (job.company or "").casefold() == company_key)
        ]
        return filtered[offset : offset + limit], len(filtered)

    def facets(self) -> dict[str, list[tuple[str, int]]]:
        """Return value counts for filterable fields, most common first."""
        counters: dict[str, dict[str, int]] = {
            "categories": {}, "cities": {}, "companies": {}, "workplaces": {},
        }
        for job in self._jobs.values():
            for name, value in (
                ("categories", job.category),
                ("cities", job.city),
                ("companies", job.company),
                ("workplaces", job.workplace),
            ):
                if value:
                    counters[name][value] = counters[name].get(value, 0) + 1
        return {
            name: sorted(values.items(), key=lambda item: (-item[1], item[0]))[:100]
            for name, values in counters.items()
        }


_COUNTRY_ALIASES = {"tunisie": "tunisia"}


def tidy_location(value: str | None) -> str | None:
    """Drop repeated parts such as "Tunis, Tunis, Tunisia" or "Tunisie, Tunisia"."""
    if not value:
        return value
    kept: list[str] = []
    seen: set[str] = set()
    for part in (piece.strip() for piece in value.split(",")):
        if not part:
            continue
        key = strip_accents(part).casefold().replace(" governorate", "").strip()
        if key in _COUNTRY_ALIASES:
            key = _COUNTRY_ALIASES[key]
            part = key.title()
        if key in seen:
            continue
        seen.add(key)
        kept.append(part)
    return ", ".join(kept) or None


def _canonical_skill_list(
    values: Iterable[str],
    extractor: LocalSkillExtractor,
) -> tuple[str, ...]:
    canonical: list[str] = []
    seen: set[str] = set()
    for value in values:
        skill = extractor.canonicalize_skill(value)
        key = normalize_skill_key(skill)
        if not key or key in seen:
            continue
        seen.add(key)
        canonical.append(skill)
    return tuple(canonical[:DEFAULT_SKILL_LIMIT])
