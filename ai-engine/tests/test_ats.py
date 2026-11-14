from __future__ import annotations

import csv
import io
import zipfile

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.api.dependencies import get_api_settings, reset_job_catalog_cache
from app.api.settings import APISettings
from app.main import app
from app.schemas.hybrid_match import JobRequirement
from app.services.ats import AtsChecker
from app.services.documents.extraction import DocumentExtractionService
from app.services.documents.layout import LayoutSignals, inspect_layout
from cv_samples import FRENCH_CV, STRONG_CV
from document_factories import make_docx, make_pdf

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

@pytest.fixture(scope="module")
def checker() -> AtsChecker:
    return AtsChecker()


def status(report, check_id: str) -> str:
    return next(check.status for check in report.checks if check.id == check_id)


def test_strong_english_cv_scores_well(checker) -> None:
    report = checker.check(STRONG_CV, document_type="pdf", page_count=1)
    assert report.score >= 85 and report.grade == "excellent"
    assert report.language == "en"
    assert {s.id for s in report.sections if s.found} >= {"summary", "experience", "education", "skills", "languages"}
    for check_id in ("email", "phone", "linkedin", "location", "quantified", "action_verbs", "dates"):
        assert status(report, check_id) == "pass", check_id
    assert report.keyword_match is None
    assert all(category.id != "keywords" for category in report.categories)


def test_french_cv_sections_and_verbs_are_recognised(checker) -> None:
    report = checker.check(FRENCH_CV, document_type="docx")
    assert report.language == "fr"
    found = {s.id for s in report.sections if s.found}
    assert {"summary", "experience", "education", "skills", "languages"} <= found
    assert status(report, "action_verbs") == "pass"
    assert status(report, "quantified") == "pass"
    assert status(report, "dates") == "pass"
    assert status(report, "linkedin") == "warning"


def test_weak_cv_gets_failures_and_prioritised_fixes(checker) -> None:
    weak = (
        "MON CV\nje suis une personne motivée et j'aime travailler. J'ai travaillé chez une "
        "entreprise où je faisais la gestion et la vente et je veux maintenant un nouveau travail.\n"
        "Contact: moi@example.com"
    )
    report = checker.check(weak, document_type="text")
    assert report.score < 50 and report.grade == "poor"
    for check_id in ("phone", "section_experience", "section_education", "bullets", "dates"):
        assert status(report, check_id) == "fail", check_id
    assert status(report, "email") == "pass"
    assert status(report, "text_layer") == "pass", "a short CV is not a scanned image"
    assert 1 <= len(report.top_fixes) <= 5
    assert "critical issue" in report.summary


def test_scanned_pdf_and_broken_fonts_fail_the_text_check(checker) -> None:
    scanned = LayoutSignals(document_type="pdf", page_count=2, chars_per_page=40.0)
    assert status(checker.check(STRONG_CV, layout=scanned), "text_layer") == "fail"
    garbled = STRONG_CV + "�" * 200
    assert status(checker.check(garbled, document_type="pdf"), "text_layer") == "fail"


def test_layout_problems_are_reported(checker) -> None:
    layout = LayoutSignals(
        document_type="docx",
        table_count=3,
        table_text_ratio=0.8,
        text_box_count=2,
        image_count=4,
        header_footer_text=True,
        header_footer_contact=True,
    )
    report = checker.check(STRONG_CV, layout=layout)
    assert status(report, "layout") == "fail"
    assert status(report, "images") == "warning"
    assert status(report, "header_footer") == "fail"
    assert report.layout_checked is True


def test_keyword_match_against_job(checker) -> None:
    requirements = [
        JobRequirement(skill=skill, importance=importance, evidence=[skill], confidence=0.8)
        for skill, importance in (
            ("Python", "required"), ("SQL", "required"), ("Apache Spark", "required"),
            ("Tableau", "preferred"), ("Kubernetes", "preferred"),
        )
    ]
    report = checker.check(
        STRONG_CV, document_type="pdf", job_requirements=requirements,
        job_id="j1", job_title="Senior Data Analyst", company="ACME",
    )
    match = report.keyword_match
    assert match is not None and match.job_id == "j1" and match.company == "ACME"
    assert set(match.matched) == {"Python", "SQL", "Tableau"}
    assert match.missing_required == ["Apache Spark"]
    assert match.missing_preferred == ["Kubernetes"]
    assert match.coverage == pytest.approx(0.7 * 2 / 3 + 0.3 * 0.5, abs=1e-3)
    assert match.title_in_cv is True
    assert any(category.id == "keywords" for category in report.categories)
    assert status(report, "job_keywords") == "warning"


def test_keyword_gaps_list_specific_skills_before_generic_ones(checker) -> None:
    requirements = [
        JobRequirement(skill="Communication", category="soft_skill", importance="required", evidence=["x"], confidence=0.8),
        JobRequirement(skill="German", importance="required", evidence=["x"], confidence=0.8),
        JobRequirement(skill="Kubernetes", category="tool_technology", importance="required", evidence=["x"], confidence=0.8),
    ]
    report = checker.check("Python developer.\n" * 40, job_requirements=requirements)
    match = report.keyword_match
    assert match.missing_required == ["Kubernetes", "German", "Communication"]
    fix = next(check.fix for check in report.checks if check.id == "job_keywords")
    assert "Kubernetes" in fix and "Communication" not in fix
    keywords = next(category for category in report.categories if category.id == "keywords")
    assert keywords.score == 0 and report.score < 75


