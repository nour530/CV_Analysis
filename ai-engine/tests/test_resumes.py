from __future__ import annotations

import csv
import io

import pytest
from docx import Document
from fastapi.testclient import TestClient

import app.api.routes.resumes as resume_routes
from app.api.dependencies import get_api_settings, reset_job_catalog_cache
from app.api.settings import APISettings
from app.main import app
from app.schemas.cv_document import CvDocument, CvExperience
from app.services.cv_builder.parser import parse_cv_text
from app.services.cv_builder.render import render_docx, render_text
from app.services.cv_builder.suggestions import SuggestionService
from app.services.documents.extraction import DocumentExtractionService
from app.services.llm.mock_provider import MockLLMProvider
from cv_samples import FRENCH_CV, STRONG_CV

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


# -- parser and renderer ---------------------------------------------------------------


def test_parser_extracts_structured_english_cv() -> None:
    doc = parse_cv_text(STRONG_CV)
    assert doc.contact.full_name == "Amira Ben Salah"
    assert doc.contact.email == "amira.bensalah@example.com"
    assert doc.contact.phone == "+216 22 345 678"
    assert doc.contact.linkedin == "linkedin.com/in/amira-bensalah"
    assert doc.contact.location == "Tunis, Tunisia"
    assert doc.summary.startswith("Data analyst with 4 years")
    first, second = doc.experience[:2]
    assert (first.title, first.company, first.start, first.current) == ("Data Analyst", "Ooredoo", "Mar 2022", True)
    assert (second.title, second.company, second.start, second.end) == ("Junior Analyst", "Monoprix", "Jul 2020", "Feb 2022")
    assert len(first.bullets) == 4 and first.bullets[0].startswith("Built 12 Power BI")
    assert [(e.degree, e.school) for e in doc.education] == [
        ("Master in Business Intelligence", "ESSEC Tunis"),
        ("Bachelor in Computer Science", "ISG Tunis"),
    ]
    assert {"Python", "SQL", "Power BI"} <= set(doc.skills)
    assert [(l.name, l.level) for l in doc.languages] == [
        ("Arabic", "native"), ("French", "fluent"), ("English", "fluent (C1)"),
    ]


def test_parser_handles_french_cv() -> None:
    doc = parse_cv_text(FRENCH_CV)
    assert doc.contact.full_name == "Youssef Trabelsi"
    assert doc.experience[0].title == "Ingénieur qualité"
    assert doc.experience[0].company == "Câblerie Sud"
    assert doc.experience[0].current is True
    assert doc.experience[1].end == "Déc 2020"
    assert doc.education[0].school == "ENIS"
    assert "AMDEC" in doc.skills
    assert doc.languages[1].name == "Français"


def test_docx_export_is_ats_safe_and_round_trips() -> None:
    doc = parse_cv_text(STRONG_CV)
    payload = render_docx(doc, language="en", template="modern")
    extracted = DocumentExtractionService().extract(payload, filename="cv.docx", content_type=None, max_chars=100_000)
    layout = extracted.layout
    assert layout.table_count == 0 and layout.text_box_count == 0 and layout.image_count == 0
    assert not layout.header_footer_contact
    assert "PROFESSIONAL EXPERIENCE" in extracted.text
    assert "Mar 2022 – Present" in extracted.text
    reparsed = parse_cv_text(extracted.text)
    assert reparsed.experience[0].bullets == doc.experience[0].bullets
    french = render_text(parse_cv_text(FRENCH_CV), "fr")
    assert "EXPÉRIENCE PROFESSIONNELLE" in french and "Aujourd'hui" in french


# -- suggestions -----------------------------------------------------------------------


def test_rule_suggestions_are_factual() -> None:
    doc = CvDocument(
        contact={"headline": "Data Analyst"},
        experience=[
            CvExperience(title="Data Analyst", company="ACME", start="Jan 2020", current=True,
                         bullets=["I built reports in Python and SQL", "Responsible for dashboards in Tableau"]),
        ],
        skills=["Excel"],
    )
    service = SuggestionService()
    suggestions = service.rules(doc, language="en")
    by_field = {}
    for item in suggestions:
        by_field.setdefault(item.field, []).append(item)
    summary = by_field["summary"][0].suggestion
    assert summary.startswith("Data Analyst with") and "years of experience" in summary
    assert by_field["bullet"][0].suggestion == "Built reports in Python and SQL"
    tip = next(item for item in by_field["tip"] if item.index == 1)
    assert "action verb" in tip.reason and "figure" in tip.reason
    assert set(by_field["skills"][0].add_skills) >= {"Python", "SQL", "Tableau"}


