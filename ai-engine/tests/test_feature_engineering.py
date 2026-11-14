from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

AI_ENGINE_DIR = Path(__file__).resolve().parents[1]
MODULE_PATH = AI_ENGINE_DIR / "data_analysis" / "03_feature_engineering.py"
FEATURES_DIR = AI_ENGINE_DIR / "data" / "features"

SPEC = importlib.util.spec_from_file_location("feature_engineering", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
fe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fe)


def skill_row(
    source_row_index: int,
    display: str,
    *,
    soc: str = "15-1252.00",
    title: str = "Software Developers",
    element_name: str = "Programming software",
    hot: str = "N",
    demand: str = "N",
) -> dict[str, object]:
    return {
        "O*NET-SOC Code": soc,
        "Title": title,
        "Title_clean": title,
        "Workplace Example": display,
        "Workplace Example_clean": display,
        "Element ID": "2.B.1.a",
        "Element Name": element_name,
        "Element Name_clean": element_name,
        "Hot Technology_clean": hot,
        "In Demand_clean": demand,
        "source_row_index": source_row_index,
    }


def build_matcher(*displays: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, object]:
    skills = pd.DataFrame(
        [skill_row(position, display) for position, display in enumerate(displays)]
    )
    return fe.build_skill_dictionary(skills)


def test_normalize_text_and_compact_key() -> None:
    assert fe.normalize_text("  Python\u00a0developer\n\nwith\tSQL  ") == "Python developer with SQL"
    assert fe.compact_key("C++") == "c++"
    assert fe.compact_key("C#") == "c#"
    assert fe.compact_key("Node.js") == "nodejs"


def test_alias_variants_are_dictionary_derived() -> None:
    variants = dict(fe.alias_variants("Adobe Experience Manager (AEM)"))
    assert variants["Adobe Experience Manager"] == "expanded_parenthetical"
    assert variants["AEM"] == "parenthetical_acronym"
    assert variants["adobeexperiencemanageraem"] == "compact_punctuation"


def test_skill_dictionary_builds_canonical_skills_and_aliases() -> None:
    skills = pd.DataFrame(
        [
            skill_row(0, "Python"),
            skill_row(1, "python"),
            skill_row(2, "PyTorch", element_name="Web platform development software"),
            skill_row(3, "MySQL", element_name="Data base user interface and query software"),
        ]
    )
    dictionary, aliases, occupation_map, matcher = fe.build_skill_dictionary(skills)

    assert len(dictionary) == 3
    assert len(occupation_map) == 4
    assert occupation_map["source_row_index"].tolist() == [0, 1, 2, 3]
    assert dictionary["canonical_skill_id"].is_unique
    assert dictionary["canonical_skill"].tolist() == ["MySQL", "PyTorch", "Python"]
    categories = dictionary.set_index("canonical_skill")["skill_category"].to_dict()
    assert categories["Python"] == fe.CATEGORY_PROGRAMMING
    assert categories["PyTorch"] == fe.CATEGORY_FRAMEWORK
    assert categories["MySQL"] == fe.CATEGORY_DATABASE
    assert aliases["match_enabled"].all()
    python_alias = aliases[aliases["canonical_skill"] == "Python"]
    assert set(python_alias["alias"].str.casefold()) == {"python"}
    assert matcher.find_matches("Experience with Python and PyTorch.")


def test_ambiguous_alias_is_disabled() -> None:
    _, aliases, _, matcher = build_matcher(
        "Alpha Management (AEM)",
        "Adaptive Engineering Method (AEM)",
    )
    aem_rows = aliases[aliases["alias_key"] == "aem"]
    assert len(aem_rows) == 2
    assert aem_rows["is_ambiguous"].all()
    assert not aem_rows["match_enabled"].any()
    assert not any(
        item["canonical_skill"] in {"Alpha Management", "Adaptive Engineering Method"}
        for item in matcher.find_matches("AEM")
    )


