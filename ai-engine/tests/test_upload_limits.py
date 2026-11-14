from __future__ import annotations

import asyncio
import csv
import logging

import pytest
from fastapi.testclient import TestClient

from app.api import uploads
from app.api.dependencies import get_api_settings
from app.api.errors import UploadValidationError
from app.api.settings import APISettings
from app.main import app
from document_factories import make_docx, make_pdf_pages

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
        "clean_description": "Build backend services with Python and Docker.",
        "job_title": "Backend Engineer",
        "required_skills": "Python",
        "preferred_skills": "Docker",
        "other_skills": "",
        "all_extracted_skills": "Python;Docker",
        "responsibilities": "Build APIs",
        "qualifications": "",
    }
]

PDF_MIME = "application/pdf"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


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


def _upload(client: TestClient, payload: bytes, *, filename: str, content_type: str):
    return client.post(
        "/api/v1/cv/analyze/upload",
        files={"file": (filename, payload, content_type)},
        data={"resume_id": "resume-1", "category": "Software Development"},
    )


def test_pdf_page_limit_returns_413_and_audits(
    client_factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = client_factory(max_pdf_pages=1)
    payload = make_pdf_pages([["Python developer"], ["Docker engineer"]])

    with caplog.at_level(logging.INFO, logger="jobmatch.audit"):
        response = _upload(
            client,
            payload,
            filename="resume.pdf",
            content_type=PDF_MIME,
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "pdf_page_limit_exceeded"
    assert "security.upload.pdf_page_limit_exceeded" in caplog.text


def test_docx_entry_limit_returns_413_and_audits(
    client_factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = client_factory(max_docx_entries=2)
    payload = make_docx(["Python developer with Docker experience."])

    with caplog.at_level(logging.INFO, logger="jobmatch.audit"):
        response = _upload(
            client,
            payload,
            filename="resume.docx",
            content_type=DOCX_MIME,
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "docx_entry_limit_exceeded"
    assert "security.upload.docx_limit_exceeded" in caplog.text


def test_docx_uncompressed_limit_returns_413(client_factory) -> None:
    client = client_factory(max_docx_uncompressed_bytes=1)
    payload = make_docx(["Python developer with Docker experience."])

    response = _upload(
        client,
        payload,
        filename="resume.docx",
        content_type=DOCX_MIME,
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "docx_uncompressed_limit_exceeded"


def test_docx_compression_ratio_limit_returns_413(client_factory) -> None:
    client = client_factory(max_docx_compression_ratio=1)
    payload = make_docx(["Python developer with Docker experience."])

    response = _upload(
        client,
        payload,
        filename="resume.docx",
        content_type=DOCX_MIME,
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "docx_compression_ratio_exceeded"


def test_oversized_upload_audits_rejection(
    client_factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = client_factory(max_upload_bytes=20)

    with caplog.at_level(logging.INFO, logger="jobmatch.audit"):
        response = _upload(
            client,
            b"x" * 40,
            filename="resume.txt",
            content_type="text/plain",
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"
    assert "security.upload.rejected" in caplog.text


def test_upload_spool_is_closed_when_limit_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSpool:
        instances: list["FakeSpool"] = []

        def __init__(self, *args, **kwargs) -> None:
            self.closed = False
            FakeSpool.instances.append(self)

        def __enter__(self) -> "FakeSpool":
            return self

        def __exit__(self, *args) -> None:
            self.closed = True

        def write(self, data: bytes) -> None:
            return None

        def seek(self, *args) -> None:
            return None

        def read(self) -> bytes:
            return b""

    class FakeUpload:
        def __init__(self, chunks: list[bytes]) -> None:
            self._chunks = chunks

        async def read(self, size: int) -> bytes:
            return self._chunks.pop(0) if self._chunks else b""

    monkeypatch.setattr(uploads.tempfile, "SpooledTemporaryFile", FakeSpool)

    with pytest.raises(UploadValidationError) as excinfo:
        asyncio.run(
            uploads._read_limited(
                FakeUpload([b"abc", b"defg"]),
                max_bytes=5,
            )
        )

    assert excinfo.value.code == "upload_too_large"
    assert FakeSpool.instances
    assert FakeSpool.instances[0].closed is True
