from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_api_settings
from app.api.settings import APISettings
from app.main import app
from app.services.llm.mock_provider import MockLLMProvider
import app.api.routes.matching as matching_routes

FIELDNAMES = [
    "job_id",
    "label_clean",
    "clean_description",
    "job_title",
    "required_skills",
    "preferred_skills",
    "other_skills",
    "all_extracted_skills",
    "responsibilities",
    "qualifications",
]

ROWS = [
    {
        "job_id": "JOB_1",
        "label_clean": "Software Development",
        "clean_description": "Build backend services with Python, Docker, and React.",
        "job_title": "Backend Engineer",
        "required_skills": "Python",
        "preferred_skills": "Docker",
        "other_skills": "React",
        "all_extracted_skills": "Python;Docker;React",
        "responsibilities": "Build APIs | Maintain services",
        "qualifications": "3+ years experience | Strong communication",
    },
    {
        "job_id": "JOB_2",
        "label_clean": "Data Science",
        "clean_description": "Analyze data with Python, pandas, and Tableau.",
        "job_title": "",
        "required_skills": "Python;pandas",
        "preferred_skills": "Tableau",
        "other_skills": "",
        "all_extracted_skills": "Python;pandas;Tableau",
        "responsibilities": "Build dashboards",
        "qualifications": "",
    },
    {
        "job_id": "JOB_3",
        "label_clean": "Network Engineering",
        "clean_description": "Administer Linux servers and network infrastructure.",
        "job_title": "Network Administrator",
        "required_skills": "Linux",
        "preferred_skills": "",
        "other_skills": "",
        "all_extracted_skills": "Linux",
        "responsibilities": "",
        "qualifications": "",
    },
    {
        "job_id": "JOB_4",
        "label_clean": "Software Development",
        "clean_description": "General software role focused on Python automation.",
        "job_title": "Automation Engineer",
        "required_skills": "",
        "preferred_skills": "",
        "other_skills": "",
        "all_extracted_skills": "Python",
        "responsibilities": "",
        "qualifications": "",
    },
]


@pytest.fixture
def catalog_path(tmp_path):
    path = tmp_path / "job_features.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(ROWS)
    return path


@pytest.fixture
def client_factory(catalog_path, auth_helper):
    clients: list[TestClient] = []

    def _create(**overrides) -> TestClient:
        options = {"jobs_features_path": catalog_path}
        options.update(overrides)
        settings = APISettings(**options)
        app.dependency_overrides[get_api_settings] = lambda: settings
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        auth_helper(client)
        clients.append(client)
        return client

    yield _create

    for client in clients:
        client.__exit__(None, None, None)
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_factory) -> TestClient:
    return client_factory(
        default_recommendation_limit=3,
        max_recommendation_limit=10,
        default_candidate_pool_size=3,
        max_candidate_pool_size=10,
    )


