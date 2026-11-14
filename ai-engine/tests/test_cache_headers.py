from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/me",
        "/api/v1/account/settings",
        "/api/v1/cv/history",
        "/api/v1/matches/history",
    ],
)
def test_sensitive_endpoints_set_no_store(path: str, auth_helper) -> None:
    with TestClient(app) as client:
        auth_helper(client)
        response = client.get(path)

    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_non_sensitive_endpoint_keeps_default_cache_policy() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.headers.get("Cache-Control") != "no-store"
