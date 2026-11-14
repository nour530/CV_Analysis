from __future__ import annotations

import asyncio
import json
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.settings import get_api_settings
from app.core.body_limit import BodySizeLimitMiddleware
from app.main import create_app


def _inner_app() -> FastAPI:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        body = await request.body()
        return {"size": len(body)}

    return app


def _scope(*, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/echo",
        "raw_path": b"/echo",
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


def _run_asgi(
    messages: list[dict],
    *,
    limit: int,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int | None, dict]:
    app = BodySizeLimitMiddleware(_inner_app(), max_body_bytes=limit)
    sent: list[dict] = []

    async def receive() -> dict:
        return messages.pop(0)

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(app(_scope(headers=headers), receive, send))
    start = next(item for item in sent if item["type"] == "http.response.start")
    body = b"".join(
        item.get("body", b"") for item in sent if item["type"] == "http.response.body"
    )
    return start["status"], json.loads(body) if body else {}


def test_declared_content_length_over_limit_is_rejected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = TestClient(
        BodySizeLimitMiddleware(_inner_app(), max_body_bytes=10)
    )
    with caplog.at_level(logging.INFO, logger="jobmatch.audit"):
        response = client.post("/echo", content=b"x" * 20)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert "security.request_body.rejected" in caplog.text
    assert "content_length" in caplog.text


def test_body_within_limit_is_accepted() -> None:
    client = TestClient(
        BodySizeLimitMiddleware(_inner_app(), max_body_bytes=10)
    )
    response = client.post("/echo", content=b"x" * 10)

    assert response.status_code == 200
    assert response.json() == {"size": 10}


def test_chunked_body_over_limit_is_rejected() -> None:
    status, payload = _run_asgi(
        [
            {"type": "http.request", "body": b"x" * 6, "more_body": True},
            {"type": "http.request", "body": b"x" * 6, "more_body": False},
        ],
        limit=10,
    )

    assert status == 413
    assert payload["error"]["code"] == "request_too_large"


def test_misleading_content_length_cannot_bypass_stream_limit() -> None:
    status, payload = _run_asgi(
        [
            {"type": "http.request", "body": b"x" * 6, "more_body": True},
            {"type": "http.request", "body": b"x" * 6, "more_body": False},
        ],
        limit=10,
        headers=[(b"content-length", b"4")],
    )

    assert status == 413
    assert payload["error"]["code"] == "request_too_large"


def test_rejection_keeps_security_headers_and_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "1024")
    get_api_settings.cache_clear()
    try:
        custom_app = create_app()
    finally:
        get_api_settings.cache_clear()

    client = TestClient(custom_app)
    response = client.post(
        "/api/v1/auth/login",
        content=b"x" * 2048,
        headers={
            "Content-Type": "application/json",
            "Origin": "http://localhost:4200",
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert response.headers["X-Request-ID"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:4200"


def test_non_http_scope_is_passed_through() -> None:
    calls: list[dict] = []

    async def inner(scope, receive, send):
        calls.append(scope)

    async def receive() -> dict:
        return {"type": "lifespan.startup"}

    async def send(message: dict) -> None:
        return None

    asyncio.run(
        BodySizeLimitMiddleware(inner, max_body_bytes=10)(
            {"type": "lifespan"}, receive, send
        )
    )

    assert calls == [{"type": "lifespan"}]
