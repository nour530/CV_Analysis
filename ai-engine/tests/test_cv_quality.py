from __future__ import annotations

import csv
import json
import re
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.api.routes.resumes as resume_routes
import app.services.cv_quality.scorer as scorer_module
from app.api.dependencies import get_api_settings, reset_job_catalog_cache
from app.api.settings import APISettings
from app.main import app
from app.schemas.cv_document import CvDocument
from app.services.cv_builder.parser import parse_cv_text
from app.services.cv_quality.evaluation import evaluate
from app.services.cv_quality.features import FEATURE_NAMES, extract_features
from app.services.cv_quality.labelling import (
    LabelCv,
    estimate_cost,
    label_line,
    parse_result_text,
    read_labels,
    request_params,
    sample_cvs,
)
from app.services.cv_quality.rewriter import CvRewriter, CvRewriteError, CvRewriteSettings, scrub_contacts
from app.services.cv_quality.rubric import CRITERION_IDS, label_schema
from app.services.cv_quality.scorer import TARGETS, CvQualityScorer, cross_validate
from cv_samples import STRONG_CV
from test_resumes import ROWS

WEAK_CV = """Sami Trabelsi
EXPERIENCE
Sales rep, Company X
- Responsible for clients
- Worked on reports
- I helped the team
SKILLS
Hard-working team player
"""


def _variants() -> tuple[list[str], np.ndarray]:
    """Synthetic graded CVs: strong CV variants lose figures, verbs or the
    summary; their labels drop accordingly."""
    rng = np.random.default_rng(0)
    lines = STRONG_CV.splitlines()
    texts, labels = [], []
    for i in range(90):
        drop_figures, weaken, drop_summary = rng.random() < 0.5, rng.random() < 0.5, rng.random() < 0.4
        out = []
        for line in lines:
            if drop_summary and line.startswith("Data analyst with"):
                continue
            if line.startswith("- "):
                if drop_figures:
                    line = re.sub(r"\d+%?", "", line)
                if weaken:
                    line = "- Responsible for " + line[2:].split(" ", 1)[-1].lower()
            out.append(line)
        out.append(f"Reference {i}")
        texts.append("\n".join(out))
        achievements = 3 if drop_figures else 9
        action = 3 if weaken else 9
        summary = 1 if drop_summary else 8
        row = {cid: 6 for cid in CRITERION_IDS} | {"achievements": achievements, "action_language": action, "summary": summary}
        row["overall"] = 30 + 5 * (achievements + action + summary) // 3
        labels.append([row[t] for t in TARGETS])
    return texts, np.array(labels, dtype=float)


def test_features_measure_cv_signals() -> None:
    strong = dict(zip(FEATURE_NAMES, extract_features(STRONG_CV)))
    weak = dict(zip(FEATURE_NAMES, extract_features(WEAK_CV)))
    assert strong["number_bullet_share"] > 0.5 and weak["number_bullet_share"] == 0
    assert weak["weak_opening_share"] > 0.5 and strong["weak_opening_share"] == 0
    assert strong["has_summary"] == 1 and weak["has_summary"] == 0
    assert weak["buzzwords_per_100_words"] > 0


def test_scorer_learns_from_graded_cvs_and_round_trips(tmp_path) -> None:
    texts, labels = _variants()
    scorer = CvQualityScorer.train(texts, labels, svd_components=10, metadata={"cvs": len(texts)})
    good, bad = scorer.score(STRONG_CV), scorer.score(WEAK_CV)
    assert good.criteria["achievements"] > bad.criteria["achievements"]
    assert good.overall > bad.overall and 0 <= bad.overall <= 100
    assert all(0 <= v <= 10 for v in good.criteria.values())

    path = tmp_path / "scorer.joblib"
    scorer.save(path)
    loaded = CvQualityScorer.load(path)
    assert loaded is not None and loaded.score(STRONG_CV) == good and loaded.metadata["cvs"] == 90
    payload = __import__("joblib").load(path)
    payload["rubric"] = "0.0.0"
    __import__("joblib").dump(payload, path)
    assert CvQualityScorer.load(path) is None, "another rubric version"
    assert CvQualityScorer.load(tmp_path / "missing.joblib") is None

    report = cross_validate(texts, labels, kind="ridge", svd_components=10)
    assert set(report) == set(TARGETS)
    assert report["achievements"]["mae"] < report["achievements"]["baseline_mae"]
    assert report["overall"]["tolerance"] == 10 and report["summary"]["tolerance"] == 1


def test_evaluation_without_model_reports_measured_facts() -> None:
    result = evaluate(parse_cv_text(WEAK_CV), None, language="en")
    assert result.available is False and result.overall is None
    by_id = {c.id: c for c in result.criteria}
    assert by_id["summary"].evidence == ["No professional summary."]
    assert any("vague phrase" in line for line in by_id["action_language"].evidence)
    checks = {c.id: c.ok for c in result.checks}
    assert checks["email"] is False and checks["skills"] is True
    french = evaluate(parse_cv_text(WEAK_CV), None, language="fr")
    assert french.criteria[0].label == "Structure" and "Pas de profil" in french.criteria[1].evidence[0]


