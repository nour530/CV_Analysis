#!/usr/bin/env python
"""Phase 2.3 - Data cleaning and preprocessing for the JobMatch AI datasets.

The script reads the three raw CSV files without modifying them, creates cleaned
versions under ``ai-engine/data/processed/``, writes audit files under
``ai-engine/data/audit/``, and produces a cleaning log and a Markdown report.

Design principles
-----------------
* Raw data is opened read-only and its SHA-256 fingerprint is checked before and
  after the run.
* Original columns and identifiers are retained. Cleaned values are added as new
  columns instead of destroying raw values.
* Duplicate résumés, short job descriptions, repeated skill examples, and the 24
  résumé categories are **not** automatically removed or remapped.
* Every transformation and every review decision is documented.

Run from the project root with the existing virtual environment, for example:

    ai-engine/venv/Scripts/python.exe ai-engine/data_analysis/02_data_cleaning.py
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import sys
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve()
DATA_ANALYSIS_DIR = SCRIPT_PATH.parent
AI_ENGINE_DIR = DATA_ANALYSIS_DIR.parent
PROJECT_ROOT = AI_ENGINE_DIR.parent
RAW_DATA_DIR = AI_ENGINE_DIR / "data" / "raw"
PROCESSED_DIR = AI_ENGINE_DIR / "data" / "processed"
AUDIT_DIR = AI_ENGINE_DIR / "data" / "audit"
OUTPUT_DIR = DATA_ANALYSIS_DIR / "outputs"

RESUME_RAW_PATH = RAW_DATA_DIR / "resumes" / "Resume.csv"
JOB_RAW_PATH = RAW_DATA_DIR / "job_descriptions" / "job_roles_combined_ready_10461.csv"
SKILLS_RAW_PATH = RAW_DATA_DIR / "job_descriptions" / "software_skills_15.csv"

RESUME_CLEAN_PATH = PROCESSED_DIR / "resumes_cleaned.csv"
JOB_CLEAN_PATH = PROCESSED_DIR / "job_roles_cleaned.csv"
SKILLS_CLEAN_PATH = PROCESSED_DIR / "software_skills_cleaned.csv"

EXCLUDED_AUDIT_PATH = AUDIT_DIR / "excluded_records.csv"
DUPLICATE_AUDIT_PATH = AUDIT_DIR / "duplicate_records.csv"
CLEANING_LOG_PATH = AUDIT_DIR / "cleaning_log.md"
CLEANING_REPORT_PATH = OUTPUT_DIR / "data_cleaning_report.md"

ENCODINGS_TO_TRY = ("utf-8-sig", "utf-8", "latin-1")
SHORT_DESCRIPTION_THRESHOLD = 300

EXPECTED_JOB_LABELS = (
    "Cybersecurity",
    "Data Analytics",
    "Data Science",
    "Network Engineering",
    "Software Development",
    "UI/UX Design",
)
EXPECTED_LABEL_LOOKUP = {label.casefold(): label for label in EXPECTED_JOB_LABELS}

HTML_TAG_PATTERN = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
HTML_ENTITY_PATTERN = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]+|#\d+|#x[0-9A-Fa-f]+);")
NON_ASCII_PATTERN = r"[^\x00-\x7F]"
MULTI_SPACE_PATTERN = r"  +"
PRIVATE_USE_PATTERN = r"[\ue000-\uf8ff]"
REPLACEMENT_CHAR = "\ufffd"

RESUME_ORIGINAL_COLUMNS = ["ID", "Resume_str", "Resume_html", "Category"]
JOB_ORIGINAL_COLUMNS = ["jobdescription", "label"]
SKILLS_ORIGINAL_COLUMNS = [
    "O*NET-SOC Code",
    "Title",
    "Workplace Example",
    "Element ID",
    "Element Name",
    "Hot Technology",
    "In Demand",
]


# ---------------------------------------------------------------------------
# Logging and errors
# ---------------------------------------------------------------------------


def configure_logging() -> None:
    """Configure console logging and make Windows stdout Unicode-safe."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


LOGGER = logging.getLogger("jobmatch-cleaning")


class CleaningError(RuntimeError):
    """Raised when a cleaning step cannot be completed safely."""


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------


