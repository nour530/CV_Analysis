from __future__ import annotations

from app.api.schemas import JobSummary
from app.services.job_catalog import CatalogJob

JOB_SUMMARY_ITEM_LIMIT = 200
JOB_SUMMARY_EXCERPT_LIMIT = 500


def _excerpt(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= JOB_SUMMARY_EXCERPT_LIMIT:
        return cleaned
    return cleaned[: JOB_SUMMARY_EXCERPT_LIMIT - 3].rstrip() + "..."


def job_summary(job: CatalogJob) -> JobSummary:
    return JobSummary(
        job_id=job.job_id,
        title=job.title,
        category=job.category,
        description_excerpt=job.description_excerpt,
        required_skills=list(job.required_skills[:JOB_SUMMARY_ITEM_LIMIT]),
        preferred_skills=list(job.preferred_skills[:JOB_SUMMARY_ITEM_LIMIT]),
    )


def custom_job_summary(
    *,
    title: str | None,
    category: str | None,
    description: str,
) -> JobSummary:
    return JobSummary(
        job_id="custom-job",
        title=title or "Custom job description",
        category=category,
        description_excerpt=_excerpt(description),
    )