def test_ai_suggestions_are_validated() -> None:
    doc = parse_cv_text(STRONG_CV)
    service = SuggestionService()
    text, items = service.build_ai_input(doc, scope="experience", experience_id=None, job_requirements=None, job_title=None)
    assert "amira.bensalah@example.com" not in text and "+216" not in text
    exp = doc.experience[1]
    raw = [
        (f"exp:{exp.id}:1", "Produced weekly KPI reports for [N] regional managers", "Adds scope"),
        (f"exp:{exp.id}:2", "Migrated reporting to Microsoft Azure and Snowflake, saving 25 hours a month", "Result"),
        (f"exp:{exp.id}:3", exp.bullets[3], "unchanged"),
        ("exp:unknown:0", "Something", "bad ref"),
        (f"exp:{exp.id}:0", "Call me on +216 22 345 678", "leak"),
    ]
    validated = service.validate_ai(doc, raw, items, language="en")
    assert [item.index for item in validated] == [1, 2]
    placeholder, invented = validated
    assert any("[N]" in warning for warning in placeholder.warnings)
    assert any("25" in warning for warning in invented.warnings)
    assert any("Snowflake" in warning for warning in invented.warnings)
    assert invented.original == exp.bullets[2]


# -- API -------------------------------------------------------------------------------


@pytest.fixture
def settings_factory(tmp_path):
    path = tmp_path / "job_features.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)

    def _make(**overrides) -> APISettings:
        return APISettings(jobs_features_path=path, jobs_catalog_source="dataset", **overrides)

    return _make


@pytest.fixture
def client(settings_factory, auth_helper):
    settings = settings_factory()
    app.dependency_overrides[get_api_settings] = lambda: settings
    reset_job_catalog_cache()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        auth_helper(test_client)
        yield test_client
    app.dependency_overrides.clear()


def _create_from_cv(client: TestClient, text: str = STRONG_CV) -> dict:
    assert client.post("/api/v1/cv/analyze", json={"resume_text": text}).status_code == 200
    created = client.post("/api/v1/resumes", json={})
    assert created.status_code == 201, created.text
    return created.json()