def human_size(num_bytes: int) -> str:
    """Return a compact human-readable byte size."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} GB"


def file_fingerprint(path: Path) -> dict[str, Any]:
    """Return file size and SHA-256 hash without modifying the file."""
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


def relative_path(path: Path) -> str:
    """Return a project-relative POSIX path for reports."""
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def md_cell(value: Any, max_chars: int = 200) -> str:
    """Convert a value into a safe single-line Markdown table cell."""
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
    """Render a GitHub-flavoured Markdown table."""
    escaped_headers = [str(header).replace("|", "\\|") for header in headers]
    lines = [
        "| " + " | ".join(escaped_headers) + " |",
        "| " + " | ".join("---" for _ in escaped_headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def percent(part: int | float, whole: int | float) -> float:
    """Return a percentage, guarding against division by zero."""
    return 0.0 if not whole else (float(part) / float(whole)) * 100.0


def length_stats(lengths: pd.Series) -> dict[str, Any]:
    """Return basic statistics for a series of text lengths."""
    if len(lengths) == 0:
        return {"count": 0, "min": 0, "median": 0.0, "mean": 0.0, "max": 0}
    return {
        "count": int(lengths.count()),
        "min": int(lengths.min()),
        "median": float(lengths.median()),
        "mean": float(lengths.mean()),
        "max": int(lengths.max()),
    }


def count_records_matching(series: pd.Series, pattern: str | re.Pattern[str]) -> int:
    """Count non-null records whose string value matches a regex pattern."""
    return int(
        series.fillna("")
        .astype("string")
        .str.contains(pattern, regex=True)
        .sum()
    )


def count_occurrences(series: pd.Series, pattern: re.Pattern[str]) -> int:
    """Count total regex occurrences across a text series."""
    return int(series.fillna("").astype(str).map(lambda value: len(pattern.findall(value))).sum())


def load_csv(path: Path) -> pd.DataFrame:
    """Load a CSV with encoding fallbacks and no silent row skipping."""
    if not path.exists():
        raise CleaningError(f"Dataset not found: {path}")

    last_error: Exception | None = None
    for encoding in ENCODINGS_TO_TRY:
        try:
            LOGGER.info("Loading %s with encoding=%s", path, encoding)
            frame = pd.read_csv(
                path,
                encoding=encoding,
                low_memory=False,
                keep_default_na=True,
            )
            LOGGER.info(
                "Loaded %s: %d rows x %d columns",
                path.name,
                frame.shape[0],
                frame.shape[1],
            )
            return frame
        except UnicodeDecodeError as exc:
            last_error = exc
            LOGGER.warning("Encoding %s failed for %s", encoding, path.name)
        except pd.errors.ParserError as exc:
            raise CleaningError(f"Failed to parse {path}: {exc}") from exc
        except OSError as exc:
            raise CleaningError(f"Failed to read {path}: {exc}") from exc

    raise CleaningError(
        f"Could not decode {path} with encodings {ENCODINGS_TO_TRY}. "
        f"Last error: {last_error}"
    )


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    """Write a CSV as UTF-8 with BOM and without the pandas index."""
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
        raise CleaningError(f"Could not write {path}: {exc}") from exc
    LOGGER.info("Wrote %s (%s)", path, human_size(path.stat().st_size))


def ensure_directories() -> None:
    """Create processed, audit, and report directories."""
    for directory in (PROCESSED_DIR, AUDIT_DIR, OUTPUT_DIR):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CleaningError(
                f"Could not create output directory {directory}: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


class HTMLTextExtractor(HTMLParser):
    """Collect visible human-readable text from an HTML document."""

    SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})
    BLOCK_TAGS = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "br",
            "dd",
            "div",
            "dl",
            "dt",
            "fieldset",
            "figcaption",
            "figure",
            "footer",
            "form",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hr",
            "li",
            "main",
            "nav",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "tbody",
            "td",
            "tfoot",
            "th",
            "thead",
            "tr",
            "ul",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self._parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            return
        if tag.lower() in self.BLOCK_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def get_text(self) -> str:
        """Return the accumulated text."""
        return "".join(self._parts)


def extract_readable_text(html_text: Any) -> str:
    """Extract visible text from HTML and decode HTML entities.

    Entities are decoded before parsing so that encoded markup (for example
    ``&lt;div&gt;``) is also removed instead of leaking into the clean text.
    """
    if not isinstance(html_text, str) or not html_text.strip():
        return ""

    decoded = html.unescape(html_text)
    parser = HTMLTextExtractor()
    parser.feed(decoded)
    parser.close()
    return parser.get_text()


def normalize_text(text: Any) -> str:
    """Normalise Unicode and whitespace while preserving meaningful symbols.

    The function applies NFC, replaces non-breaking and Unicode spaces, removes
    zero-width space/BOM/soft hyphen artefacts, converts control characters to
    spaces, collapses repeated whitespace, and trims the result. Accents,
    punctuation, bullets, dashes, and other meaningful characters are preserved.
    """
    if not isinstance(text, str) or not text:
        return ""

    value = unicodedata.normalize("NFC", text)

    # Common invisible or layout-only characters observed in the EDA.
    value = value.replace("\u00a0", " ")  # no-break space
    value = value.replace("\u2007", " ")  # figure space
    value = value.replace("\u202f", " ")  # narrow no-break space
    value = value.replace("\u200b", "")  # zero-width space
    value = value.replace("\ufeff", "")  # byte-order mark
    value = value.replace("\u00ad", "")  # soft hyphen

    # Line/paragraph separators become ordinary spaces; control characters too.
    value = re.sub(r"[\t\n\r\f\v\u2028\u2029]+", " ", value)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", value)

    # Any remaining Unicode space separator becomes an ordinary space.
    value = "".join(
        " " if unicodedata.category(character) == "Zs" else character
        for character in value
    )
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_label(value: Any) -> tuple[str, bool, bool]:
    """Return (canonical_label, is_valid, changed) for a job-role label."""
    original = value if isinstance(value, str) else ""
    cleaned = normalize_text(original)
    if not cleaned:
        return "", False, original != ""

    canonical = EXPECTED_LABEL_LOOKUP.get(cleaned.casefold())
    if canonical is None:
        # Punctuation-insensitive fallback for harmless formatting differences.
        key = re.sub(r"[^a-z0-9]+", " ", cleaned.casefold()).strip()
        alt_lookup = {
            re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip(): label
            for label in EXPECTED_JOB_LABELS
        }
        canonical = alt_lookup.get(key)

    if canonical is None:
        return cleaned, False, cleaned != original
    return canonical, True, canonical != original


def normalize_flag(value: Any) -> tuple[str, bool]:
    """Normalise a Y/N flag and report whether it is valid."""
    cleaned = normalize_text(value).upper() if isinstance(value, str) else ""
    return cleaned, cleaned in {"Y", "N"}


def has_html_markup(text: Any) -> bool:
    """Return True when a string still contains HTML-like markup."""
    return isinstance(text, str) and HTML_TAG_PATTERN.search(text) is not None


# ---------------------------------------------------------------------------
# Duplicate handling
# ---------------------------------------------------------------------------


def attach_duplicate_groups(
    frame: pd.DataFrame,
    column: str,
    prefix: str,
    flag_column: str,
    group_column: str,
) -> dict[str, int]:
    """Annotate exact duplicate values with a stable group identifier."""
    values = frame[column].fillna("").astype(str)
    counts = values.value_counts()
    duplicate_values = sorted(counts[counts.gt(1)].index.tolist())
    mapping = {
        value: f"{prefix}_{position:03d}"
        for position, value in enumerate(duplicate_values, start=1)
    }
    frame[group_column] = values.map(mapping)
    frame[flag_column] = frame[group_column].notna()
    return {
        "groups": len(duplicate_values),
        "rows": int(frame[flag_column].sum()),
    }


def attach_full_row_duplicate_groups(
    frame: pd.DataFrame, columns: list[str], prefix: str
) -> tuple[pd.Series, dict[str, int]]:
    """Return a group-id series for genuinely identical full rows."""
    duplicate_mask = frame.duplicated(subset=columns, keep=False)
    if not duplicate_mask.any():
        return (
            pd.Series([None] * len(frame), index=frame.index, dtype="object"),
            {"groups": 0, "rows": 0},
        )

    keys = frame.apply(
        lambda row: "\u241f".join(
            "" if pd.isna(row[column]) else str(row[column]) for column in columns
        ),
        axis=1,
    )
    counts = keys.value_counts()
    duplicate_keys = sorted(counts[counts.gt(1)].index.tolist())
    mapping = {
        key: f"{prefix}_{position:03d}"
        for position, key in enumerate(duplicate_keys, start=1)
    }
    groups = keys.map(mapping)
    return groups, {
        "groups": len(duplicate_keys),
        "rows": int(groups.notna().sum()),
    }


def collect_duplicate_rows(
    frame: pd.DataFrame,
    dataset: str,
    basis: str,
    group_column: str,
    id_column: str,
    label_column: str,
    original_text_column: str,
    clean_text_column: str,
) -> list[dict[str, Any]]:
    """Build audit rows for all records that belong to a duplicate group."""
    if group_column not in frame.columns:
        return []

    subset = frame[frame[group_column].notna()].copy()
    if subset.empty:
        return []

    subset = subset.sort_values([group_column, "source_row_index"])
    rows: list[dict[str, Any]] = []
    for _, row in subset.iterrows():
        original_text = row[original_text_column]
        clean_text = row[clean_text_column]
        rows.append(
            {
                "dataset": dataset,
                "duplicate_basis": basis,
                "duplicate_group": row[group_column],
                "record_id": row[id_column],
                "source_row_index": int(row["source_row_index"]),
                "label": row[label_column],
                "original_length": len(original_text) if isinstance(original_text, str) else 0,
                "clean_length": len(clean_text) if isinstance(clean_text, str) else 0,
                "preview": md_cell(clean_text, 180),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Dataset cleaning
# ---------------------------------------------------------------------------


def clean_resumes(raw: pd.DataFrame) -> dict[str, Any]:
    """Clean the résumé dataset and return processed data, stats, and audits."""
    LOGGER.info("Cleaning Resume.csv")
    frame = raw.loc[:, RESUME_ORIGINAL_COLUMNS].copy()
    frame["source_row_index"] = list(range(len(frame)))

    clean_texts: list[str] = []
    sources: list[str] = []
    html_parse_errors = 0

    for _, row in frame.iterrows():
        html_value = row["Resume_html"] if isinstance(row["Resume_html"], str) else ""
        fallback_value = row["Resume_str"] if isinstance(row["Resume_str"], str) else ""

        html_text = ""
        if html_value.strip():
            try:
                html_text = extract_readable_text(html_value)
            except Exception:  # noqa: BLE001 - malformed HTML must not stop the run
                html_parse_errors += 1
                LOGGER.exception("HTML parse error for résumé ID %s", row["ID"])

        if html_text.strip():
            clean_texts.append(normalize_text(html_text))
            sources.append("Resume_html")
        elif fallback_value.strip():
            clean_texts.append(normalize_text(fallback_value))
            sources.append("Resume_str_fallback")
        else:
            clean_texts.append("")
            sources.append("empty")

    frame["clean_text"] = clean_texts
    frame["clean_text_length"] = frame["clean_text"].str.len().astype("int64")
    frame["text_source"] = sources
    frame["has_replacement_char"] = frame["clean_text"].str.contains(
        REPLACEMENT_CHAR, regex=False, na=False
    )
    frame["has_private_use_char"] = frame["clean_text"].str.contains(
        PRIVATE_USE_PATTERN, regex=True, na=False
    )

    raw_duplicates = attach_duplicate_groups(
        frame,
        column="Resume_str",
        prefix="RESUME_STR_DUP",
        flag_column="is_duplicate_resume_str",
        group_column="duplicate_group_id",
    )
    clean_duplicates = attach_duplicate_groups(
        frame,
        column="clean_text",
        prefix="RESUME_CLEAN_DUP",
        flag_column="is_duplicate_clean_text",
        group_column="clean_duplicate_group_id",
    )

    excluded_mask = frame["clean_text_length"].eq(0)
    excluded = frame.loc[excluded_mask].copy()
    processed = frame.loc[~excluded_mask].copy()

    duplicate_rows = collect_duplicate_rows(
        frame,
        dataset="Resume.csv",
        basis="exact_Resume_str",
        group_column="duplicate_group_id",
        id_column="ID",
        label_column="Category",
        original_text_column="Resume_str",
        clean_text_column="clean_text",
    )
    duplicate_rows += collect_duplicate_rows(
        frame,
        dataset="Resume.csv",
        basis="exact_clean_text",
        group_column="clean_duplicate_group_id",
        id_column="ID",
        label_column="Category",
        original_text_column="Resume_str",
        clean_text_column="clean_text",
    )

    raw_html = frame["Resume_html"].fillna("").astype(str)
    raw_str = frame["Resume_str"].fillna("").astype(str)
    clean_series = frame["clean_text"]

    stats = {
        "raw_rows": int(len(raw)),
        "raw_columns": int(len(RESUME_ORIGINAL_COLUMNS)),
        "processed_rows": int(len(processed)),
        "processed_columns": int(len(processed.columns)),
        "excluded_rows": int(len(excluded)),
        "excluded_ids": [int(value) for value in excluded["ID"].tolist()],
        "text_source_counts": {
            str(key): int(value)
            for key, value in frame["text_source"].value_counts().items()
        },
        "html_records": int(raw_html.str.strip().ne("").sum()),
        "html_parse_errors": int(html_parse_errors),
        "script_style_tags": count_occurrences(
            raw_html, re.compile(r"<\s*(?:script|style)\b", re.IGNORECASE)
        ),
        "html_entity_references": count_occurrences(raw_html, HTML_ENTITY_PATTERN),
        "raw_records_with_html_markup": count_records_matching(raw_html, HTML_TAG_PATTERN),
        "clean_records_with_html_markup": count_records_matching(
            clean_series, HTML_TAG_PATTERN
        ),
        "raw_records_with_nbsp": count_records_matching(raw_str, "\u00a0"),
        "clean_records_with_nbsp": count_records_matching(clean_series, "\u00a0"),
        "raw_records_with_multiple_spaces": count_records_matching(
            raw_str, MULTI_SPACE_PATTERN
        ),
        "clean_records_with_multiple_spaces": count_records_matching(
            clean_series, MULTI_SPACE_PATTERN
        ),
        "raw_records_with_non_ascii": count_records_matching(raw_str, NON_ASCII_PATTERN),
        "clean_records_with_non_ascii": count_records_matching(
            clean_series, NON_ASCII_PATTERN
        ),
        "raw_records_with_replacement_char": count_records_matching(
            raw_str, REPLACEMENT_CHAR
        ),
        "clean_records_with_replacement_char": int(frame["has_replacement_char"].sum()),
        "raw_records_with_private_use": count_records_matching(
            raw_str, PRIVATE_USE_PATTERN
        ),
        "clean_records_with_private_use": int(frame["has_private_use_char"].sum()),
        "clean_length_stats": length_stats(processed["clean_text_length"]),
        "raw_duplicate_groups": int(raw_duplicates["groups"]),
        "raw_duplicate_rows": int(raw_duplicates["rows"]),
        "clean_duplicate_groups": int(clean_duplicates["groups"]),
        "clean_duplicate_rows": int(clean_duplicates["rows"]),
        "category_counts": {
            str(key): int(value)
            for key, value in frame["Category"].value_counts().items()
        },
    }

    added_columns = [
        ("source_row_index", "Zero-based position of the record in the raw CSV."),
        ("clean_text", "Visible text extracted from Resume_html, NFC-normalised and whitespace-normalised."),
        ("clean_text_length", "Character length of clean_text."),
        ("text_source", "Resume_html, Resume_str_fallback, or empty."),
        ("is_duplicate_resume_str", "True when Resume_str exactly matches another row."),
        ("duplicate_group_id", "Group identifier for exact Resume_str duplicates."),
        ("is_duplicate_clean_text", "True when clean_text exactly matches another row."),
        ("clean_duplicate_group_id", "Group identifier for exact clean_text duplicates."),
        ("has_replacement_char", "True when clean_text contains U+FFFD replacement characters."),
        ("has_private_use_char", "True when clean_text contains private-use Unicode characters."),
    ]

    LOGGER.info(
        "Resume cleaning complete: %d raw, %d processed, %d excluded",
        len(raw),
        len(processed),
        len(excluded),
    )
    return {
        "name": "Resume.csv",
        "raw": raw,
        "processed": processed,
        "excluded": excluded,
        "stats": stats,
        "duplicate_rows": duplicate_rows,
        "added_columns": added_columns,
    }


def clean_jobs(raw: pd.DataFrame) -> dict[str, Any]:
    """Clean the job-role dataset and return processed data, stats, and audits."""
    LOGGER.info("Cleaning job_roles_combined_ready_10461.csv")
    frame = raw.loc[:, JOB_ORIGINAL_COLUMNS].copy()
    frame["source_row_index"] = list(range(len(frame)))
    frame["clean_description"] = frame["jobdescription"].map(normalize_text)
    frame["description_length"] = frame["clean_description"].str.len().astype("int64")
    frame["short_description_flag"] = frame["description_length"].lt(
        SHORT_DESCRIPTION_THRESHOLD
    )
    frame["is_empty_description"] = frame["description_length"].eq(0)

    label_results = frame["label"].map(normalize_label)
    frame["label_clean"] = [item[0] for item in label_results]
    frame["is_valid_label"] = [item[1] for item in label_results]
    frame["label_changed"] = [item[2] for item in label_results]
    frame["has_replacement_char"] = frame["clean_description"].str.contains(
        REPLACEMENT_CHAR, regex=False, na=False
    )
    frame["has_private_use_char"] = frame["clean_description"].str.contains(
        PRIVATE_USE_PATTERN, regex=True, na=False
    )

    job_duplicates = attach_duplicate_groups(
        frame,
        column="jobdescription",
        prefix="JOB_DESC_DUP",
        flag_column="_is_duplicate_description",
        group_column="_duplicate_group_id",
    )
    duplicate_rows = collect_duplicate_rows(
        frame,
        dataset="job_roles_combined_ready_10461.csv",
        basis="exact_jobdescription",
        group_column="_duplicate_group_id",
        id_column="source_row_index",
        label_column="label",
        original_text_column="jobdescription",
        clean_text_column="clean_description",
    )
    frame = frame.drop(columns=["_is_duplicate_description", "_duplicate_group_id"])

    raw_desc = frame["jobdescription"].fillna("").astype(str)
    clean_desc = frame["clean_description"]
    raw_lengths = raw_desc.str.len()
    raw_short_mask = raw_lengths.lt(SHORT_DESCRIPTION_THRESHOLD)
    short_records = frame.loc[frame["short_description_flag"]].copy()
    short_records = short_records.sort_values(
        ["description_length", "source_row_index"]
    ).head(10)

    raw_label_counts = {
        str(key): int(value) for key, value in raw["label"].value_counts().items()
    }
    clean_label_counts = {
        str(key): int(value)
        for key, value in frame["label_clean"].value_counts().items()
    }

    stats = {
        "raw_rows": int(len(raw)),
        "raw_columns": int(len(JOB_ORIGINAL_COLUMNS)),
        "processed_rows": int(len(frame)),
        "processed_columns": int(len(frame.columns)),
        "excluded_rows": 0,
        "empty_descriptions_raw": count_records_matching(raw_desc, r"^\s*$"),
        "empty_descriptions_clean": int(frame["is_empty_description"].sum()),
        "short_descriptions_raw": int(raw_short_mask.sum()),
        "short_descriptions_clean": int(frame["short_description_flag"].sum()),
        "short_description_threshold": SHORT_DESCRIPTION_THRESHOLD,
        "raw_length_stats": length_stats(raw_lengths),
        "clean_length_stats": length_stats(frame["description_length"]),
        "raw_records_with_multiple_spaces": count_records_matching(
            raw_desc, MULTI_SPACE_PATTERN
        ),
        "clean_records_with_multiple_spaces": count_records_matching(
            clean_desc, MULTI_SPACE_PATTERN
        ),
        "raw_records_with_non_ascii": count_records_matching(
            raw_desc, NON_ASCII_PATTERN
        ),
        "clean_records_with_non_ascii": count_records_matching(
            clean_desc, NON_ASCII_PATTERN
        ),
        "raw_records_with_replacement_char": count_records_matching(
            raw_desc, REPLACEMENT_CHAR
        ),
        "clean_records_with_replacement_char": int(
            frame["has_replacement_char"].sum()
        ),
        "raw_records_with_private_use": count_records_matching(
            raw_desc, PRIVATE_USE_PATTERN
        ),
        "clean_records_with_private_use": int(frame["has_private_use_char"].sum()),
        "label_counts_raw": raw_label_counts,
        "label_counts_clean": clean_label_counts,
        "label_changed_count": int(frame["label_changed"].sum()),
        "invalid_label_count": int((~frame["is_valid_label"]).sum()),
        "duplicate_description_groups": int(job_duplicates["groups"]),
        "duplicate_description_rows": int(job_duplicates["rows"]),
        "short_records_preview": [
            {
                "source_row_index": int(row["source_row_index"]),
                "label": row["label_clean"],
                "original_length": int(len(row["jobdescription"])),
                "clean_length": int(row["description_length"]),
                "preview": md_cell(row["clean_description"], 160),
            }
            for _, row in short_records.iterrows()
        ],
    }

    added_columns = [
        ("source_row_index", "Zero-based position of the record in the raw CSV."),
        ("clean_description", "NFC-normalised, whitespace-normalised job description."),
        ("description_length", "Character length of clean_description."),
        ("short_description_flag", "True when clean_description is shorter than 300 characters."),
        ("is_empty_description", "True when clean_description is empty."),
        ("label_clean", "Canonical label from the six documented job-role categories."),
        ("label_changed", "True when canonicalisation changed the original label string."),
        ("is_valid_label", "True when the label maps to one of the six documented categories."),
        ("has_replacement_char", "True when clean_description contains U+FFFD replacement characters."),
        ("has_private_use_char", "True when clean_description contains private-use Unicode characters."),
    ]

    LOGGER.info(
        "Job cleaning complete: %d raw, %d processed, %d short descriptions flagged",
        len(raw),
        len(frame),
        int(frame["short_description_flag"].sum()),
    )
    return {
        "name": "job_roles_combined_ready_10461.csv",
        "raw": raw,
        "processed": frame,
        "excluded": frame.iloc[0:0].copy(),
        "stats": stats,
        "duplicate_rows": duplicate_rows,
        "added_columns": added_columns,
    }


def clean_skills(raw: pd.DataFrame) -> dict[str, Any]:
    """Clean the software-skills reference dataset."""
    LOGGER.info("Cleaning software_skills_15.csv")
    frame = raw.loc[:, SKILLS_ORIGINAL_COLUMNS].copy()
    frame["source_row_index"] = list(range(len(frame)))

    frame["Title_clean"] = frame["Title"].map(normalize_text)
    frame["Workplace Example_clean"] = frame["Workplace Example"].map(normalize_text)
    frame["Element Name_clean"] = frame["Element Name"].map(normalize_text)

    hot_results = frame["Hot Technology"].map(normalize_flag)
    demand_results = frame["In Demand"].map(normalize_flag)
    frame["Hot Technology_clean"] = [item[0] for item in hot_results]
    frame["In Demand_clean"] = [item[0] for item in demand_results]
    frame["is_valid_hot_technology"] = [item[1] for item in hot_results]
    frame["is_valid_in_demand"] = [item[1] for item in demand_results]
    frame["has_replacement_char"] = (
        frame["Title_clean"].str.contains(REPLACEMENT_CHAR, regex=False, na=False)
        | frame["Workplace Example_clean"].str.contains(
            REPLACEMENT_CHAR, regex=False, na=False
        )
        | frame["Element Name_clean"].str.contains(
            REPLACEMENT_CHAR, regex=False, na=False
        )
    )
    frame["has_private_use_char"] = (
        frame["Title_clean"].str.contains(PRIVATE_USE_PATTERN, regex=True, na=False)
        | frame["Workplace Example_clean"].str.contains(
            PRIVATE_USE_PATTERN, regex=True, na=False
        )
        | frame["Element Name_clean"].str.contains(
            PRIVATE_USE_PATTERN, regex=True, na=False
        )
    )

    full_duplicate_groups, full_duplicate_summary = attach_full_row_duplicate_groups(
        frame, SKILLS_ORIGINAL_COLUMNS, prefix="SKILLS_FULL_ROW_DUP"
    )
    frame["_full_duplicate_group_id"] = full_duplicate_groups
    duplicate_rows = collect_duplicate_rows(
        frame,
        dataset="software_skills_15.csv",
        basis="genuinely_identical_full_row",
        group_column="_full_duplicate_group_id",
        id_column="source_row_index",
        label_column="Title",
        original_text_column="Workplace Example",
        clean_text_column="Workplace Example_clean",
    )
    frame = frame.drop(columns=["_full_duplicate_group_id"])

    raw_workplace = raw["Workplace Example"].fillna("").astype(str)
    clean_workplace = frame["Workplace Example_clean"]
    raw_title = raw["Title"].fillna("").astype(str)
    clean_title = frame["Title_clean"]
    raw_element = raw["Element Name"].fillna("").astype(str)
    clean_element = frame["Element Name_clean"]

    original_pairs = (
        frame.groupby(["O*NET-SOC Code", "Workplace Example"], dropna=False)
        .size()
        .sort_index()
    )
    cleaned_pairs = (
        frame.groupby(["O*NET-SOC Code", "Workplace Example_clean"], dropna=False)
        .size()
        .sort_index()
    )

    stats = {
        "raw_rows": int(len(raw)),
        "raw_columns": int(len(SKILLS_ORIGINAL_COLUMNS)),
        "processed_rows": int(len(frame)),
        "processed_columns": int(len(frame.columns)),
        "excluded_rows": 0,
        "title_changed": int((frame["Title_clean"] != raw_title).sum()),
        "workplace_example_changed": int(
            (frame["Workplace Example_clean"] != raw_workplace).sum()
        ),
        "element_name_changed": int((frame["Element Name_clean"] != raw_element).sum()),
        "unique_titles_raw": int(raw_title.nunique()),
        "unique_titles_clean": int(clean_title.nunique()),
        "unique_workplace_examples_raw": int(raw_workplace.nunique()),
        "unique_workplace_examples_clean": int(clean_workplace.nunique()),
        "repeated_workplace_example_rows_raw": int(raw_workplace.duplicated().sum()),
        "repeated_workplace_example_rows_clean": int(
            clean_workplace.duplicated().sum()
        ),
        "unique_occupation_example_pairs": int(len(original_pairs)),
        "unique_cleaned_occupation_example_pairs": int(len(cleaned_pairs)),
        "full_duplicate_groups": int(full_duplicate_summary["groups"]),
        "full_duplicate_rows": int(full_duplicate_summary["rows"]),
        "invalid_hot_technology": int((~frame["is_valid_hot_technology"]).sum()),
        "invalid_in_demand": int((~frame["is_valid_in_demand"]).sum()),
        "raw_records_with_non_ascii": count_records_matching(
            raw_workplace, NON_ASCII_PATTERN
        )
        + count_records_matching(raw_title, NON_ASCII_PATTERN)
        + count_records_matching(raw_element, NON_ASCII_PATTERN),
        "clean_records_with_non_ascii": count_records_matching(
            clean_workplace, NON_ASCII_PATTERN
        )
        + count_records_matching(clean_title, NON_ASCII_PATTERN)
        + count_records_matching(clean_element, NON_ASCII_PATTERN),
        "raw_records_with_multiple_spaces": count_records_matching(
            raw_workplace, MULTI_SPACE_PATTERN
        )
        + count_records_matching(raw_title, MULTI_SPACE_PATTERN)
        + count_records_matching(raw_element, MULTI_SPACE_PATTERN),
        "clean_records_with_multiple_spaces": count_records_matching(
            clean_workplace, MULTI_SPACE_PATTERN
        )
        + count_records_matching(clean_title, MULTI_SPACE_PATTERN)
        + count_records_matching(clean_element, MULTI_SPACE_PATTERN),
        "raw_records_with_replacement_char": count_records_matching(
            raw_workplace, REPLACEMENT_CHAR
        )
        + count_records_matching(raw_title, REPLACEMENT_CHAR)
        + count_records_matching(raw_element, REPLACEMENT_CHAR),
        "clean_records_with_replacement_char": int(
            frame["has_replacement_char"].sum()
        ),
        "raw_records_with_private_use": count_records_matching(
            raw_workplace, PRIVATE_USE_PATTERN
        )
        + count_records_matching(raw_title, PRIVATE_USE_PATTERN)
        + count_records_matching(raw_element, PRIVATE_USE_PATTERN),
        "clean_records_with_private_use": int(frame["has_private_use_char"].sum()),
    }

    added_columns = [
        ("source_row_index", "Zero-based position of the record in the raw CSV."),
        ("Title_clean", "NFC-normalised, whitespace-normalised occupation title."),
        ("Workplace Example_clean", "NFC-normalised, whitespace-normalised technology example."),
        ("Element Name_clean", "NFC-normalised, whitespace-normalised O*NET skill category."),
        ("Hot Technology_clean", "Normalised Y/N hot-technology flag."),
        ("In Demand_clean", "Normalised Y/N in-demand flag."),
        ("is_valid_hot_technology", "True when Hot Technology is Y or N after normalisation."),
        ("is_valid_in_demand", "True when In Demand is Y or N after normalisation."),
        ("has_replacement_char", "True when a cleaned text field contains U+FFFD."),
        ("has_private_use_char", "True when a cleaned text field contains private-use Unicode."),
    ]

    LOGGER.info(
        "Skills cleaning complete: %d raw, %d processed, %d genuine full-row duplicates",
        len(raw),
        len(frame),
        int(full_duplicate_summary["rows"]),
    )
    return {
        "name": "software_skills_15.csv",
        "raw": raw,
        "processed": frame,
        "excluded": frame.iloc[0:0].copy(),
        "stats": stats,
        "duplicate_rows": duplicate_rows,
        "added_columns": added_columns,
    }


# ---------------------------------------------------------------------------
# Output and audit assembly
# ---------------------------------------------------------------------------


def build_excluded_audit(resume_result: dict[str, Any]) -> pd.DataFrame:
    """Build the excluded-record audit table."""
    excluded = resume_result["excluded"]
    rows: list[dict[str, Any]] = []
    for _, row in excluded.iterrows():
        rows.append(
            {
                "dataset": "Resume.csv",
                "record_id": row["ID"],
                "source_row_index": int(row["source_row_index"]),
                "label": row["Category"],
                "reason": "empty_or_whitespace_only_after_html_extraction_and_fallback",
                "original_Resume_str": row["Resume_str"],
                "original_Resume_html": row["Resume_html"],
                "clean_text_length": int(row["clean_text_length"]),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "dataset",
            "record_id",
            "source_row_index",
            "label",
            "reason",
            "original_Resume_str",
            "original_Resume_html",
            "clean_text_length",
        ],
    )


def build_duplicate_audit(results: list[dict[str, Any]]) -> pd.DataFrame:
    """Build the combined duplicate audit table."""
    rows: list[dict[str, Any]] = []
    for result in results:
        rows.extend(result["duplicate_rows"])
    return pd.DataFrame(
        rows,
        columns=[
            "dataset",
            "duplicate_basis",
            "duplicate_group",
            "record_id",
            "source_row_index",
            "label",
            "original_length",
            "clean_length",
            "preview",
        ],
    )


def write_processed_outputs(results: list[dict[str, Any]]) -> None:
    """Write the three processed CSV files."""
    write_csv(results[0]["processed"], RESUME_CLEAN_PATH)
    write_csv(results[1]["processed"], JOB_CLEAN_PATH)
    write_csv(results[2]["processed"], SKILLS_CLEAN_PATH)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_outputs(
    results: list[dict[str, Any]],
    fingerprints_before: dict[str, dict[str, Any]],
    fingerprints_after: dict[str, dict[str, Any]],
    output_paths: list[Path],
    checks: list[dict[str, Any]],
) -> None:
    """Run automated data-quality assertions and collect their results."""

    def check(name: str, condition: bool, detail: str) -> None:
        checks.append(
            {
                "check": name,
                "passed": bool(condition),
                "detail": detail,
            }
        )

    resume_result, job_result, skills_result = results
    raw_resume = resume_result["raw"]
    clean_resume = resume_result["processed"]
    excluded_resume = resume_result["excluded"]
    raw_job = job_result["raw"]
    clean_job = job_result["processed"]
    raw_skills = skills_result["raw"]
    clean_skills = skills_result["processed"]

    # Raw file integrity ---------------------------------------------------
    for name, before in fingerprints_before.items():
        after = fingerprints_after.get(name, {})
        check(
            f"Raw file unchanged: {name}",
            before.get("sha256") == after.get("sha256"),
            f"size={before.get('size_human')}; SHA-256 before={before.get('sha256', '')[:16]}..., "
            f"after={after.get('sha256', '')[:16]}...",
        )

    # Résumé checks --------------------------------------------------------
    check(
        "Résumé count equals raw minus excluded",
        len(clean_resume) == len(raw_resume) - len(excluded_resume),
        f"raw={len(raw_resume)}, excluded={len(excluded_resume)}, processed={len(clean_resume)}",
    )
    raw_ids = set(raw_resume["ID"].tolist())
    clean_ids = set(clean_resume["ID"].tolist())
    excluded_ids = set(excluded_resume["ID"].tolist())
    check(
        "Résumé IDs are preserved",
        clean_ids == raw_ids - excluded_ids,
        f"processed IDs={len(clean_ids)}, excluded IDs={sorted(excluded_ids)}, "
        f"missing={sorted((raw_ids - excluded_ids) - clean_ids)[:5]}",
    )
    check(
        "Excluded résumés are exactly the empty records",
        bool(excluded_resume["clean_text_length"].eq(0).all())
        and bool(clean_resume["clean_text_length"].gt(0).all()),
        f"excluded empty={int(excluded_resume['clean_text_length'].eq(0).sum())}, "
        f"processed non-empty={int(clean_resume['clean_text_length'].gt(0).sum())}",
    )
    check(
        "Résumé clean text contains no HTML markup",
        count_records_matching(clean_resume["clean_text"], HTML_TAG_PATTERN) == 0,
        f"records with markup={count_records_matching(clean_resume['clean_text'], HTML_TAG_PATTERN)}",
    )
    check(
        "Résumé clean text is NFC-normalised",
        bool(
            clean_resume["clean_text"]
            .map(lambda text: unicodedata.normalize("NFC", text) == text)
            .all()
        ),
        "all processed clean_text values equal their NFC form",
    )
    accent_text = " ".join(clean_resume["clean_text"].astype(str).tolist())
    accent_candidates = "éèêàâçôöîïùûüñ"
    accents_present = sorted(set(accent_candidates) & set(accent_text))
    check(
        "Meaningful accented Unicode characters remain",
        len(accents_present) >= 3,
        f"accented characters present: {', '.join(accents_present)}",
    )
    check(
        "Résumé whitespace artefacts are removed",
        count_records_matching(clean_resume["clean_text"], MULTI_SPACE_PATTERN) == 0
        and count_records_matching(clean_resume["clean_text"], "\u00a0") == 0,
        "no double spaces and no non-breaking spaces in clean_text",
    )
    check(
        "Exact duplicate résumés are preserved, not deleted",
        int(clean_resume["is_duplicate_resume_str"].sum())
        == int(raw_resume["Resume_str"].duplicated(keep=False).sum()),
        f"raw duplicate rows={int(raw_resume['Resume_str'].duplicated(keep=False).sum())}, "
        f"processed duplicate rows={int(clean_resume['is_duplicate_resume_str'].sum())}",
    )

    # Job checks -----------------------------------------------------------
    check(
        "Job record count is preserved",
        len(clean_job) == len(raw_job),
        f"raw={len(raw_job)}, processed={len(clean_job)}",
    )
    raw_label_counts = raw_job["label"].value_counts().sort_index()
    clean_label_counts = clean_job["label_clean"].value_counts().sort_index()
    check(
        "All six job-role labels are preserved",
        set(clean_job["label_clean"].tolist()) == set(EXPECTED_JOB_LABELS)
        and len(clean_job["label_clean"].unique()) == 6,
        f"labels={sorted(clean_job['label_clean'].unique().tolist())}",
    )
    check(
        "Job-role distribution is unchanged",
        raw_label_counts.equals(clean_label_counts),
        f"raw={raw_label_counts.to_dict()}, clean={clean_label_counts.to_dict()}",
    )
    check(
        "No invalid job labels",
        int((~clean_job["is_valid_label"]).sum()) == 0,
        f"invalid labels={int((~clean_job['is_valid_label']).sum())}",
    )
    check(
        "Short job descriptions are retained",
        len(clean_job) == len(raw_job)
        and int(clean_job["short_description_flag"].sum())
        == int(clean_job["description_length"].lt(SHORT_DESCRIPTION_THRESHOLD).sum()),
        f"short descriptions flagged={int(clean_job['short_description_flag'].sum())}",
    )
    check(
        "Job clean descriptions are NFC-normalised",
        bool(
            clean_job["clean_description"]
            .map(lambda text: unicodedata.normalize("NFC", text) == text)
            .all()
        ),
        "all clean_description values equal their NFC form",
    )

    # Skills checks --------------------------------------------------------
    check(
        "Skills record count is preserved",
        len(clean_skills) == len(raw_skills),
        f"raw={len(raw_skills)}, processed={len(clean_skills)}",
    )
    raw_pairs = (
        raw_skills.groupby(["O*NET-SOC Code", "Workplace Example"], dropna=False)
        .size()
        .sort_index()
    )
    clean_pairs = (
        clean_skills.groupby(["O*NET-SOC Code", "Workplace Example"], dropna=False)
        .size()
        .sort_index()
    )
    check(
        "Valid many-to-many skill relationships remain intact",
        len(clean_skills) == len(raw_skills)
        and int(raw_skills["Workplace Example"].nunique())
        == int(clean_skills["Workplace Example"].nunique())
        and int(raw_skills["Workplace Example"].duplicated().sum())
        == int(clean_skills["Workplace Example"].duplicated().sum())
        and raw_pairs.equals(clean_pairs),
        f"unique workplace examples={int(clean_skills['Workplace Example'].nunique())}, "
        f"repeated example rows={int(clean_skills['Workplace Example'].duplicated().sum())}, "
        f"unique occupation-example pairs={len(clean_pairs)}",
    )
    check(
        "No genuine identical skill rows were removed",
        int(clean_skills[SKILLS_ORIGINAL_COLUMNS].duplicated().sum())
        == int(raw_skills[SKILLS_ORIGINAL_COLUMNS].duplicated().sum()),
        f"raw={int(raw_skills[SKILLS_ORIGINAL_COLUMNS].duplicated().sum())}, "
        f"processed={int(clean_skills[SKILLS_ORIGINAL_COLUMNS].duplicated().sum())}",
    )
    check(
        "Skill Y/N flags are valid",
        int((~clean_skills["is_valid_hot_technology"]).sum()) == 0
        and int((~clean_skills["is_valid_in_demand"]).sum()) == 0,
        f"invalid Hot Technology={int((~clean_skills['is_valid_hot_technology']).sum())}, "
        f"invalid In Demand={int((~clean_skills['is_valid_in_demand']).sum())}",
    )
    check(
        "Skills clean text columns are NFC-normalised",
        bool(
            clean_skills["Title_clean"].map(
                lambda text: unicodedata.normalize("NFC", text) == text
            ).all()
            and clean_skills["Workplace Example_clean"]
            .map(lambda text: unicodedata.normalize("NFC", text) == text)
            .all()
            and clean_skills["Element Name_clean"]
            .map(lambda text: unicodedata.normalize("NFC", text) == text)
            .all()
        ),
        "all cleaned skill text fields equal their NFC form",
    )

    # Output files ---------------------------------------------------------
    for path in output_paths:
        check(
            f"Output file exists: {path.name}",
            path.exists() and path.stat().st_size > 0,
            f"path={relative_path(path)}, size={human_size(path.stat().st_size) if path.exists() else 'missing'}",
        )

    failures = [item for item in checks if not item["passed"]]
    assert not failures, "Validation failed: " + "; ".join(
        f"{item['check']} ({item['detail']})" for item in failures
    )


# ---------------------------------------------------------------------------
# Report and log generation
# ---------------------------------------------------------------------------


def collect_run_metadata() -> dict[str, str]:
    """Collect environment details for reproducibility."""
    return {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "pandas": pd.__version__,
        "script": relative_path(SCRIPT_PATH),
        "cwd": str(Path.cwd()),
    }


def format_counts(counts: dict[str, int]) -> str:
    """Format a count mapping as a compact ordered string."""
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{key}: {value:,}" for key, value in ordered)


def build_before_after_rows(results: list[dict[str, Any]]) -> list[list[Any]]:
    """Build the before/after statistics table rows."""
    rows: list[list[Any]] = []
    for result in results:
        stats = result["stats"]
        rows.append(
            [
                result["name"],
                f"{stats['raw_rows']:,}",
                f"{stats['processed_rows']:,}",
                f"{stats['excluded_rows']:,}",
                f"{stats['raw_columns']:,}",
                f"{stats['processed_columns']:,}",
            ]
        )
    return rows


def build_validation_rows(checks: list[dict[str, Any]]) -> list[list[Any]]:
    """Build the validation table rows."""
    return [
        [
            md_cell(item["check"], 90),
            "PASS" if item["passed"] else "FAIL",
            md_cell(item["detail"], 180),
        ]
        for item in checks
    ]


def build_cleaning_log(
    results: list[dict[str, Any]],
    fingerprints_before: dict[str, dict[str, Any]],
    fingerprints_after: dict[str, dict[str, Any]],
    checks: list[dict[str, Any]],
    validation_error: str | None,
    run_metadata: dict[str, str],
    output_paths: list[Path],
) -> str:
    """Build the detailed data-cleaning log."""
    resume_result, job_result, skills_result = results
    resume_stats = resume_result["stats"]
    job_stats = job_result["stats"]
    skills_stats = skills_result["stats"]

    lines: list[str] = []
    lines.append("# JobMatch AI - Data Cleaning Log (Phase 2.3)")
    lines.append("")
    lines.append(f"**Generated:** {run_metadata['timestamp']}")
    lines.append("")
    lines.append(f"**Script:** `{run_metadata['script']}`")
    lines.append("")
    lines.append(f"**Python:** `{run_metadata['python']}` | **pandas:** `{run_metadata['pandas']}`")
    lines.append("")

    lines.append("## 1. Scope and data-safety decisions")
    lines.append("")
    lines.append("- Raw files under `ai-engine/data/raw/` were opened read-only and never overwritten.")
    lines.append("- SHA-256 fingerprints were recorded before and after the run and compared.")
    lines.append("- Original columns and identifiers are retained in every processed file.")
    lines.append("- No duplicate résumés were deleted; they are flagged and written to an audit file.")
    lines.append("- No short job descriptions were deleted; they are flagged for review.")
    lines.append("- No résumé category was mapped to the six job-role labels.")
    lines.append("- Repeated workplace examples in the skills dictionary were retained.")
    lines.append("- No machine-learning model, feature extraction, or job-matching logic was run.")
    lines.append("")

    lines.append("## 2. Environment and inputs")
    lines.append("")
    input_rows = []
    for name, before in fingerprints_before.items():
        input_rows.append(
            [
                name,
                before["size_human"],
                f"`{before['sha256'][:16]}...`",
            ]
        )
    lines.append(markdown_table(["Raw dataset", "Size", "SHA-256 prefix"], input_rows))
    lines.append("")

    lines.append("## 3. Before/after record counts")
    lines.append("")
    lines.append(
        markdown_table(
            ["Dataset", "Raw rows", "Processed rows", "Excluded rows", "Raw columns", "Processed columns"],
            build_before_after_rows(results),
        )
    )
    lines.append("")
    lines.append(
        "**Résumé exclusion rule:** a record is excluded from the modeling-ready "
        "résumé file only when both HTML-derived text and the `Resume_str` fallback "
        "are empty after normalisation. The excluded record remains available in the "
        "raw file and in the excluded-records audit."
    )
    lines.append("")

    lines.append("## 4. Résumé cleaning")
    lines.append("")
    lines.append("### Transformations applied")
    lines.append("")
    lines.append(
        "1. **HTML parsing:** every `Resume_html` value was parsed with a standard-library "
        "`HTMLParser`. Script/style/noscript/template content was skipped, block elements "
        "were converted to word boundaries, and only visible text was collected."
    )
    lines.append(
        "2. **Entity decoding:** HTML entities were decoded with `html.unescape`, then "
        "parsed again so encoded markup did not leak into the output."
    )
    lines.append(
        "3. **Unicode normalisation:** all clean text was converted to Unicode NFC."
    )
    lines.append(
        "4. **Whitespace normalisation:** non-breaking/figure/narrow spaces were converted "
        "to ordinary spaces; zero-width space, BOM, and soft hyphen artefacts were removed; "
        "line breaks and control characters were converted to spaces; repeated whitespace "
        "was collapsed and the result trimmed."
    )
    lines.append(
        "5. **Fallback:** when HTML extraction produced no text but `Resume_str` did, the "
        "normalised `Resume_str` was used and `text_source` records the fallback."
    )
    lines.append(
        "6. **Empty-record handling:** records with no content after extraction and fallback "
        "were excluded from `resumes_cleaned.csv` and written to `excluded_records.csv`."
    )
    lines.append(
        "7. **Duplicate handling:** exact duplicates were detected on both raw `Resume_str` "
        "and `clean_text`, flagged in the processed file, and exported to `duplicate_records.csv`. "
        "No duplicate was removed."
    )
    lines.append(
        "8. **Preservation:** `ID`, `Resume_str`, `Resume_html`, and `Category` were kept unchanged; "
        "the cleaned representation is an added column."
    )
    lines.append("")
    lines.append("### Measured results")
    lines.append("")
    lines.append(
        markdown_table(
            ["Metric", "Value"],
            [
                ["Raw records", f"{resume_stats['raw_rows']:,}"],
                ["Processed records", f"{resume_stats['processed_rows']:,}"],
                ["Excluded records", f"{resume_stats['excluded_rows']:,}"],
                ["HTML records parsed", f"{resume_stats['html_records']:,}"],
                ["HTML parse errors", f"{resume_stats['html_parse_errors']:,}"],
                ["Script/style tags found", f"{resume_stats['script_style_tags']:,}"],
                ["HTML entity references found", f"{resume_stats['html_entity_references']:,}"],
                ["Raw records with HTML markup", f"{resume_stats['raw_records_with_html_markup']:,}"],
                ["Clean records with HTML markup", f"{resume_stats['clean_records_with_html_markup']:,}"],
                ["Raw records with non-breaking spaces", f"{resume_stats['raw_records_with_nbsp']:,}"],
                ["Clean records with non-breaking spaces", f"{resume_stats['clean_records_with_nbsp']:,}"],
                ["Raw records with multiple spaces", f"{resume_stats['raw_records_with_multiple_spaces']:,}"],
                ["Clean records with multiple spaces", f"{resume_stats['clean_records_with_multiple_spaces']:,}"],
                ["Raw records with non-ASCII characters", f"{resume_stats['raw_records_with_non_ascii']:,}"],
                ["Clean records with non-ASCII characters", f"{resume_stats['clean_records_with_non_ascii']:,}"],
                ["Raw records with U+FFFD replacement char", f"{resume_stats['raw_records_with_replacement_char']:,}"],
                ["Clean records with U+FFFD replacement char", f"{resume_stats['clean_records_with_replacement_char']:,}"],
                ["Raw records with private-use chars", f"{resume_stats['raw_records_with_private_use']:,}"],
                ["Clean records with private-use chars", f"{resume_stats['clean_records_with_private_use']:,}"],
                ["Clean text min length", f"{resume_stats['clean_length_stats']['min']:,}"],
                ["Clean text median length", f"{resume_stats['clean_length_stats']['median']:.0f}"],
                ["Clean text mean length", f"{resume_stats['clean_length_stats']['mean']:.0f}"],
                ["Clean text max length", f"{resume_stats['clean_length_stats']['max']:,}"],
                ["Text source counts", format_counts(resume_stats["text_source_counts"])],
                ["Category counts", format_counts(resume_stats["category_counts"])],
            ],
        )
    )
    lines.append("")
    lines.append("### Excluded résumé records")
    lines.append("")
    if resume_result["excluded"].empty:
        lines.append("No résumé records were excluded.")
    else:
        excluded_rows = [
            [
                int(row["ID"]),
                int(row["source_row_index"]),
                row["Category"],
                "empty after HTML extraction and fallback",
                int(row["clean_text_length"]),
            ]
            for _, row in resume_result["excluded"].iterrows()
        ]
        lines.append(
            markdown_table(
                ["Record ID", "Source row index", "Category", "Reason", "Clean length"],
                excluded_rows,
            )
        )
    lines.append("")
    lines.append("### Duplicate résumé analysis")
    lines.append("")
    lines.append(
        markdown_table(
            ["Basis", "Groups", "Rows involved", "Deletion"],
            [
                [
                    "Exact `Resume_str`",
                    f"{resume_stats['raw_duplicate_groups']:,}",
                    f"{resume_stats['raw_duplicate_rows']:,}",
                    "None (flagged for human review)",
                ],
                [
                    "Exact `clean_text`",
                    f"{resume_stats['clean_duplicate_groups']:,}",
                    f"{resume_stats['clean_duplicate_rows']:,}",
                    "None (flagged for human review)",
                ],
            ],
        )
    )
    lines.append("")
    if resume_result["duplicate_rows"]:
        duplicate_rows = [
            [
                row["duplicate_basis"],
                row["duplicate_group"],
                md_cell(row["record_id"], 20),
                row["source_row_index"],
                row["label"],
                row["original_length"],
                row["clean_length"],
            ]
            for row in resume_result["duplicate_rows"]
        ]
        lines.append(
            markdown_table(
                [
                    "Basis",
                    "Group",
                    "Record ID",
                    "Source row",
                    "Category",
                    "Raw length",
                    "Clean length",
                ],
                duplicate_rows,
            )
        )
    lines.append("")
    lines.append("### Columns added to the résumé output")
    lines.append("")
    lines.append(
        markdown_table(
            ["Column", "Purpose"],
            [[f"`{column}`", description] for column, description in resume_result["added_columns"]],
        )
    )
    lines.append("")

    lines.append("## 5. Job-role cleaning")
    lines.append("")
    lines.append("### Transformations applied")
    lines.append("")
    lines.append(
        "1. **Description normalisation:** NFC, non-breaking-space replacement, invisible "
        "character removal, whitespace collapsing, and trimming, using the same function as "
        "the résumé text."
    )
    lines.append(
        "2. **Label normalisation:** labels were whitespace-normalised, NFC-normalised, and "
        "mapped case-insensitively to the six documented role categories. The original label "
        "remains in `label`; the canonical value is in `label_clean`, and `label_changed` records "
        "whether the string changed."
    )
    lines.append(
        "3. **Review flags:** descriptions shorter than 300 characters are flagged in "
        "`short_description_flag`; empty descriptions are flagged in `is_empty_description`; "
        "labels outside the six categories are flagged in `is_valid_label`."
    )
    lines.append(
        "4. **Duplicate detection:** exact duplicate job descriptions were detected for audit "
        "but no description was removed."
    )
    lines.append("")
    lines.append("### Measured results")
    lines.append("")
    lines.append(
        markdown_table(
            ["Metric", "Raw", "Clean"],
            [
                ["Records", f"{job_stats['raw_rows']:,}", f"{job_stats['processed_rows']:,}"],
                ["Empty descriptions", f"{job_stats['empty_descriptions_raw']:,}", f"{job_stats['empty_descriptions_clean']:,}"],
                [
                    f"Descriptions under {job_stats['short_description_threshold']} characters",
                    f"{job_stats['short_descriptions_raw']:,}",
                    f"{job_stats['short_descriptions_clean']:,}",
                ],
                ["Records with multiple spaces", f"{job_stats['raw_records_with_multiple_spaces']:,}", f"{job_stats['clean_records_with_multiple_spaces']:,}"],
                ["Records with non-ASCII characters", f"{job_stats['raw_records_with_non_ascii']:,}", f"{job_stats['clean_records_with_non_ascii']:,}"],
                ["Records with U+FFFD replacement char", f"{job_stats['raw_records_with_replacement_char']:,}", f"{job_stats['clean_records_with_replacement_char']:,}"],
                ["Records with private-use chars", f"{job_stats['raw_records_with_private_use']:,}", f"{job_stats['clean_records_with_private_use']:,}"],
                ["Min description length", f"{job_stats['raw_length_stats']['min']:,}", f"{job_stats['clean_length_stats']['min']:,}"],
                ["Median description length", f"{job_stats['raw_length_stats']['median']:.0f}", f"{job_stats['clean_length_stats']['median']:.0f}"],
                ["Mean description length", f"{job_stats['raw_length_stats']['mean']:.0f}", f"{job_stats['clean_length_stats']['mean']:.0f}"],
                ["Max description length", f"{job_stats['raw_length_stats']['max']:,}", f"{job_stats['clean_length_stats']['max']:,}"],
            ],
        )
    )
    lines.append("")
    lines.append("### Label preservation")
    lines.append("")
    label_rows = []
    for label in EXPECTED_JOB_LABELS:
        label_rows.append(
            [
                label,
                f"{job_stats['label_counts_raw'].get(label, 0):,}",
                f"{job_stats['label_counts_clean'].get(label, 0):,}",
            ]
        )
    lines.append(markdown_table(["Label", "Raw count", "Clean count"], label_rows))
    lines.append("")
    lines.append(
        f"- Labels changed by normalisation: **{job_stats['label_changed_count']:,}**."
    )
    lines.append(
        f"- Invalid labels after normalisation: **{job_stats['invalid_label_count']:,}**."
    )
    lines.append(
        f"- Exact duplicate job-description groups: **{job_stats['duplicate_description_groups']:,}** "
        f"({job_stats['duplicate_description_rows']:,} rows). None were deleted."
    )
    lines.append("")
    lines.append("### Short descriptions flagged for review")
    lines.append("")
    lines.append(
        f"**{job_stats['short_descriptions_clean']:,}** cleaned descriptions are shorter than "
        f"{job_stats['short_description_threshold']} characters. They remain in the processed "
        "file. First 10 examples:"
    )
    lines.append("")
    if job_stats["short_records_preview"]:
        lines.append(
            markdown_table(
                ["Source row", "Label", "Raw length", "Clean length", "Preview"],
                [
                    [
                        item["source_row_index"],
                        md_cell(item["label"], 40),
                        item["original_length"],
                        item["clean_length"],
                        md_cell(item["preview"], 150),
                    ]
                    for item in job_stats["short_records_preview"]
                ],
            )
        )
    lines.append("")
    lines.append("### Columns added to the job-role output")
    lines.append("")
    lines.append(
        markdown_table(
            ["Column", "Purpose"],
            [[f"`{column}`", description] for column, description in job_result["added_columns"]],
        )
    )
    lines.append("")

    lines.append("## 6. Software-skills cleaning")
    lines.append("")
    lines.append("### Transformations applied")
    lines.append("")
    lines.append(
        "1. **Text normalisation:** `Title`, `Workplace Example`, and `Element Name` were "
        "NFC-normalised, whitespace-normalised, and trimmed. Original columns were retained."
    )
    lines.append(
        "2. **Flag normalisation:** `Hot Technology` and `In Demand` were whitespace/Unicode "
        "normalised and uppercased; original columns were retained."
    )
    lines.append(
        "3. **Occupation titles:** titles were standardised for Unicode and whitespace only. "
        "Case was not forced so that official O*NET capitalisation and acronyms are preserved."
    )
    lines.append(
        "4. **Many-to-many relationships:** repeated workplace examples were retained. They are "
        "valid because the same technology can be linked to multiple occupations."
    )
    lines.append(
        "5. **Genuine duplicate rows:** fully identical rows were detected for reporting only; "
        "none were removed."
    )
    lines.append("")
    lines.append("### Measured results")
    lines.append("")
    lines.append(
        markdown_table(
            ["Metric", "Raw", "Clean"],
            [
                ["Records", f"{skills_stats['raw_rows']:,}", f"{skills_stats['processed_rows']:,}"],
                ["Unique occupation titles", f"{skills_stats['unique_titles_raw']:,}", f"{skills_stats['unique_titles_clean']:,}"],
                ["Unique workplace examples", f"{skills_stats['unique_workplace_examples_raw']:,}", f"{skills_stats['unique_workplace_examples_clean']:,}"],
                ["Rows repeating a workplace example", f"{skills_stats['repeated_workplace_example_rows_raw']:,}", f"{skills_stats['repeated_workplace_example_rows_clean']:,}"],
                ["Unique occupation-example pairs", f"{skills_stats['unique_occupation_example_pairs']:,}", f"{skills_stats['unique_cleaned_occupation_example_pairs']:,}"],
                ["Rows with multiple spaces", f"{skills_stats['raw_records_with_multiple_spaces']:,}", f"{skills_stats['clean_records_with_multiple_spaces']:,}"],
                ["Rows with non-ASCII characters", f"{skills_stats['raw_records_with_non_ascii']:,}", f"{skills_stats['clean_records_with_non_ascii']:,}"],
                ["Rows with U+FFFD replacement char", f"{skills_stats['raw_records_with_replacement_char']:,}", f"{skills_stats['clean_records_with_replacement_char']:,}"],
                ["Rows with private-use chars", f"{skills_stats['raw_records_with_private_use']:,}", f"{skills_stats['clean_records_with_private_use']:,}"],
            ],
        )
    )
    lines.append("")
    lines.append(
        f"- Title values changed by normalisation: **{skills_stats['title_changed']:,}**."
    )
    lines.append(
        f"- Workplace-example values changed: **{skills_stats['workplace_example_changed']:,}**."
    )
    lines.append(
        f"- Element-name values changed: **{skills_stats['element_name_changed']:,}**."
    )
    lines.append(
        f"- Genuine identical full-row groups: **{skills_stats['full_duplicate_groups']:,}** "
        f"({skills_stats['full_duplicate_rows']:,} rows). None were removed."
    )
    lines.append(
        f"- Invalid Hot Technology flags: **{skills_stats['invalid_hot_technology']:,}**; "
        f"invalid In Demand flags: **{skills_stats['invalid_in_demand']:,}**."
    )
    lines.append("")
    lines.append("### Columns added to the skills output")
    lines.append("")
    lines.append(
        markdown_table(
            ["Column", "Purpose"],
            [[f"`{column}`", description] for column, description in skills_result["added_columns"]],
        )
    )
    lines.append("")

    lines.append("## 7. Output and audit files")
    lines.append("")
    output_rows = []
    for path in output_paths:
        output_rows.append(
            [
                f"`{relative_path(path)}`",
                human_size(path.stat().st_size) if path.exists() else "missing",
            ]
        )
    lines.append(markdown_table(["File", "Size"], output_rows))
    lines.append("")

    lines.append("## 8. Validation results")
    lines.append("")
    lines.append(
        "The following checks were executed automatically by the script. A failure "
        "would make the script exit with a non-zero status."
    )
    lines.append("")
    lines.append(markdown_table(["Check", "Result", "Detail"], build_validation_rows(checks)))
    lines.append("")
    if validation_error:
        lines.append(f"**Validation error:** {validation_error}")
    else:
        lines.append("**All automated validation checks passed.**")
    lines.append("")

    lines.append("## 9. Remaining data-quality issues")
    lines.append("")
    lines.append(
        f"- **Exact duplicate résumés retained:** {resume_stats['raw_duplicate_groups']} groups "
        f"({resume_stats['raw_duplicate_rows']} rows) still require a human deduplication decision."
    )
    lines.append(
        f"- **U+FFFD replacement characters remain:** {resume_stats['clean_records_with_replacement_char']:,} "
        f"résumé records and {job_stats['clean_records_with_replacement_char']:,} job records contain "
        "replacement characters. They were preserved rather than guessed or deleted."
    )
    lines.append(
        f"- **Private-use Unicode characters remain:** {resume_stats['clean_records_with_private_use']:,} "
        f"résumé records and {job_stats['clean_records_with_private_use']:,} job records contain "
        "private-use glyphs (often PDF bullet symbols)."
    )
    lines.append(
        f"- **Short job descriptions retained:** {job_stats['short_descriptions_clean']:,} cleaned "
        f"descriptions are below {SHORT_DESCRIPTION_THRESHOLD} characters and may be low-information "
        "or truncated."
    )
    lines.append(
        "- **Résumé categories remain unmapped:** all 24 categories are preserved; no mapping to "
        "the six job-role labels was applied."
    )
    lines.append(
        f"- **Repeated skill examples remain:** {skills_stats['repeated_workplace_example_rows_clean']:,} "
        "rows repeat a workplace example across occupations. This is intentional many-to-many data."
    )
    lines.append(
        "- **No semantic deduplication, near-duplicate detection, language detection, spelling "
        "correction, or mojibake repair was performed.**"
    )
    lines.append("")

    lines.append("## 10. Decisions requiring human review")
    lines.append("")
    lines.append(
        "1. Confirm whether résumé ID `12632728` should remain excluded from the modeling-ready "
        "file or be repaired. The raw record is retained and the exclusion is audited."
    )
    lines.append(
        "2. Decide whether the exact duplicate résumé pairs should be removed, merged, or kept. "
        "They are currently retained and flagged in `duplicate_records.csv`."
    )
    lines.append(
        "3. Confirm that HTML-derived `clean_text` is the preferred résumé text source. "
        "`Resume_str` is retained as a fallback and original field."
    )
    lines.append(
        "4. Decide whether to map or filter the 24 résumé categories for the IT-focused job matcher."
    )
    lines.append(
        "5. Review short job descriptions before removing or down-weighting them."
    )
    lines.append(
        "6. Decide whether replacement characters and private-use glyphs should be repaired, "
        "normalised to standard symbols, or left as-is."
    )
    lines.append(
        "7. Decide how repeated skill/occupation relationships should be aggregated for feature "
        "engineering without destroying the many-to-many mapping."
    )
    lines.append("")

    lines.append("## 11. Assumptions")
    lines.append("")
    lines.append(
        "- `utf-8-sig` is the correct encoding for the three raw files; fallback encodings are "
        "attempted only on decode failure."
    )
    lines.append(
        "- `Resume_html` is the primary résumé text source; `Resume_str` is used only when HTML "
        "extraction yields no text."
    )
    lines.append(
        "- A résumé is considered invalid for modelling only when both text sources are empty "
        "after normalisation."
    )
    lines.append(
        "- The six job labels are the documented categories from the Phase 2.2 inspection."
    )
    lines.append(
        "- Cleaning standardises representation; it does not correct content, resolve semantic "
        "errors, or make deduplication decisions."
    )
    lines.append("")

    return "\n".join(lines)


def build_cleaning_report(
    results: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    validation_error: str | None,
    run_metadata: dict[str, str],
    output_paths: list[Path],
) -> str:
    """Build the concise cleaning report for Phase 2.3."""
    resume_result, job_result, skills_result = results
    resume_stats = resume_result["stats"]
    job_stats = job_result["stats"]
    skills_stats = skills_result["stats"]

    total_raw = sum(result["stats"]["raw_rows"] for result in results)
    total_processed = sum(result["stats"]["processed_rows"] for result in results)
    total_excluded = sum(result["stats"]["excluded_rows"] for result in results)

    lines: list[str] = []
    lines.append("# JobMatch AI - Data Cleaning Report (Phase 2.3)")
    lines.append("")
    lines.append(f"**Generated:** {run_metadata['timestamp']}")
    lines.append("")
    lines.append(
        "This report summarises the measured results of the cleaning pipeline. "
        "Detailed transformation records, flags, and review decisions are in "
        "`ai-engine/data/audit/cleaning_log.md`."
    )
    lines.append("")

    lines.append("## 1. Executive summary")
    lines.append("")
    lines.append(
        f"- **Measured:** {total_raw:,} raw records were read and {total_processed:,} "
        f"modeling-ready records were written across the three datasets; "
        f"{total_excluded:,} invalid résumé record was excluded."
    )
    lines.append(
        f"- **Measured:** all {resume_stats['html_records']:,} résumé HTML records were parsed; "
        f"{resume_stats['clean_records_with_html_markup']:,} clean résumé records still contain "
        "HTML markup."
    )
    lines.append(
        f"- **Measured:** {job_stats['short_descriptions_clean']:,} job descriptions are shorter "
        f"than {SHORT_DESCRIPTION_THRESHOLD} characters and were retained with review flags; "
        "all six job-role labels and their distribution are preserved."
    )
    lines.append(
        f"- **Measured:** all {skills_stats['processed_rows']:,} skill rows were retained, including "
        f"{skills_stats['repeated_workplace_example_rows_clean']:,} rows that repeat a workplace "
        "example across occupations."
    )
    lines.append(
        f"- **Measured:** exact duplicate résumés were preserved and audited; no duplicate, short "
        "description, repeated skill, or category mapping was removed automatically."
    )
    if validation_error:
        lines.append(
            f"- **Validation:** FAILED - {validation_error}"
        )
    else:
        lines.append("- **Validation:** all automated checks passed.")
    lines.append("")

    lines.append("## 2. Before/after dataset statistics")
    lines.append("")
    lines.append(
        markdown_table(
            ["Dataset", "Raw rows", "Processed rows", "Excluded rows", "Raw columns", "Processed columns"],
            build_before_after_rows(results),
        )
    )
    lines.append("")
    lines.append(
        "Résumé rows decrease by exactly the number of invalid empty records. Job-role and "
        "software-skills row counts are unchanged."
    )
    lines.append("")

    lines.append("## 3. Cleaning operations")
    lines.append("")
    lines.append("### Résumés")
    lines.append("")
    lines.append(
        "- Parsed `Resume_html` with a standard-library HTML parser, skipped script/style content, "
        "decoded HTML entities, and extracted visible text."
    )
    lines.append(
        "- Applied NFC, replaced non-breaking/Unicode spaces, removed zero-width/BOM/soft-hyphen "
        "artefacts, collapsed repeated whitespace, and trimmed."
    )
    lines.append(
        "- Added `clean_text` and related flags while preserving `ID`, `Resume_str`, `Resume_html`, "
        "and `Category`."
    )
    lines.append(
        f"- Excluded {resume_stats['excluded_rows']:,} empty record from the modeling-ready file and "
        "wrote it to the excluded-records audit."
    )
    lines.append(
        f"- Detected {resume_stats['raw_duplicate_groups']:,} exact `Resume_str` duplicate groups "
        f"({resume_stats['raw_duplicate_rows']:,} rows) and retained them for human review."
    )
    lines.append("")
    lines.append("### Job roles")
    lines.append("")
    lines.append(
        "- Normalised descriptions with NFC and whitespace rules while preserving punctuation, "
        "accents, and meaningful symbols."
    )
    lines.append(
        "- Canonicalised labels case-insensitively against the six documented role categories; "
        "original labels were retained."
    )
    lines.append(
        f"- Flagged {job_stats['short_descriptions_clean']:,} descriptions shorter than "
        f"{SHORT_DESCRIPTION_THRESHOLD} characters; none were deleted."
    )
    lines.append(
        f"- Found {job_stats['duplicate_description_groups']:,} exact duplicate description groups; "
        "none were deleted."
    )
    lines.append("")
    lines.append("### Software skills")
    lines.append("")
    lines.append(
        "- Normalised `Title`, `Workplace Example`, `Element Name`, and Y/N flags while retaining "
        "all original columns."
    )
    lines.append(
        f"- Preserved all {skills_stats['processed_rows']:,} rows and "
        f"{skills_stats['unique_occupation_example_pairs']:,} unique occupation-example pairs."
    )
    lines.append(
        f"- Found {skills_stats['full_duplicate_groups']:,} genuinely identical full-row groups; "
        "none were removed."
    )
    lines.append("")

    lines.append("## 4. Excluded records")
    lines.append("")
    if resume_result["excluded"].empty:
        lines.append("No records were excluded.")
    else:
        excluded_rows = [
            [
                int(row["ID"]),
                int(row["source_row_index"]),
                row["Category"],
                "empty after HTML extraction and fallback",
            ]
            for _, row in resume_result["excluded"].iterrows()
        ]
        lines.append(
            markdown_table(
                ["Record ID", "Source row index", "Category", "Reason"],
                excluded_rows,
            )
        )
    lines.append("")
    lines.append(
        "The full excluded record, including original text fields, is in "
        "`ai-engine/data/audit/excluded_records.csv`."
    )
    lines.append("")

    lines.append("## 5. Duplicate analysis")
    lines.append("")
    lines.append(
        markdown_table(
            ["Dataset/basis", "Groups", "Rows involved", "Action"],
            [
                [
                    "Résumé: exact `Resume_str`",
                    f"{resume_stats['raw_duplicate_groups']:,}",
                    f"{resume_stats['raw_duplicate_rows']:,}",
                    "Flagged and audited; retained",
                ],
                [
                    "Résumé: exact `clean_text`",
                    f"{resume_stats['clean_duplicate_groups']:,}",
                    f"{resume_stats['clean_duplicate_rows']:,}",
                    "Flagged and audited; retained",
                ],
                [
                    "Job descriptions: exact text",
                    f"{job_stats['duplicate_description_groups']:,}",
                    f"{job_stats['duplicate_description_rows']:,}",
                    "Audited; retained",
                ],
                [
                    "Skills: genuinely identical full rows",
                    f"{skills_stats['full_duplicate_groups']:,}",
                    f"{skills_stats['full_duplicate_rows']:,}",
                    "Audited; retained",
                ],
                [
                    "Skills: repeated workplace examples",
                    "Many-to-many",
                    f"{skills_stats['repeated_workplace_example_rows_clean']:,}",
                    "Valid relationships; retained",
                ],
            ],
        )
    )
    lines.append("")
    lines.append(
        "Duplicate records are listed in `ai-engine/data/audit/duplicate_records.csv`. "
        "Repeated workplace examples are intentionally not treated as duplicate records."
    )
    lines.append("")

    lines.append("## 6. Data-quality improvements")
    lines.append("")
    lines.append(
        markdown_table(
            ["Metric", "Before", "After"],
            [
                [
                    "Résumé records with HTML markup",
                    f"{resume_stats['raw_records_with_html_markup']:,}",
                    f"{resume_stats['clean_records_with_html_markup']:,}",
                ],
                [
                    "Résumé records with non-breaking spaces",
                    f"{resume_stats['raw_records_with_nbsp']:,}",
                    f"{resume_stats['clean_records_with_nbsp']:,}",
                ],
                [
                    "Résumé records with multiple spaces",
                    f"{resume_stats['raw_records_with_multiple_spaces']:,}",
                    f"{resume_stats['clean_records_with_multiple_spaces']:,}",
                ],
                [
                    "Empty résumé records",
                    f"{resume_stats['raw_rows'] - resume_stats['processed_rows']:,}",
                    f"{resume_stats['excluded_rows']:,} excluded",
                ],
                [
                    "Job descriptions with multiple spaces",
                    f"{job_stats['raw_records_with_multiple_spaces']:,}",
                    f"{job_stats['clean_records_with_multiple_spaces']:,}",
                ],
                [
                    "Skills text fields with multiple spaces",
                    f"{skills_stats['raw_records_with_multiple_spaces']:,}",
                    f"{skills_stats['clean_records_with_multiple_spaces']:,}",
                ],
            ],
        )
    )
    lines.append("")
    lines.append(
        f"- **Unicode preserved:** {resume_stats['clean_records_with_non_ascii']:,} résumé records "
        f"and {job_stats['clean_records_with_non_ascii']:,} job-description records still contain "
        "meaningful non-ASCII characters after cleaning."
    )
    lines.append(
        f"- **Labels preserved:** six job-role labels remain with the same distribution; "
        f"{job_stats['label_changed_count']:,} label strings changed during canonicalisation."
    )
    lines.append(
        f"- **Skills preserved:** {skills_stats['processed_rows']:,} rows, "
        f"{skills_stats['unique_workplace_examples_clean']:,} unique workplace examples, and "
        f"{skills_stats['unique_occupation_example_pairs']:,} occupation-example pairs remain."
    )
    lines.append("")

    lines.append("## 7. Validation results")
    lines.append("")
    lines.append(
        markdown_table(
            ["Check", "Result", "Detail"],
            build_validation_rows(checks),
        )
    )
    lines.append("")
    if validation_error:
        lines.append(f"**Validation error:** {validation_error}")
    else:
        lines.append("**All automated validation checks passed.**")
    lines.append("")

    lines.append("## 8. Remaining limitations")
    lines.append("")
    lines.append(
        f"- {resume_stats['clean_records_with_replacement_char']:,} résumé records and "
        f"{job_stats['clean_records_with_replacement_char']:,} job records still contain U+FFFD "
        "replacement characters; these indicate source encoding loss and were not guessed."
    )
    lines.append(
        f"- {resume_stats['clean_records_with_private_use']:,} résumé records and "
        f"{job_stats['clean_records_with_private_use']:,} job records still contain private-use "
        "Unicode glyphs, typically PDF bullet symbols."
    )
    lines.append(
        "- Exact duplicates are detected but not resolved; near-duplicates and semantic duplicates "
        "were not analysed."
    )
    lines.append(
        "- The 24 résumé categories remain unmapped to the six job-role labels."
    )
    lines.append(
        "- No stemming, lemmatisation, stopword removal, lowercasing of descriptions, language "
        "detection, spelling correction, or feature extraction was performed."
    )
    lines.append(
        "- HTML extraction preserves visible text but not layout; `Resume_html` and `Resume_str` "
        "remain available as original fields."
    )
    lines.append("")

    lines.append("## 9. Recommendations for feature engineering")
    lines.append("")
    lines.append(
        "1. Use `clean_text` for résumé features and `clean_description` for job-description "
        "features; keep `Resume_str` and `Resume_html` only for traceability."
    )
    lines.append(
        "2. Decide the duplicate policy before train/test splitting to avoid leakage: duplicate "
        "résumés should be grouped into the same split or removed after human review."
    )
    lines.append(
        "3. Decide the résumé category scope before modelling. Do not map the 24 categories to the "
        "six job labels without an explicit, documented mapping."
    )
    lines.append(
        "4. Use `label_clean` for job-role targets and preserve the six-class distribution; plan "
        "for class weighting or stratified splits because Software Development is 79.9% of rows."
    )
    lines.append(
        "5. Review `short_description_flag` rows before removing or down-weighting them; some may "
        "still carry useful signal."
    )
    lines.append(
        "6. Use `Title_clean`, `Workplace Example_clean`, and `Element Name_clean` for skill "
        "normalisation, while preserving the many-to-many occupation-example structure."
    )
    lines.append(
        "7. Treat replacement characters and private-use glyphs as a documented limitation; decide "
        "whether to repair them at source or ignore them during tokenisation."
    )
    lines.append(
        "8. Do not use `ID` or `source_row_index` as model features; they are identifiers only."
    )
    lines.append("")

    lines.append("## 10. Output files")
    lines.append("")
    output_rows = []
    for path in output_paths:
        output_rows.append(
            [
                f"`{relative_path(path)}`",
                human_size(path.stat().st_size) if path.exists() else "missing",
            ]
        )
    lines.append(markdown_table(["File", "Size"], output_rows))
    lines.append("")
    lines.append(
        "Detailed decisions and transformation records are in "
        "`ai-engine/data/audit/cleaning_log.md`."
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------


def main() -> int:
    """Execute the cleaning pipeline and return a process exit code."""
    configure_logging()
    LOGGER.info("Project root: %s", PROJECT_ROOT)
    LOGGER.info("Raw data: %s", RAW_DATA_DIR)
    LOGGER.info("Processed data: %s", PROCESSED_DIR)
    LOGGER.info("Audit data: %s", AUDIT_DIR)

    try:
        ensure_directories()
    except CleaningError as exc:
        LOGGER.error("%s", exc)
        return 1

    raw_paths = {
        "Resume.csv": RESUME_RAW_PATH,
        "job_roles_combined_ready_10461.csv": JOB_RAW_PATH,
        "software_skills_15.csv": SKILLS_RAW_PATH,
    }

    fingerprints_before: dict[str, dict[str, Any]] = {}
    try:
        for name, path in raw_paths.items():
            if not path.exists():
                raise CleaningError(f"Raw dataset not found: {path}")
            fingerprints_before[name] = file_fingerprint(path)
            LOGGER.info(
                "Fingerprint before: %s %s %s",
                name,
                fingerprints_before[name]["size_human"],
                fingerprints_before[name]["sha256"][:16],
            )

        raw_resume = load_csv(RESUME_RAW_PATH)
        raw_job = load_csv(JOB_RAW_PATH)
        raw_skills = load_csv(SKILLS_RAW_PATH)

        resume_result = clean_resumes(raw_resume)
        job_result = clean_jobs(raw_job)
        skills_result = clean_skills(raw_skills)
        results = [resume_result, job_result, skills_result]

        write_processed_outputs(results)

        excluded_audit = build_excluded_audit(resume_result)
        duplicate_audit = build_duplicate_audit(results)
        write_csv(excluded_audit, EXCLUDED_AUDIT_PATH)
        write_csv(duplicate_audit, DUPLICATE_AUDIT_PATH)

        fingerprints_after = {
            name: file_fingerprint(path) for name, path in raw_paths.items()
        }
        for name, after in fingerprints_after.items():
            LOGGER.info(
                "Fingerprint after:  %s %s %s",
                name,
                after["size_human"],
                after["sha256"][:16],
            )

        output_paths = [
            RESUME_CLEAN_PATH,
            JOB_CLEAN_PATH,
            SKILLS_CLEAN_PATH,
            EXCLUDED_AUDIT_PATH,
            DUPLICATE_AUDIT_PATH,
        ]

        checks: list[dict[str, Any]] = []
        validation_error: str | None = None
        try:
            validate_outputs(
                results=results,
                fingerprints_before=fingerprints_before,
                fingerprints_after=fingerprints_after,
                output_paths=output_paths,
                checks=checks,
            )
        except AssertionError as exc:
            validation_error = str(exc)
            LOGGER.error("Validation failed: %s", validation_error)
        except Exception as exc:  # noqa: BLE001 - report unexpected validation failures
            validation_error = f"{type(exc).__name__}: {exc}"
            LOGGER.exception("Validation raised an unexpected error")

        run_metadata = collect_run_metadata()
        cleaning_log = build_cleaning_log(
            results=results,
            fingerprints_before=fingerprints_before,
            fingerprints_after=fingerprints_after,
            checks=checks,
            validation_error=validation_error,
            run_metadata=run_metadata,
            output_paths=output_paths,
        )
        cleaning_report = build_cleaning_report(
            results=results,
            checks=checks,
            validation_error=validation_error,
            run_metadata=run_metadata,
            output_paths=output_paths,
        )

        CLEANING_LOG_PATH.write_text(cleaning_log, encoding="utf-8")
        CLEANING_REPORT_PATH.write_text(cleaning_report, encoding="utf-8")
        LOGGER.info("Cleaning log written: %s", CLEANING_LOG_PATH)
        LOGGER.info("Cleaning report written: %s", CLEANING_REPORT_PATH)

    except CleaningError as exc:
        LOGGER.error("Cleaning pipeline failed: %s", exc)
        return 1
    except Exception:  # noqa: BLE001 - log unexpected failures with a traceback
        LOGGER.exception("Unexpected cleaning pipeline failure")
        return 1

    print("\n" + "=" * 78)
    print("DATA CLEANING SUMMARY")
    print("=" * 78)
    for result in results:
        stats = result["stats"]
        print(
            f"- {result['name']}: {stats['raw_rows']:,} raw -> "
            f"{stats['processed_rows']:,} processed | "
            f"{stats['excluded_rows']:,} excluded | "
            f"{stats['processed_columns']:,} columns"
        )
    print(f"- Excluded audit: {EXCLUDED_AUDIT_PATH}")
    print(f"- Duplicate audit: {DUPLICATE_AUDIT_PATH}")
    print(f"- Cleaning log: {CLEANING_LOG_PATH}")
    print(f"- Cleaning report: {CLEANING_REPORT_PATH}")
    print(
        "- Validation: "
        + ("PASSED" if validation_error is None else f"FAILED - {validation_error}")
    )
    print("=" * 78)

    return 0 if validation_error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
