from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import reset_job_catalog_cache
from app.api.settings import APISettings, get_api_settings
from app.db.models import JobPosting
from app.db.session import get_session_factory
from app.main import app
from app.services.job_catalog import JobCatalog
from app.services.jobs.sources import (
    AshbySource,
    LeverSource,
    RecruiteeSource,
    RemotiveSource,
    SmartRecruitersSource,
    build_sources,
    load_source_configs,
    parse_datetime,
)
from app.services.jobs.sync import active_postings, sync_jobs
from app.services.jobs.text import html_to_text
from app.services.jobs.types import FetchedJob, JobSourceError, make_posting_id

LONG_TEXT = (
    "<p>Nous recherchons un développeur Python pour notre équipe à Tunis.</p>"
    "<ul><li>Maîtrise de Python, Django et SQL</li><li>Docker est un plus</li></ul>"
    "<p>Anglais courant requis. Travail en équipe.</p>"
)


def _client(routes: dict[str, object], calls: list[str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if calls is not None:
            calls.append(url)
        for prefix, payload in routes.items():
            if url.startswith(prefix):
                if isinstance(payload, int):
                    return httpx.Response(payload)
                return httpx.Response(200, json=payload)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _smartrecruiters_routes(released: str = "2026-11-13T15:20:34.040Z") -> dict:
    base = "https://api.smartrecruiters.com/v1/companies/ACME"
    return {
        f"{base}/postings/111": {
            "applyUrl": "https://jobs.smartrecruiters.com/ACME/111-python-dev",
            "jobAd": {
                "sections": {
                    "companyDescription": {"title": "About", "text": "<p>ACME builds cars.</p>"},
                    "jobDescription": {"title": "Job", "text": LONG_TEXT},
                    "qualifications": {"title": "Profile", "text": "<p>Kubernetes required.</p>"},
                }
            },
        },
        f"{base}/postings": {
            "totalFound": 1,
            "content": [
                {
                    "id": "111",
                    "name": "Développeur Python",
                    "jobAdId": "ad-1",
                    "releasedDate": released,
                    "company": {"name": "ACME"},
                    "location": {
                        "city": "Tunis",
                        "country": "tn",
                        "fullLocation": "Tunis, Tunisia",
                        "remote": False,
                        "hybrid": True,
                    },
                    "typeOfEmployment": {"label": "Full-time"},
                    "experienceLevel": {"label": "Mid-Senior Level"},
                    "function": {"label": "Information Technology"},
                }
            ],
        },
    }


class StaticSource:
    def __init__(self, jobs: list[FetchedJob], *, name="static", company="acme", error=None):
        self.name, self.company = name, company
        self.jobs, self.error = jobs, error
        self.seen_versions: dict[str, str] = {}

    def fetch(self, known_versions):
        self.seen_versions = dict(known_versions)
        if self.error:
            raise JobSourceError(self.error)
        return list(self.jobs)


def _job(external_id: str, *, title="Data Analyst", description=None, version="v1") -> FetchedJob:
    return FetchedJob(
        source="static",
        source_company="acme",
        external_id=external_id,
        title=title,
        company="ACME",
        apply_url=f"https://example.com/jobs/{external_id}",
        description=description
        if description is not None
        else "Analyse de données avec Python, SQL et Power BI. Anglais courant requis. " * 3,
        version=version,
        city="Tunis",
        country_code="tn",
        posted_at=datetime(2026, 11, 1),
    )


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    reset_job_catalog_cache()
    yield
    reset_job_catalog_cache()
    app.dependency_overrides.clear()


def test_html_to_text_keeps_structure_and_decodes_entities() -> None:
    text = html_to_text("<p>Hello&nbsp;&amp; welcome</p><ul><li>One</li><li>Two</li></ul><script>x</script>")
    assert "Hello & welcome" in text
    assert "- One" in text and "- Two" in text
    assert "x" not in text.split()


def test_parse_datetime_handles_source_formats() -> None:
    assert parse_datetime("2026-11-13T15:20:34.040Z") == datetime(2026, 11, 13, 15, 20, 34, 40000)
    assert parse_datetime("2026-11-13 14:48:07 UTC") == datetime(2026, 11, 13, 14, 48, 7)
    assert parse_datetime(1767782276982).year == 2026
    assert parse_datetime("not a date") is None


def test_posting_ids_are_url_safe_and_bounded() -> None:
    assert make_posting_id("lever", "Yassir", "561a-27") == "lever-yassir-561a-27"
    long_id = make_posting_id("ashby", "x" * 200, "id")
    assert len(long_id) <= 120 and "/" not in long_id


def test_smartrecruiters_fetches_details_only_for_new_versions() -> None:
    calls: list[str] = []
    source = SmartRecruitersSource(client=_client(_smartrecruiters_routes(), calls), company="ACME")

    jobs = source.fetch({})
    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Développeur Python"
    assert job.workplace == "hybrid" and job.city == "Tunis" and job.country_code == "tn"
    assert job.apply_url.endswith("111-python-dev")
    assert "Django" in (job.description or "")
    assert "ACME builds cars" not in (job.description or "")
    assert "Kubernetes" in (job.skills_text or "")
    assert any("/postings/111" in url for url in calls)

    calls.clear()
    unchanged = source.fetch({"111": job.version})
    assert unchanged[0].description is None
    assert not any("/postings/111" in url for url in calls)


def test_recruitee_lever_and_ashby_filter_to_tunisia() -> None:
    recruitee = RecruiteeSource(
        client=_client(
            {
                "https://ozeol.recruitee.com/api/offers/": {
                    "offers": [
                        {"id": 1, "title": "Prospector", "status": "published", "country_code": "TN",
                         "city": "sousse", "description": LONG_TEXT, "careers_url": "https://ozeol.recruitee.com/o/p",
                         "company_name": "Ozeol", "remote": False, "hybrid": False, "published_at": "2026-11-13 14:48:07 UTC"},
                        {"id": 2, "title": "Paris role", "status": "published", "country_code": "FR",
                         "description": LONG_TEXT, "careers_url": "https://ozeol.recruitee.com/o/q"},
                    ]
                }
            }
        ),
        company="ozeol",
    )
    lever = LeverSource(
        client=_client(
            {
                "https://api.lever.co/v0/postings/Yassir": [
                    {"id": "a", "text": "Account Manager", "country": "TN", "description": LONG_TEXT,
                     "categories": {"location": "Tunis, Tunisia"}, "hostedUrl": "https://jobs.lever.co/Yassir/a",
                     "workplaceType": "onsite", "createdAt": 1767782276982},
                    {"id": "b", "text": "Algiers", "country": "DZ", "categories": {"location": "Algiers"},
                     "hostedUrl": "https://jobs.lever.co/Yassir/b"},
                    {"id": "c", "text": "HR Specialist", "country": "DZ", "description": LONG_TEXT,
                     "categories": {"location": "Algiers, Algeria",
                                    "allLocations": ["Algiers, Algeria", "Agadir", "Tunis, Tunisia"]},
                     "hostedUrl": "https://jobs.lever.co/Yassir/c"},
                ]
            }
        ),
        company="Yassir",
    )
    ashby = AshbySource(
        client=_client(
            {
                "https://api.ashbyhq.com/posting-api/job-board/enpal": {
                    "jobs": [
                        {"id": "x", "title": "Sales", "location": "Tunis", "isListed": True,
                         "address": {"postalAddress": {"addressCountry": "Tunisia", "addressLocality": "Tunis"}},
                         "jobUrl": "https://jobs.ashbyhq.com/enpal/x", "descriptionPlain": "Sales role " * 20,
                         "workplaceType": "OnSite"},
                        {"id": "y", "title": "Berlin", "location": "Berlin", "isListed": True,
                         "jobUrl": "https://jobs.ashbyhq.com/enpal/y"},
                    ]
                }
            }
        ),
        company="enpal",
    )

    assert [job.title for job in recruitee.fetch({})] == ["Prospector"]
    lever_jobs = lever.fetch({})
    assert [job.title for job in lever_jobs] == ["Account Manager", "HR Specialist"]
    assert lever_jobs[0].workplace == "onsite"
    multi = lever_jobs[1]
    assert (multi.city, multi.country_code) == ("Tunis", "TN")
    assert multi.location == "Tunis, Tunisia (+2 other locations)"
    ashby_jobs = ashby.fetch({})
    assert [job.title for job in ashby_jobs] == ["Sales"]
    assert ashby_jobs[0].country_code == "TN"


def test_remotive_keeps_only_regions_open_to_tunisia() -> None:
    source = RemotiveSource(
        client=_client(
            {
                "https://remotive.com/api/remote-jobs": {
                    "jobs": [
                        {"id": 1, "title": "Full-Stack Developer", "company_name": "Fluence",
                         "url": "https://remotive.com/remote-jobs/software-dev/fsd-1",
                         "candidate_required_location": "Worldwide", "description": LONG_TEXT},
                        {"id": 2, "title": "US only", "company_name": "Co",
                         "url": "https://remotive.com/remote-jobs/x-2",
                         "candidate_required_location": "USA", "description": LONG_TEXT},
                    ]
                }
            }
        )
    )
    jobs = source.fetch({})
    assert [job.title for job in jobs] == ["Full-Stack Developer"]
    assert jobs[0].workplace == "remote" and jobs[0].apply_url.startswith("https://remotive.com/")


def test_source_http_errors_raise_job_source_error() -> None:
    source = LeverSource(client=_client({"https://api.lever.co/": 503}), company="Yassir")
    with pytest.raises(JobSourceError):
        source.fetch({})


def test_load_source_configs_and_build(tmp_path) -> None:
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {"type": "smartrecruiters", "company": "ACME", "country": "tn"},
                    {"type": "lever", "company": "Off", "enabled": False},
                    {"type": "remotive"},
                ]
            }
        ),
        encoding="utf-8",
    )
    configs = load_source_configs(path)
    assert [config.type for config in configs] == ["smartrecruiters", "remotive"]
    sources = build_sources(configs, _client({}), request_delay_seconds=0)
    assert isinstance(sources[0], SmartRecruitersSource) and sources[0].company == "ACME"

    path.write_text(json.dumps({"sources": [{"type": "scraper"}]}), encoding="utf-8")
    with pytest.raises(JobSourceError):
        load_source_configs(path)