def test_health_and_request_id(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"]

    response = client.get("/api/v1/health", headers={"X-Request-ID": "request-123"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "request-123"

    response = client.get("/health", headers={"X-Request-ID": "bad value"})
    assert response.headers["x-request-id"] != "bad value"


def test_cv_analyze_extracts_canonical_skills(client):
    response = client.post(
        "/api/v1/cv/analyze",
        json={
            "resume_text": "Python developer using Docker, PostgreSQL, and React.",
            "resume_id": "resume-1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["resume_id"] == "resume-1"
    assert body["skill_count"] >= 3
    assert {"Python", "Docker", "PostgreSQL", "React"} <= {
        item["skill"] for item in body["profile"]["skills"]
    }
    assert body["skills_by_category"]


def test_cv_analyze_validation_does_not_echo_input(client):
    secret = "MY_PRIVATE_RESUME_CONTENT"
    response = client.post(
        "/api/v1/cv/analyze", json={"resume_text": " ", "private": secret}
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert response.json()["error"]["code"] == "validation_error"


def test_cv_upload_accepts_text_and_rejects_unsupported_format(client):
    response = client.post(
        "/api/v1/cv/analyze/upload",
        files={
            "file": (
                "resume.txt",
                io.BytesIO(b"Python developer with Docker experience."),
                "text/plain",
            )
        },
    )
    assert response.status_code == 200
    assert response.json()["skill_count"] >= 2

    response = client.post(
        "/api/v1/cv/analyze/upload",
        files={"file": ("resume.rtf", b"{\\rtf1}", "application/rtf")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_document_type"


def test_cv_upload_rejects_oversized_file(client_factory):
    client = client_factory(max_upload_bytes=20)
    response = client.post(
        "/api/v1/cv/analyze/upload",
        files={"file": ("resume.txt", b"x" * 40, "text/plain")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"


def test_job_list_search_detail_and_not_found(client):
    response = client.get("/api/v1/jobs", params={"limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == len(ROWS)
    assert len(body["jobs"]) == 2

    response = client.get("/api/v1/jobs", params={"category": "Data Science"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["jobs"][0]["job_id"] == "JOB_2"

    response = client.get("/api/v1/jobs", params={"q": "network"})
    assert response.status_code == 200
    assert response.json()["jobs"][0]["job_id"] == "JOB_3"

    response = client.get("/api/v1/jobs/JOB_1")
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["title"] == "Backend Engineer"
    assert body["responsibilities"] == ["Build APIs", "Maintain services"]
    assert body["other_skills"] == ["React"]

    response = client.get("/api/v1/jobs/UNKNOWN")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_match_catalog_job_is_local_and_deterministic(client):
    payload = {
        "resume_text": "Python engineer using Docker and React.",
        "job_id": "JOB_1",
        "include_llm": False,
    }
    first = client.post("/api/v1/matches/job", json=payload)
    second = client.post("/api/v1/matches/job", json=payload)
    assert first.status_code == 200
    body = first.json()
    assert body["job"]["job_id"] == "JOB_1"
    assert body["llm_requested"] is False
    assert body["llm_authorized"] is False
    assert body["match"]["job_id"] == "JOB_1"
    assert body["match"]["overall_compatibility_score"] > 0
    assert body["match"]["recommendation_source"] == "local_deterministic"
    assert (
        body["match"]["overall_compatibility_score"]
        == second.json()["match"]["overall_compatibility_score"]
    )


def test_match_free_text_job_uses_custom_summary(client):
    response = client.post(
        "/api/v1/matches/job",
        json={
            "resume_text": "Python engineer using Docker and PostgreSQL.",
            "job_text": "We need a Python engineer with Docker and PostgreSQL.",
            "job_title": "Platform Engineer",
            "job_category": "Software Development",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["job"]["job_id"] == "custom-job"
    assert body["job"]["title"] == "Platform Engineer"
    assert body["match"]["classification"] in {
        "strong_match",
        "moderate_match",
        "weak_match",
    }


def test_match_requires_exactly_one_job_source(client):
    response = client.post(
        "/api/v1/matches/job",
        json={"resume_text": "Python developer."},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_recommendations_rank_and_filter_jobs(client):
    response = client.post(
        "/api/v1/matches/recommendations",
        json={
            "resume_text": "Python developer using Docker and React.",
            "limit": 2,
            "candidate_pool_size": 4,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_count"] == len(ROWS)
    assert body["evaluated_count"] == len(ROWS)
    assert body["recommendations"][0]["rank"] == 1
    assert body["recommendations"][0]["job"]["job_id"] == "JOB_1"
    assert len(body["recommendations"]) == 2
    scores = [item["match"]["overall_compatibility_score"] for item in body["recommendations"]]
    assert scores == sorted(scores, reverse=True)

    filtered = client.post(
        "/api/v1/matches/recommendations",
        json={
            "resume_text": "Python developer using Docker and React.",
            "job_category": "Data Science",
            "limit": 2,
        },
    )
    assert filtered.status_code == 200
    assert filtered.json()["candidate_count"] == 1
    assert filtered.json()["recommendations"][0]["job"]["job_id"] == "JOB_2"

    missing = client.post(
        "/api/v1/matches/recommendations",
        json={
            "resume_text": "Python developer.",
            "job_category": "Cybersecurity",
            "limit": 2,
        },
    )
    assert missing.status_code == 200
    assert missing.json()["recommendations"] == []
    assert missing.json()["warnings"]


def test_recommendation_limits_are_enforced(client):
    response = client.post(
        "/api/v1/matches/recommendations",
        json={"resume_text": "Python developer.", "limit": 11},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "recommendation_limit_exceeded"

    response = client.post(
        "/api/v1/matches/recommendations",
        json={
            "resume_text": "Python developer.",
            "candidate_pool_size": 11,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "candidate_pool_too_large"


def test_llm_explanation_requires_both_authorizations(
    client_factory, monkeypatch
):
    client = client_factory(allow_remote_llm_analysis=False)
    monkeypatch.setattr(
        matching_routes, "get_llm_provider", lambda: MockLLMProvider()
    )
    payload = {
        "resume_text": "Python engineer using Docker.",
        "job_id": "JOB_1",
        "include_llm": True,
        "allow_remote_analysis": True,
    }
    denied = client.post("/api/v1/matches/job", json=payload)
    assert denied.status_code == 200
    assert denied.json()["llm_authorized"] is False
    assert denied.json()["match"]["explanation"] is None


def test_llm_explanation_cannot_change_local_result(client_factory, monkeypatch):
    client = client_factory(allow_remote_llm_analysis=True)
    monkeypatch.setattr(
        matching_routes, "get_llm_provider", lambda: MockLLMProvider()
    )
    payload = {
        "resume_text": "Python engineer using Docker.",
        "job_id": "JOB_1",
        "include_llm": True,
        "allow_remote_analysis": True,
    }
    response = client.post("/api/v1/matches/job", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["llm_authorized"] is True
    assert body["match"]["explanation"] is not None
    assert body["match"]["overall_compatibility_score"] >= 0
    component = next(
        item
        for item in body["match"]["components"]
        if item["name"] == "openai_explanation"
    )
    assert component["status"] == "success"

    local = client.post(
        "/api/v1/matches/job",
        json={
            "resume_text": payload["resume_text"],
            "job_id": payload["job_id"],
            "include_llm": False,
        },
    )
    assert (
        local.json()["match"]["overall_compatibility_score"]
        == body["match"]["overall_compatibility_score"]
    )


def test_catalog_unavailable_returns_503(client_factory, tmp_path):
    client = client_factory(jobs_features_path=tmp_path / "missing.csv")
    response = client.get("/api/v1/jobs")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "job_catalog_unavailable"