def _docx_with_header_contact() -> bytes:
    document = Document()
    document.sections[0].header.paragraphs[0].text = "amira@example.com · +216 22 345 678"
    for line in STRONG_CV.splitlines():
        document.add_paragraph(line)
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Python", "SQL"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_docx_layout_inspection_reads_headers_and_tables() -> None:
    payload = _docx_with_header_contact()
    document = DocumentExtractionService().extract(
        payload, filename="cv.docx", content_type=DOCX_MIME, max_chars=100_000
    )
    layout = document.layout
    assert layout is not None and layout.document_type == "docx"
    assert layout.table_count == 1 and 0 < layout.table_text_ratio < 0.2
    assert layout.header_footer_contact is True
    assert LayoutSignals.from_dict(layout.to_dict()) == layout


def test_word_bullet_lists_survive_extraction(checker) -> None:
    document = Document()
    document.add_paragraph("EXPERIENCE")
    for text in ("Built 12 dashboards", "Reduced costs by 20%", "Led a team of 5"):
        document.add_paragraph(text, style="List Bullet")
    buffer = io.BytesIO()
    document.save(buffer)
    extracted = DocumentExtractionService().extract(
        buffer.getvalue(), filename="cv.docx", content_type=DOCX_MIME, max_chars=10_000
    )
    assert "- Built 12 dashboards" in extracted.text
    assert checker.check(extracted.text).stats.bullet_count == 3


def test_pdf_layout_inspection_and_failure_tolerance() -> None:
    pdf = make_pdf(["Amira Ben Salah", "amira@example.com", "Experience"])
    layout = inspect_layout(pdf, "pdf", "Amira Ben Salah amira@example.com Experience", 1)
    assert layout.page_count == 1 and layout.font_count == 1 and layout.chars_per_page is not None
    broken = inspect_layout(b"not a pdf", "pdf", "text", 1)
    assert broken.notes == ["layout_inspection_failed"]
    with zipfile.ZipFile(io.BytesIO(make_docx(["Hello"]))) as archive:
        assert "word/document.xml" in archive.namelist()


ROWS = [
    {
        "job_id": "JOB_1",
        "label_clean": "Data Science",
        "clean_description": "Analyse data with Python, SQL and Apache Spark. Tableau is a plus.",
        "job_title": "Data Analyst",
        "required_skills": "Python;SQL;Apache Spark",
        "preferred_skills": "Tableau",
        "other_skills": "",
        "all_extracted_skills": "Python;SQL;Apache Spark;Tableau",
        "responsibilities": "",
        "qualifications": "",
    }
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


def test_ats_endpoint_requires_a_saved_cv(client) -> None:
    response = client.post("/api/v1/cv/ats", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "cv_not_found"


def test_cv_analysis_returns_and_stores_ats_report(client) -> None:
    analysed = client.post("/api/v1/cv/analyze", json={"resume_text": STRONG_CV})
    assert analysed.status_code == 200, analysed.text
    ats = analysed.json()["ats"]
    assert ats["score"] >= 85 and ats["layout_checked"] is False

    latest = client.get("/api/v1/cv/latest").json()
    assert latest["ats"]["score"] == ats["score"]

    against_job = client.post("/api/v1/cv/ats", json={"job_id": "JOB_1"})
    assert against_job.status_code == 200, against_job.text
    match = against_job.json()["keyword_match"]
    assert match["job_title"] == "Data Analyst"
    assert match["missing_required"] == ["Apache Spark"]

    pasted = client.post(
        "/api/v1/cv/ats",
        json={"job_text": "Required: Kubernetes and Terraform.", "job_title": "DevOps Engineer"},
    )
    assert pasted.status_code == 200
    assert set(pasted.json()["keyword_match"]["missing_required"]) == {"Kubernetes", "Terraform"}

    assert client.post("/api/v1/cv/ats", json={"job_id": "NOPE"}).status_code == 404
    both = client.post("/api/v1/cv/ats", json={"job_id": "JOB_1", "job_text": "x"})
    assert both.status_code == 422


def test_docx_upload_ats_report_includes_layout(client) -> None:
    response = client.post(
        "/api/v1/cv/analyze/upload",
        files={"file": ("cv.docx", _docx_with_header_contact(), DOCX_MIME)},
    )
    assert response.status_code == 200, response.text
    ats = response.json()["ats"]
    assert ats["layout_checked"] is True
    checks = {check["id"]: check["status"] for check in ats["checks"]}
    assert checks["header_footer"] == "fail"
    assert checks["layout"] == "warning"

    recheck = client.post("/api/v1/cv/ats", json={"analysis_id": response.json()["analysis_id"]})
    assert {c["id"]: c["status"] for c in recheck.json()["checks"]}["header_footer"] == "fail"