def test_sync_creates_updates_closes_and_purges() -> None:
    first = StaticSource([_job("1"), _job("2", title="Comptable")])
    with get_session_factory()() as db:
        result = sync_jobs(db, [first], now=datetime(2026, 11, 10))
        assert result.active_total == 2
        assert result.sources[0].created == 2
        row = db.get(JobPosting, make_posting_id("static", "acme", "1"))
        assert "Python" in row.required_skills + row.preferred_skills + row.other_skills
        assert row.city == "Tunis" and row.country_code == "TN"

    second = StaticSource(
        [
            _job("1", description=None),
            _job("3", title="Quality Engineer", description="AMDEC, 8D et ISO 9001 exigés. " * 5),
        ]
    )
    with get_session_factory()() as db:
        result = sync_jobs(db, [second], now=datetime(2026, 11, 11))
        outcome = result.sources[0]
        assert second.seen_versions == {"1": "v1", "2": "v1"}
        assert (outcome.created, outcome.unchanged, outcome.closed) == (1, 1, 1)
        assert result.active_total == 2
        closed = db.get(JobPosting, make_posting_id("static", "acme", "2"))
        assert closed.is_active is False and closed.closed_at == datetime(2026, 11, 11)

    with get_session_factory()() as db:
        result = sync_jobs(
            db, [StaticSource([], error="temporarily down")], now=datetime(2026, 12, 30)
        )
        assert result.sources[0].error == "temporarily down"
        assert result.purged == 1
        assert result.active_total == 2, "a failing source must not close postings"
        assert db.get(JobPosting, make_posting_id("static", "acme", "2")) is None


