"""Retention: each user keeps only their newest CV analyses and saved matches."""

from __future__ import annotations

import csv

import pytest
from fastapi.testclient import TestClient

import app.services.user_records as user_records
from app.api.dependencies import get_api_settings, reset_job_catalog_cache
from app.api.settings import APISettings
from app.main import app
from conftest import register_user
from cv_samples import STRONG_CV
from test_resumes import ROWS


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "job_features.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)
    settings = APISettings(jobs_features_path=path, jobs_catalog_source="dataset")
    app.dependency_overrides[get_api_settings] = lambda: settings
    reset_job_catalog_cache()
    monkeypatch.setattr(user_records, "CV_ANALYSIS_KEEP", 2, raising=False)
    monkeypatch.setattr(user_records, "SAVED_MATCH_KEEP", 2, raising=False)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        register_user(test_client)
        yield test_client
    app.dependency_overrides.clear()


def test_only_the_newest_cv_analyses_are_kept(client) -> None:
    first = client.post("/api/v1/cv/analyze", json={"resume_text": STRONG_CV + "\nVersion one"}).json()
    resume = client.post("/api/v1/resumes", json={})  # built from the first analysis
    assert resume.status_code == 201
    for version in ("two", "three"):
        assert client.post("/api/v1/cv/analyze", json={"resume_text": STRONG_CV + f"\nVersion {version}"}).status_code == 200

    history = client.get("/api/v1/cv/history").json()
    items = history["items"] if isinstance(history, dict) and "items" in history else history
    ids = [item["analysis_id"] if "analysis_id" in item else item["id"] for item in items]
    assert len(ids) == 2, "the oldest analysis is deleted"
    assert first["analysis_id"] not in ids

    # The CV built from the deleted analysis still opens.
    reopened = client.get(f"/api/v1/resumes/{resume.json()['id']}")
    assert reopened.status_code == 200


def test_another_users_history_is_not_pruned(client) -> None:
    for version in ("one", "two"):
        client.post("/api/v1/cv/analyze", json={"resume_text": STRONG_CV + f"\nVersion {version}"})
    other = TestClient(app, raise_server_exceptions=False)
    register_user(other)
    for version in ("a", "b", "c"):
        other.post("/api/v1/cv/analyze", json={"resume_text": STRONG_CV + f"\nOther {version}"})
    mine = client.get("/api/v1/cv/history").json()
    items = mine["items"] if isinstance(mine, dict) and "items" in mine else mine
    assert len(items) == 2
