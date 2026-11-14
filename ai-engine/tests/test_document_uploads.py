from __future__ import annotations

import csv

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_api_settings
from app.api.settings import APISettings
from app.main import app
from app.services.documents.extraction import normalize_extracted_text
from document_factories import make_docx, make_encrypted_pdf, make_pdf

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


@pytest.fixture
def client(client_factory) -> TestClient:
    return client_factory()


def _upload(
    client: TestClient,
    payload: bytes,
    *,
    filename: str,
    content_type: str,
):
    return client.post(
        "/api/v1/cv/analyze/upload",
        files={"file": (filename, payload, content_type)},
        data={"resume_id": "resume-1", "category": "Software Development"},
    )


def test_txt_upload_remains_backward_compatible(client):
    response = _upload(
        client,
        b"Python developer with Docker experience.",
        filename="resume.txt",
        content_type="text/plain",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["skill_count"] >= 2
    assert body["document"] == {
        "filename": "resume.txt",
        "document_type": "text",
        "page_count": None,
        "extraction_status": "success",
        "character_count": 40,
    }


def test_pdf_upload_extracts_text_and_integrates_with_cv_analysis(client):
    payload = make_pdf(
        [
            "Python developer with Docker experience.",
            "Built and maintained backend services.",
        ]
    )
    response = _upload(
        client,
        payload,
        filename="resume.pdf",
        content_type=PDF_MIME,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["document"]["document_type"] == "pdf"
    assert body["document"]["page_count"] == 1
    assert {"Python", "Docker"} <= {
        item["skill"] for item in body["profile"]["skills"]
    }
    assert "Python developer" in body["profile"]["semantic_text"]


def test_docx_upload_extracts_paragraphs_and_tables(client):
    payload = make_docx(
        ["Python developer with Docker experience."],
        [["Python", "Docker"], ["PostgreSQL", "React"]],
    )
    response = _upload(
        client,
        payload,
        filename="resume.docx",
        content_type=DOCX_MIME,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["document"]["document_type"] == "docx"
    assert body["document"]["page_count"] is None
    assert {"Python", "Docker", "PostgreSQL", "React"} <= {
        item["skill"] for item in body["profile"]["skills"]
    }
    assert "Python | Docker" in body["profile"]["semantic_text"]


def test_unicode_cv_content_is_preserved(client):
    response = _upload(
        client,
        "Compétences: Python, Docker\n日本語の職務経歴".encode("utf-8"),
        filename="resume.txt",
        content_type="text/plain; charset=utf-8",
    )

    assert response.status_code == 200
    semantic_text = response.json()["profile"]["semantic_text"]
    assert "Compétences" in semantic_text
    assert "日本語の職務経歴" in semantic_text


def test_empty_document_returns_400(client):
    response = _upload(
        client,
        b"",
        filename="resume.txt",
        content_type="text/plain",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_document"


def test_binary_text_upload_returns_400(client):
    response = _upload(
        client,
        b"Python\x00Docker",
        filename="resume.txt",
        content_type="text/plain",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_document"


def test_corrupted_pdf_returns_400(client):
    response = _upload(
        client,
        b"%PDF-1.7\nthis is not a valid pdf",
        filename="resume.pdf",
        content_type=PDF_MIME,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_document"


def test_corrupted_docx_returns_400(client):
    response = _upload(
        client,
        b"PK\x03\x04this is not a valid docx",
        filename="resume.docx",
        content_type=DOCX_MIME,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_document"


def test_encrypted_pdf_returns_400(client):
    response = _upload(
        client,
        make_encrypted_pdf(),
        filename="resume.pdf",
        content_type=PDF_MIME,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "encrypted_document"


def test_encrypted_docx_signature_returns_400(client):
    response = _upload(
        client,
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1not-a-real-docx",
        filename="resume.docx",
        content_type=DOCX_MIME,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "encrypted_document"


def test_unsupported_format_returns_415(client):
    response = _upload(
        client,
        b"{\\rtf1 Python developer}",
        filename="resume.rtf",
        content_type="application/rtf",
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_document_type"


def test_incorrect_mime_type_returns_415(client):
    response = _upload(
        client,
        make_pdf(["Python developer"]),
        filename="resume.pdf",
        content_type="text/plain",
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "mime_type_mismatch"


def test_oversized_file_returns_413(client_factory):
    client = client_factory(max_upload_bytes=20)
    response = _upload(
        client,
        b"x" * 40,
        filename="resume.txt",
        content_type="text/plain",
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "upload_too_large"


def test_extracted_text_exceeding_limit_returns_413(client_factory):
    client = client_factory(max_text_length=20)
    response = _upload(
        client,
        b"Python developer with Docker experience.",
        filename="resume.txt",
        content_type="text/plain",
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "text_too_large"


def test_image_only_pdf_returns_422_with_ocr_limitation(client):
    response = _upload(
        client,
        make_pdf([]),
        filename="scanned.pdf",
        content_type=PDF_MIME,
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "no_usable_text"
    assert "OCR" in body["error"]["message"]


def test_normalization_preserves_paragraphs_and_removes_controls():
    value = "First line\x00\r\n\r\n\r\nSecond\u00a0line\u200b with spaces"

    assert normalize_extracted_text(value) == "First line\n\nSecond line with spaces"
