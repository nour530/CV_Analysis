from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Workplace = Literal["onsite", "hybrid", "remote", "unknown"]


class JobSourceError(RuntimeError):
    """Raised when a job source cannot be fetched or parsed."""


@dataclass(slots=True)
class FetchedJob:
    """A job posting normalised from any external source.

    ``description`` is ``None`` when the source reported that a posting is
    unchanged since the last sync (``version`` matches the stored one), so the
    expensive detail request was skipped.
    """

    source: str
    source_company: str
    external_id: str
    title: str
    company: str
    apply_url: str
    description: str | None
    version: str
    location: str | None = None
    city: str | None = None
    country_code: str | None = None
    workplace: Workplace = "unknown"
    employment_type: str | None = None
    experience_level: str | None = None
    category: str | None = None
    posted_at: datetime | None = None
    salary_text: str | None = None
    company_logo_url: str | None = None
    # Text used for skill extraction when it differs from ``description``
    # (e.g. without the employer's boilerplate "about us" and legal sections).
    skills_text: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def posting_id(self) -> str:
        return make_posting_id(self.source, self.source_company, self.external_id)


def make_posting_id(source: str, company: str, external_id: str) -> str:
    """Return a URL-safe, stable identifier for a posting (max 120 chars)."""
    raw = f"{source}-{company}-{external_id}".casefold()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in raw)
    if len(safe) <= 120:
        return safe
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{safe[:100]}-{digest}"


def content_hash(*parts: str | None) -> str:
    joined = "␟".join(part or "" for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
