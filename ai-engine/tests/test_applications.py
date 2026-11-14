from __future__ import annotations

import csv

import pytest
from fastapi.testclient import TestClient

import app.api.routes.applications as application_routes
from app.api.dependencies import get_api_settings, reset_job_catalog_cache
from app.api.settings import APISettings
from app.main import app
from cv_samples import STRONG_CV

ROWS = [
    {
        "job_id": job_id,
        "label_clean": "Data Science",
        "clean_description": description,
        "job_title": title,
        "required_skills": required,
        "preferred_skills": "",
        "other_skills": "",
        "all_extracted_skills": required,
        "responsibilities": "",
        "qualifications": "",
    }
    for job_id, title, required, description in (
        ("JOB_1", "Data Analyst", "Python;SQL;Power BI", "Analyse data with Python, SQL and Power BI dashboards."),
        ("JOB_2", "Welder", "Welding", "Weld steel structures in a workshop."),
    )
]


@pytest.fixture
def client(tmp_path, auth_helper):
    path = tmp_path / "job_features.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)
    settings = APISettings(jobs_features_path=path, jobs_catalog_source="dataset")
    app.dependency_overrides[get_api_settings] = lambda: settings
    reset_job_catalog_cache()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        auth_helper(test_client)
        yield test_client
    app.dependency_overrides.clear()


def test_match_scores_need_a_cv_and_rank_relevant_jobs(client) -> None:
    empty = client.post("/api/v1/matches/scores", json={"job_ids": ["JOB_1"]})
    assert empty.status_code == 200 and empty.json() == {"has_cv": False, "scores": []}

    client.post("/api/v1/cv/analyze", json={"resume_text": STRONG_CV})
    body = client.post("/api/v1/matches/scores", json={"job_ids": ["JOB_1", "JOB_2", "NOPE", "JOB_1"]}).json()
    assert body["has_cv"] is True
    scores = {item["job_id"]: item["score"] for item in body["scores"]}
    assert set(scores) == {"JOB_1", "JOB_2"}
    assert scores["JOB_1"] > scores["JOB_2"]

    too_many = client.post("/api/v1/matches/scores", json={"job_ids": [f"J{i}" for i in range(51)]})
    assert too_many.status_code == 422


def test_tracker_lifecycle(client) -> None:
    client.post("/api/v1/cv/analyze", json={"resume_text": STRONG_CV})
    created = client.post("/api/v1/applications", json={"job_id": "JOB_1"})
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["status"] == "saved" and item["title"] == "Data Analyst"
    assert item["applied_at"] is None and item["match_score"] > 0.5
    assert item["job_active"] is True

    again = client.post("/api/v1/applications", json={"job_id": "JOB_1"})
    assert again.json()["id"] == item["id"], "saving twice returns the existing entry"

    applied = client.patch(f"/api/v1/applications/{item['id']}", json={"status": "applied", "notes": "  Sent via site  "})
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied" and applied.json()["applied_at"] is not None
    assert applied.json()["notes"] == "Sent via site"

    interview = client.patch(f"/api/v1/applications/{item['id']}", json={"status": "interview"}).json()
    assert interview["applied_at"] == applied.json()["applied_at"]
    assert interview["notes"] == "Sent via site", "fields not sent are left unchanged"

    listing = client.get("/api/v1/applications").json()
    assert listing["counts"]["interview"] == 1 and listing["counts"]["saved"] == 0
    assert client.get("/api/v1/applications").headers["cache-control"] == "no-store"

    assert client.delete(f"/api/v1/applications/{item['id']}").status_code == 204
    assert client.get("/api/v1/applications").json()["items"] == []


def test_tracker_validation_and_isolation(client, auth_helper) -> None:
    assert client.post("/api/v1/applications", json={"job_id": "NOPE"}).status_code == 404
    created = client.post("/api/v1/applications", json={"job_id": "JOB_2", "status": "applied"}).json()
    assert created["applied_at"] is not None and created["match_score"] is None
    assert client.patch(f"/api/v1/applications/{created['id']}", json={"status": "hired"}).status_code == 422
    assert client.patch(f"/api/v1/applications/{created['id']}", json={"resume_id": "missing"}).status_code == 404

    resume = client.post("/api/v1/resumes", json={"blank": True}).json()
    linked = client.patch(f"/api/v1/applications/{created['id']}", json={"resume_id": resume["id"]})
    assert linked.json()["resume_id"] == resume["id"]

    with TestClient(app, raise_server_exceptions=False) as other:
        auth_helper(other)
        assert other.get("/api/v1/applications").json()["items"] == []
        assert other.patch(f"/api/v1/applications/{created['id']}", json={"status": "offer"}).status_code == 404
        assert other.delete(f"/api/v1/applications/{created['id']}").status_code == 404


def test_tracker_limit(client, monkeypatch) -> None:
    monkeypatch.setattr(application_routes, "MAX_APPLICATIONS_PER_USER", 1)
    assert client.post("/api/v1/applications", json={"job_id": "JOB_1"}).status_code == 201
    blocked = client.post("/api/v1/applications", json={"job_id": "JOB_2"})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "application_limit_reached"


def test_cv_history_includes_ats_score(client) -> None:
    client.post("/api/v1/cv/analyze", json={"resume_text": STRONG_CV})
    items = client.get("/api/v1/cv/history").json()["items"]
    assert items[0]["ats_score"] >= 85
