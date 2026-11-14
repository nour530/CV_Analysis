"""Adapters for public job-board APIs that publish real company postings.

Every adapter uses an official, documented, key-free endpoint that employers
publish for job distribution. No site is scraped. Each adapter returns
``FetchedJob`` objects in a single normalised shape.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.services.jobs.text import html_to_text, join_sections
from app.services.jobs.types import FetchedJob, JobSourceError, Workplace

LOGGER = logging.getLogger("jobmatch.jobs")
USER_AGENT = "JobMatchAI-JobSync/1.0"
DEFAULT_TIMEOUT_SECONDS = 25.0
MAX_PAGES = 50


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _opt(value: Any, limit: int = 300) -> str | None:
    text = _str(value)
    return text[:limit] if text else None


def parse_datetime(value: Any) -> datetime | None:
    """Parse ISO strings, '... UTC' strings or epoch milliseconds to naive UTC."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 10_000_000_000 else value
            parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
        else:
            text = str(value).strip().replace(" UTC", "+00:00").replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError, OSError):
        return None


def _workplace(*, remote: Any = None, hybrid: Any = None, label: Any = None) -> Workplace:
    text = _str(label).casefold().replace("-", "").replace("_", "").replace(" ", "")
    if text in {"remote", "fullyremote"}:
        return "remote"
    if text == "hybrid":
        return "hybrid"
    if text in {"onsite", "office", "inoffice"}:
        return "onsite"
    if remote is True:
        return "remote"
    if hybrid is True:
        return "hybrid"
    if remote is False and hybrid is False:
        return "onsite"
    return "unknown"


def _country_match(values: Iterable[Any], wanted: Sequence[str]) -> bool:
    keys = [item.casefold() for item in wanted if item]
    for value in values:
        text = _str(value).casefold()
        if not text:
            continue
        for key in keys:
            if len(key) <= 3:
                if text == key:
                    return True
            elif key in text:
                return True
    return False


class JobSource(Protocol):
    name: str
    company: str

    def fetch(self, known_versions: Mapping[str, str]) -> list[FetchedJob]:
        """Return all current postings; ``known_versions`` maps external IDs to
        the version stored by the previous sync so unchanged details can be
        skipped."""


@dataclass(slots=True)
class _HttpSource:
    client: httpx.Client
    company: str
    request_delay_seconds: float = 0.0

    def _get_json(self, url: str, params: Mapping[str, Any] | None = None) -> Any:
        try:
            response = self.client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise JobSourceError(f"{type(exc).__name__} for {url}") from exc
        if response.status_code == 404:
            raise JobSourceError(f"Board not found: {url}")
        if response.status_code >= 400:
            raise JobSourceError(f"HTTP {response.status_code} for {url}")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise JobSourceError(f"Invalid JSON from {url}") from exc

    def _pause(self) -> None:
        if self.request_delay_seconds > 0:
            time.sleep(self.request_delay_seconds)


@dataclass(slots=True)
class SmartRecruitersSource(_HttpSource):
    """SmartRecruiters Posting API (public, no key)."""

    country: str = "tn"
    name: str = "smartrecruiters"
    base_url: str = "https://api.smartrecruiters.com/v1/companies"

    def fetch(self, known_versions: Mapping[str, str]) -> list[FetchedJob]:
        listings: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            page = self._get_json(
                f"{self.base_url}/{self.company}/postings",
                {"country": self.country, "limit": 100, "offset": offset},
            )
            content = page.get("content") or []
            listings.extend(item for item in content if isinstance(item, dict))
            offset += len(content)
            if not content or offset >= int(page.get("totalFound") or 0):
                break
            self._pause()

        jobs: list[FetchedJob] = []
        for item in listings:
            external_id = _str(item.get("id"))
            if not external_id:
                continue
            version = f"{item.get('releasedDate')}|{item.get('jobAdId')}"
            description: str | None = None
            skills_text: str | None = None
            apply_url = (
                f"https://jobs.smartrecruiters.com/{self.company}/{external_id}"
            )
            if known_versions.get(external_id) != version:
                self._pause()
                try:
                    detail = self._get_json(
                        f"{self.base_url}/{self.company}/postings/{external_id}"
                    )
                except JobSourceError as exc:
                    LOGGER.warning("smartrecruiters_detail_failed %s", exc)
                    continue
                sections = ((detail.get("jobAd") or {}).get("sections")) or {}
                description = join_sections(
                    *(
                        (
                            _str((sections.get(key) or {}).get("title")) or None,
                            (sections.get(key) or {}).get("text"),
                        )
                        # The employer's "about us" section is identical on
                        # every posting, so it is left out of the stored text.
                        for key in (
                            "jobDescription",
                            "qualifications",
                            "additionalInformation",
                        )
                    )
                )
                skills_text = join_sections(
                    *(
                        (None, (sections.get(key) or {}).get("text"))
                        for key in ("jobDescription", "qualifications")
                    )
                )
                apply_url = _str(detail.get("applyUrl")) or _str(
                    detail.get("postingUrl")
                ) or apply_url
            location = item.get("location") or {}
            jobs.append(
                FetchedJob(
                    source=self.name,
                    source_company=self.company,
                    external_id=external_id,
                    title=_str(item.get("name")),
                    company=_str((item.get("company") or {}).get("name")) or self.company,
                    apply_url=apply_url,
                    description=description,
                    skills_text=skills_text,
                    version=version,
                    location=_opt(location.get("fullLocation")),
                    city=_opt(location.get("city"), 120),
                    country_code=_opt(location.get("country"), 2),
                    workplace=_workplace(
                        remote=location.get("remote"), hybrid=location.get("hybrid")
                    ),
                    employment_type=_opt((item.get("typeOfEmployment") or {}).get("label"), 60),
                    experience_level=_opt((item.get("experienceLevel") or {}).get("label"), 60),
                    category=_opt(
                        (item.get("function") or {}).get("label")
                        or (item.get("department") or {}).get("label"),
                        120,
                    ),
                    posted_at=parse_datetime(item.get("releasedDate")),
                )
            )
        return jobs