def test_matcher_respects_boundaries_and_short_alias_context() -> None:
    _, _, _, matcher = build_matcher("C", "C++", "R", "Go", "Java")

    assert [item["canonical_skill"] for item in matcher.find_matches("C programming language")] == ["C"]
    assert [item["canonical_skill"] for item in matcher.find_matches("C++ developer")] == ["C++"]
    assert [item["canonical_skill"] for item in matcher.find_matches("R programming")] == ["R"]
    assert [item["canonical_skill"] for item in matcher.find_matches("Go developer")] == ["Go"]
    assert matcher.find_matches("I use C every day") == []
    assert [item["canonical_skill"] for item in matcher.find_matches("java developer")] == ["Java"]


def test_extract_sections_preserves_offsets() -> None:
    text = (
        "Summary\nExperienced engineer.\n"
        "Technical Skills\nPython, SQL\n"
        "Work Experience\nDeveloper at Acme"
    )
    sections = fe.extract_sections(text, fe.RESUME_HEADING_SPECS, "resume")
    section_types = [section["section_type"] for section in sections]

    assert "summary" in section_types
    assert "skills" in section_types
    assert "experience" in section_types
    for section in sections:
        assert 0 <= section["start_char"] <= section["end_char"] <= len(text)
        assert section["section_text"] == text[section["start_char"] : section["end_char"]].strip()


def test_extract_job_sections() -> None:
    text = (
        "Job Description\nBuild reliable data pipelines.\n"
        "Responsibilities\nDevelop models and reports.\n"
        "Requirements\nPython and SQL required."
    )
    sections = fe.extract_sections(text, fe.JOB_HEADING_SPECS, "job")
    section_types = [section["section_type"] for section in sections]

    assert "job_description" in section_types
    assert "responsibilities" in section_types
    assert "requirements" in section_types


def test_extract_degree_and_institution_mentions() -> None:
    degrees = fe.extract_degree_mentions(
        "Bachelor of Science in Computer Science from New York University",
        allow_weak=True,
    )
    institutions = fe.extract_institution_mentions(
        "Bachelor of Science from New York University"
    )

    assert any("bachelor" in degree.casefold() for degree in degrees)
    assert institutions == ["New York University"]


def test_extract_stated_experience_years() -> None:
    range_result = fe.extract_stated_experience_years("3-5 years of experience")
    assert range_result["min"] == 3
    assert range_result["max"] == 5
    assert range_result["open_ended"] is False

    plus_result = fe.extract_stated_experience_years("5+ years of experience")
    assert plus_result["min"] == 5
    assert plus_result["max"] == 5
    assert plus_result["open_ended"] is True

    minimum_result = fe.extract_stated_experience_years("Minimum 2 years experience")
    assert minimum_result["min"] == 2
    assert minimum_result["open_ended"] is True

    assert fe.extract_stated_experience_years("10 years")["min"] is None


def test_extract_job_title_sources() -> None:
    explicit = fe.extract_job_title("Job Title: Senior Data Engineer", "Data Science")
    assert explicit["job_title"] == "Senior Data Engineer"
    assert explicit["job_title_source"] == "explicit_field"

    cue = fe.extract_job_title(
        "We are looking for a Senior Python Developer to join our team.",
        "Software Development",
    )
    assert cue["job_title"] == "Senior Python Developer"
    assert cue["job_title_source"] == "cue_phrase"

    missing = fe.extract_job_title("Great benefits and a friendly team.", "Other")
    assert missing["job_title"] == ""
    assert missing["job_title_source"] == "not_identified"


def test_classify_requirement() -> None:
    assert (
        fe.classify_requirement("Python required", 0, 6, "full_text")[0] == "required"
    )
    assert (
        fe.classify_requirement("Docker preferred", 0, 6, "full_text")[0]
        == "preferred"
    )
    assert fe.classify_requirement("SQL", 0, 3, "full_text")[0] == "mentioned"
    assert fe.classify_requirement("SQL", 0, 3, "required_skills")[0] == "required"
    assert fe.classify_requirement("SQL", 0, 3, "preferred_skills")[0] == "preferred"


