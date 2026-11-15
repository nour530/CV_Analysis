from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.services.career.classifier as classifier_module
import app.services.career.esco as esco_module
import app.services.career.paths as paths_module
from app.api.dependencies import get_api_settings, reset_job_catalog_cache
from app.api.settings import APISettings
from app.main import app
from app.services.career.esco import EscoCatalog, SkillMatcher
from app.services.career.paths import CareerAdvisor
from cv_samples import STRONG_CV

FIXTURES = Path(__file__).parent / "fixtures" / "esco"
NURSE_CV = """Fatma Ben Ali
Sfax, Tunisia
PROFILE
Registered nurse with 6 years of patient care in surgery wards.
EXPERIENCE
Nurse, Hôpital Habib Bourguiba, Sfax | 2019 – Present
- Provided nursing care to 20 patients per shift
- Applied infection control and hygiene procedures
SKILLS
Patient care, infection control, pharmacology, teamwork
"""


@pytest.fixture
def catalog(monkeypatch) -> EscoCatalog:
    monkeypatch.setenv("ESCO_DATA_DIR", str(FIXTURES))
    esco_module.get_esco_catalog.cache_clear()
    classifier_module._trained.cache_clear()
    paths_module._advisor.cache_clear()
    yield esco_module.get_esco_catalog()
    esco_module.get_esco_catalog.cache_clear()
    classifier_module._trained.cache_clear()
    paths_module._advisor.cache_clear()


def test_isco_codes_map_to_the_most_specific_field(catalog) -> None:
    assert catalog.domain_for_code("2511.4") == "it"
    assert catalog.domain_for_code("2221.1") == "health"
    assert catalog.domain_for_code("2411.1") == "finance"
    assert catalog.domain_for_code("4311.1") == "finance"  # 431 beats 41 (business)
    assert catalog.domain_for_code("1411.2") == "hospitality"  # 1411 beats 14 (business)
    assert catalog.domain_for_code("3434.1") == "hospitality"  # chefs, not arts (343)
    assert catalog.domain_for_code("") is None


def test_search_occupations_in_english_and_french(catalog) -> None:
    assert [o.uri for o in catalog.search_occupations("comptable")] == ["o:accountant", "o:bookkeeper"]
    assert catalog.search_occupations("nurse")[0].uri == "o:nurse"
    assert all(o.domain_id == "health" for o in catalog.search_occupations("", domain_id="health"))


def test_skill_matcher_handles_synonyms_and_accents(catalog) -> None:
    matcher = SkillMatcher(catalog)
    found = matcher.present("Expérience: tenue de la comptabilite et fiscalité; Power BI dashboards", ["s:acct", "s:tax", "s:dataviz", "s:care"])
    assert found == ["s:acct", "s:tax", "s:dataviz"]


def test_classifier_learns_fields_from_occupations(catalog) -> None:
    classifier = classifier_module.get_domain_classifier()
    assert classifier is not None
    assert classifier.predict(NURSE_CV)[0].domain_id == "health"
    assert classifier.predict(STRONG_CV)[0].domain_id == "it"
    assert classifier.predict("Comptable: états financiers, fiscalité, techniques comptables")[0].domain_id == "finance"
    assert classifier.predict("") == []


def test_saved_classifier_loads_only_for_matching_data(catalog, tmp_path, monkeypatch) -> None:
    path = tmp_path / "model.joblib"
    trained = classifier_module.DomainClassifier.train(catalog, ["Chef de cuisine, pâtisserie, menus"], ["hospitality"])
    trained.save(catalog, path)
    loaded = classifier_module.DomainClassifier.load(catalog, path)
    assert loaded is not None and loaded.predict(NURSE_CV)[0].domain_id == "health"

    other = EscoCatalog(FIXTURES)
    other.occupations = other.occupations[:-1]
    assert classifier_module.DomainClassifier.load(other, path) is None, "different ESCO data"
    monkeypatch.setattr(classifier_module.sklearn, "__version__", "0.0")
    assert classifier_module.DomainClassifier.load(catalog, path) is None, "different scikit-learn"
    assert classifier_module.DomainClassifier.load(catalog, tmp_path / "missing.joblib") is None
    (tmp_path / "broken.joblib").write_bytes(b"not a model")
    assert classifier_module.DomainClassifier.load(catalog, tmp_path / "broken.joblib") is None


