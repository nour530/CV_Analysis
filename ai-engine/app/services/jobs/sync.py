"""Synchronise real job postings from external sources into the database."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import JobPosting, utcnow
from app.services.jobs.sources import JobSource
from app.services.jobs.types import FetchedJob, JobSourceError, content_hash
from app.services.nlp.skills import LocalSkillExtractor, normalize_skill_key

LOGGER = logging.getLogger("jobmatch.jobs")
MIN_DESCRIPTION_CHARS = 80
MAX_DESCRIPTION_CHARS = 100_000
MAX_SKILLS_PER_LEVEL = 200


@dataclass(slots=True)
class SourceSyncResult:
    source: str
    company: str
    fetched: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    closed: int = 0
    skipped: int = 0
    error: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "source": self.source,
            "company": self.company,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "closed": self.closed,
            "skipped": self.skipped,
            "error": self.error,
        }


@dataclass(slots=True)
class JobSyncResult:
    sources: list[SourceSyncResult] = field(default_factory=list)
    purged: int = 0
    active_total: int = 0
    dry_run: bool = False

    def to_payload(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "active_total": self.active_total,
            "purged": self.purged,
            "failed_sources": sum(1 for item in self.sources if item.error),
            "sources": [item.to_payload() for item in self.sources],
        }


def _skills_by_importance(
    extractor: LocalSkillExtractor, title: str, description: str
) -> tuple[list[str], list[str], list[str]]:
    levels: dict[str, list[str]] = {"required": [], "preferred": [], "mentioned": []}
    seen: set[str] = set()
    for requirement in extractor.extract_requirements(f"{title}\n{description}"):
        key = normalize_skill_key(requirement.skill)
        if not key or key in seen:
            continue
        seen.add(key)
        bucket = levels.get(requirement.importance, levels["mentioned"])
        if len(bucket) < MAX_SKILLS_PER_LEVEL:
            bucket.append(requirement.skill)
    return levels["required"], levels["preferred"], levels["mentioned"]


def _tidy_city(value: str | None) -> str | None:
    """Normalise city capitalisation so facets do not split 'sousse'/'Sousse'."""
    if not value or not value.strip():
        return None
    text = " ".join(value.split())
    return text.title() if text.isupper() or text.islower() else text


def _apply(
    row: JobPosting,
    job: FetchedJob,
    description: str,
    digest: str,
    extractor: LocalSkillExtractor,
) -> None:
    row.version = job.version[:200]
    row.content_hash = digest
    row.title = job.title[:300]
    row.company = job.company[:200]
    row.company_logo_url = job.company_logo_url
    row.description = description
    row.location = job.location
    row.city = _tidy_city(job.city)
    row.country_code = job.country_code.upper() if job.country_code else None
    row.workplace = job.workplace
    row.employment_type = job.employment_type
    row.experience_level = job.experience_level
    row.category = job.category
    row.salary_text = job.salary_text
    row.apply_url = job.apply_url[:1000]
    row.posted_at = job.posted_at
    required, preferred, other = _skills_by_importance(
        extractor, job.title, (job.skills_text or description)[:MAX_DESCRIPTION_CHARS]
    )
    row.required_skills = required
    row.preferred_skills = preferred
    row.other_skills = other


def _refresh_skills(row: JobPosting, extractor: LocalSkillExtractor) -> None:
    # Only the stored description is available here; boilerplate sections that
    # adapters exclude via ``skills_text`` are dropped on the next content change.
    required, preferred, other = _skills_by_importance(
        extractor, row.title, row.description
    )
    row.required_skills, row.preferred_skills, row.other_skills = required, preferred, other


def _sync_source(
    db: Session,
    source: JobSource,
    extractor: LocalSkillExtractor,
    now: datetime,
    refresh_skills: bool = False,
) -> SourceSyncResult:
    result = SourceSyncResult(source=source.name, company=source.company)
    existing = {
        row.external_id: row
        for row in db.scalars(
            select(JobPosting).where(
                JobPosting.source == source.name,
                JobPosting.source_company == source.company,
            )
        )
    }
    known_versions = {key: row.version for key, row in existing.items() if row.is_active}
    try:
        fetched = source.fetch(known_versions)
    except JobSourceError as exc:
        # Never close postings because a source is temporarily unreachable.
        result.error = str(exc)[:300]
        return result

    result.fetched = len(fetched)
    seen: set[str] = set()
    for job in fetched:
        if not job.title or not job.apply_url.startswith(("https://", "http://")):
            result.skipped += 1
            continue
        if job.external_id in seen:
            continue
        seen.add(job.external_id)
        row = existing.get(job.external_id)

        if job.description is None:
            if row is None:
                result.skipped += 1
                continue
            row.last_seen_at = now
            if not row.is_active:
                row.is_active, row.closed_at = True, None
            if refresh_skills:
                _refresh_skills(row, extractor)
            result.unchanged += 1
            continue

        description = job.description.strip()[:MAX_DESCRIPTION_CHARS]
        if len(description) < MIN_DESCRIPTION_CHARS:
            result.skipped += 1
            continue
        digest = content_hash(
            job.title, job.company, description, job.location, job.apply_url,
            job.employment_type, job.workplace, job.salary_text,
        )
        if row is None:
            row = JobPosting(
                id=job.posting_id,
                source=job.source,
                source_company=job.source_company,
                external_id=job.external_id,
                first_seen_at=now,
            )
            _apply(row, job, description, digest, extractor)
            row.last_seen_at, row.is_active = now, True
            db.add(row)
            existing[job.external_id] = row
            result.created += 1
            continue

        if row.content_hash != digest:
            _apply(row, job, description, digest, extractor)
            result.updated += 1
        else:
            row.version = job.version[:200]
            if refresh_skills:
                _refresh_skills(row, extractor)
            result.unchanged += 1
        row.last_seen_at = now
        row.is_active, row.closed_at = True, None

    for external_id, row in existing.items():
        if row.is_active and external_id not in seen:
            row.is_active = False
            row.closed_at = now
            result.closed += 1
    return result


def sync_jobs(
    db: Session,
    sources: Sequence[JobSource],
    *,
    extractor: LocalSkillExtractor | None = None,
    closed_retention_days: int = 30,
    dry_run: bool = False,
    refresh_skills: bool = False,
    now: datetime | None = None,
) -> JobSyncResult:
    """Fetch every source, upsert postings, close vanished ones, purge old ones.

    Each source is committed independently so one failing board does not undo
    the others. With ``dry_run`` nothing is committed.
    """
    skill_extractor = extractor or LocalSkillExtractor()
    moment = now or utcnow()
    result = JobSyncResult(dry_run=dry_run)
    for source in sources:
        try:
            outcome = _sync_source(
                db, source, skill_extractor, moment, refresh_skills=refresh_skills
            )
        except Exception as exc:  # defensive: a bad payload must not stop the run
            db.rollback()
            outcome = SourceSyncResult(
                source=getattr(source, "name", "unknown"),
                company=getattr(source, "company", "unknown"),
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
        else:
            if dry_run:
                db.rollback()
            else:
                db.commit()
        result.sources.append(outcome)
        LOGGER.info("job_source_synced %s", outcome.to_payload())

    cutoff = moment - timedelta(days=max(0, closed_retention_days))
    stale = select(JobPosting.id).where(
        JobPosting.is_active.is_(False), JobPosting.closed_at < cutoff
    )
    result.purged = len(db.scalars(stale).all())
    if not dry_run and result.purged:
        db.execute(
            delete(JobPosting).where(
                JobPosting.is_active.is_(False), JobPosting.closed_at < cutoff
            )
        )
        db.commit()
    result.active_total = len(
        db.scalars(select(JobPosting.id).where(JobPosting.is_active.is_(True))).all()
    )
    return result


def active_postings(db: Session) -> list[JobPosting]:
    return list(
        db.scalars(
            select(JobPosting)
            .where(JobPosting.is_active.is_(True))
            .order_by(JobPosting.posted_at.desc().nulls_last(), JobPosting.id)
        )
    )