def test_extract_resume_record() -> None:
    _, _, _, matcher = build_matcher("Python", "MySQL")
    row = pd.Series(
        {
            "ID": 123,
            "source_row_index": 7,
            "Category": "Data Science",
            "clean_text": (
                "Summary\nSoftware engineer with Python and MySQL.\n"
                "Education\nBachelor of Science\n"
                "Work Experience\n2020 - 2022 Developer"
            ),
        }
    )
    feature, sections, skill_rows = fe.extract_resume_record(row, matcher)

    assert feature["resume_id"] == 123
    assert feature["source_row_index"] == 7
    assert feature["technical_skills"] == "MySQL; Python"
    assert feature["has_education"] is True
    assert feature["has_experience"] is True
    assert feature["experience_entry_count_inferred"] == 1
    assert feature["feature_version"] == fe.FEATURE_VERSION
    assert sections
    assert {item["canonical_skill"] for item in skill_rows} == {"MySQL", "Python"}


def test_extract_job_record() -> None:
    _, _, _, matcher = build_matcher("Python", "MySQL", "Docker")
    row = pd.Series(
        {
            "source_row_index": 0,
            "label": "Data Science",
            "label_clean": "Data Science",
            "clean_description": (
                "Job Title: Data Engineer. "
                "Required Skills: Python, MySQL. "
                "Preferred Skills: Docker."
            ),
            "short_description_flag": False,
        }
    )
    feature, sections, skill_rows, keyword_rows = fe.extract_job_record(
        row, matcher, [("python", 0.5)]
    )

    assert feature["job_id"] == "JOB_00000000"
    assert feature["job_title"] == "Data Engineer"
    assert feature["required_skills"] == "MySQL; Python"
    assert feature["preferred_skills"] == "Docker"
    assert feature["feature_version"] == fe.FEATURE_VERSION
    assert sections
    assert {item["canonical_skill"] for item in skill_rows} == {"Docker", "MySQL", "Python"}
    assert keyword_rows[0]["keyword"] == "python"


def test_compute_tfidf_keywords() -> None:
    results, method = fe.compute_tfidf_keywords(
        [
            "Python developer with SQL and data pipelines",
            "Python engineer with Docker and SQL",
            "Data analyst with SQL reporting",
        ],
        top_n=3,
    )
    assert method in {"scikit-learn_tfidf", "frequency_idf_fallback"}
    assert len(results) == 3
    assert all(len(items) <= 3 for items in results)
    assert all(all(isinstance(score, float) for _, score in items) for items in results)


def test_generated_manifest_outputs_are_valid() -> None:
    manifest_path = FEATURES_DIR / "feature_manifest.json"
    if not manifest_path.exists():
        pytest.skip("Feature outputs have not been generated yet.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["phase"] == "2.4"
    assert manifest["validation"]["passed"] is True
    assert manifest["representations"]["embeddings_generated"] is False
    assert manifest["representations"]["external_api_calls"] is False
    assert manifest["statistics"]["resumes"]["rows"] == 2483
    assert manifest["statistics"]["jobs"]["rows"] == 10461
    assert manifest["statistics"]["dictionary"]["canonical_skills"] == 1541
    assert manifest["statistics"]["dictionary"]["skill_occupation_relationships"] == 6217
    for name in [
        "resume_features.csv",
        "job_features.csv",
        "skills_dictionary.csv",
        "skill_aliases.csv",
        "skill_occupation_map.csv",
        "resume_skill_matches.csv",
        "job_skill_matches.csv",
        "resume_sections.csv",
        "job_sections.csv",
        "job_keywords.csv",
    ]:
        assert (FEATURES_DIR / name).exists()
