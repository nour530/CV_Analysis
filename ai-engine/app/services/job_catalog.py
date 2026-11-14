from __future__ import annotations

import csv
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app.schemas.hybrid_match import JobProfile, JobRequirement
from app.services.nlp.embeddings import LocalTfidfEmbedder
from app.services.nlp.skills import LocalSkillExtractor, normalize_skill_key

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
                    self._embedder = LocalTfidfEmbedder().fit(
                        self.reference_corpus()
                    )
        return self._embedder

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
    ) -> tuple[list[CatalogJob], int]:
        category_key = category.strip().casefold() if category else None
        query_key = query.strip().casefold() if query else None
        filtered = [
            job
            for job in self._jobs.values()
            if (category_key is None or (job.category or "").casefold() == category_key)
            and (query_key is None or query_key in job.search_text)
        ]
        return filtered[offset : offset + limit], len(filtered)


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