# -- rewriter -------------------------------------------------------------------------------


def _improved_json(**overrides) -> dict:
    data = {
        "contact": {"full_name": "Amira Ben Salah", "headline": "Data Analyst", "location": "Tunis, Tunisia"},
        "summary": "Data Analyst with 4 years of experience in retail and telecom analytics, using Python, SQL and Power BI.",
        "experience": [
            {
                "title": "Data Analyst", "company": "Ooredoo", "location": "Tunis", "start": "Mar 2022", "end": "", "current": True,
                "bullets": ["Build 12 Power BI dashboards used by 150 managers across 6 regions",
                            "Cut monthly reporting time by 40% by automating SQL pipelines in Python",
                            "Raised conversion by 8% through A/B tests on 3 pricing offers, reaching [N] customers"],
            },
            {
                "title": "Junior Analyst", "company": "Carrefour", "location": "Tunis", "start": "Jul 2020", "end": "Feb 2022", "current": False,
                "bullets": ["Analysed sales of 2,000 products to optimise stock across 25 stores, saving 90 hours"],
            },
        ],
        "education": [
            {"degree": "Master in Business Intelligence", "school": "ESSEC Tunis", "location": "", "start": "2018", "end": "2020", "details": []},
        ],
        "skills": ["Python", "SQL", "Microsoft Power BI", "Kubernetes"],
        "languages": [{"name": "Arabic", "level": "native"}],
        "certifications": [],
        "projects": [],
        "changes": [{"section": "experience", "note": "Bullets now show the result first."}],
    }
    data.update(overrides)
    return data


class FakeClient:
    def __init__(self, payload: dict | None = None, stop_reason: str = "end_turn") -> None:
        self.calls: list[dict] = []
        self.payload = payload or _improved_json()
        self.stop_reason = stop_reason
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.calls.append(params)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            model=params["model"],
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
        )


def test_rewriter_hides_contacts_restores_them_and_flags_new_facts() -> None:
    original = parse_cv_text(STRONG_CV)
    client = FakeClient()
    result = CvRewriter(CvRewriteSettings(), client=client).rewrite(original, language="en", job_title="BI Analyst", job_skills=["Power BI"])

    sent = client.calls[0]["messages"][0]["content"]
    assert "amira.bensalah@example.com" not in sent and "+216 22 345 678" not in sent and "linkedin.com/in" not in sent
    assert "[EMAIL]" in sent and "[PHONE]" in sent and "Target-job skills: Power BI" in sent
    assert client.calls[0]["output_config"]["format"]["type"] == "json_schema"

    improved = result.improved
    assert improved.contact.email == original.contact.email and improved.contact.phone == original.contact.phone
    assert improved.contact.linkedin == original.contact.linkedin
    assert improved.experience[0].id == original.experience[0].id, "ids kept for matching roles"
    assert improved.experience[0].current and improved.experience[0].end is None

    sections = {s.id: s for s in result.sections}
    experience = " ".join(sections["experience"].warnings)
    assert "Carrefour" in experience, "employer not in the CV"
    assert "90" in experience and "[N]" in experience
    assert "2,000" not in experience and "40" not in experience.split("Figures not in your CV:")[1].split(".")[0]
    assert "Kubernetes" in " ".join(sections["skills"].warnings)
    assert "Microsoft Power BI" not in " ".join(sections["skills"].warnings), "all words appear in the CV"
    assert sections["experience"].note == "Bullets now show the result first."
    assert sections["certifications"].changed is True and sections["contact"].changed is True


def test_rewriter_errors_are_reported() -> None:
    doc = parse_cv_text(STRONG_CV)
    with pytest.raises(CvRewriteError, match="declined"):
        CvRewriter(CvRewriteSettings(), client=FakeClient(stop_reason="refusal")).rewrite(doc, language="en")
    with pytest.raises(CvRewriteError, match="unreadable"):
        CvRewriter(CvRewriteSettings(), client=FakeClient(payload={"oops": 1})).rewrite(doc, language="en")


def test_scrub_contacts_keeps_years_and_figures() -> None:
    text = scrub_contacts("Call +216 22 345 678 or a.b@x.tn, see github.com/ab and www.site.tn. Sold 2,000 units in 2023.")
    assert text == "Call [PHONE] or [EMAIL], see [WEBSITE] and [WEBSITE] Sold 2,000 units in 2023."


# -- API ----------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, auth_helper):
    path = tmp_path / "job_features.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)
    state = {"settings": APISettings(jobs_features_path=path, jobs_catalog_source="dataset")}
    app.dependency_overrides[get_api_settings] = lambda: state["settings"]
    reset_job_catalog_cache()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        auth_helper(test_client)
        test_client.state = state  # type: ignore[attr-defined]
        test_client.path = path  # type: ignore[attr-defined]
        yield test_client
    app.dependency_overrides.clear()