@dataclass(slots=True)
class RecruiteeSource(_HttpSource):
    """Recruitee careers-site offers API (public, no key)."""

    country_codes: Sequence[str] = ("TN",)
    name: str = "recruitee"

    def fetch(self, known_versions: Mapping[str, str]) -> list[FetchedJob]:
        payload = self._get_json(f"https://{self.company}.recruitee.com/api/offers/")
        wanted = [code.upper() for code in self.country_codes]
        jobs: list[FetchedJob] = []
        for offer in payload.get("offers") or []:
            if not isinstance(offer, dict) or offer.get("status") not in (None, "published"):
                continue
            locations = [loc for loc in offer.get("locations") or [] if isinstance(loc, dict)]
            codes = [_str(offer.get("country_code")).upper()] + [
                _str(loc.get("country_code")).upper() for loc in locations
            ]
            if not any(code in wanted for code in codes):
                continue
            city = _opt(offer.get("city"), 120)
            country_code = _opt(offer.get("country_code"), 2)
            location_text = _opt(offer.get("location"))
            if _str(offer.get("country_code")).upper() not in wanted:
                local = next(
                    loc for loc in locations if _str(loc.get("country_code")).upper() in wanted
                )
                city = _opt(local.get("city") or local.get("name"), 120)
                country_code = _opt(local.get("country_code"), 2)
                parts: dict[str, str] = {}
                for part in (city, _opt(local.get("state")), _opt(local.get("country"))):
                    if part and part.casefold() not in parts:
                        parts[part.casefold()] = part.title() if part.islower() else part
                location_text = ", ".join(parts.values())
                if len(locations) > 1:
                    location_text += f" (+{len(locations) - 1} other locations)"
            external_id = _str(str(offer.get("id") or ""))
            if not external_id:
                continue
            salary = offer.get("salary") or {}
            salary_text = None
            if isinstance(salary, dict) and (salary.get("min") or salary.get("max")):
                bounds = "-".join(
                    str(salary[key]) for key in ("min", "max") if salary.get(key)
                )
                salary_text = " ".join(
                    part
                    for part in (bounds, _str(salary.get("currency")), _str(salary.get("period")))
                    if part
                )[:120]
            jobs.append(
                FetchedJob(
                    source=self.name,
                    source_company=self.company,
                    external_id=external_id,
                    title=_str(offer.get("title")),
                    company=_str(offer.get("company_name")) or self.company,
                    apply_url=_str(offer.get("careers_url"))
                    or _str(offer.get("careers_apply_url")),
                    description=join_sections(
                        (None, offer.get("description")),
                        ("Requirements", offer.get("requirements")),
                    ),
                    version=_str(offer.get("updated_at")),
                    location=location_text,
                    city=city,
                    country_code=country_code,
                    workplace=_workplace(
                        remote=offer.get("remote"), hybrid=offer.get("hybrid")
                    ),
                    employment_type=_opt(offer.get("employment_type_code"), 60),
                    experience_level=_opt(offer.get("experience_code"), 60),
                    category=_opt(offer.get("department") or offer.get("category_code"), 120),
                    posted_at=parse_datetime(offer.get("published_at")),
                    salary_text=salary_text,
                )
            )
        return jobs


