#!/usr/bin/env python
"""Phase 2.4 - Feature engineering for JobMatch AI.

The script reads the cleaned datasets, builds a reusable skills dictionary,
extracts structured résumé and job-description features, writes feature tables
under ``ai-engine/data/features/``, and produces a validation report.

The pipeline is deterministic and local. It does not create embeddings, call an
external API, or train a model.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve()
DATA_ANALYSIS_DIR = SCRIPT_PATH.parent
AI_ENGINE_DIR = DATA_ANALYSIS_DIR.parent
PROJECT_ROOT = AI_ENGINE_DIR.parent

PROCESSED_DIR = AI_ENGINE_DIR / "data" / "processed"
FEATURES_DIR = AI_ENGINE_DIR / "data" / "features"
OUTPUT_DIR = DATA_ANALYSIS_DIR / "outputs"

RESUME_INPUT_PATH = PROCESSED_DIR / "resumes_cleaned.csv"
JOB_INPUT_PATH = PROCESSED_DIR / "job_roles_cleaned.csv"
SKILLS_INPUT_PATH = PROCESSED_DIR / "software_skills_cleaned.csv"

RESUME_FEATURES_PATH = FEATURES_DIR / "resume_features.csv"
JOB_FEATURES_PATH = FEATURES_DIR / "job_features.csv"
SKILLS_DICTIONARY_PATH = FEATURES_DIR / "skills_dictionary.csv"
SKILL_ALIASES_PATH = FEATURES_DIR / "skill_aliases.csv"
SKILL_OCCUPATION_MAP_PATH = FEATURES_DIR / "skill_occupation_map.csv"
RESUME_SKILL_MATCHES_PATH = FEATURES_DIR / "resume_skill_matches.csv"
JOB_SKILL_MATCHES_PATH = FEATURES_DIR / "job_skill_matches.csv"
RESUME_SECTIONS_PATH = FEATURES_DIR / "resume_sections.csv"
JOB_SECTIONS_PATH = FEATURES_DIR / "job_sections.csv"
JOB_KEYWORDS_PATH = FEATURES_DIR / "job_keywords.csv"
FEATURE_MANIFEST_PATH = FEATURES_DIR / "feature_manifest.json"
FEATURE_REPORT_PATH = OUTPUT_DIR / "feature_engineering_report.md"

FEATURE_VERSION = "2.4.0"
ENCODINGS_TO_TRY = ("utf-8-sig", "utf-8", "latin-1")
KEYWORD_TOP_N = 10

CATEGORY_PROGRAMMING = "programming_language"
CATEGORY_FRAMEWORK = "framework_library"
CATEGORY_DATABASE = "database"
CATEGORY_TOOL = "tool_technology"
SKILL_CATEGORIES = (
    CATEGORY_PROGRAMMING,
    CATEGORY_FRAMEWORK,
    CATEGORY_DATABASE,
    CATEGORY_TOOL,
)
REQUIREMENT_TYPES = ("required", "preferred", "mentioned")

RESUME_REQUIRED_COLUMNS = ("ID", "Category", "source_row_index", "clean_text")
JOB_REQUIRED_COLUMNS = (
    "source_row_index",
    "jobdescription",
    "clean_description",
    "label",
    "label_clean",
)
SKILLS_REQUIRED_COLUMNS = (
    "O*NET-SOC Code",
    "Title_clean",
    "Workplace Example_clean",
    "Element ID",
    "Element Name_clean",
    "Hot Technology_clean",
    "In Demand_clean",
    "source_row_index",
)

RAW_PATH_BY_NAME = {
    "Resume.csv": AI_ENGINE_DIR / "data" / "raw" / "resumes" / "Resume.csv",
    "job_roles_combined_ready_10461.csv": (
        AI_ENGINE_DIR
        / "data"
        / "raw"
        / "job_descriptions"
        / "job_roles_combined_ready_10461.csv"
    ),
    "software_skills_15.csv": (
        AI_ENGINE_DIR / "data" / "raw" / "job_descriptions" / "software_skills_15.csv"
    ),
}
PROCESSED_PATH_BY_NAME = {
    "resumes_cleaned.csv": RESUME_INPUT_PATH,
    "job_roles_cleaned.csv": JOB_INPUT_PATH,
    "software_skills_cleaned.csv": SKILLS_INPUT_PATH,
}


# ---------------------------------------------------------------------------
# Logging and errors
# ---------------------------------------------------------------------------


class FeatureEngineeringError(RuntimeError):
    """Raised when a feature-engineering step cannot complete safely."""


def configure_logging() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


LOGGER = logging.getLogger("jobmatch-features")


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------


def normalize_text(value: Any) -> str:
    """Normalise Unicode and whitespace while preserving meaningful symbols."""
    if not isinstance(value, str) or not value:
        return ""
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\u00a0", " ")
    text = text.replace("\u2007", " ")
    text = text.replace("\u202f", " ")
    text = text.replace("\u200b", "")
    text = text.replace("\ufeff", "")
    text = text.replace("\u00ad", "")
    text = re.sub(r"[\t\n\r\f\v\u2028\u2029]+", " ", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    text = "".join(
        " " if unicodedata.category(character) == "Zs" else character
        for character in text
    )
    return re.sub(r"\s+", " ", text).strip()


def normalize_key(value: Any) -> str:
    """Return the case-insensitive key used for aliases and comparisons."""
    return normalize_text(value).casefold()


def compact_key(value: Any) -> str:
    """Return a punctuation-light key while retaining language symbols."""
    return re.sub(r"[^a-z0-9+#]+", "", normalize_key(value))


def unique_join(
    values: Iterable[Any],
    separator: str = "; ",
    limit: int | None = None,
    sort: bool = True,
) -> str:
    cleaned: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, float) and pd.isna(value):
            continue
        text = normalize_text(str(value))
        if text:
            cleaned.append(text)
    unique = sorted(set(cleaned), key=lambda item: (item.casefold(), item)) if sort else list(dict.fromkeys(cleaned))
    if limit is not None:
        unique = unique[:limit]
    return separator.join(unique)


def relative_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} GB"


def file_fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    size = path.stat().st_size
    return {
        "size_bytes": size,
        "size_human": human_size(size),
        "sha256": digest.hexdigest(),
    }


def fingerprint_paths(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    fingerprints: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        if not path.exists():
            raise FeatureEngineeringError(f"Required file not found: {path}")
        fingerprints[name] = file_fingerprint(path)
    return fingerprints


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FeatureEngineeringError(f"Dataset not found: {path}")
    last_error: Exception | None = None
    for encoding in ENCODINGS_TO_TRY:
        try:
            frame = pd.read_csv(path, encoding=encoding, low_memory=False)
            LOGGER.info(
                "Loaded %s with %s: %d rows x %d columns",
                path.name,
                encoding,
                frame.shape[0],
                frame.shape[1],
            )
            return frame
        except UnicodeDecodeError as exc:
            last_error = exc
        except (pd.errors.ParserError, OSError) as exc:
            raise FeatureEngineeringError(f"Could not read {path}: {exc}") from exc
    raise FeatureEngineeringError(
        f"Could not decode {path} with {ENCODINGS_TO_TRY}: {last_error}"
    )


def require_columns(frame: pd.DataFrame, columns: Iterable[str], name: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise FeatureEngineeringError(
            f"{name} is missing required columns: {missing}. "
            f"Available columns: {list(frame.columns)}"
        )


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(
            path,
            index=False,
            encoding="utf-8-sig",
            lineterminator="\n",
            na_rep="",
        )
    except OSError as exc:
        raise FeatureEngineeringError(f"Could not write {path}: {exc}") from exc
    LOGGER.info("Wrote %s (%s)", path, human_size(path.stat().st_size))


def write_json(payload: dict[str, Any], path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise FeatureEngineeringError(f"Could not write {path}: {exc}") from exc
    LOGGER.info("Wrote %s (%s)", path, human_size(path.stat().st_size))


def ensure_directories() -> None:
    for directory in (FEATURES_DIR, OUTPUT_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FeatureEngineeringError(
                f"Could not create output directory {directory}: {exc}"
            ) from exc


def md_cell(value: Any, max_chars: int = 220) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = " ".join(str(value).split())
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text.replace("|", "\\|")


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    escaped_headers = [str(header).replace("|", "\\|") for header in headers]
    lines = [
        "| " + " | ".join(escaped_headers) + " |",
        "| " + " | ".join("---" for _ in escaped_headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def format_counts(counts: dict[str, int]) -> str:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{key}: {value:,}" for key, value in ordered)


def percent(part: int | float, whole: int | float) -> float:
    return 0.0 if not whole else (float(part) / float(whole)) * 100.0


def safe_mean(values: pd.Series) -> float:
    return float(values.mean()) if len(values) else 0.0


def context_window(text: str, start: int, end: int, before: int = 120, after: int = 160) -> str:
    left = max(0, start - before)
    right = min(len(text), end + after)
    return normalize_text(text[left:right])


# ---------------------------------------------------------------------------
# Skills dictionary and matching
# ---------------------------------------------------------------------------

PROGRAMMING_LANGUAGE_NAMES = frozenset(
    {
        "a programming language apl",
        "ada",
        "adobe actionscript",
        "advanced business application programming abap",
        "algorithmic language algol",
        "american national standards institute ansi c",
        "awk",
        "bash",
        "beginner's all-purpose symbolic instruction code basic",
        "bourbon shell",
        "bourne shell",
        "c",
        "c#",
        "c++",
        "call-processing language cpl",
        "cascading style sheets css",
        "common business oriented language cobol",
        "computer on-line real-time applications language coral 66",
        "c shell",
        "dos shell script",
        "dynamic hypertext markup language dhtml",
        "eiffel",
        "extensible hypertext markup language xhtml",
        "formula translation/translator fortran",
        "forth",
        "go",
        "groovy",
        "haskell",
        "html",
        "hypertext markup language html",
        "icon programming language",
        "interface definition language idl",
        "j",
        "java",
        "javascript",
        "job control language jcl",
        "kornshell",
        "lisp",
        "list processing language lisp",
        "lua",
        "microsoft powershell",
        "microsoft visual basic",
        "microsoft visual basic for applications vba",
        "microsoft visual basic scripting edition vbscript",
        "microsoft visual basic.net",
        "mumps m",
        "objective c",
        "oracle java",
        "oracle pl/sql",
        "pascal",
        "perl",
        "php",
        "programming language one pl/i",
        "prolog",
        "python",
        "r",
        "restructured extended executor rexx",
        "ruby",
        "scala",
        "scheme",
        "shell script",
        "smalltalk",
        "string oriented symbolic language snobol",
        "structured query language sql",
        "sun microsystems java",
        "swift",
        "typescript",
        "transact-sql",
        "unix shell",
        "visual basic",
        "xquery",
        "xsl",
        "xslt",
    }
)

FRAMEWORK_LIBRARY_NAMES = frozenset(
    {
        "adobe flex",
        "allaire coldfusion",
        "apache spark",
        "apache struts",
        "asp.net",
        "backbone.js",
        "bootstrap",
        "django",
        "ext js",
        "flask",
        "google angular",
        "grails",
        "jquery",
        "keras",
        "langchain",
        "meteorjs",
        "microsoft .net framework",
        "microsoft asp.net",
        "microsoft asp.net core mvc",
        "microsoft silverlight",
        "node.js",
        "numpy",
        "oracle application express apex",
        "pandas",
        "pyspark",
        "pytorch",
        "react",
        "react native",
        "react redux",
        "requirejs",
        "ruby on rails",
        "scikit-learn",
        "scipy",
        "spacy",
        "spring framework",
        "tensorflow",
        "vue.js",
        "webpack",
        "zend framework",
    }
)

DATABASE_NAMES = frozenset(
    {
        "amazon dynamodb",
        "amazon redshift",
        "apache cassandra",
        "apache hbase",
        "apache hive",
        "bigquery",
        "couchbase server",
        "elasticsearch",
        "filemaker pro",
        "greenplum database",
        "ibm db2",
        "mariadb",
        "microsoft access",
        "microsoft sql server",
        "mongodb",
        "mysql",
        "neo4j",
        "nosql",
        "oracle database",
        "postgresql",
        "redis",
        "teradata database",
    }
)

DATABASE_ELEMENT_HINTS = (
    "data base management system software",
    "data base user interface and query software",
    "object oriented data base management software",
    "data base reporting software",
)
FRAMEWORK_ELEMENT_HINTS = (
    "web platform development software",
    "web application framework software",
    "web framework software",
    "graphical user interface development software",
    "application server software",
)
LANGUAGE_WORD_PATTERN = re.compile(r"\b(?:programming\s+language|language)\b", re.IGNORECASE)
LANGUAGE_TOKEN_PATTERN = re.compile(
    r"\b(?:java|javascript|typescript|python|ruby|php|scala|swift|kotlin|perl|lua|"
    r"haskell|scheme|pascal|cobol|fortran|ada|lisp|prolog|awk|basic|abap|groovy|"
    r"actionscript|c\+\+|c#)\b",
    re.IGNORECASE,
)
LANGUAGE_BLOCK_PATTERN = re.compile(
    r"\b(?:message|service|server|framework|library|api|beans|pages|persistence|"
    r"servlet|profiler|studio|platform|enterprise|edition|sdk|jdk|jre|runtime|"
    r"virtual machine|ide|compiler|debugger|parser|object notation|json|generator|"
    r"builder|optimizer|reporting|manager|loader|integration|recovery)\b",
    re.IGNORECASE,
)
FRAMEWORK_NAME_PATTERN = re.compile(
    r"\b(?:framework|library|libraries|toolkit|redux|spark|tensorflow|pytorch|keras|"
    r"scikit-learn|pandas|numpy|scipy|spacy|langchain|react|angular|vue|django|"
    r"flask|spring|node\.?js|jquery|bootstrap|asp\.net|rails)\b",
    re.IGNORECASE,
)
DATABASE_NAME_PATTERN = re.compile(
    r"\b(?:database|db2|nosql|mongodb|postgresql|mysql|maria(?:db)?|redis|"
    r"cassandra|hbase|hive|redshift|bigquery|neo4j|dynamodb|sql\s*server)\b",
    re.IGNORECASE,
)

PARENTHETICAL_PATTERN = re.compile(
    r"^(?P<base>.+?)\s*\((?P<acronym>[A-Za-z][A-Za-z0-9+#./-]{1,14})\)\s*$"
)
SHORT_ALIAS_CONTEXT = frozenset({"c", "r", "j", "go"})
PROGRAMMING_CONTEXT_PATTERN = re.compile(
    r"\b(?:language|programming|coding|scripting|developer|software|technical\s+skills|"
    r"skills|experience|experienced|proficien(?:t|cy)|familiar|knowledge|framework|"
    r"library|stack|engineer|architect)\b",
    re.IGNORECASE,
)
ALIAS_BOUNDARY_LEFT = r"(?<![A-Za-z0-9+#])"
ALIAS_BOUNDARY_RIGHT = r"(?![A-Za-z0-9+#])"


def classify_skill(canonical_skill: str, element_names: Iterable[str]) -> tuple[str, str]:
    """Classify a dictionary skill using transparent, documented rules."""
    key = normalize_key(canonical_skill)
    element_text = " | ".join(normalize_key(name) for name in element_names)

    if key in PROGRAMMING_LANGUAGE_NAMES or LANGUAGE_WORD_PATTERN.search(canonical_skill):
        return CATEGORY_PROGRAMMING, "language_name_or_language_word"
    if (
        LANGUAGE_TOKEN_PATTERN.search(canonical_skill)
        and not LANGUAGE_BLOCK_PATTERN.search(canonical_skill)
    ):
        return CATEGORY_PROGRAMMING, "language_token_rule"
    if key in FRAMEWORK_LIBRARY_NAMES or FRAMEWORK_NAME_PATTERN.search(canonical_skill):
        return CATEGORY_FRAMEWORK, "framework_library_name_rule"
    if key in DATABASE_NAMES or DATABASE_NAME_PATTERN.search(canonical_skill):
        return CATEGORY_DATABASE, "database_name_rule"
    if any(hint in element_text for hint in DATABASE_ELEMENT_HINTS):
        return CATEGORY_DATABASE, "onet_element_database_rule"
    if any(hint in element_text for hint in FRAMEWORK_ELEMENT_HINTS):
        return CATEGORY_FRAMEWORK, "onet_element_framework_rule"
    return CATEGORY_TOOL, "default_tool_rule"


def alias_variants(display: str) -> list[tuple[str, str]]:
    """Return deterministic aliases derived only from a dictionary entry."""
    variants: list[tuple[str, str]] = [(display, "canonical_surface")]
    parenthetical = PARENTHETICAL_PATTERN.match(display)
    if parenthetical:
        base = normalize_text(parenthetical.group("base"))
        acronym = normalize_text(parenthetical.group("acronym"))
        if base and base != display:
            variants.append((base, "expanded_parenthetical"))
        if acronym and acronym != display:
            variants.append((acronym, "parenthetical_acronym"))

    compact = compact_key(display)
    if len(compact) >= 2 and normalize_key(compact) != normalize_key(display):
        variants.append((compact, "compact_punctuation"))

    deduplicated: dict[tuple[str, str], tuple[str, str]] = {}
    for alias, alias_type in variants:
        if alias:
            deduplicated[(normalize_key(alias), alias_type)] = (alias, alias_type)
    return list(deduplicated.values())


def build_skill_dictionary(
    skills: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, "SkillMatcher"]:
    """Build canonical skills, aliases, occupation links, and a matcher."""
    LOGGER.info("Building skills dictionary from %d skill rows", len(skills))
    frame = skills.copy()
    frame["_display"] = frame["Workplace Example_clean"].map(normalize_text)
    frame["_skill_key"] = frame["_display"].map(normalize_key)

    if frame["_skill_key"].eq("").any():
        raise FeatureEngineeringError("The skills dictionary contains an empty skill name.")

    canonical_by_key: dict[str, str] = {}
    for key, group in frame.groupby("_skill_key", sort=True):
        counts = group["_display"].value_counts()
        ranked = sorted(counts.index, key=lambda value: (-counts[value], value.casefold(), value))
        canonical_by_key[str(key)] = normalize_text(ranked[0])

    canonical_names = sorted(set(canonical_by_key.values()), key=lambda value: (value.casefold(), value))
    canonical_id_by_name = {
        name: f"SKILL_{position:05d}"
        for position, name in enumerate(canonical_names, start=1)
    }
    frame["canonical_skill"] = frame["_skill_key"].map(canonical_by_key)
    frame["canonical_skill_id"] = frame["canonical_skill"].map(canonical_id_by_name)

    dictionary_rows: list[dict[str, Any]] = []
    alias_rows: list[dict[str, Any]] = []
    for canonical_skill, group in frame.groupby("canonical_skill", sort=True):
        element_names = sorted(set(group["Element Name_clean"].dropna().astype(str)))
        category, category_basis = classify_skill(canonical_skill, element_names)
        source_rows = sorted({int(value) for value in group["source_row_index"]})
        canonical_id = canonical_id_by_name[canonical_skill]
        occupation_codes = sorted(set(group["O*NET-SOC Code"].dropna().astype(str)))
        occupation_titles = sorted(set(group["Title_clean"].dropna().astype(str)))
        element_ids = sorted(set(group["Element ID"].dropna().astype(str)))

        variants: dict[tuple[str, str], tuple[str, str]] = {}
        for display in sorted(set(group["_display"].astype(str))):
            for alias, alias_type in alias_variants(display):
                variants[(normalize_key(alias), alias_type)] = (alias, alias_type)
        for alias, alias_type in sorted(variants.values(), key=lambda item: (normalize_key(item[0]), item[1])):
            alias_rows.append(
                {
                    "canonical_skill_id": canonical_id,
                    "canonical_skill": canonical_skill,
                    "alias": alias,
                    "alias_key": normalize_key(alias),
                    "alias_type": alias_type,
                    "source_row_count": len(source_rows),
                    "source_row_indices": "; ".join(str(value) for value in source_rows),
                }
            )

        dictionary_rows.append(
            {
                "canonical_skill_id": canonical_id,
                "canonical_skill": canonical_skill,
                "skill_key": normalize_key(canonical_skill),
                "skill_category": category,
                "category_basis": category_basis,
                "aliases": unique_join([item[0] for item in variants.values()]),
                "alias_count": len(variants),
                "onet_element_names": unique_join(element_names),
                "onet_element_ids": unique_join(element_ids),
                "occupation_codes": unique_join(occupation_codes),
                "occupation_titles": unique_join(occupation_titles),
                "occupation_count": len(occupation_codes),
                "hot_technology": "Y" if (group["Hot Technology_clean"] == "Y").any() else "N",
                "in_demand": "Y" if (group["In Demand_clean"] == "Y").any() else "N",
                "source_row_count": len(source_rows),
                "source_row_indices": "; ".join(str(value) for value in source_rows),
            }
        )

    dictionary = pd.DataFrame(dictionary_rows)
    aliases = pd.DataFrame(alias_rows)

    conflict_lookup = (
        aliases.groupby("alias_key")["canonical_skill"]
        .agg(lambda values: sorted(set(values)))
        .to_dict()
    )

    def conflicting_names(row: pd.Series) -> str:
        others = [
            name
            for name in conflict_lookup.get(row["alias_key"], [])
            if name != row["canonical_skill"]
        ]
        return "; ".join(others)

    aliases["is_ambiguous"] = aliases["alias_key"].map(
        lambda key: len(conflict_lookup.get(key, [])) > 1
    )
    aliases["conflicting_canonical_skills"] = aliases.apply(conflicting_names, axis=1)
    aliases["match_enabled"] = ~aliases["is_ambiguous"]
    aliases.insert(0, "alias_id", [f"ALIAS_{position:06d}" for position in range(1, len(aliases) + 1)])

    dictionary["alias_count"] = dictionary["canonical_skill_id"].map(
        aliases.groupby("canonical_skill_id").size()
    ).fillna(0).astype(int)
    dictionary["ambiguous_alias_count"] = dictionary["canonical_skill_id"].map(
        aliases[aliases["is_ambiguous"]].groupby("canonical_skill_id").size()
    ).fillna(0).astype(int)
    dictionary["match_alias_count"] = dictionary["canonical_skill_id"].map(
        aliases[aliases["match_enabled"]].groupby("canonical_skill_id").size()
    ).fillna(0).astype(int)

    occupation_map = frame[
        [
            "source_row_index",
            "O*NET-SOC Code",
            "Title",
            "Title_clean",
            "Workplace Example",
            "Workplace Example_clean",
            "Element ID",
            "Element Name",
            "Element Name_clean",
            "Hot Technology_clean",
            "In Demand_clean",
            "canonical_skill_id",
            "canonical_skill",
        ]
    ].copy()
    occupation_map = occupation_map.rename(
        columns={
            "Hot Technology_clean": "Hot Technology",
            "In Demand_clean": "In Demand",
        }
    )
    occupation_map["skill_category"] = occupation_map["canonical_skill"].map(
        dictionary.set_index("canonical_skill")["skill_category"]
    )

    matcher = SkillMatcher(dictionary, aliases)
    LOGGER.info(
        "Skills dictionary: %d canonical skills, %d aliases (%d ambiguous), %d skill-occupation rows",
        len(dictionary),
        len(aliases),
        int(aliases["is_ambiguous"].sum()),
        len(occupation_map),
    )
    return dictionary, aliases, occupation_map, matcher


class SkillMatcher:
    """Case-insensitive, alias-aware dictionary matcher with safe boundaries."""

    def __init__(self, dictionary: pd.DataFrame, aliases: pd.DataFrame) -> None:
        self.category_by_skill = dictionary.set_index("canonical_skill")["skill_category"].to_dict()
        self.id_by_skill = dictionary.set_index("canonical_skill")["canonical_skill_id"].to_dict()

        enabled = aliases[aliases["match_enabled"]].copy()
        enabled["_literal_key"] = enabled["alias"].map(normalize_key)
        enabled = enabled.drop_duplicates(["canonical_skill", "_literal_key"])

        self.alias_lookup: dict[str, dict[str, str]] = {}
        for _, row in enabled.iterrows():
            literal_key = row["_literal_key"]
            if literal_key not in self.alias_lookup:
                self.alias_lookup[literal_key] = {
                    "canonical_skill": row["canonical_skill"],
                    "alias": row["alias"],
                    "alias_type": row["alias_type"],
                    "requires_context": literal_key in SHORT_ALIAS_CONTEXT,
                }

        literals = sorted(
            self.alias_lookup.keys(),
            key=lambda value: (-len(value), value),
        )
        self.patterns: list[re.Pattern[str]] = []
        chunk_size = 300
        for start in range(0, len(literals), chunk_size):
            chunk = literals[start : start + chunk_size]
            expression = (
                ALIAS_BOUNDARY_LEFT
                + "(?:"
                + "|".join(re.escape(value) for value in chunk)
                + ")"
                + ALIAS_BOUNDARY_RIGHT
            )
            self.patterns.append(re.compile(expression, re.IGNORECASE))

    def _context_ok(self, text: str, start: int, end: int) -> bool:
        window = text[max(0, start - 120) : min(len(text), end + 120)]
        return PROGRAMMING_CONTEXT_PATTERN.search(window) is not None

    def find_matches(self, text: Any) -> list[dict[str, Any]]:
        if not isinstance(text, str) or not text:
            return []

        raw: list[dict[str, Any]] = []
        for pattern in self.patterns:
            for match in pattern.finditer(text):
                literal_key = normalize_key(match.group(0))
                info = self.alias_lookup.get(literal_key)
                if info is None:
                    continue
                if info["requires_context"] and not self._context_ok(
                    text, match.start(), match.end()
                ):
                    continue
                raw.append(
                    {
                        "canonical_skill": info["canonical_skill"],
                        "canonical_skill_id": self.id_by_skill[info["canonical_skill"]],
                        "skill_category": self.category_by_skill[info["canonical_skill"]],
                        "alias": info["alias"],
                        "alias_type": info["alias_type"],
                        "start": match.start(),
                        "end": match.end(),
                    }
                )

        raw.sort(key=lambda item: (item["start"], -(item["end"] - item["start"]), item["canonical_skill"]))
        deduplicated: list[dict[str, Any]] = []
        seen: set[tuple[int, int, str]] = set()
        for item in raw:
            key = (item["start"], item["end"], item["canonical_skill"])
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(item)

        kept: list[dict[str, Any]] = []
        for item in deduplicated:
            contained = any(
                other["start"] <= item["start"]
                and item["end"] <= other["end"]
                and (other["end"] - other["start"]) > (item["end"] - item["start"])
                for other in deduplicated
            )
            if not contained:
                kept.append(item)

        for item in kept:
            item["evidence"] = context_window(text, item["start"], item["end"])
            item["match_confidence"] = self._confidence(item)
        return kept

    @staticmethod
    def _confidence(item: dict[str, Any]) -> str:
        alias_key = normalize_key(item["alias"])
        if alias_key in SHORT_ALIAS_CONTEXT:
            return "low"
        if item["alias_type"] in {"canonical_surface", "expanded_parenthetical"} and len(alias_key) >= 4:
            return "high"
        return "medium"

    def find_grouped(self, text: Any) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for match in self.find_matches(text):
            grouped[match["canonical_skill"]].append(match)

        results: list[dict[str, Any]] = []
        for canonical_skill, matches in grouped.items():
            matches.sort(key=lambda item: (item["start"], item["end"]))
            best = sorted(
                matches,
                key=lambda item: (
                    {"high": 0, "medium": 1, "low": 2}[item["match_confidence"]],
                    item["start"],
                ),
            )[0]
            results.append(
                {
                    "canonical_skill": canonical_skill,
                    "canonical_skill_id": best["canonical_skill_id"],
                    "skill_category": best["skill_category"],
                    "match_count": len(matches),
                    "matched_aliases": unique_join([item["alias"] for item in matches]),
                    "matched_alias": best["alias"],
                    "alias_type": best["alias_type"],
                    "match_confidence": best["match_confidence"],
                    "first_evidence": best["evidence"],
                    "matches": matches,
                }
            )
        return sorted(results, key=lambda item: item["canonical_skill"].casefold())


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

RESUME_HEADING_SPECS = [
    (r"professional\s+summary", "summary"),
    (r"summary\s+of\s+qualifications", "summary"),
    (r"professional\s+profile", "summary"),
    (r"career\s+summary", "summary"),
    (r"technical\s+skills", "skills"),
    (r"core\s+competencies", "skills"),
    (r"work\s+experience", "experience"),
    (r"professional\s+experience", "experience"),
    (r"work\s+history", "experience"),
    (r"employment\s+history", "experience"),
    (r"professional\s+summary", "summary"),
    (r"objective", "summary"),
    (r"highlights", "highlights"),
    (r"accomplishments", "accomplishments"),
    (r"qualifications", "qualifications"),
    (r"certifications", "certifications"),
    (r"certificates", "certifications"),
    (r"projects", "projects"),
    (r"languages", "languages"),
    (r"skills", "skills"),
    (r"summary", "summary"),
    (r"experience", "experience"),
    (r"employment", "employment"),
    (r"education", "education"),
]

JOB_HEADING_SPECS = [
    (r"job\s+description", "job_description"),
    (r"essential\s+duties\s*/?\s*responsibilities", "responsibilities"),
    (r"major\s+responsibilities\s+and\s+duties", "responsibilities"),
    (r"responsibilities\s+and\s+duties", "responsibilities"),
    (r"key\s+responsibilities", "responsibilities"),
    (r"essential\s+duties", "responsibilities"),
    (r"major\s+responsibilities", "responsibilities"),
    (r"minimum\s+qualifications", "minimum_qualifications"),
    (r"preferred\s+qualifications", "preferred_qualifications"),
    (r"required\s+skills", "required_skills"),
    (r"preferred\s+skills", "preferred_skills"),
    (r"desired\s+skills", "preferred_skills"),
    (r"nice\s+to\s+have", "preferred_skills"),
    (r"responsibilities", "responsibilities"),
    (r"duties", "duties"),
    (r"qualifications", "qualifications"),
    (r"requirements", "requirements"),
    (r"competencies", "requirements"),
    (r"education\s+and\s+experience", "qualifications"),
    (r"description", "description"),
    (r"overview", "overview"),
    (r"major\s+purpose", "summary"),
    (r"summary", "summary"),
    (r"benefits", "benefits"),
    (r"education", "education"),
    (r"experience", "experience"),
    (r"skills", "skills"),
    (r"about", "about"),
]


def compile_heading_pattern(specs: list[tuple[str, str]]) -> tuple[re.Pattern[str], dict[str, str]]:
    ordered = sorted(specs, key=lambda item: (-len(item[0]), item[0]))
    parts = []
    group_types: dict[str, str] = {}
    for position, (pattern, section_type) in enumerate(ordered):
        group_name = f"H{position}"
        parts.append(f"(?P<{group_name}>{pattern})")
        group_types[group_name] = section_type
    return re.compile("|".join(parts), re.IGNORECASE), group_types


RESUME_HEADING_PATTERN, RESUME_GROUP_TYPES = compile_heading_pattern(RESUME_HEADING_SPECS)
JOB_HEADING_PATTERN, JOB_GROUP_TYPES = compile_heading_pattern(JOB_HEADING_SPECS)


def heading_boundary_is_valid(
    text: str,
    start: int,
    end: int,
    heading: str,
    section_type: str,
    profile: str,
) -> bool:
    if start == 0:
        return True

    before = text[:start].rstrip()
    after = text[end:]
    after_lstrip = after.lstrip()

    if after_lstrip.startswith(":"):
        return True
    if before and before[-1] in ".:;!?•|\n":
        return True
    if heading.isupper():
        return True

    next_word_match = re.match(r"\s*([A-Za-z]+)", after)
    next_word = next_word_match.group(1).casefold() if next_word_match else ""
    if section_type in {"experience", "required_skills", "preferred_skills"} and next_word in {
        "with",
        "in",
        "using",
        "required",
        "preferred",
        "and",
    }:
        return False

    if profile == "resume":
        if start < 250:
            previous_words = before.split()
            if previous_words and previous_words[-1].isupper():
                return True
        if heading and heading[0].isupper() and next_word not in {"and", "or", "of", "with", "in", "for"}:
            return True

    return False


def extract_sections(text: Any, specs: list[tuple[str, str]], profile: str) -> list[dict[str, Any]]:
    if not isinstance(text, str) or not text.strip():
        return []
    if profile == "resume":
        pattern, group_types = RESUME_HEADING_PATTERN, RESUME_GROUP_TYPES
    else:
        pattern, group_types = JOB_HEADING_PATTERN, JOB_GROUP_TYPES

    headings: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        group_name = match.lastgroup
        if group_name is None:
            continue
        section_type = group_types[group_name]
        heading = match.group(0)
        if not heading_boundary_is_valid(
            text, match.start(), match.end(), heading, section_type, profile
        ):
            continue
        headings.append(
            {
                "heading": normalize_text(heading),
                "section_type": section_type,
                "heading_start": match.start(),
                "heading_end": match.end(),
            }
        )

    if not headings:
        return [
            {
                "heading": "",
                "section_type": "full_text",
                "heading_start": 0,
                "heading_end": 0,
                "start_char": 0,
                "end_char": len(text),
                "section_text": text.strip(),
            }
        ]

    sections: list[dict[str, Any]] = []
    if headings[0]["heading_start"] > 0:
        preamble = text[: headings[0]["heading_start"]].strip()
        if preamble:
            sections.append(
                {
                    "heading": "",
                    "section_type": "preamble",
                    "heading_start": 0,
                    "heading_end": 0,
                    "start_char": 0,
                    "end_char": headings[0]["heading_start"],
                    "section_text": preamble,
                }
            )

    for position, heading in enumerate(headings):
        content_start = heading["heading_end"]
        if text[content_start : content_start + 1] == ":":
            content_start += 1
        while content_start < len(text) and text[content_start].isspace():
            content_start += 1
        content_end = (
            headings[position + 1]["heading_start"]
            if position + 1 < len(headings)
            else len(text)
        )
        if content_end < content_start:
            content_end = content_start
        sections.append(
            {
                "heading": heading["heading"],
                "section_type": heading["section_type"],
                "heading_start": heading["heading_start"],
                "heading_end": heading["heading_end"],
                "start_char": content_start,
                "end_char": content_end,
                "section_text": text[content_start:content_end].strip(),
            }
        )
    return sections


def section_text_for_types(sections: list[dict[str, Any]], types: Iterable[str]) -> str:
    wanted = set(types)
    texts = [
        section["section_text"]
        for section in sections
        if section["section_type"] in wanted and section["section_text"]
    ]
    return " | ".join(texts)


def section_for_position(sections: list[dict[str, Any]], position: int) -> dict[str, Any] | None:
    for section in sections:
        if section["start_char"] <= position <= section["end_char"]:
            return section
    return None


SENTENCE_PATTERN = re.compile(r"[^.!?;\u2022\n]+(?:[.!?;]|$)", re.UNICODE)


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    results: list[tuple[int, int, str]] = []
    for match in SENTENCE_PATTERN.finditer(text):
        sentence = normalize_text(match.group(0))
        if sentence:
            results.append((match.start(), match.end(), sentence))
    return results


# ---------------------------------------------------------------------------
# Résumé extraction
# ---------------------------------------------------------------------------

DEGREE_PATTERN = re.compile(
    r"(?i)\b(?:associate(?:'s)?(?:\s+degree)?|bachelor(?:'s)?(?:\s+degree)?|"
    r"master(?:'s)?(?:\s+degree)?|doctor(?:ate|al)?(?:\s+degree)?|ph\.?\s*d\.?|mba|"
    r"b\.?\s*s\.?\s*c?\.?|b\.?\s*a\.?|m\.?\s*s\.?\s*c?\.?|m\.?\s*a\.?|"
    r"high\s+school\s+diploma|ged|diploma)\b"
    r"(?:\s+(?:of|in)\s+[A-Za-z][A-Za-z &,'\-/]{2,50})?"
)
STRONG_DEGREE_PATTERN = re.compile(
    r"(?i)\b(?:bachelor(?:'s)?|master(?:'s)?|doctor(?:ate|al)?|ph\.?\s*d\.?|mba|"
    r"associate(?:'s)?|high\s+school\s+diploma|ged|diploma)\b"
    r"(?:\s+(?:of|in)\s+[A-Za-z][A-Za-z &,'\-/]{2,50})?"
)
INSTITUTION_PATTERN = re.compile(
    r"\b[A-Z][A-Za-z&.'\-]+(?:\s+[A-Z][A-Za-z&.'\-]+){1,5}\s+"
    r"(?:University|College|Institute|School|Academy|Polytechnic)\b"
)
INSTITUTION_OF_PATTERN = re.compile(
    r"\b(?:University|College|Institute|School|Academy|Polytechnic)\s+of\s+"
    r"[A-Z][A-Za-z&.'\- ]{2,60}\b"
)

EXPERIENCE_RANGE_PATTERN = re.compile(
    r"(?i)(?P<lo>\d{1,2})\s*\+?\s*(?:-|–|to)\s*(?P<hi>\d{1,2})\s*\+?\s*(?:years?|yrs?)"
)
EXPERIENCE_PLUS_PATTERN = re.compile(r"(?i)(?P<lo>\d{1,2})\s*\+\s*(?:years?|yrs?)")
EXPERIENCE_AT_LEAST_PATTERN = re.compile(
    r"(?i)(?:at\s+least|minimum(?:\s+of)?|over|more\s+than)\s*"
    r"(?P<lo>\d{1,2})\s*\+?\s*(?:years?|yrs?)"
)
EXPERIENCE_YEARS_EXPERIENCE_PATTERN = re.compile(
    r"(?i)(?P<lo>\d{1,2})\s*(?:years?|yrs?)(?:\s+of)?\s+"
    r"(?:[A-Za-z]+\s+){0,3}experience"
)
DATE_RANGE_PATTERN = re.compile(
    r"(?i)(?:(?:0?[1-9]|1[0-2])/\d{4}|\b(?:19|20)\d{2}\b)\s*"
    r"(?:to|-|–)\s*"
    r"(?:current|present|(?:0?[1-9]|1[0-2])/\d{4}|\b(?:19|20)\d{2}\b)"
)


def extract_degree_mentions(text: str, allow_weak: bool) -> list[str]:
    pattern = DEGREE_PATTERN if allow_weak else STRONG_DEGREE_PATTERN
    mentions = [normalize_text(match.group(0)) for match in pattern.finditer(text)]
    return sorted(set(mentions), key=lambda value: (value.casefold(), value))


def extract_institution_mentions(text: str) -> list[str]:
    mentions = [match.group(0) for match in INSTITUTION_PATTERN.finditer(text)]
    mentions += [match.group(0) for match in INSTITUTION_OF_PATTERN.finditer(text)]
    cleaned = [normalize_text(value).strip(" ,.;-") for value in mentions]
    return sorted(
        {value for value in cleaned if len(value) >= 8},
        key=lambda value: (value.casefold(), value),
    )


def extract_stated_experience_years(text: str) -> dict[str, Any]:
    values: list[dict[str, Any]] = []

    for match in EXPERIENCE_RANGE_PATTERN.finditer(text):
        low = int(match.group("lo"))
        high = int(match.group("hi"))
        values.append(
            {
                "min": min(low, high),
                "max": max(low, high),
                "open_ended": "+" in match.group(0),
                "evidence": match.group(0),
            }
        )
    for match in EXPERIENCE_PLUS_PATTERN.finditer(text):
        value = int(match.group("lo"))
        values.append(
            {"min": value, "max": value, "open_ended": True, "evidence": match.group(0)}
        )
    for match in EXPERIENCE_AT_LEAST_PATTERN.finditer(text):
        value = int(match.group("lo"))
        values.append(
            {"min": value, "max": value, "open_ended": True, "evidence": match.group(0)}
        )
    for match in EXPERIENCE_YEARS_EXPERIENCE_PATTERN.finditer(text):
        value = int(match.group("lo"))
        values.append(
            {"min": value, "max": value, "open_ended": False, "evidence": match.group(0)}
        )

    if not values:
        return {
            "min": None,
            "max": None,
            "open_ended": False,
            "evidence": "",
        }
    return {
        "min": min(item["min"] for item in values),
        "max": max(item["max"] for item in values),
        "open_ended": any(item["open_ended"] for item in values),
        "evidence": unique_join([item["evidence"] for item in values], separator=" | ", limit=4),
    }


def extract_resume_record(
    row: pd.Series,
    matcher: SkillMatcher,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    text = normalize_text(row["clean_text"])
    sections = extract_sections(text, RESUME_HEADING_SPECS, profile="resume")
    education_text = section_text_for_types(sections, {"education"})
    experience_text = section_text_for_types(
        sections, {"experience", "employment", "work_history"}
    )

    if education_text:
        degrees = extract_degree_mentions(education_text, allow_weak=True)
        institutions = extract_institution_mentions(education_text)
        education_source = "explicit_section" if (degrees or institutions) else "explicit_section_no_detail"
    else:
        degrees = extract_degree_mentions(text, allow_weak=False)
        institutions = extract_institution_mentions(text)
        education_source = "keyword_scan" if (degrees or institutions) else "none"

    stated_years = extract_stated_experience_years(text)
    date_range_source = experience_text or text
    entry_count = len(DATE_RANGE_PATTERN.findall(date_range_source))
    if experience_text:
        experience_source = "explicit_section"
    elif stated_years["min"] is not None:
        experience_source = "stated_years_scan"
    else:
        experience_source = "none"

    grouped_matches = matcher.find_grouped(text)
    skill_rows: list[dict[str, Any]] = []
    by_category: dict[str, list[str]] = {category: [] for category in SKILL_CATEGORIES}
    for match in grouped_matches:
        by_category[match["skill_category"]].append(match["canonical_skill"])
        skill_rows.append(
            {
                "resume_id": int(row["ID"]),
                "source_row_index": int(row["source_row_index"]),
                "category": row["Category"],
                "canonical_skill_id": match["canonical_skill_id"],
                "canonical_skill": match["canonical_skill"],
                "skill_category": match["skill_category"],
                "matched_alias": match["matched_alias"],
                "matched_aliases": match["matched_aliases"],
                "alias_type": match["alias_type"],
                "match_count": match["match_count"],
                "match_confidence": match["match_confidence"],
                "first_evidence": match["first_evidence"],
            }
        )

    technical_skills = sorted(
        {match["canonical_skill"] for match in grouped_matches},
        key=lambda value: (value.casefold(), value),
    )

    feature = {
        "resume_id": int(row["ID"]),
        "source_row_index": int(row["source_row_index"]),
        "category": row["Category"],
        "clean_text_length": len(text),
        "clean_text": text,
        "technical_skills": unique_join(technical_skills),
        "technical_skill_count": len(technical_skills),
        "programming_languages": unique_join(by_category[CATEGORY_PROGRAMMING]),
        "programming_language_count": len(set(by_category[CATEGORY_PROGRAMMING])),
        "frameworks_libraries": unique_join(by_category[CATEGORY_FRAMEWORK]),
        "framework_library_count": len(set(by_category[CATEGORY_FRAMEWORK])),
        "databases": unique_join(by_category[CATEGORY_DATABASE]),
        "database_count": len(set(by_category[CATEGORY_DATABASE])),
        "tools_technologies": unique_join(by_category[CATEGORY_TOOL]),
        "tool_technology_count": len(set(by_category[CATEGORY_TOOL])),
        "education_section": education_text,
        "education_degrees": unique_join(degrees),
        "education_institutions": unique_join(institutions),
        "education_source": education_source,
        "has_education": bool(education_text or degrees or institutions),
        "experience_section": experience_text,
        "stated_experience_years_min": stated_years["min"],
        "stated_experience_years_max": stated_years["max"],
        "stated_experience_open_ended": stated_years["open_ended"],
        "experience_years_evidence": stated_years["evidence"],
        "experience_entry_count_inferred": entry_count,
        "experience_source": experience_source,
        "has_experience": bool(experience_text or stated_years["min"] is not None or entry_count),
        "feature_version": FEATURE_VERSION,
    }

    section_rows = [
        {
            "resume_id": int(row["ID"]),
            "source_row_index": int(row["source_row_index"]),
            "section_type": section["section_type"],
            "heading": section["heading"],
            "heading_start": section["heading_start"],
            "heading_end": section["heading_end"],
            "start_char": section["start_char"],
            "end_char": section["end_char"],
            "section_text": section["section_text"],
            "text_length": len(section["section_text"]),
        }
        for section in sections
    ]
    return feature, section_rows, skill_rows


# ---------------------------------------------------------------------------
# Job extraction
# ---------------------------------------------------------------------------

ROLE_WORD_PATTERN = re.compile(
    r"(?i)\b(?:developer|engineer|analyst|architect|administrator|manager|"
    r"specialist|consultant|designer|scientist|technician|programmer|tester|"
    r"qa|test|lead|director|coordinator|support|security|network|data|software|"
    r"web|mobile|frontend|front-end|backend|back-end|full\s+stack|devops|cloud|"
    r"systems?|business\s+intelligence|bi)\b"
)
TITLE_STOP_PATTERN = re.compile(
    r"(?i)\s+(?:location|duration|salary|company|job\s+description|description|"
    r"responsibilities|requirements|qualifications|candidates?|position|role|"
    r"to\s+(?:join|work|support|help|be)|who|that|with|for)\b"
)

PREFERRED_CUE_PATTERN = re.compile(
    r"(?i)\b(?:preferred|nice\s+to\s+have|desired|a\s+plus|plus|bonus|advantage|"
    r"ideally|preferred\s+qualifications?|preferred\s+skills?)\b"
)
REQUIRED_CUE_PATTERN = re.compile(
    r"(?i)\b(?:required|must\s+have|must|minimum|mandatory|need(?:ed|s)?|"
    r"qualifications?|proficien(?:t|cy)|strong\s+knowledge|experience\s+with|"
    r"knowledge\s+of|familiar\s+with|hands-on|expertise|ability\s+to|"
    r"requirements?|required\s+skills?)\b"
)
RESPONSIBILITY_CUE_PATTERN = re.compile(
    r"(?i)\b(?:responsible\s+for|responsibilities|duties|you\s+will|will\s+be|"
    r"develop|design|build|maintain|manage|implement|support|install|configure|"
    r"troubleshoot|analy[sz]e|create|lead|coordinate|provide|ensure|perform|"
    r"assist|review|test|monitor|operate)\b"
)
QUALIFICATION_CUE_PATTERN = re.compile(
    r"(?i)\b(?:required|must|minimum|qualifications?|degree|years?\s+of\s+experience|"
    r"certification|certified|proficien(?:t|cy)|knowledge\s+of|experience\s+with|"
    r"familiar\s+with|education)\b"
)

JOB_TITLE_EXPLICIT_PATTERN = re.compile(
    r"(?i)\b(?:job\s+title|title|position|role)\s*[:\-]\s*"
    r"(?P<title>[^|;\n]{2,140})"
)
JOB_TITLE_CUE_PATTERN = re.compile(
    r"(?i)\b(?:looking\s+for|seeking|hiring|need\s+for|need\s+an?|"
    r"has\s+an?\s+(?:immediate\s+)?(?:opening|need)\s+for)\s+"
    r"(?:a|an)?\s*(?P<title>[A-Z][^.;|]{2,120})"
)


def is_plausible_job_title(candidate: str) -> bool:
    title = normalize_text(candidate).strip(" -–—:;,.|")
    if not title or len(title) < 3 or len(title) > 110:
        return False
    if len(title.split()) > 12:
        return False
    if not ROLE_WORD_PATTERN.search(title):
        return False
    if re.search(
        r"(?i)\b(?:job\s+description|description|responsibilities|requirements|"
        r"qualifications|location|duration|salary|company|benefits)\b",
        title,
    ):
        return False
    if re.search(r"(?i)^(?:we|our|the|this|if|looking|seeking|greetings)\b", title):
        return False
    return True


def clean_title_candidate(candidate: str) -> str:
    title = normalize_text(candidate)
    title = TITLE_STOP_PATTERN.split(title, maxsplit=1)[0]
    title = re.split(r"\s+[|•;]\s*", title, maxsplit=1)[0]
    title = re.split(r"\s*[.,]\s+", title, maxsplit=1)[0]
    return title.strip(" -–—:;,.|")


def extract_job_title(text: str, label: str) -> dict[str, Any]:
    for pattern, source, confidence in (
        (JOB_TITLE_EXPLICIT_PATTERN, "explicit_field", "high"),
        (JOB_TITLE_CUE_PATTERN, "cue_phrase", "medium"),
    ):
        for match in pattern.finditer(text):
            candidate = clean_title_candidate(match.group("title"))
            if is_plausible_job_title(candidate):
                return {
                    "job_title": candidate,
                    "job_title_source": source,
                    "job_title_confidence": confidence,
                }

    leading = text[:220]
    for separator in (" - ", " – ", " — ", " | "):
        if separator in leading:
            candidate = clean_title_candidate(leading.split(separator, maxsplit=1)[0])
            if is_plausible_job_title(candidate):
                return {
                    "job_title": candidate,
                    "job_title_source": "leading_text",
                    "job_title_confidence": "medium",
                }

    candidate = clean_title_candidate(
        re.split(
            r"(?i)\s+(?:with|who|that|to|in|at|for|location|duration|candidates?)\b",
            leading,
            maxsplit=1,
        )[0]
    )
    if is_plausible_job_title(candidate):
        return {
            "job_title": candidate,
            "job_title_source": "leading_text",
            "job_title_confidence": "medium",
        }

    return {
        "job_title": "",
        "job_title_source": "not_identified",
        "job_title_confidence": "none",
        "role_label": label,
    }


def nearest_cue(
    text: str,
    start: int,
    end: int,
    pattern: re.Pattern[str],
    bounds: tuple[int, int] | None = None,
) -> tuple[int, str] | None:
    lower_bound, upper_bound = bounds if bounds is not None else (0, len(text))
    left = max(lower_bound, start - 220)
    right = min(upper_bound, end + 220)
    best: tuple[int, str] | None = None
    for match in pattern.finditer(text, left, right):
        if match.end() <= start:
            distance = start - match.end()
        elif match.start() >= end:
            distance = match.start() - end
        else:
            distance = 0
        if best is None or distance < best[0]:
            best = (distance, normalize_text(match.group(0)))
    return best


def classify_requirement(
    text: str,
    start: int,
    end: int,
    section_type: str,
    section_bounds: tuple[int, int] | None = None,
) -> tuple[str, str, str]:
    preferred = nearest_cue(
        text, start, end, PREFERRED_CUE_PATTERN, bounds=section_bounds
    )
    required = nearest_cue(
        text, start, end, REQUIRED_CUE_PATTERN, bounds=section_bounds
    )

    if preferred is not None and (required is None or preferred[0] < required[0]):
        return "preferred", preferred[1], section_type
    if required is not None:
        return "required", required[1], section_type

    if section_type in {"preferred_qualifications", "preferred_skills", "nice_to_have"}:
        return "preferred", "", section_type
    if section_type in {
        "required_skills",
        "requirements",
        "qualifications",
        "minimum_qualifications",
        "skills",
        "education",
        "experience",
    }:
        return "required", "", section_type
    return "mentioned", "", section_type


def extract_fallback_sentences(
    text: str,
    cue_pattern: re.Pattern[str],
    limit: int = 8,
) -> str:
    scored: list[tuple[int, int, str]] = []
    for start, _end, sentence in split_sentences(text):
        cues = cue_pattern.findall(sentence)
        if cues:
            scored.append((len(cues), start, sentence))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = [item[2] for item in scored[:limit]]
    return " | ".join(selected)


def extract_job_record(
    row: pd.Series,
    matcher: SkillMatcher,
    keywords: list[tuple[str, float]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    text = normalize_text(row["clean_description"])
    label = normalize_text(row["label_clean"])
    sections = extract_sections(text, JOB_HEADING_SPECS, profile="job")
    title_info = extract_job_title(text, label)

    responsibilities = section_text_for_types(
        sections,
        {"responsibilities", "duties", "key_responsibilities"},
    )
    responsibilities_source = "section" if responsibilities else "none"
    if not responsibilities:
        responsibilities = extract_fallback_sentences(text, RESPONSIBILITY_CUE_PATTERN)
        responsibilities_source = "sentence_cue" if responsibilities else "none"

    qualifications = section_text_for_types(
        sections,
        {
            "qualifications",
            "minimum_qualifications",
            "preferred_qualifications",
            "required_skills",
            "preferred_skills",
            "requirements",
            "skills",
            "education",
            "experience",
        },
    )
    qualifications_source = "section" if qualifications else "none"
    if not qualifications:
        qualifications = extract_fallback_sentences(text, QUALIFICATION_CUE_PATTERN)
        qualifications_source = "sentence_cue" if qualifications else "none"

    grouped_matches = matcher.find_grouped(text)
    skill_rows: list[dict[str, Any]] = []
    requirement_lists: dict[str, list[str]] = {kind: [] for kind in REQUIREMENT_TYPES}
    for match in grouped_matches:
        classified: list[tuple[str, str, str, dict[str, Any]]] = []
        for occurrence in match["matches"]:
            section = section_for_position(sections, occurrence["start"])
            section_type = section["section_type"] if section else "full_text"
            section_bounds = (
                (section["start_char"], section["end_char"]) if section else None
            )
            requirement_type, cue, used_section = classify_requirement(
                text,
                occurrence["start"],
                occurrence["end"],
                section_type,
                section_bounds=section_bounds,
            )
            classified.append((requirement_type, cue, used_section, occurrence))

        priority = {"required": 3, "preferred": 2, "mentioned": 1}
        classified.sort(
            key=lambda item: (
                -priority[item[0]],
                {"high": 0, "medium": 1, "low": 2}[item[3]["match_confidence"]],
                item[3]["start"],
            )
        )
        requirement_type, cue, section_type, best_occurrence = classified[0]
        requirement_lists[requirement_type].append(match["canonical_skill"])
        skill_rows.append(
            {
                "job_id": f"JOB_{int(row['source_row_index']):08d}",
                "source_row_index": int(row["source_row_index"]),
                "label_clean": label,
                "canonical_skill_id": match["canonical_skill_id"],
                "canonical_skill": match["canonical_skill"],
                "skill_category": match["skill_category"],
                "requirement_type": requirement_type,
                "requirement_cue": cue,
                "section_type": section_type,
                "matched_alias": match["matched_alias"],
                "matched_aliases": match["matched_aliases"],
                "alias_type": match["alias_type"],
                "match_count": match["match_count"],
                "match_confidence": match["match_confidence"],
                "evidence": context_window(text, best_occurrence["start"], best_occurrence["end"]),
            }
        )

    required_skills = sorted(
        set(requirement_lists["required"]), key=lambda value: (value.casefold(), value)
    )
    preferred_skills = sorted(
        set(requirement_lists["preferred"]), key=lambda value: (value.casefold(), value)
    )
    mentioned_skills = sorted(
        set(requirement_lists["mentioned"]), key=lambda value: (value.casefold(), value)
    )
    all_skills = sorted(
        {match["canonical_skill"] for match in grouped_matches},
        key=lambda value: (value.casefold(), value),
    )

    feature = {
        "job_id": f"JOB_{int(row['source_row_index']):08d}",
        "source_row_index": int(row["source_row_index"]),
        "label": row["label"],
        "label_clean": label,
        "clean_description_length": len(text),
        "clean_description": text,
        "short_description_flag": bool(row["short_description_flag"]),
        "job_title": title_info["job_title"],
        "job_title_source": title_info["job_title_source"],
        "job_title_confidence": title_info["job_title_confidence"],
        "required_skills": unique_join(required_skills),
        "required_skill_count": len(required_skills),
        "preferred_skills": unique_join(preferred_skills),
        "preferred_skill_count": len(preferred_skills),
        "other_skills": unique_join(mentioned_skills),
        "other_skill_count": len(mentioned_skills),
        "all_extracted_skills": unique_join(all_skills),
        "all_extracted_skill_count": len(all_skills),
        "responsibilities": responsibilities,
        "responsibilities_source": responsibilities_source,
        "qualifications": qualifications,
        "qualifications_source": qualifications_source,
        "relevant_keywords": "; ".join(item[0] for item in keywords),
        "relevant_keyword_count": len(keywords),
        "feature_version": FEATURE_VERSION,
    }

    section_rows = [
        {
            "job_id": f"JOB_{int(row['source_row_index']):08d}",
            "source_row_index": int(row["source_row_index"]),
            "section_type": section["section_type"],
            "heading": section["heading"],
            "heading_start": section["heading_start"],
            "heading_end": section["heading_end"],
            "start_char": section["start_char"],
            "end_char": section["end_char"],
            "section_text": section["section_text"],
            "text_length": len(section["section_text"]),
        }
        for section in sections
    ]
    keyword_rows = [
        {
            "job_id": f"JOB_{int(row['source_row_index']):08d}",
            "source_row_index": int(row["source_row_index"]),
            "rank": rank,
            "keyword": keyword,
            "score": score,
        }
        for rank, (keyword, score) in enumerate(keywords, start=1)
    ]
    return feature, section_rows, skill_rows, keyword_rows


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

GENERIC_JOB_STOPWORDS = frozenset(
    {
        "ability",
        "able",
        "across",
        "also",
        "applicant",
        "applicants",
        "apply",
        "candidate",
        "candidates",
        "company",
        "client",
        "clients",
        "degree",
        "environment",
        "equivalent",
        "etc",
        "excellent",
        "experience",
        "experienced",
        "field",
        "including",
        "job",
        "knowledge",
        "looking",
        "must",
        "need",
        "needs",
        "opportunity",
        "plus",
        "position",
        "preferred",
        "related",
        "required",
        "requirement",
        "requirements",
        "role",
        "skill",
        "skills",
        "strong",
        "team",
        "teams",
        "using",
        "work",
        "working",
        "year",
        "years",
    }
)


def compute_tfidf_keywords(
    texts: list[str],
    top_n: int = KEYWORD_TOP_N,
) -> tuple[list[list[tuple[str, float]]], str]:
    """Return top TF-IDF terms per text and the method name."""
    if importlib.util.find_spec("sklearn") is not None:
        from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

        stop_words = sorted(set(ENGLISH_STOP_WORDS) | GENERIC_JOB_STOPWORDS)
        vectorizer = TfidfVectorizer(
            stop_words=stop_words,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.85,
            max_features=40000,
            sublinear_tf=True,
            token_pattern=r"(?u)\b[A-Za-z][A-Za-z0-9+#./\-]{1,}\b",
        )
        matrix = vectorizer.fit_transform(texts)
        feature_names = vectorizer.get_feature_names_out()
        results: list[list[tuple[str, float]]] = []
        for row_index in range(matrix.shape[0]):
            row = matrix.getrow(row_index)
            if row.nnz == 0:
                results.append([])
                continue
            order = np.argsort(row.data)[-top_n:][::-1]
            results.append(
                [
                    (str(feature_names[row.indices[position]]), float(row.data[position]))
                    for position in order
                ]
            )
        return results, "scikit-learn_tfidf"

    token_pattern = re.compile(r"\b[a-z][a-z0-9+#./\-]{1,}\b")
    stop_words = GENERIC_JOB_STOPWORDS
    tokenized: list[list[str]] = []
    document_frequency: Counter[str] = Counter()
    for text in texts:
        tokens = [
            token
            for token in token_pattern.findall(normalize_text(text).casefold())
            if token not in stop_words
        ]
        tokenized.append(tokens)
        document_frequency.update(set(tokens))

    results = []
    document_count = max(1, len(texts))
    for tokens in tokenized:
        counts = Counter(tokens)
        scored = [
            (token, count * math.log((document_count + 1) / (document_frequency[token] + 1)))
            for token, count in counts.items()
        ]
        scored.sort(key=lambda item: (-item[1], item[0]))
        results.append(scored[:top_n])
    return results, "frequency_idf_fallback"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def build_resume_outputs(
    resumes: pd.DataFrame,
    matcher: SkillMatcher,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    LOGGER.info("Extracting features for %d résumés", len(resumes))
    features: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    skill_rows: list[dict[str, Any]] = []
    for position, (_, row) in enumerate(resumes.iterrows(), start=1):
        feature, section_rows, record_skill_rows = extract_resume_record(row, matcher)
        features.append(feature)
        sections.extend(section_rows)
        skill_rows.extend(record_skill_rows)
        if position % 500 == 0:
            LOGGER.info("Résumé features: %d/%d", position, len(resumes))
    return pd.DataFrame(features), pd.DataFrame(sections), pd.DataFrame(skill_rows)


def build_job_outputs(
    jobs: pd.DataFrame,
    matcher: SkillMatcher,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    LOGGER.info("Extracting features for %d job descriptions", len(jobs))
    texts = [normalize_text(value) for value in jobs["clean_description"].tolist()]
    keyword_lists, keyword_method = compute_tfidf_keywords(texts)
    LOGGER.info("Keyword extraction method: %s", keyword_method)

    features: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    skill_rows: list[dict[str, Any]] = []
    keyword_rows: list[dict[str, Any]] = []
    for position, ((_, row), keywords) in enumerate(
        zip(jobs.iterrows(), keyword_lists, strict=True), start=1
    ):
        feature, section_rows, record_skill_rows, record_keyword_rows = extract_job_record(
            row, matcher, keywords
        )
        features.append(feature)
        sections.extend(section_rows)
        skill_rows.extend(record_skill_rows)
        keyword_rows.extend(record_keyword_rows)
        if position % 1000 == 0:
            LOGGER.info("Job features: %d/%d", position, len(jobs))
    return (
        pd.DataFrame(features),
        pd.DataFrame(sections),
        pd.DataFrame(skill_rows),
        pd.DataFrame(keyword_rows),
    )


def collect_environment() -> dict[str, str]:
    environment = {
        "python": sys.version.split()[0],
        "pandas": pd.__version__,
        "numpy": np.__version__,
    }
    if importlib.util.find_spec("sklearn") is not None:
        import sklearn

        environment["scikit-learn"] = sklearn.__version__
    else:
        environment["scikit-learn"] = "not_installed"
    if importlib.util.find_spec("spacy") is not None:
        import spacy

        environment["spacy"] = spacy.__version__
    else:
        environment["spacy"] = "not_installed"
    return environment


def build_statistics(
    resumes: pd.DataFrame,
    jobs: pd.DataFrame,
    dictionary: pd.DataFrame,
    aliases: pd.DataFrame,
    occupation_map: pd.DataFrame,
    resume_features: pd.DataFrame,
    job_features: pd.DataFrame,
    resume_skill_matches: pd.DataFrame,
    job_skill_matches: pd.DataFrame,
    resume_sections: pd.DataFrame,
    job_sections: pd.DataFrame,
    job_keywords: pd.DataFrame,
) -> dict[str, Any]:
    skill_category_counts = dictionary["skill_category"].value_counts().to_dict()
    alias_type_counts = aliases["alias_type"].value_counts().to_dict()
    resume_section_counts = resume_sections["section_type"].value_counts().to_dict()
    job_section_counts = job_sections["section_type"].value_counts().to_dict()
    title_source_counts = job_features["job_title_source"].value_counts().to_dict()
    requirement_counts = job_skill_matches["requirement_type"].value_counts().to_dict()

    top_resume_skills = (
        resume_skill_matches.groupby("canonical_skill")
        .size()
        .sort_values(ascending=False)
        .head(20)
        .to_dict()
    )
    top_job_skills = (
        job_skill_matches.groupby("canonical_skill")
        .size()
        .sort_values(ascending=False)
        .head(20)
        .to_dict()
    )
    top_required_skills = (
        job_skill_matches[job_skill_matches["requirement_type"] == "required"]
        .groupby("canonical_skill")
        .size()
        .sort_values(ascending=False)
        .head(20)
        .to_dict()
    )

    resume_rows = len(resumes)
    job_rows = len(jobs)
    return {
        "dictionary": {
            "canonical_skills": int(len(dictionary)),
            "aliases": int(len(aliases)),
            "ambiguous_aliases": int(aliases["is_ambiguous"].sum()),
            "match_enabled_aliases": int(aliases["match_enabled"].sum()),
            "occupations": int(occupation_map["O*NET-SOC Code"].nunique()),
            "occupation_titles": int(occupation_map["Title_clean"].nunique()),
            "skill_occupation_relationships": int(len(occupation_map)),
            "category_counts": {str(key): int(value) for key, value in skill_category_counts.items()},
            "alias_type_counts": {str(key): int(value) for key, value in alias_type_counts.items()},
            "hot_technology_skills": int((dictionary["hot_technology"] == "Y").sum()),
            "in_demand_skills": int((dictionary["in_demand"] == "Y").sum()),
        },
        "resumes": {
            "rows": int(resume_rows),
            "features": int(len(resume_features.columns)),
            "rows_with_skills": int((resume_features["technical_skill_count"] > 0).sum()),
            "skill_coverage_percent": percent(
                int((resume_features["technical_skill_count"] > 0).sum()), resume_rows
            ),
            "total_skill_mentions": int(resume_features["technical_skill_count"].sum()),
            "mean_skills_per_resume": safe_mean(resume_features["technical_skill_count"]),
            "max_skills_per_resume": int(resume_features["technical_skill_count"].max())
            if resume_rows
            else 0,
            "rows_with_programming_languages": int(
                (resume_features["programming_language_count"] > 0).sum()
            ),
            "rows_with_frameworks_libraries": int(
                (resume_features["framework_library_count"] > 0).sum()
            ),
            "rows_with_databases": int((resume_features["database_count"] > 0).sum()),
            "rows_with_tools_technologies": int(
                (resume_features["tool_technology_count"] > 0).sum()
            ),
            "education_coverage_percent": percent(
                int(resume_features["has_education"].sum()), resume_rows
            ),
            "experience_coverage_percent": percent(
                int(resume_features["has_experience"].sum()), resume_rows
            ),
            "education_source_counts": {
                str(key): int(value)
                for key, value in resume_features["education_source"].value_counts().items()
            },
            "experience_source_counts": {
                str(key): int(value)
                for key, value in resume_features["experience_source"].value_counts().items()
            },
            "section_counts": {
                str(key): int(value) for key, value in resume_section_counts.items()
            },
            "top_skills": {str(key): int(value) for key, value in top_resume_skills.items()},
        },
        "jobs": {
            "rows": int(job_rows),
            "features": int(len(job_features.columns)),
            "titles_identified": int((job_features["job_title"] != "").sum()),
            "title_identification_percent": percent(
                int((job_features["job_title"] != "").sum()), job_rows
            ),
            "title_source_counts": {
                str(key): int(value) for key, value in title_source_counts.items()
            },
            "rows_with_required_skills": int((job_features["required_skill_count"] > 0).sum()),
            "rows_with_preferred_skills": int(
                (job_features["preferred_skill_count"] > 0).sum()
            ),
            "rows_with_other_skills": int((job_features["other_skill_count"] > 0).sum()),
            "rows_with_any_skills": int((job_features["all_extracted_skill_count"] > 0).sum()),
            "skill_coverage_percent": percent(
                int((job_features["all_extracted_skill_count"] > 0).sum()), job_rows
            ),
            "responsibilities_coverage_percent": percent(
                int((job_features["responsibilities_source"] != "none").sum()), job_rows
            ),
            "qualifications_coverage_percent": percent(
                int((job_features["qualifications_source"] != "none").sum()), job_rows
            ),
            "requirement_type_counts": {
                str(key): int(value) for key, value in requirement_counts.items()
            },
            "section_counts": {
                str(key): int(value) for key, value in job_section_counts.items()
            },
            "top_skills": {str(key): int(value) for key, value in top_job_skills.items()},
            "top_required_skills": {
                str(key): int(value) for key, value in top_required_skills.items()
            },
            "keyword_rows": int(len(job_keywords)),
            "rows_with_keywords": int((job_features["relevant_keyword_count"] > 0).sum()),
        },
    }


def validate_pipeline(
    raw_fingerprints_before: dict[str, dict[str, Any]],
    raw_fingerprints_after: dict[str, dict[str, Any]],
    processed_fingerprints_before: dict[str, dict[str, Any]],
    processed_fingerprints_after: dict[str, dict[str, Any]],
    resumes: pd.DataFrame,
    jobs: pd.DataFrame,
    skills: pd.DataFrame,
    dictionary: pd.DataFrame,
    aliases: pd.DataFrame,
    occupation_map: pd.DataFrame,
    matcher: SkillMatcher,
    resume_features: pd.DataFrame,
    job_features: pd.DataFrame,
    resume_skill_matches: pd.DataFrame,
    job_skill_matches: pd.DataFrame,
    resume_sections: pd.DataFrame,
    job_sections: pd.DataFrame,
    job_keywords: pd.DataFrame,
    output_paths: list[Path],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []

    def check(name: str, condition: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(condition), "detail": detail})

    for name, before in raw_fingerprints_before.items():
        after = raw_fingerprints_after.get(name, {})
        check(
            f"Raw file unchanged: {name}",
            before.get("sha256") == after.get("sha256"),
            f"SHA-256 before={before.get('sha256', '')[:16]}..., "
            f"after={after.get('sha256', '')[:16]}...",
        )
    for name, before in processed_fingerprints_before.items():
        after = processed_fingerprints_after.get(name, {})
        check(
            f"Processed file unchanged: {name}",
            before.get("sha256") == after.get("sha256"),
            f"SHA-256 before={before.get('sha256', '')[:16]}..., "
            f"after={after.get('sha256', '')[:16]}...",
        )

    resume_ids = set(int(value) for value in resumes["ID"].tolist())
    feature_ids = set(int(value) for value in resume_features["resume_id"].tolist())
    check(
        "Résumé feature IDs exactly cover processed résumé IDs",
        resume_ids == feature_ids,
        f"processed={len(resume_ids)}, features={len(feature_ids)}, "
        f"missing={len(resume_ids - feature_ids)}, extra={len(feature_ids - resume_ids)}",
    )
    check(
        "Résumé feature IDs are unique",
        resume_features["resume_id"].is_unique,
        f"rows={len(resume_features)}, unique={resume_features['resume_id'].nunique()}",
    )
    check(
        "Résumé feature count equals processed count",
        len(resume_features) == len(resumes),
        f"processed={len(resumes)}, features={len(resume_features)}",
    )
    check(
        "Résumé clean text is present",
        bool(resume_features["clean_text"].fillna("").str.strip().ne("").all()),
        f"empty={int(resume_features['clean_text'].fillna('').str.strip().eq('').sum())}",
    )

    processed_job_indices = [int(value) for value in jobs["source_row_index"].tolist()]
    feature_job_indices = [int(value) for value in job_features["source_row_index"].tolist()]
    check(
        "Job source row indices are preserved in order",
        processed_job_indices == feature_job_indices,
        f"processed={len(processed_job_indices)}, features={len(feature_job_indices)}",
    )
    check(
        "Job feature IDs are unique",
        job_features["job_id"].is_unique,
        f"rows={len(job_features)}, unique={job_features['job_id'].nunique()}",
    )
    check(
        "Job feature count equals processed count",
        len(job_features) == len(jobs),
        f"processed={len(jobs)}, features={len(job_features)}",
    )
    check(
        "Job clean descriptions are present",
        bool(job_features["clean_description"].fillna("").str.strip().ne("").all()),
        f"empty={int(job_features['clean_description'].fillna('').str.strip().eq('').sum())}",
    )

    skill_source_rows = set(int(value) for value in skills["source_row_index"].tolist())
    mapped_source_rows = set(int(value) for value in occupation_map["source_row_index"].tolist())
    check(
        "Skills occupation map covers every processed skill row",
        skill_source_rows == mapped_source_rows,
        f"processed={len(skill_source_rows)}, mapped={len(mapped_source_rows)}",
    )
    check(
        "Canonical skill IDs are unique",
        dictionary["canonical_skill_id"].is_unique,
        f"rows={len(dictionary)}, unique={dictionary['canonical_skill_id'].nunique()}",
    )
    check(
        "Skill categories use the documented values",
        set(dictionary["skill_category"].unique()) <= set(SKILL_CATEGORIES),
        f"categories={sorted(dictionary['skill_category'].unique().tolist())}",
    )
    check(
        "Ambiguous aliases are excluded from matching",
        not bool((aliases["is_ambiguous"] & aliases["match_enabled"]).any()),
        f"ambiguous={int(aliases['is_ambiguous'].sum())}, "
        f"ambiguous_enabled={int((aliases['is_ambiguous'] & aliases['match_enabled']).sum())}",
    )

    dictionary_skills = set(dictionary["canonical_skill"].tolist())
    resume_match_skills = set(resume_skill_matches["canonical_skill"].tolist())
    job_match_skills = set(job_skill_matches["canonical_skill"].tolist())
    check(
        "Every résumé skill match exists in the skills dictionary",
        resume_match_skills <= dictionary_skills,
        f"matched={len(resume_match_skills)}, unknown={len(resume_match_skills - dictionary_skills)}",
    )
    check(
        "Every job skill match exists in the skills dictionary",
        job_match_skills <= dictionary_skills,
        f"matched={len(job_match_skills)}, unknown={len(job_match_skills - dictionary_skills)}",
    )
    check(
        "Requirement types use documented values",
        set(job_skill_matches["requirement_type"].unique()) <= set(REQUIREMENT_TYPES),
        f"types={sorted(job_skill_matches['requirement_type'].unique().tolist())}",
    )

    alias_test_results: list[dict[str, Any]] = []
    compact_aliases = aliases[
        (aliases["alias_type"] == "compact_punctuation") & aliases["match_enabled"]
    ]
    if not compact_aliases.empty:
        sample = compact_aliases.iloc[0]
        found = matcher.find_matches(f"Experience with {sample['alias']} required.")
        matched = any(
            item["canonical_skill"] == sample["canonical_skill"] for item in found
        )
        alias_test_results.append(
            {
                "test": "compact punctuation alias",
                "alias": sample["alias"],
                "canonical_skill": sample["canonical_skill"],
                "passed": matched,
            }
        )
        check(
            "Alias normalization matches a compact punctuation alias",
            matched,
            f"alias={sample['alias']}, canonical={sample['canonical_skill']}",
        )

    acronym_aliases = aliases[
        (aliases["alias_type"] == "parenthetical_acronym") & aliases["match_enabled"]
    ]
    if not acronym_aliases.empty:
        sample = acronym_aliases.iloc[0]
        found = matcher.find_matches(f"Experience with {sample['alias']} required.")
        matched = any(
            item["canonical_skill"] == sample["canonical_skill"] for item in found
        )
        alias_test_results.append(
            {
                "test": "parenthetical acronym alias",
                "alias": sample["alias"],
                "canonical_skill": sample["canonical_skill"],
                "passed": matched,
            }
        )
        check(
            "Alias normalization matches a parenthetical acronym",
            matched,
            f"alias={sample['alias']}, canonical={sample['canonical_skill']}",
        )

    canonical_aliases = aliases[aliases["alias_type"] == "canonical_surface"]
    if not canonical_aliases.empty:
        sample = canonical_aliases.iloc[0]
        found = matcher.find_matches(f"Experience with {sample['alias'].upper()} required.")
        matched = any(
            item["canonical_skill"] == sample["canonical_skill"] for item in found
        )
        alias_test_results.append(
            {
                "test": "case-insensitive canonical alias",
                "alias": sample["alias"],
                "canonical_skill": sample["canonical_skill"],
                "passed": matched,
            }
        )
        check(
            "Alias normalization is case-insensitive",
            matched,
            f"alias={sample['alias']}, canonical={sample['canonical_skill']}",
        )

    resume_text_by_id = {
        int(row["ID"]): row["clean_text"] for _, row in resumes.iterrows()
    }
    invalid_resume_sections = 0
    for _, row in resume_sections.iterrows():
        text = resume_text_by_id.get(int(row["resume_id"]), "")
        if not (0 <= int(row["start_char"]) <= int(row["end_char"]) <= len(text)):
            invalid_resume_sections += 1
    job_text_by_index = {
        int(row["source_row_index"]): row["clean_description"]
        for _, row in jobs.iterrows()
    }
    invalid_job_sections = 0
    for _, row in job_sections.iterrows():
        text = job_text_by_index.get(int(row["source_row_index"]), "")
        if not (0 <= int(row["start_char"]) <= int(row["end_char"]) <= len(text)):
            invalid_job_sections += 1
    check(
        "Résumé section offsets are valid",
        invalid_resume_sections == 0,
        f"invalid={invalid_resume_sections}, sections={len(resume_sections)}",
    )
    check(
        "Job section offsets are valid",
        invalid_job_sections == 0,
        f"invalid={invalid_job_sections}, sections={len(job_sections)}",
    )
    check(
        "Every résumé has at least one extracted section",
        set(resume_sections["resume_id"].astype(int)) == resume_ids,
        f"covered={resume_sections['resume_id'].nunique()}, processed={len(resume_ids)}",
    )
    check(
        "Every job has at least one extracted section",
        set(job_sections["source_row_index"].astype(int)) == set(processed_job_indices),
        f"covered={job_sections['source_row_index'].nunique()}, processed={len(processed_job_indices)}",
    )
    check(
        "Job keywords are traceable to job rows",
        set(job_keywords["source_row_index"].astype(int)) <= set(processed_job_indices),
        f"keyword_ranges={job_keywords['source_row_index'].nunique()}, jobs={len(processed_job_indices)}",
    )

    for path in output_paths:
        check(
            f"Output file exists: {path.name}",
            path.exists() and path.stat().st_size > 0,
            f"path={relative_path(path)}, size={human_size(path.stat().st_size) if path.exists() else 'missing'}",
        )

    check(
        "Alias normalization tests passed",
        all(item["passed"] for item in alias_test_results) if alias_test_results else False,
        f"tests={len(alias_test_results)}, failed={sum(not item['passed'] for item in alias_test_results)}",
    )
    return checks, alias_test_results


def build_manifest(
    environment: dict[str, str],
    raw_fingerprints: dict[str, dict[str, Any]],
    processed_fingerprints: dict[str, dict[str, Any]],
    statistics: dict[str, Any],
    checks: list[dict[str, Any]],
    output_paths: list[Path],
    keyword_method: str,
) -> dict[str, Any]:
    output_info: dict[str, Any] = {}
    for path in output_paths:
        output_info[path.name] = {
            "path": relative_path(path),
            "size_bytes": path.stat().st_size,
            "size_human": human_size(path.stat().st_size),
            "sha256": file_fingerprint(path)["sha256"],
        }
    return {
        "phase": "2.4",
        "phase_name": "Feature Engineering",
        "feature_version": FEATURE_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "script": relative_path(SCRIPT_PATH),
        "environment": environment,
        "inputs": {
            **{
                f"raw/{name}": {
                    "path": relative_path(path),
                    "size_human": raw_fingerprints[name]["size_human"],
                    "sha256": raw_fingerprints[name]["sha256"],
                }
                for name, path in RAW_PATH_BY_NAME.items()
            },
            **{
                f"processed/{name}": {
                    "path": relative_path(path),
                    "size_human": processed_fingerprints[name]["size_human"],
                    "sha256": processed_fingerprints[name]["sha256"],
                }
                for name, path in PROCESSED_PATH_BY_NAME.items()
            },
        },
        "outputs": output_info,
        "representations": {
            "resume_semantic_text_column": "clean_text",
            "job_semantic_text_column": "clean_description",
            "resume_sparse_features": "technical_skills and category-specific skill columns",
            "job_sparse_features": "required_skills, preferred_skills, other_skills",
            "skill_match_tables": [
                "resume_skill_matches.csv",
                "job_skill_matches.csv",
            ],
            "hybrid_matching": "Concatenate clean text with canonical skills at runtime.",
            "embeddings_generated": False,
            "external_api_calls": False,
        },
        "settings": {
            "keyword_top_n": KEYWORD_TOP_N,
            "keyword_method": keyword_method,
            "short_alias_context_required": sorted(SHORT_ALIAS_CONTEXT),
            "allow_weak_degree_mentions_only_in_education_section": True,
        },
        "statistics": statistics,
        "validation": {
            "passed": all(item["passed"] for item in checks),
            "checks": checks,
        },
        "known_limitations": [
            "No embeddings were generated and no external LLM/API was called.",
            "Skills are matched against canonical dictionary names and deterministic aliases only.",
            "Vendor-qualified dictionary names can reduce recall for shorter common names.",
            "Skill category assignment is a documented heuristic, not an O*NET claim.",
            "Job-title, section, requirement, education, and experience extraction are rule-based.",
            "TF-IDF keywords are lexical and carry no semantic understanding.",
        ],
    }


def build_report(
    environment: dict[str, str],
    statistics: dict[str, Any],
    checks: list[dict[str, Any]],
    output_paths: list[Path],
    keyword_method: str,
    validation_error: str | None,
    alias_test_results: list[dict[str, Any]],
) -> str:
    resume_stats = statistics["resumes"]
    job_stats = statistics["jobs"]
    dictionary_stats = statistics["dictionary"]

    lines: list[str] = []
    lines.append("# JobMatch AI - Feature Engineering Report (Phase 2.4)")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().astimezone().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"**Script:** `{relative_path(SCRIPT_PATH)}`")
    lines.append("")
    lines.append(
        f"**Environment:** Python `{environment['python']}`, pandas `{environment['pandas']}`, "
        f"NumPy `{environment['numpy']}`, scikit-learn `{environment['scikit-learn']}`, "
        f"spaCy `{environment['spacy']}`"
    )
    lines.append("")

    lines.append("## 1. Executive summary")
    lines.append("")
    lines.append(
        f"- **Measured:** built **{dictionary_stats['canonical_skills']:,}** canonical skills "
        f"and **{dictionary_stats['aliases']:,}** deterministic aliases from "
        f"**{dictionary_stats['skill_occupation_relationships']:,}** skill-occupation rows."
    )
    lines.append(
        f"- **Measured:** extracted features for **{resume_stats['rows']:,}** résumés and "
        f"**{job_stats['rows']:,}** job descriptions; source identifiers were preserved."
    )
    lines.append(
        f"- **Measured:** **{resume_stats['rows_with_skills']:,}** résumés "
        f"({resume_stats['skill_coverage_percent']:.1f}%) and "
        f"**{job_stats['rows_with_any_skills']:,}** jobs "
        f"({job_stats['skill_coverage_percent']:.1f}%) contain at least one dictionary skill."
    )
    lines.append(
        f"- **Measured:** job titles were identified for **{job_stats['titles_identified']:,}** "
        f"jobs ({job_stats['title_identification_percent']:.1f}%); the remaining titles were "
        "left empty instead of being invented."
    )
    lines.append(
        f"- **Representation:** `clean_text` and `clean_description` are the semantic text fields; "
        "canonical skill columns and long-form match tables are the sparse/hybrid features."
    )
    lines.append(
        "- **Scope:** no embeddings, external API calls, model training, FastAPI endpoints, or "
        "Angular changes were made."
    )
    if validation_error:
        lines.append(f"- **Validation:** FAILED - {validation_error}")
    else:
        lines.append("- **Validation:** all automated checks passed.")
    lines.append("")

    lines.append("## 2. Inputs and outputs")
    lines.append("")
    lines.append(
        markdown_table(
            ["Input", "Rows", "Columns"],
            [
                ["`resumes_cleaned.csv`", f"{resume_stats['rows']:,}", "14"],
                ["`job_roles_cleaned.csv`", f"{job_stats['rows']:,}", "12"],
                [
                    "`software_skills_cleaned.csv`",
                    f"{dictionary_stats['skill_occupation_relationships']:,}",
                    "17",
                ],
            ],
        )
    )
    lines.append("")
    output_rows = []
    for path in output_paths:
        output_rows.append(
            [f"`{relative_path(path)}`", human_size(path.stat().st_size) if path.exists() else "missing"]
        )
    lines.append(markdown_table(["Output file", "Size"], output_rows))
    lines.append("")

    lines.append("## 3. Skills dictionary")
    lines.append("")
    lines.append(
        markdown_table(
            ["Metric", "Value"],
            [
                ["Canonical skills", f"{dictionary_stats['canonical_skills']:,}"],
                ["Aliases", f"{dictionary_stats['aliases']:,}"],
                ["Match-enabled aliases", f"{dictionary_stats['match_enabled_aliases']:,}"],
                ["Ambiguous aliases", f"{dictionary_stats['ambiguous_aliases']:,}"],
                ["O*NET occupations", f"{dictionary_stats['occupations']:,}"],
                ["Skill-occupation relationships", f"{dictionary_stats['skill_occupation_relationships']:,}"],
                ["Hot-technology skills", f"{dictionary_stats['hot_technology_skills']:,}"],
                ["In-demand skills", f"{dictionary_stats['in_demand_skills']:,}"],
            ],
        )
    )
    lines.append("")
    lines.append(
        markdown_table(
            ["Skill category", "Canonical skills"],
            [
                [category, f"{dictionary_stats['category_counts'].get(category, 0):,}"]
                for category in SKILL_CATEGORIES
            ],
        )
    )
    lines.append("")
    lines.append(
        "Aliases are generated only from the dictionary entry itself: the cleaned surface form, "
        "a parenthetical expansion/acronym, and a punctuation-light form. Aliases that map to more "
        "than one canonical skill are marked ambiguous and excluded from matching."
    )
    lines.append("")
    lines.append(
        markdown_table(
            ["Alias type", "Aliases"],
            [[str(key), f"{value:,}"] for key, value in sorted(dictionary_stats["alias_type_counts"].items())],
        )
    )
    lines.append("")

    lines.append("## 4. Résumé features")
    lines.append("")
    lines.append(
        markdown_table(
            ["Metric", "Value"],
            [
                ["Rows", f"{resume_stats['rows']:,}"],
                ["Rows with skills", f"{resume_stats['rows_with_skills']:,} ({resume_stats['skill_coverage_percent']:.1f}%)"],
                ["Mean skills per résumé", f"{resume_stats['mean_skills_per_resume']:.2f}"],
                ["Maximum skills in one résumé", f"{resume_stats['max_skills_per_resume']:,}"],
                ["Rows with programming languages", f"{resume_stats['rows_with_programming_languages']:,}"],
                ["Rows with frameworks/libraries", f"{resume_stats['rows_with_frameworks_libraries']:,}"],
                ["Rows with databases", f"{resume_stats['rows_with_databases']:,}"],
                ["Rows with tools/technologies", f"{resume_stats['rows_with_tools_technologies']:,}"],
                ["Education coverage", f"{resume_stats['education_coverage_percent']:.1f}%"],
                ["Experience coverage", f"{resume_stats['experience_coverage_percent']:.1f}%"],
            ],
        )
    )
    lines.append("")
    lines.append(
        "Education and experience fields state their evidence source. "
        "`explicit_section` means the value came from a detected section; "
        "`keyword_scan` means a degree/institution or years phrase was found without a section; "
        "`experience_entry_count_inferred` is explicitly labelled as inferred."
    )
    lines.append("")
    if resume_stats["top_skills"]:
        lines.append(
            markdown_table(
                ["Top résumé skills", "Résumés containing"],
                [[md_cell(key, 80), f"{value:,}"] for key, value in resume_stats["top_skills"].items()],
            )
        )
        lines.append("")

    lines.append("## 5. Job features")
    lines.append("")
    lines.append(
        markdown_table(
            ["Metric", "Value"],
            [
                ["Rows", f"{job_stats['rows']:,}"],
                ["Job titles identified", f"{job_stats['titles_identified']:,} ({job_stats['title_identification_percent']:.1f}%)"],
                ["Rows with required skills", f"{job_stats['rows_with_required_skills']:,}"],
                ["Rows with preferred skills", f"{job_stats['rows_with_preferred_skills']:,}"],
                ["Rows with other mentioned skills", f"{job_stats['rows_with_other_skills']:,}"],
                ["Rows with any dictionary skill", f"{job_stats['rows_with_any_skills']:,}"],
                ["Responsibilities coverage", f"{job_stats['responsibilities_coverage_percent']:.1f}%"],
                ["Qualifications coverage", f"{job_stats['qualifications_coverage_percent']:.1f}%"],
                ["Keyword extraction method", f"`{keyword_method}`"],
                ["Rows with keywords", f"{job_stats['rows_with_keywords']:,}"],
            ],
        )
    )
    lines.append("")
    lines.append(
        markdown_table(
            ["Job title source", "Rows"],
            [[str(key), f"{value:,}"] for key, value in sorted(job_stats["title_source_counts"].items())],
        )
    )
    lines.append("")
    lines.append(
        markdown_table(
            ["Requirement type", "Skill matches"],
            [[str(key), f"{value:,}"] for key, value in sorted(job_stats["requirement_type_counts"].items())],
        )
    )
    lines.append("")
    if job_stats["top_required_skills"]:
        lines.append(
            markdown_table(
                ["Top required skills", "Job mentions"],
                [[md_cell(key, 80), f"{value:,}"] for key, value in job_stats["top_required_skills"].items()],
            )
        )
        lines.append("")

    lines.append("## 6. Extraction methods")
    lines.append("")
    lines.append("### Résumés")
    lines.append("")
    lines.append(
        "- Parsed the cleaned text with a heading dictionary and extracted section text with "
        "character offsets. Sections are emitted in `resume_sections.csv` for traceability."
    )
    lines.append(
        "- Matched skills against the reusable dictionary with case-insensitive, boundary-aware "
        "alias matching. Short/ambiguous aliases require nearby programming context."
    )
    lines.append(
        "- Aggregated canonical skills into technical skills, programming languages, "
        "frameworks/libraries, databases, and tools/technologies."
    )
    lines.append(
        "- Extracted degree and institution mentions only from explicit education text; "
        "when no education section existed, only strong degree keywords were scanned."
    )
    lines.append(
        "- Extracted explicitly stated experience years and counted date ranges as an explicitly "
        "labelled inferred entry count."
    )
    lines.append("")
    lines.append("### Job descriptions")
    lines.append("")
    lines.append(
        "- Extracted titles only from explicit title/role fields, hiring cue phrases, or credible "
        "leading text; no title was invented when evidence was insufficient."
    )
    lines.append(
        "- Split descriptions into headings such as responsibilities, requirements, qualifications, "
        "preferred skills, education, and benefits, preserving offsets in `job_sections.csv`."
    )
    lines.append(
        "- Classified skill mentions as `required`, `preferred`, or `mentioned` using the nearest "
        "cue phrase and the containing section."
    )
    lines.append(
        "- Extracted responsibilities and qualifications from explicit sections; otherwise used "
        "cue-scored sentences as a labelled fallback."
    )
    lines.append(
        "- Generated lexical keywords with TF-IDF and stored term/score rows in `job_keywords.csv`."
    )
    lines.append("")

    lines.append("## 7. Feature representation")
    lines.append("")
    lines.append(
        "- **Sentence Transformers / semantic similarity:** use `clean_text` for résumés and "
        "`clean_description` for jobs. These are unchanged cleaned text fields."
    )
    lines.append(
        "- **Scikit-learn sparse features:** use the long-form `resume_skill_matches.csv` and "
        "`job_skill_matches.csv` tables, or the semicolon-separated canonical skill columns in the "
        "feature tables."
    )
    lines.append(
        "- **Hybrid matching:** combine semantic similarity on clean text with skill overlap, "
        "required/preferred weighting, and dictionary categories."
    )
    lines.append(
        "- **Future LLM analysis:** `resume_features.csv` and `job_features.csv` retain source IDs, "
        "clean text, structured skills, sections, education/experience evidence, and keywords."
    )
    lines.append(
        "- **Not generated:** embeddings, vector indexes, model artefacts, or external API calls."
    )
    lines.append("")

    lines.append("## 8. Validation")
    lines.append("")
    lines.append(
        f"**{sum(item['passed'] for item in checks)}/{len(checks)}** automated checks passed."
    )
    lines.append("")
    lines.append(
        markdown_table(
            ["Check", "Result", "Detail"],
            [
                [md_cell(item["check"], 90), "PASS" if item["passed"] else "FAIL", md_cell(item["detail"], 180)]
                for item in checks
            ],
        )
    )
    lines.append("")
    if alias_test_results:
        lines.append(
            markdown_table(
                ["Alias test", "Alias", "Canonical", "Result"],
                [
                    [
                        item["test"],
                        md_cell(item["alias"], 60),
                        md_cell(item["canonical_skill"], 60),
                        "PASS" if item["passed"] else "FAIL",
                    ]
                    for item in alias_test_results
                ],
            )
        )
        lines.append("")
    if validation_error:
        lines.append(f"**Validation error:** {validation_error}")
    else:
        lines.append("**All automated validation checks passed.**")
    lines.append("")

    lines.append("## 9. Limitations")
    lines.append("")
    lines.append(
        "- Skill matching is limited to dictionary names and deterministic aliases. Vendor-qualified "
        "entries (for example, vendor-prefixed product names) can reduce recall for shorter common "
        "names. No arbitrary synonym was added."
    )
    lines.append(
        "- Skill-category assignment is a documented heuristic based on dictionary names, O*NET "
        "element names, and explicit language/framework/database rules. It is not an official "
        "O*NET classification."
    )
    lines.append(
        "- Section, title, requirement, education, and experience extraction are rule-based and "
        "may miss unheaded or unusually formatted content."
    )
    lines.append(
        "- Education and experience values are only extracted when stated; the date-range count is "
        "inferred and explicitly labelled."
    )
    lines.append(
        "- TF-IDF keywords are lexical, not semantic. They do not replace embeddings or LLM analysis."
    )
    lines.append(
        "- Duplicate résumés and the class imbalance documented in earlier phases remain unresolved "
        "and must be handled before model training and train/test splitting."
    )
    lines.append(
        "- spaCy was not used in this phase because no trained pipeline/model was required for the "
        "deterministic rules; the cleaned text is ready for a spaCy pipeline later."
    )
    lines.append("")

    lines.append("## 10. Recommendations for NLP and embeddings")
    lines.append("")
    lines.append(
        "1. Use `clean_text` and `clean_description` as the canonical semantic text inputs for "
        "Sentence Transformers; do not embed raw HTML or duplicated feature text."
    )
    lines.append(
        "2. Build a sparse skill matrix by pivoting the long-form match tables; weight required "
        "skills above preferred/mentioned skills for job matching."
    )
    lines.append(
        "3. Start with a hybrid score combining semantic similarity and dictionary-skill overlap, "
        "then evaluate it against a human-labelled relevance set."
    )
    lines.append(
        "4. Review the ambiguous alias report and add only human-approved aliases; do not use "
        "ambiguous aliases automatically."
    )
    lines.append(
        "5. Resolve duplicate résumés and define the résumé-category scope before train/test split "
        "to avoid leakage and label noise."
    )
    lines.append(
        "6. Plan for class weighting or stratified evaluation because job-role labels are highly "
        "imbalanced."
    )
    lines.append(
        "7. Use the section and keyword tables as retrieval context for a future LLM, but keep "
        "source IDs and evidence fields for traceability."
    )
    lines.append(
        "8. Do not use `resume_id`, `job_id`, or `source_row_index` as model features; they are "
        "identifiers only."
    )
    lines.append("")
    lines.append("## 11. Output files")
    lines.append("")
    lines.append(markdown_table(["File", "Size"], output_rows))
    lines.append("")
    lines.append(
        "Machine-readable metadata, hashes, settings, and validation results are in "
        "`ai-engine/data/features/feature_manifest.json`."
    )
    lines.append("")
    return "\n".join(lines)


def run_pipeline() -> int:
    configure_logging()
    LOGGER.info("Project root: %s", PROJECT_ROOT)
    LOGGER.info("Feature output: %s", FEATURES_DIR)

    try:
        ensure_directories()
        raw_fingerprints_before = fingerprint_paths(RAW_PATH_BY_NAME)
        processed_fingerprints_before = fingerprint_paths(PROCESSED_PATH_BY_NAME)

        resumes = load_csv(RESUME_INPUT_PATH)
        jobs = load_csv(JOB_INPUT_PATH)
        skills = load_csv(SKILLS_INPUT_PATH)
        require_columns(resumes, RESUME_REQUIRED_COLUMNS, "resumes_cleaned.csv")
        require_columns(jobs, JOB_REQUIRED_COLUMNS, "job_roles_cleaned.csv")
        require_columns(skills, SKILLS_REQUIRED_COLUMNS, "software_skills_cleaned.csv")

        if resumes["ID"].duplicated().any():
            raise FeatureEngineeringError("Résumé IDs are not unique.")
        if jobs["source_row_index"].duplicated().any():
            raise FeatureEngineeringError("Job source_row_index values are not unique.")
        if skills["source_row_index"].duplicated().any():
            raise FeatureEngineeringError("Skill source_row_index values are not unique.")
        if resumes["clean_text"].fillna("").str.strip().eq("").any():
            raise FeatureEngineeringError("Processed résumés contain empty clean_text values.")
        if jobs["clean_description"].fillna("").str.strip().eq("").any():
            raise FeatureEngineeringError("Processed jobs contain empty clean_description values.")

        dictionary, aliases, occupation_map, matcher = build_skill_dictionary(skills)
        resume_features, resume_sections, resume_skill_matches = build_resume_outputs(
            resumes, matcher
        )
        job_features, job_sections, job_skill_matches, job_keywords = build_job_outputs(
            jobs, matcher
        )

        environment = collect_environment()
        statistics = build_statistics(
            resumes=resumes,
            jobs=jobs,
            dictionary=dictionary,
            aliases=aliases,
            occupation_map=occupation_map,
            resume_features=resume_features,
            job_features=job_features,
            resume_skill_matches=resume_skill_matches,
            job_skill_matches=job_skill_matches,
            resume_sections=resume_sections,
            job_sections=job_sections,
            job_keywords=job_keywords,
        )

        outputs = [
            (resume_features, RESUME_FEATURES_PATH),
            (job_features, JOB_FEATURES_PATH),
            (dictionary, SKILLS_DICTIONARY_PATH),
            (aliases, SKILL_ALIASES_PATH),
            (occupation_map, SKILL_OCCUPATION_MAP_PATH),
            (resume_skill_matches, RESUME_SKILL_MATCHES_PATH),
            (job_skill_matches, JOB_SKILL_MATCHES_PATH),
            (resume_sections, RESUME_SECTIONS_PATH),
            (job_sections, JOB_SECTIONS_PATH),
            (job_keywords, JOB_KEYWORDS_PATH),
        ]
        for frame, path in outputs:
            write_csv(frame, path)
        output_paths = [path for _, path in outputs]

        processed_fingerprints_after = fingerprint_paths(PROCESSED_PATH_BY_NAME)
        raw_fingerprints_after = fingerprint_paths(RAW_PATH_BY_NAME)

        validation_error: str | None = None
        checks: list[dict[str, Any]] = []
        alias_test_results: list[dict[str, Any]] = []
        try:
            checks, alias_test_results = validate_pipeline(
                raw_fingerprints_before=raw_fingerprints_before,
                raw_fingerprints_after=raw_fingerprints_after,
                processed_fingerprints_before=processed_fingerprints_before,
                processed_fingerprints_after=processed_fingerprints_after,
                resumes=resumes,
                jobs=jobs,
                skills=skills,
                dictionary=dictionary,
                aliases=aliases,
                occupation_map=occupation_map,
                matcher=matcher,
                resume_features=resume_features,
                job_features=job_features,
                resume_skill_matches=resume_skill_matches,
                job_skill_matches=job_skill_matches,
                resume_sections=resume_sections,
                job_sections=job_sections,
                job_keywords=job_keywords,
                output_paths=output_paths,
            )
            if any(not item["passed"] for item in checks):
                validation_error = "; ".join(
                    f"{item['check']} ({item['detail']})"
                    for item in checks
                    if not item["passed"]
                )
        except Exception as exc:  # noqa: BLE001 - report unexpected validation failures
            validation_error = f"{type(exc).__name__}: {exc}"
            LOGGER.exception("Validation raised an unexpected error")

        keyword_method = (
            "scikit-learn_tfidf"
            if importlib.util.find_spec("sklearn") is not None
            else "frequency_idf_fallback"
        )
        manifest = build_manifest(
            environment=environment,
            raw_fingerprints=raw_fingerprints_before,
            processed_fingerprints=processed_fingerprints_before,
            statistics=statistics,
            checks=checks,
            output_paths=output_paths,
            keyword_method=keyword_method,
        )
        write_json(manifest, FEATURE_MANIFEST_PATH)

        report = build_report(
            environment=environment,
            statistics=statistics,
            checks=checks,
            output_paths=output_paths,
            keyword_method=keyword_method,
            validation_error=validation_error,
            alias_test_results=alias_test_results,
        )
        FEATURE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        FEATURE_REPORT_PATH.write_text(report, encoding="utf-8")
        LOGGER.info("Feature report written: %s", FEATURE_REPORT_PATH)

    except FeatureEngineeringError as exc:
        LOGGER.error("Feature engineering failed: %s", exc)
        return 1
    except Exception:  # noqa: BLE001 - log unexpected failures with a traceback
        LOGGER.exception("Unexpected feature-engineering failure")
        return 1

    print("\n" + "=" * 80)
    print("FEATURE ENGINEERING SUMMARY")
    print("=" * 80)
    print(
        f"- Skills dictionary: {len(dictionary):,} canonical skills | "
        f"{len(aliases):,} aliases | {int(aliases['is_ambiguous'].sum()):,} ambiguous"
    )
    print(
        f"- Résumés: {len(resume_features):,} rows | "
        f"{int((resume_features['technical_skill_count'] > 0).sum()):,} with skills | "
        f"{len(resume_skill_matches):,} skill matches"
    )
    print(
        f"- Jobs: {len(job_features):,} rows | "
        f"{int((job_features['all_extracted_skill_count'] > 0).sum()):,} with skills | "
        f"{len(job_skill_matches):,} skill matches | {len(job_keywords):,} keyword rows"
    )
    print(f"- Feature directory: {FEATURES_DIR}")
    print(f"- Manifest: {FEATURE_MANIFEST_PATH}")
    print(f"- Report: {FEATURE_REPORT_PATH}")
    print("- Validation: " + ("PASSED" if validation_error is None else f"FAILED - {validation_error}"))
    print("=" * 80)
    return 0 if validation_error is None else 1


if __name__ == "__main__":
    raise SystemExit(run_pipeline())