def test_cv_training_documents_map_categories(tmp_path) -> None:
    path = tmp_path / "Resume.csv"
    path.write_text(
        "ID,Resume_str,Resume_html,Category\n"
        "1,Registered nurse,,HEALTHCARE\n"
        "2,Personal trainer,,FITNESS\n"
        "3,,<p>Chef de partie</p>,CHEF\n",
        encoding="utf-8",
    )
    texts, labels, categories = classifier_module.cv_training_documents(path)
    assert labels == ["health", "hospitality"] and categories == ["HEALTHCARE", "CHEF"]
    assert "Chef de partie" in texts[1]
    assert classifier_module.cv_training_documents(tmp_path / "none.csv") == ([], [], [])


def test_advisor_suggests_roles_and_skill_gaps(catalog) -> None:
    advisor = CareerAdvisor(catalog)
    roles = advisor.suggest(NURSE_CV, limit=3)
    assert roles[0].occupation.uri == "o:nurse"
    assert "provide nursing care" in roles[0].have
    gap = advisor.skill_gap(catalog.by_uri["o:scientist"], STRONG_CV)
    assert gap.missing == ["SQL"]
    assert {"machine learning", "Python (computer programming)"} <= set(gap.have)
    assert gap.coverage == round(3 / 8, 3), "coverage counts against at least MIN_SKILLS skills"
    team_last = advisor.skill_gap(catalog.by_uri["o:developer"], "nothing relevant")
    assert team_last.missing[-1] == "work in teams", "transversal skills are listed last"
    french = advisor.skill_gap(catalog.by_uri["o:accountant"], "Bilan", language="fr")
    assert "techniques comptables" in french.missing


@pytest.fixture
def client(catalog, tmp_path, auth_helper):
    settings = APISettings(jobs_catalog_source="dataset")
    app.dependency_overrides[get_api_settings] = lambda: settings
    reset_job_catalog_cache()
    with TestClient(app, raise_server_exceptions=False) as test_client:
        auth_helper(test_client)
        yield test_client
    app.dependency_overrides.clear()


def test_career_api(client) -> None:
    domains = client.get("/api/v1/career/domains", params={"lang": "fr"}).json()
    health = next(d for d in domains["domains"] if d["id"] == "health")
    assert health["label"] == "Santé et soins" and health["occupations"] == 3
    assert "ESCO" in domains["attribution"]

    occupations = client.get("/api/v1/career/occupations", params={"q": "data", "lang": "en"}).json()["occupations"]
    assert {o["title"] for o in occupations} == {"data analyst", "data scientist"}

    empty = client.get("/api/v1/career/insights").json()
    assert empty["has_cv"] is False and empty["suggested_roles"] == []

    client.post("/api/v1/cv/analyze", json={"resume_text": NURSE_CV})
    client.put(
        "/api/v1/account/settings",
        json={"recommendation_limit": 10, "candidate_pool_size": 30, "allow_remote_analysis": False,
              "target_domain": "health", "target_role": "midwife"},
    )
    insights = client.get("/api/v1/career/insights")
    assert insights.headers["cache-control"] == "no-store"
    body = insights.json()
    assert body["detected_domains"][0]["id"] == "health"
    assert body["target_domain"]["id"] == "health" and body["target_role"] == "midwife"
    assert body["target_role_fit"]["title"] == "midwife"
    assert "provide nursing care" in body["target_role_fit"]["skills_you_have"]
    assert all(role["domain_id"] == "health" for role in body["suggested_roles"])

    # Saving other preferences without the goal fields keeps the goal.
    client.put("/api/v1/account/settings", json={"recommendation_limit": 12, "candidate_pool_size": 30, "allow_remote_analysis": False})
    settings = client.get("/api/v1/account/settings").json()
    assert settings["target_role"] == "midwife" and settings["target_domain"] == "health"