@dataclass(slots=True)
class LeverSource(_HttpSource):
    """Lever postings API (public, no key)."""

    country_codes: Sequence[str] = ("TN",)
    country_names: Sequence[str] = ("Tunisia", "Tunisie")
    display_name: str | None = None
    name: str = "lever"

    def fetch(self, known_versions: Mapping[str, str]) -> list[FetchedJob]:
        payload = self._get_json(
            f"https://api.lever.co/v0/postings/{self.company}", {"mode": "json"}
        )
        if not isinstance(payload, list):
            raise JobSourceError("Unexpected Lever payload.")
        wanted_codes = [code.upper() for code in self.country_codes]
        jobs: list[FetchedJob] = []
        for posting in payload:
            if not isinstance(posting, dict):
                continue
            categories = posting.get("categories") or {}
            locations = [categories.get("location"), *(categories.get("allLocations") or [])]
            if _str(posting.get("country")).upper() not in wanted_codes and not _country_match(
                locations, self.country_names
            ):
                continue
            external_id = _str(posting.get("id"))
            if not external_id:
                continue
            location_text = _str(categories.get("location"))
            country_code = _opt(posting.get("country"), 2)
            if _str(posting.get("country")).upper() not in wanted_codes:
                local = next(
                    (
                        value
                        for value in locations
                        if _country_match([value], self.country_names)
                    ),
                    location_text,
                )
                others = len({_str(value) for value in locations if _str(value)}) - 1
                location_text = _str(local) + (
                    f" (+{others} other locations)" if others > 0 else ""
                )
                country_code = wanted_codes[0] if wanted_codes else None
            lists = [
                (_str(item.get("text")) or None, item.get("content"))
                for item in posting.get("lists") or []
                if isinstance(item, dict)
            ]
            jobs.append(
                FetchedJob(
                    source=self.name,
                    source_company=self.company,
                    external_id=external_id,
                    title=_str(posting.get("text")),
                    company=self.display_name or self.company,
                    apply_url=_str(posting.get("hostedUrl")) or _str(posting.get("applyUrl")),
                    description=join_sections(
                        (None, posting.get("description")),
                        *lists,
                        (None, posting.get("additional")),
                    ),
                    version=str(posting.get("updatedAt") or posting.get("createdAt") or ""),
                    location=_opt(location_text),
                    city=_opt(location_text.split(",")[0].split(" (+")[0], 120),
                    country_code=country_code,
                    workplace=_workplace(label=posting.get("workplaceType")),
                    employment_type=_opt(categories.get("commitment"), 60),
                    category=_opt(categories.get("department") or categories.get("team"), 120),
                    posted_at=parse_datetime(posting.get("createdAt")),
                )
            )
        return jobs


@dataclass(slots=True)
class AshbySource(_HttpSource):
    """Ashby public job-board API (no key)."""

    country_names: Sequence[str] = ("Tunisia", "Tunisie", "Tunis")
    display_name: str | None = None
    name: str = "ashby"

    def fetch(self, known_versions: Mapping[str, str]) -> list[FetchedJob]:
        payload = self._get_json(
            f"https://api.ashbyhq.com/posting-api/job-board/{self.company}"
        )
        jobs: list[FetchedJob] = []
        for posting in payload.get("jobs") or []:
            if not isinstance(posting, dict) or posting.get("isListed") is False:
                continue
            address = ((posting.get("address") or {}).get("postalAddress")) or {}
            secondary = [
                value
                for loc in posting.get("secondaryLocations") or []
                if isinstance(loc, dict)
                for value in (
                    loc.get("location"),
                    (((loc.get("address") or {}).get("postalAddress")) or {}).get(
                        "addressCountry"
                    ),
                )
            ]
            if not _country_match(
                [address.get("addressCountry"), posting.get("location"), *secondary],
                self.country_names,
            ):
                continue
            external_id = _str(posting.get("id"))
            if not external_id:
                continue
            jobs.append(
                FetchedJob(
                    source=self.name,
                    source_company=self.company,
                    external_id=external_id,
                    title=_str(posting.get("title")),
                    company=self.display_name or self.company,
                    apply_url=_str(posting.get("jobUrl")) or _str(posting.get("applyUrl")),
                    description=_str(posting.get("descriptionPlain"))
                    or html_to_text(posting.get("descriptionHtml")),
                    version=_str(posting.get("publishedAt")),
                    location=_opt(posting.get("location")),
                    city=_opt(address.get("addressLocality"), 120),
                    country_code="TN" if _country_match([address.get("addressCountry")], self.country_names) else None,
                    workplace=_workplace(
                        remote=posting.get("isRemote"), label=posting.get("workplaceType")
                    ),
                    employment_type=_opt(posting.get("employmentType"), 60),
                    category=_opt(posting.get("department") or posting.get("team"), 120),
                    posted_at=parse_datetime(posting.get("publishedAt")),
                )
            )
        return jobs