def _resume(client: TestClient) -> dict:
    assert client.post("/api/v1/cv/analyze", json={"resume_text": STRONG_CV}).status_code == 200
    created = client.post("/api/v1/resumes", json={})
    assert created.status_code == 201, created.text
    return created.json()


def test_evaluation_endpoint_uses_the_trained_model(client, monkeypatch) -> None:
    resume = _resume(client)
    url = f"/api/v1/resumes/{resume['id']}/evaluation"
    monkeypatch.setattr(resume_routes, "get_cv_scorer", lambda: None)
    untrained = client.post(url).json()
    assert untrained["available"] is False and untrained["overall"] is None and "not been trained" in untrained["notice"]
    assert len(untrained["criteria"]) == 8 and untrained["criteria"][2]["evidence"]

    texts, labels = _variants()
    scorer = CvQualityScorer.train(texts, labels, svd_components=10, metadata={"cvs": 90, "kind": "ridge"})
    monkeypatch.setattr(resume_routes, "get_cv_scorer", lambda: scorer)
    trained = client.post(url).json()
    assert trained["available"] is True and 0 <= trained["overall"] <= 100
    assert trained["model"] == {"kind": "ridge", "cvs": 90}
    assert all(c["score"] is not None for c in trained["criteria"])


def test_ai_rewrite_needs_server_flag_consent_and_key(client, monkeypatch) -> None:
    resume = _resume(client)
    url = f"/api/v1/resumes/{resume['id']}/ai-rewrite"
    fake = FakeClient()
    app.dependency_overrides[resume_routes.get_cv_rewriter] = lambda: CvRewriter(CvRewriteSettings(), client=fake)
    monkeypatch.setattr(resume_routes, "get_cv_scorer", lambda: None)

    assert client.post(url, json={}).json()["error"]["code"] == "ai_disabled"
    client.state["settings"] = APISettings(jobs_features_path=client.path, jobs_catalog_source="dataset", allow_remote_llm_analysis=True)
    denied = client.post(url, json={})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "ai_consent_required"
    client.put("/api/v1/account/settings", json={"recommendation_limit": 10, "candidate_pool_size": 30, "allow_remote_analysis": True})

    app.dependency_overrides[resume_routes.get_cv_rewriter] = lambda: None
    assert client.post(url, json={}).json()["error"]["code"] == "ai_not_configured"
    assert fake.calls == []

    app.dependency_overrides[resume_routes.get_cv_rewriter] = lambda: CvRewriter(CvRewriteSettings(), client=fake)
    response = client.post(url, json={"job_id": "JOB_1"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["improved"]["contact"]["email"] == resume["content"]["contact"]["email"]
    assert {s["id"] for s in body["sections"]} >= {"summary", "experience", "skills"}
    assert body["score_before"] is None and body["model"] == "claude-opus-5-5"
    assert len(fake.calls) == 1 and "Target job:" in fake.calls[0]["messages"][0]["content"]
    CvDocument.model_validate(body["improved"])


# -- labelling ------------------------------------------------------------------------------------


def test_labelling_helpers(tmp_path) -> None:
    cvs = [LabelCv(str(i), "IT" if i % 3 else "HR", f"CV {i} " * 50) for i in range(30)]
    sample = sample_cvs(cvs, 10)
    assert len(sample) == 10 and sample == sample_cvs(cvs, 10)
    assert {cv.category for cv in sample} == {"IT", "HR"} and sum(cv.category == "HR" for cv in sample) == 5

    params = request_params(sample[0], model="claude-sonnet-5-5", effort="medium")
    assert params["output_config"]["format"]["schema"] == label_schema()
    assert "<cv>" in params["messages"][0]["content"] and "anonymised" in params["system"]
    tokens, cost = estimate_cost(sample, "claude-sonnet-5-5")
    assert tokens > 0 and 0 < cost < 1

    assert parse_result_text("not json") is None
    label = parse_result_text(json.dumps({**{c: 14 for c in CRITERION_IDS}, "overall": -5}))
    assert label["structure"] == 10 and label["overall"] == 0

    path = tmp_path / "labels.jsonl"
    path.write_text(label_line(sample[0], label, "claude-sonnet-5-5") + "\n" + json.dumps({"id": "x", "rubric": "0.1", "labels": {}}) + "\n")
    assert list(read_labels(path)) == [sample[0].id]


def test_get_cv_scorer_is_none_until_trained(monkeypatch) -> None:
    monkeypatch.setattr(scorer_module.CvQualityScorer, "load", classmethod(lambda cls, path=None: None))
    scorer_module._loaded.cache_clear()
    assert scorer_module.get_cv_scorer() is None
    scorer_module._loaded.cache_clear()