def test_create_edit_export_and_delete_resume(client) -> None:
    resume = _create_from_cv(client)
    assert resume["title"] == "Amira Ben Salah"
    assert resume["language"] == "en" and resume["ats_score"] >= 85
    assert resume["content"]["experience"][0]["company"] == "Ooredoo"

    content = resume["content"]
    content["summary"] = "Data analyst."
    content["experience"][0]["bullets"].append("Mentored 3 junior analysts")
    saved = client.put(f"/api/v1/resumes/{resume['id']}", json={"content": content, "template": "compact", "title": "Data CV"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["template"] == "compact" and saved.json()["title"] == "Data CV"
    assert "Mentored 3 junior analysts" in client.get(f"/api/v1/resumes/{resume['id']}").json()["content"]["experience"][0]["bullets"]

    listing = client.get("/api/v1/resumes").json()["items"]
    assert [item["id"] for item in listing] == [resume["id"]]

    export = client.get(f"/api/v1/resumes/{resume['id']}/export.docx")
    assert export.status_code == 200
    assert export.headers["content-disposition"] == 'attachment; filename="Amira_Ben_Salah_CV.docx"'
    assert export.headers["cache-control"] == "no-store"
    text = "\n".join(p.text for p in Document(io.BytesIO(export.content)).paragraphs)
    assert "Mentored 3 junior analysts" in text

    ats = client.post(f"/api/v1/resumes/{resume['id']}/ats", json={"job_id": "JOB_1"}).json()
    assert ats["layout_checked"] is True
    assert ats["keyword_match"]["missing_required"] == ["Apache Spark"]

    assert client.delete(f"/api/v1/resumes/{resume['id']}").status_code == 204
    assert client.get(f"/api/v1/resumes/{resume['id']}").status_code == 404


def test_blank_resume_and_isolation_between_users(client, auth_helper) -> None:
    blank = client.post("/api/v1/resumes", json={"blank": True, "language": "fr"})
    assert blank.status_code == 201
    assert blank.json()["content"]["experience"] == [] and blank.json()["language"] == "fr"

    with TestClient(app, raise_server_exceptions=False) as other:
        auth_helper(other)
        assert other.get(f"/api/v1/resumes/{blank.json()['id']}").status_code == 404
        assert other.delete(f"/api/v1/resumes/{blank.json()['id']}").status_code == 404
        assert other.get("/api/v1/resumes").json()["items"] == []


def test_resume_limit(client, monkeypatch) -> None:
    monkeypatch.setattr(resume_routes, "MAX_RESUMES_PER_USER", 2)
    assert client.post("/api/v1/resumes", json={"blank": True}).status_code == 201
    assert client.post("/api/v1/resumes", json={"blank": True}).status_code == 201
    third = client.post("/api/v1/resumes", json={"blank": True})
    assert third.status_code == 409 and third.json()["error"]["code"] == "resume_limit_reached"


def _enable_user_consent(client: TestClient) -> None:
    response = client.put(
        "/api/v1/account/settings",
        json={"recommendation_limit": 10, "candidate_pool_size": 30, "allow_remote_analysis": True},
    )
    assert response.status_code == 200, response.text


def test_suggestions_fall_back_to_rules_until_every_gate_allows_ai(client, settings_factory, monkeypatch) -> None:
    resume = _create_from_cv(client)
    calls: list[str] = []

    class SpyProvider(MockLLMProvider):
        def improve_cv(self, items_text, *, language="en"):
            calls.append(items_text)
            return super().improve_cv(items_text, language=language)

    monkeypatch.setattr(resume_routes, "get_llm_provider", lambda: SpyProvider())
    url = f"/api/v1/resumes/{resume['id']}/suggestions"

    off = client.post(url, json={"allow_remote_analysis": False}).json()
    assert off["source"] == "rules" and "Turn on AI" in off["notice"]

    server_off = client.post(url, json={"allow_remote_analysis": True}).json()
    assert server_off["source"] == "rules" and "turned off on this server" in server_off["notice"]

    settings = settings_factory(allow_remote_llm_analysis=True)
    app.dependency_overrides[get_api_settings] = lambda: settings
    no_consent = client.post(url, json={"allow_remote_analysis": True}).json()
    assert no_consent["source"] == "rules" and "Settings" in no_consent["notice"]
    assert calls == []

    _enable_user_consent(client)
    failed = client.post(url, json={"allow_remote_analysis": True}).json()
    assert failed["source"] == "rules" and "did not respond" in failed["notice"]
    assert len(calls) == 1 and "amira.bensalah@example.com" not in calls[0]

    exp_id = resume["content"]["experience"][0]["id"]
    monkeypatch.setattr(
        resume_routes,
        "get_llm_provider",
        lambda: MockLLMProvider(
            {
                "cv_improvement": {
                    "suggestions": [
                        {"ref": f"exp:{exp_id}:3", "text": "Trained 20 business users on self-service Power BI reporting", "reason": "Names the tool"},
                        {"ref": "summary", "text": "Data analyst with 4 years of experience in retail and telecom, building Power BI and SQL reporting.", "reason": "Adds strengths"},
                    ]
                }
            }
        ),
    )
    ai = client.post(url, json={"allow_remote_analysis": True, "job_id": "JOB_1"}).json()
    assert ai["source"] == "ai" and ai["job_title"] == "Data Analyst"
    rewrites = [item for item in ai["suggestions"] if item["suggestion"] and item["field"] in ("bullet", "summary")]
    assert {item["field"] for item in rewrites} == {"bullet", "summary"}
    bullet = next(item for item in rewrites if item["field"] == "bullet")
    assert bullet["target_id"] == exp_id and bullet["index"] == 3
    assert bullet["original"] == "Trained 20 business users on self-service reporting"
    assert any("check every word" in warning for warning in bullet["warnings"])
    job_skills = next(item for item in ai["suggestions"] if item["field"] == "skills" and "Apache Spark" in item["add_skills"])
    assert "only if you really have them" in job_skills["reason"]


def test_delete_cv_history_keeps_resumes(client) -> None:
    resume = _create_from_cv(client)
    assert resume["source_analysis_id"]
    assert client.delete("/api/v1/cv/history").status_code == 204
    kept = client.get(f"/api/v1/resumes/{resume['id']}")
    assert kept.status_code == 200 and kept.json()["source_analysis_id"] is None