@dataclass(slots=True)
class RemotiveSource(_HttpSource):
    """Remotive remote-jobs API. Remotive asks API users to link back to the
    original posting and to fetch at most a few times per day."""

    allowed_locations: Sequence[str] = ("worldwide", "anywhere", "africa", "emea", "tunisia")
    company: str = "remotive"
    name: str = "remotive"

    def fetch(self, known_versions: Mapping[str, str]) -> list[FetchedJob]:
        payload = self._get_json("https://remotive.com/api/remote-jobs")
        jobs: list[FetchedJob] = []
        for posting in payload.get("jobs") or []:
            if not isinstance(posting, dict):
                continue
            region = _str(posting.get("candidate_required_location"))
            if not _country_match([region], [item for item in self.allowed_locations if len(item) > 3]):
                continue
            external_id = _str(str(posting.get("id") or ""))
            if not external_id:
                continue
            jobs.append(
                FetchedJob(
                    source=self.name,
                    source_company=self.company,
                    external_id=external_id,
                    title=_str(posting.get("title")),
                    company=_str(posting.get("company_name")) or "Unknown company",
                    apply_url=_str(posting.get("url")),
                    description=html_to_text(posting.get("description")),
                    version=_str(posting.get("publication_date")),
                    location=f"Remote ({region})" if region else "Remote",
                    workplace="remote",
                    employment_type=_opt(posting.get("job_type"), 60),
                    category=_opt(posting.get("category"), 120),
                    posted_at=parse_datetime(posting.get("publication_date")),
                    salary_text=_opt(posting.get("salary"), 120),
                    company_logo_url=_opt(
                        posting.get("company_logo_url") or posting.get("company_logo"), 1000
                    ),
                )
            )
        return jobs


SOURCE_TYPES: dict[str, Callable[..., JobSource]] = {
    "smartrecruiters": SmartRecruitersSource,
    "recruitee": RecruiteeSource,
    "lever": LeverSource,
    "ashby": AshbySource,
    "remotive": RemotiveSource,
}

SOURCE_LABELS = {
    "smartrecruiters": "SmartRecruiters",
    "recruitee": "Recruitee",
    "lever": "Lever",
    "ashby": "Ashby",
    "remotive": "Remotive",
}


@dataclass(slots=True)
class SourceConfig:
    type: str
    company: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


def load_source_configs(path: str | Path) -> list[SourceConfig]:
    """Load the job-source list from a JSON file."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JobSourceError(f"Could not read job sources from {source}: {exc}") from exc
    configs: list[SourceConfig] = []
    for entry in payload.get("sources") or []:
        if not isinstance(entry, dict) or entry.get("enabled") is False:
            continue
        kind = _str(entry.get("type")).casefold()
        if kind not in SOURCE_TYPES:
            raise JobSourceError(f"Unknown job source type: {kind!r}")
        options = {
            key: value
            for key, value in entry.items()
            if key not in {"type", "company", "enabled", "note", "description"}
        }
        configs.append(SourceConfig(type=kind, company=_opt(entry.get("company"), 120), options=options))
    return configs


def build_http_client(timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> httpx.Client:
    return httpx.Client(
        timeout=timeout_seconds,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )


def build_sources(
    configs: Iterable[SourceConfig],
    client: httpx.Client,
    *,
    request_delay_seconds: float = 0.2,
) -> list[JobSource]:
    sources: list[JobSource] = []
    for config in configs:
        factory = SOURCE_TYPES[config.type]
        kwargs: dict[str, Any] = {
            "client": client,
            "request_delay_seconds": request_delay_seconds,
            **config.options,
        }
        if config.company:
            kwargs["company"] = config.company
        elif config.type != "remotive":
            raise JobSourceError(f"Source type {config.type!r} requires a company.")
        for key in ("country_codes", "country_names", "allowed_locations"):
            if key in kwargs and isinstance(kwargs[key], list):
                kwargs[key] = tuple(kwargs[key])
        sources.append(factory(**kwargs))
    return sources