def test_sync_dry_run_and_skips_invalid_postings() -> None:
    bad = _job("x", description="too short")
    no_link = _job("y")
    no_link.apply_url = "javascript:alert(1)"
    with get_session_factory()() as db:
        result = sync_jobs(db, [StaticSource([_job("1"), bad, no_link])], dry_run=True)
        assert result.sources[0].created == 1 and result.sources[0].skipped == 2
        assert result.active_total == 0
        assert active_postings(db) == []


def test_catalog_from_postings_supports_filters_and_facets() -> None:
    with get_session_factory()() as db:
        sync_jobs(
            db,
            [
                StaticSource(
                    [
                        _job("1"),
                        FetchedJob(
                            source="static", source_company="acme", external_id="2",
                            title="Remote QA", company="Other", apply_url="https://e.com/2",
                            description="Test automation with Selenium and Python. " * 4,
                            version="v", workplace="remote", city="Sousse",
                        ),
                    ]
                )
            ],
        )
        catalog = JobCatalog.from_postings(active_postings(db))
    assert len(catalog) == 2
    assert catalog.list_jobs(workplace="remote")[1] == 1
    assert catalog.list_jobs(location="tunis")[1] == 1
    assert catalog.list_jobs(location="tun")[1] == 0
    assert catalog.list_jobs(company="other")[1] == 1
    assert catalog.list_jobs(query="python sql")[1] == 1
    facets = catalog.facets()
    assert ("Tunis", 1) in facets["cities"] and ("remote", 1) in facets["workplaces"]
    assert catalog.embedder.is_fitted


def test_jobs_api_serves_live_postings(tmp_path) -> None:
    with get_session_factory()() as db:
        sync_jobs(db, [StaticSource([_job("1"), _job("2", title="Comptable")])])

    settings = APISettings(jobs_catalog_source="database", jobs_features_path=tmp_path / "none.csv")
    app.dependency_overrides[get_api_settings] = lambda: settings
    with TestClient(app) as client:
        listing = client.get("/api/v1/jobs", params={"location": "Tunis"})
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["total"] == 2
        job = body["jobs"][0]
        assert job["company"] == "ACME" and job["apply_url"].startswith("https://example.com/")
        assert job["source"] == "static" and job["posted_at"].startswith("2026-11-01")

        facets = client.get("/api/v1/jobs/facets").json()
        assert facets["live"] is True and facets["total"] == 2

        detail = client.get(f"/api/v1/jobs/{job['job_id']}")
        assert detail.status_code == 200
        assert "Power BI" in detail.json()["description"]


def test_auto_mode_falls_back_to_dataset_until_jobs_are_synced(tmp_path) -> None:
    settings = APISettings(jobs_catalog_source="auto")
    app.dependency_overrides[get_api_settings] = lambda: settings
    with TestClient(app) as client:
        facets = client.get("/api/v1/jobs/facets").json()
        assert facets["live"] is False and facets["total"] > 0


def test_settings_reject_unknown_catalog_source() -> None:
    from app.api.settings import APIConfigurationError

    with pytest.raises(APIConfigurationError):
        APISettings.from_env(
            {
                "APP_ENV": "test",
                "JWT_SECRET_KEY": "test-only-jwt-secret-key-with-at-least-32-characters",
                "JOBS_CATALOG_SOURCE": "scrape",
            }
        )
