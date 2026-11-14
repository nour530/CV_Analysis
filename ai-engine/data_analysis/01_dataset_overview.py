#!/usr/bin/env python
"""Phase 2.2 - Exploratory data analysis for the JobMatch AI datasets.

This script loads the three raw datasets, profiles their structure and quality,
creates a set of initial visualisations, and writes a Markdown report.

The original CSV files are opened read-only. File fingerprints (size + SHA-256)
are recorded before and after the analysis to verify that they were not modified.

Run from the project root with the project virtual environment, for example:

    ai-engine/venv/Scripts/python.exe ai-engine/data_analysis/01_dataset_overview.py

or from any working directory, because all paths are derived from this file.
"""

from __future__ import annotations

import hashlib
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

# Use a non-interactive backend so the script works in headless environments.
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns


# ---------------------------------------------------------------------------
# Paths (derived from this file, never hard-coded to this machine)
# ---------------------------------------------------------------------------

SCRIPT_PATH = Path(__file__).resolve()
DATA_ANALYSIS_DIR = SCRIPT_PATH.parent
AI_ENGINE_DIR = DATA_ANALYSIS_DIR.parent
PROJECT_ROOT = AI_ENGINE_DIR.parent
RAW_DATA_DIR = AI_ENGINE_DIR / "data" / "raw"
OUTPUT_DIR = DATA_ANALYSIS_DIR / "outputs"
FIGURES_DIR = OUTPUT_DIR / "figures"
REPORT_PATH = OUTPUT_DIR / "dataset_overview_report.md"

ENCODINGS_TO_TRY = ("utf-8-sig", "utf-8", "latin-1")


# ---------------------------------------------------------------------------
# Dataset configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetSpec:
    """Metadata describing one raw dataset and how it should be analysed."""

    name: str
    relative_path: str
    description: str
    source_notes: str
    text_columns: tuple[str, ...] = ()
    primary_text_column: str | None = None
    categorical_columns: tuple[str, ...] = ()
    short_text_thresholds: dict[str, int] = field(default_factory=dict)
    id_column: str | None = None
    label_column: str | None = None


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        name="Resume.csv",
        relative_path="resumes/Resume.csv",
        description=(
            "Résumé collection with plain-text and HTML résumé versions and a "
            "single occupation/category label."
        ),
        source_notes=(
            "No local README was supplied for this file. Column semantics are "
            "inferred from the column names and measured values."
        ),
        text_columns=("Resume_str", "Resume_html"),
        primary_text_column="Resume_str",
        categorical_columns=("Category",),
        short_text_thresholds={"Resume_str": 500, "Resume_html": 1000},
        id_column="ID",
        label_column="Category",
    ),
    DatasetSpec(
        name="job_roles_combined_ready_10461.csv",
        relative_path="job_descriptions/job_roles_combined_ready_10461.csv",
        description=(
            "English IT job descriptions, each labelled with one of six role "
            "categories. Job titles were intentionally removed by the dataset "
            "authors so that models learn from description text."
        ),
        source_notes=(
            "Source metadata from ai-engine/data/raw/job_descriptions/README.md "
            "(PromptCloud Dice.com data, Rahman et al. Mendeley data, and "
            "O*NET-derived role mapping)."
        ),
        text_columns=("jobdescription",),
        primary_text_column="jobdescription",
        categorical_columns=("label",),
        short_text_thresholds={"jobdescription": 300},
        label_column="label",
    ),
    DatasetSpec(
        name="software_skills_15.csv",
        relative_path="job_descriptions/software_skills_15.csv",
        description=(
            "O*NET-derived technology/skill dictionary for SOC 15- IT "
            "occupations. Each row links an occupation to a workplace "
            "technology example and an O*NET skill category."
        ),
        source_notes=(
            "Source metadata from ai-engine/data/raw/job_descriptions/README.md "
            "(O*NET 30.3, CC BY 4.0; modified by the dataset authors)."
        ),
        text_columns=("Workplace Example",),
        primary_text_column="Workplace Example",
        categorical_columns=(
            "O*NET-SOC Code",
            "Title",
            "Element ID",
            "Element Name",
            "Hot Technology",
            "In Demand",
        ),
        # Short values such as "C", "R", and "J" are valid language names, so no
        # low-information threshold is applied to this reference column.
        short_text_thresholds={},
    ),
)


# Human-readable column descriptions used in the report. These are documented
# assumptions where no formal data dictionary exists.
COLUMN_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "Resume.csv": {
        "ID": "Unique résumé identifier (numeric identifier, not a feature).",
        "Resume_str": "Plain-text résumé content.",
        "Resume_html": "HTML-formatted résumé content (may contain markup).",
        "Category": "Occupation/profession label assigned to the résumé.",
    },
    "job_roles_combined_ready_10461.csv": {
        "jobdescription": "English IT job description text.",
        "label": "Role category label (six classes).",
    },
    "software_skills_15.csv": {
        "O*NET-SOC Code": "O*NET-SOC occupation code.",
        "Title": "O*NET occupation title.",
        "Workplace Example": "Example technology/software product or tool.",
        "Element ID": "O*NET element identifier for the skill category.",
        "Element Name": "O*NET skill/technology category name.",
        "Hot Technology": "O*NET hot-technology flag (Y/N).",
        "In Demand": "O*NET in-demand flag (Y/N).",
    },
}


# ---------------------------------------------------------------------------
# Logging and small helpers
# ---------------------------------------------------------------------------


def configure_logging() -> None:
    """Configure console logging and make stdout safe on Windows code pages."""
    if hasattr(sys.stdout, "reconfigure"):
        # Windows consoles often default to cp1252, which cannot print every
        # character found in résumé text. We only print ASCII summaries, but
        # this also protects any future log messages.
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


LOGGER = logging.getLogger("jobmatch-eda")


class DatasetReadError(RuntimeError):
    """Raised when a dataset cannot be located, decoded, or parsed."""


def human_size(num_bytes: int) -> str:
    """Return a compact human-readable byte size."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} GB"


def file_fingerprint(path: Path) -> dict[str, Any]:
    """Return size and SHA-256 hash for a file without modifying it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "size_bytes": path.stat().st_size,
        "size_human": human_size(path.stat().st_size),
        "sha256": digest.hexdigest(),
    }


def md_cell(value: Any, max_chars: int = 120) -> str:
    """Convert a value to a safe, single-line Markdown table cell."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    text = " ".join(str(value).split())
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text.replace("|", "\\|")


def preview_text(value: Any, max_chars: int = 250) -> str:
    """Return a single-line, truncated preview of a long text value."""
    return md_cell(value, max_chars=max_chars)


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


# ---------------------------------------------------------------------------
# Loading and profiling
# ---------------------------------------------------------------------------


def resolve_dataset_path(spec: DatasetSpec) -> Path:
    """Resolve a dataset path and raise a helpful error when it is missing."""
    path = RAW_DATA_DIR / spec.relative_path
    if path.exists():
        return path

    searched = [
        path,
        PROJECT_ROOT / "ai-engine" / "data" / "raw" / spec.relative_path,
        RAW_DATA_DIR / Path(spec.relative_path).name,
    ]
    message = (
        f"Dataset '{spec.name}' was not found. Expected location: {path}. "
        "Searched: " + ", ".join(str(item) for item in searched)
    )
    raise DatasetReadError(message)


def load_dataset(spec: DatasetSpec) -> tuple[pd.DataFrame, str]:
    """Load a CSV with encoding fallbacks and no silent row dropping."""
    path = resolve_dataset_path(spec)
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
                "Loaded %s: %d rows x %d columns", spec.name, frame.shape[0], frame.shape[1]
            )
            return frame, encoding
        except UnicodeDecodeError as exc:
            last_error = exc
            LOGGER.warning("Encoding %s failed for %s; trying next", encoding, spec.name)
        except pd.errors.ParserError as exc:
            raise DatasetReadError(
                f"Failed to parse '{spec.name}' at {path}: {exc}"
            ) from exc
        except OSError as exc:
            raise DatasetReadError(
                f"Failed to read '{spec.name}' at {path}: {exc}"
            ) from exc

    raise DatasetReadError(
        f"Could not decode '{spec.name}' with encodings {ENCODINGS_TO_TRY}. "
        f"Last error: {last_error}"
    )


def column_profile(frame: pd.DataFrame, spec: DatasetSpec) -> pd.DataFrame:
    """Build a per-column profile with dtype, missingness and uniqueness."""
    row_count = len(frame)
    missing = frame.isna().sum()
    unique = frame.nunique(dropna=False)
    descriptions = COLUMN_DESCRIPTIONS.get(spec.name, {})

    return pd.DataFrame(
        {
            "Column": frame.columns,
            "Data type": [str(dtype) for dtype in frame.dtypes],
            "Non-null count": [int(row_count - int(missing[col])) for col in frame.columns],
            "Missing count": [int(missing[col]) for col in frame.columns],
            "Missing %": [percent(int(missing[col]), row_count) for col in frame.columns],
            "Unique values": [int(unique[col]) for col in frame.columns],
            "Description (assumed)": [
                descriptions.get(col, "No documented description available.")
                for col in frame.columns
            ],
        }
    )


def numeric_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Return descriptive statistics for numeric columns (if any)."""
    numeric = frame.select_dtypes(include=[np.number])
    if numeric.empty:
        return pd.DataFrame()
    return numeric.describe(percentiles=[0.01, 0.25, 0.5, 0.75, 0.99]).T


def text_column_summary(
    frame: pd.DataFrame, spec: DatasetSpec, column: str
) -> dict[str, Any]:
    """Measure length, emptiness, and representative content for a text column."""
    series = frame[column].astype("string").fillna("")
    lengths = series.str.len().astype("int64")
    stripped_lengths = series.str.strip().str.len().astype("int64")

    empty_mask = stripped_lengths.eq(0)
    threshold = spec.short_text_thresholds.get(column)
    short_mask = pd.Series(False, index=frame.index)
    if threshold is not None:
        short_mask = lengths.lt(threshold) & ~empty_mask

    stats = {
        "count": int(lengths.count()),
        "min": int(lengths.min()) if len(lengths) else 0,
        "max": int(lengths.max()) if len(lengths) else 0,
        "mean": float(lengths.mean()) if len(lengths) else 0.0,
        "median": float(lengths.median()) if len(lengths) else 0.0,
        "std": float(lengths.std()) if len(lengths) > 1 else 0.0,
        "p01": float(lengths.quantile(0.01)) if len(lengths) else 0.0,
        "p05": float(lengths.quantile(0.05)) if len(lengths) else 0.0,
        "p95": float(lengths.quantile(0.95)) if len(lengths) else 0.0,
        "p99": float(lengths.quantile(0.99)) if len(lengths) else 0.0,
    }

    non_ascii_mask = series.str.contains(r"[^\x00-\x7F]", regex=True, na=False)
    multi_space_mask = series.str.contains(r"  +", regex=True, na=False)
    markup_mask = series.str.contains(r"<[^>]+>", regex=True, na=False)

    id_lookup = None
    label_lookup = None
    if spec.id_column and spec.id_column in frame.columns:
        id_lookup = frame[spec.id_column]
    if spec.label_column and spec.label_column in frame.columns:
        label_lookup = frame[spec.label_column]

    empty_ids: list[Any] = []
    if empty_mask.any():
        empty_indices = frame.index[empty_mask][:10]
        for idx in empty_indices:
            empty_ids.append(id_lookup.loc[idx] if id_lookup is not None else idx)

    shortest_rows: list[dict[str, Any]] = []
    shortest_indices = lengths[~empty_mask].nsmallest(5).index
    for idx in shortest_indices:
        shortest_rows.append(
            {
                "id": id_lookup.loc[idx] if id_lookup is not None else idx,
                "label": label_lookup.loc[idx] if label_lookup is not None else "",
                "length": int(lengths.loc[idx]),
                "preview": preview_text(series.loc[idx] if series.loc[idx] else "", 140),
            }
        )

    # Representative samples near the median length, not the longest documents.
    p40 = lengths.quantile(0.40)
    p60 = lengths.quantile(0.60)
    representative_indices = lengths[
        (lengths >= p40) & (lengths <= p60) & ~empty_mask
    ].head(2).index
    representative_rows: list[dict[str, Any]] = []
    for idx in representative_indices:
        representative_rows.append(
            {
                "id": id_lookup.loc[idx] if id_lookup is not None else idx,
                "label": label_lookup.loc[idx] if label_lookup is not None else "",
                "length": int(lengths.loc[idx]),
                "preview": preview_text(series.loc[idx], 250),
            }
        )

    return {
        "column": column,
        "stats": stats,
        "empty_count": int(empty_mask.sum()),
        "empty_ids": empty_ids,
        "short_count": int(short_mask.sum()),
        "short_threshold": threshold,
        "non_ascii_pct": percent(int(non_ascii_mask.sum()), len(series)),
        "multi_space_pct": percent(int(multi_space_mask.sum()), len(series)),
        "markup_pct": percent(int(markup_mask.sum()), len(series)),
        "shortest_rows": shortest_rows,
        "representative_rows": representative_rows,
    }


def duplicate_summary(frame: pd.DataFrame, spec: DatasetSpec) -> dict[str, Any]:
    """Measure exact duplicate rows and duplicate values in key columns."""
    full_row_duplicates = int(frame.duplicated().sum())
    text_duplicates: dict[str, int] = {}
    duplicate_ids: dict[str, list[Any]] = {}

    for column in spec.text_columns:
        duplicated_mask = frame.duplicated(subset=[column], keep=False) & frame[column].notna()
        text_duplicates[column] = int(frame.duplicated(subset=[column]).sum())
        # Listing row identifiers is only useful for a small number of duplicates.
        # Large repeat counts (for example the skills dictionary) are summarised
        # in the report instead of printing an arbitrary list of row indices.
        if duplicated_mask.any() and text_duplicates[column] <= 20:
            if spec.id_column and spec.id_column in frame.columns:
                ids = frame.loc[duplicated_mask, spec.id_column].astype(str).tolist()
            else:
                ids = frame.index[duplicated_mask].astype(str).tolist()
            duplicate_ids[column] = ids[:10]

    return {
        "full_row_duplicates": full_row_duplicates,
        "text_duplicates": text_duplicates,
        "duplicate_ids": duplicate_ids,
    }


def categorical_summary(
    frame: pd.DataFrame, spec: DatasetSpec
) -> dict[str, pd.DataFrame]:
    """Compute value counts for the configured categorical columns."""
    summaries: dict[str, pd.DataFrame] = {}
    for column in spec.categorical_columns:
        if column not in frame.columns:
            continue
        counts = (
            frame[column]
            .astype("string")
            .fillna("<missing>")
            .value_counts(dropna=False)
            .rename_axis(column)
            .reset_index(name="count")
        )
        counts["percentage"] = counts["count"].apply(
            lambda value: percent(int(value), len(frame))
        )
        summaries[column] = counts
    return summaries


def analyse_dataset(spec: DatasetSpec) -> dict[str, Any]:
    """Load and profile one dataset, returning all measured artefacts."""
    path = resolve_dataset_path(spec)
    fingerprint_before = file_fingerprint(path)
    frame, encoding = load_dataset(spec)

    LOGGER.info("Profiling %s", spec.name)
    result: dict[str, Any] = {
        "spec": spec,
        "path": path,
        "frame": frame,
        "encoding": encoding,
        "fingerprint_before": fingerprint_before,
        "dimensions": {
            "rows": int(frame.shape[0]),
            "columns": int(frame.shape[1]),
            "memory_bytes": int(frame.memory_usage(deep=True).sum()),
        },
        "column_profile": column_profile(frame, spec),
        "numeric_summary": numeric_summary(frame),
        "text_summaries": [
            text_column_summary(frame, spec, column)
            for column in spec.text_columns
            if column in frame.columns
        ],
        "duplicates": duplicate_summary(frame, spec),
        "categorical_summaries": categorical_summary(frame, spec),
        "samples": frame.head(5).copy(),
    }
    LOGGER.info(
        "Profiled %s: %d rows, %d columns, %d full-row duplicates",
        spec.name,
        result["dimensions"]["rows"],
        result["dimensions"]["columns"],
        result["duplicates"]["full_row_duplicates"],
    )
    return result


# ---------------------------------------------------------------------------
# Visualisations
# ---------------------------------------------------------------------------


def save_figure(fig: plt.Figure, path: Path) -> None:
    """Save a Matplotlib figure and close it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    LOGGER.info("Saved figure: %s", path)


def plot_missing_values(results: list[dict[str, Any]], path: Path) -> None:
    """Plot a heatmap and totals of missing cells for every dataset column."""
    dataset_names = [result["spec"].name for result in results]
    row_labels: list[str] = []
    matrix_values: list[list[float]] = []

    for result in results:
        for column in result["frame"].columns:
            row_labels.append(f"{result['spec'].name} :: {column}")
            row = []
            for other in results:
                if column in other["frame"].columns:
                    row.append(float(other["frame"][column].isna().sum()))
                else:
                    row.append(np.nan)
            matrix_values.append(row)

    matrix = pd.DataFrame(
        matrix_values,
        index=row_labels,
        columns=dataset_names,
    )

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(14, max(5, 0.42 * len(row_labels) + 2)),
        gridspec_kw={"width_ratios": [3.5, 1]},
    )

    sns.heatmap(
        matrix,
        ax=axes[0],
        annot=True,
        fmt=".0f",
        cmap="Blues",
        cbar_kws={"label": "Missing cells"},
        mask=matrix.isna(),
        linewidths=0.4,
        linecolor="white",
    )
    axes[0].set_title("Missing cells per column (measured; 0 = no missing values)")
    axes[0].set_xlabel("Dataset")
    axes[0].set_ylabel("Dataset :: Column")
    axes[0].tick_params(axis="y", labelsize=8)

    totals = [int(result["frame"].isna().sum().sum()) for result in results]
    total_frame = pd.DataFrame({"Dataset": dataset_names, "Missing cells": totals})
    sns.barplot(
        data=total_frame,
        x="Dataset",
        y="Missing cells",
        hue="Dataset",
        ax=axes[1],
        palette="Blues_d",
        legend=False,
    )
    axes[1].set_title("Total missing cells per dataset")
    axes[1].set_xlabel("")
    axes[1].set_ylabel("Missing cells")
    axes[1].tick_params(axis="x", rotation=30, labelsize=8)
    for index, value in enumerate(totals):
        axes[1].text(index, value, f" {value}", ha="center", va="bottom", fontsize=9)
    axes[1].set_ylim(0, max(1, max(totals) * 1.2 if totals else 1))

    fig.suptitle("Missing-value overview", fontsize=14, fontweight="bold")
    fig.tight_layout()
    save_figure(fig, path)


def plot_dimensions(results: list[dict[str, Any]], path: Path) -> None:
    """Compare dataset row and column counts."""
    frame = pd.DataFrame(
        {
            "Dataset": [result["spec"].name for result in results],
            "Rows": [result["dimensions"]["rows"] for result in results],
            "Columns": [result["dimensions"]["columns"] for result in results],
        }
    )

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    sns.barplot(
        data=frame,
        x="Dataset",
        y="Rows",
        hue="Dataset",
        ax=axes[0],
        palette="viridis",
        legend=False,
    )
    axes[0].set_title("Number of rows per dataset")
    axes[0].set_xlabel("")
    axes[0].set_ylabel("Rows")
    axes[0].tick_params(axis="x", rotation=20)
    for index, value in enumerate(frame["Rows"]):
        axes[0].text(index, value, f" {value:,}", ha="center", va="bottom", fontsize=9)

    sns.barplot(
        data=frame,
        x="Dataset",
        y="Columns",
        hue="Dataset",
        ax=axes[1],
        palette="mako",
        legend=False,
    )
    axes[1].set_title("Number of columns per dataset")
    axes[1].set_xlabel("")
    axes[1].set_ylabel("Columns")
    axes[1].tick_params(axis="x", rotation=20)
    for index, value in enumerate(frame["Columns"]):
        axes[1].text(index, value, f" {value}", ha="center", va="bottom", fontsize=9)

    fig.suptitle("Dataset dimensions comparison", fontsize=14, fontweight="bold")
    fig.tight_layout()
    save_figure(fig, path)


def plot_numeric_distributions(results: list[dict[str, Any]], path: Path) -> None:
    """Plot distributions for numeric columns found in any dataset."""
    numeric_items: list[tuple[str, str, pd.Series]] = []
    for result in results:
        numeric_frame = result["frame"].select_dtypes(include=[np.number])
        for column in numeric_frame.columns:
            numeric_items.append(
                (result["spec"].name, column, numeric_frame[column].dropna())
            )

    if not numeric_items:
        fig, ax = plt.subplots(figsize=(9, 3))
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            "Measured: no numeric columns were found in any dataset.",
            ha="center",
            va="center",
            fontsize=12,
        )
        ax.set_title("Numeric distributions")
        save_figure(fig, path)
        return

    fig, axes = plt.subplots(
        len(numeric_items),
        2,
        figsize=(13, max(4, 3.4 * len(numeric_items))),
        squeeze=False,
    )

    for row_index, (dataset_name, column, series) in enumerate(numeric_items):
        hist_ax = axes[row_index, 0]
        box_ax = axes[row_index, 1]

        sns.histplot(series, bins=40, kde=True, ax=hist_ax, color="#4C72B0")
        hist_ax.set_title(f"{dataset_name} :: {column} distribution")
        hist_ax.set_xlabel(column)
        hist_ax.set_ylabel("Frequency")

        sns.boxplot(x=series, ax=box_ax, color="#55A868")
        box_ax.set_title(f"{dataset_name} :: {column} boxplot")
        box_ax.set_xlabel(column)

        stats_text = (
            f"min={series.min():.2f} | median={series.median():.2f} | "
            f"mean={series.mean():.2f} | max={series.max():.2f}"
        )
        hist_ax.text(
            0.01,
            0.98,
            stats_text,
            transform=hist_ax.transAxes,
            va="top",
            fontsize=8,
            bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
        )

    fig.suptitle(
        "Numerical distributions (identifiers included; interpret with care)",
        fontsize=14,
        fontweight="bold",
    )
    fig.tight_layout()
    save_figure(fig, path)


def plot_text_length_distribution(
    result: dict[str, Any],
    summary: dict[str, Any],
    path: Path,
) -> None:
    """Plot a log-scaled text-length histogram and boxplot for one column."""
    frame = result["frame"]
    column = summary["column"]
    lengths = frame[column].astype("string").fillna("").str.len().astype("int64")
    plot_lengths = lengths[lengths > 0]

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(11, 7),
        gridspec_kw={"height_ratios": [3, 1]},
        sharex=False,
    )

    if len(plot_lengths) > 0:
        sns.histplot(plot_lengths, bins=60, ax=axes[0], color="#4C72B0", log_scale=(True, False))
        mean_value = summary["stats"]["mean"]
        median_value = summary["stats"]["median"]
        axes[0].axvline(mean_value, color="#C44E52", linestyle="--", linewidth=1.5, label=f"mean={mean_value:.0f}")
        axes[0].axvline(median_value, color="#55A868", linestyle="-.", linewidth=1.5, label=f"median={median_value:.0f}")
        axes[0].legend(loc="upper right")
        axes[0].set_xscale("log")
        axes[0].set_xlabel(f"{column} length (characters, log scale)")
        axes[0].set_ylabel("Number of records")
        axes[0].set_title(
            f"{result['spec'].name} :: {column} length distribution "
            f"(min={summary['stats']['min']}, max={summary['stats']['max']})"
        )
        sns.boxplot(x=plot_lengths, ax=axes[1], color="#55A868")
        axes[1].set_xscale("log")
        axes[1].set_xlabel(f"{column} length (characters, log scale)")
    else:
        for axis in axes:
            axis.axis("off")
        axes[0].text(0.5, 0.5, "No non-empty text values found.", ha="center", va="center")

    fig.tight_layout()
    save_figure(fig, path)


def plot_category_distributions(
    resume_result: dict[str, Any],
    job_result: dict[str, Any],
    path: Path,
) -> None:
    """Plot the label distributions of the résumé and job datasets."""
    fig, axes = plt.subplots(1, 2, figsize=(16, 9))

    for axis, result, column, palette in (
        (axes[0], resume_result, "Category", "crest"),
        (axes[1], job_result, "label", "flare"),
    ):
        counts = (
            result["frame"][column]
            .astype("string")
            .value_counts(dropna=False)
            .sort_values(ascending=True)
        )
        sns.barplot(
            x=counts.to_numpy(),
            y=counts.index.to_numpy(),
            hue=counts.index.to_numpy(),
            ax=axis,
            palette=palette,
            legend=False,
        )
        axis.set_title(f"{result['spec'].name} :: {column} distribution")
        axis.set_xlabel("Number of records")
        axis.set_ylabel(column)
        axis.tick_params(axis="y", labelsize=8)
        max_value = int(counts.max()) if len(counts) else 0
        axis.set_xlim(0, max_value * 1.18 if max_value else 1)
        for position, value in enumerate(counts.to_numpy()):
            axis.text(value, position, f" {int(value):,}", va="center", fontsize=8)

    fig.suptitle("Categorical label distributions", fontsize=15, fontweight="bold")
    fig.tight_layout()
    save_figure(fig, path)


def plot_software_skills_distributions(
    skills_result: dict[str, Any], path: Path
) -> None:
    """Plot flag counts and top skill categories/occupations for the skills data."""
    frame = skills_result["frame"]
    fig, axes = plt.subplots(2, 2, figsize=(16, 11))

    for axis, column, palette in (
        (axes[0, 0], "Hot Technology", "Set2"),
        (axes[0, 1], "In Demand", "Set2"),
    ):
        counts = frame[column].astype("string").value_counts(dropna=False)
        sns.barplot(
            x=counts.index.to_numpy(),
            y=counts.to_numpy(),
            hue=counts.index.to_numpy(),
            ax=axis,
            palette=palette,
            legend=False,
        )
        axis.set_title(f"{column} counts")
        axis.set_xlabel(column)
        axis.set_ylabel("Number of skills")
        for position, value in enumerate(counts.to_numpy()):
            axis.text(position, value, f" {int(value):,}", ha="center", va="bottom", fontsize=9)

    for axis, column, palette in (
        (axes[1, 0], "Element Name", "viridis"),
        (axes[1, 1], "Title", "magma"),
    ):
        counts = (
            frame[column]
            .astype("string")
            .value_counts()
            .head(15)
            .sort_values(ascending=True)
        )
        sns.barplot(
            x=counts.to_numpy(),
            y=counts.index.to_numpy(),
            hue=counts.index.to_numpy(),
            ax=axis,
            palette=palette,
            legend=False,
        )
        axis.set_title(f"Top 15 {column} values")
        axis.set_xlabel("Number of skills")
        axis.set_ylabel(column)
        axis.tick_params(axis="y", labelsize=8)
        for position, value in enumerate(counts.to_numpy()):
            axis.text(value, position, f" {int(value):,}", va="center", fontsize=8)

    fig.suptitle(
        "Software skills dataset distributions",
        fontsize=15,
        fontweight="bold",
    )
    fig.tight_layout()
    save_figure(fig, path)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def build_report(
    results: list[dict[str, Any]],
    figures: list[dict[str, str]],
    integrity: list[dict[str, Any]],
    errors: list[str],
    run_metadata: dict[str, str],
) -> str:
    """Build the complete Markdown report from measured analysis results."""
    result_by_name = {result["spec"].name: result for result in results}
    resume_result = result_by_name.get("Resume.csv")
    job_result = result_by_name.get("job_roles_combined_ready_10461.csv")

    lines: list[str] = []
    lines.append("# JobMatch AI - Dataset Overview Report (Phase 2.2 EDA)")
    lines.append("")
    lines.append(f"**Generated:** {run_metadata['timestamp']}")
    lines.append("")
    lines.append(
        "This report contains **measured results** from the three raw datasets plus "
        "clearly labelled **interpretations and assumptions**. No data cleaning, "
        "filtering, transformation, or model training was performed, and the original "
        "CSV files were not modified."
    )
    lines.append("")

    # 1. Executive summary -------------------------------------------------
    total_rows = sum(result["dimensions"]["rows"] for result in results)
    total_columns = sum(result["dimensions"]["columns"] for result in results)
    total_missing = sum(int(result["frame"].isna().sum().sum()) for result in results)
    total_full_duplicates = sum(
        result["duplicates"]["full_row_duplicates"] for result in results
    )

    lines.append("## 1. Executive summary")
    lines.append("")
    lines.append(
        f"- **Measured:** {len(results)} of {len(DATASETS)} datasets were loaded "
        f"successfully, containing {total_rows:,} rows and {total_columns:,} columns "
        "in total (column counts summed across datasets)."
    )
    lines.append(
        f"- **Measured:** {total_missing:,} missing cells and "
        f"{total_full_duplicates:,} exact full-row duplicates were found across all "
        "three datasets."
    )
    if resume_result is not None:
        primary = next(
            (
                item
                for item in resume_result["text_summaries"]
                if item["column"] == resume_result["spec"].primary_text_column
            ),
            None,
        )
        lines.append(
            f"- **Measured:** `Resume.csv` contains "
            f"{resume_result['dimensions']['rows']:,} résumés and "
            f"{resume_result['frame']['Category'].nunique():,} occupation categories."
            + (
                f" The primary text column has a median length of "
                f"{primary['stats']['median']:.0f} characters, "
                f"{primary['empty_count']} empty record(s), and "
                f"{primary['short_count']} record(s) below the "
                f"{primary['short_threshold']}-character review threshold."
                if primary
                else ""
            )
        )
    if job_result is not None:
        label_counts = job_result["categorical_summaries"].get("label")
        if label_counts is not None and not label_counts.empty:
            dominant = label_counts.iloc[0]
            lines.append(
                f"- **Measured:** `job_roles_combined_ready_10461.csv` is strongly "
                f"imbalanced: `{dominant['label']}` accounts for "
                f"{int(dominant['count']):,} rows "
                f"({dominant['percentage']:.1f}% of the dataset)."
            )
    skills_result = result_by_name.get("software_skills_15.csv")
    if skills_result is not None:
        lines.append(
            f"- **Measured:** `software_skills_15.csv` is a reference/dictionary "
            f"table with {skills_result['dimensions']['rows']:,} skill rows covering "
            f"{skills_result['frame']['Title'].nunique():,} occupation titles and "
            f"{skills_result['frame']['Workplace Example'].nunique():,} unique "
            "workplace examples; it is not a set of job postings."
        )
    lines.append(
        "- **Interpretation:** The datasets are loadable and structurally clean "
        "(no missing cells), but they require targeted cleaning and imbalance-aware "
        "modelling in later phases."
    )
    lines.append("")

    # 2. Dataset descriptions ---------------------------------------------
    lines.append("## 2. Dataset descriptions")
    lines.append("")
    for result in results:
        spec = result["spec"]
        lines.append(f"### {spec.name}")
        lines.append("")
        lines.append(f"- **Path:** `{result['path'].relative_to(PROJECT_ROOT).as_posix()}`")
        lines.append(f"- **Description:** {spec.description}")
        lines.append(f"- **Source notes:** {spec.source_notes}")
        lines.append(
            f"- **Measured dimensions:** {result['dimensions']['rows']:,} rows x "
            f"{result['dimensions']['columns']:,} columns"
        )
        lines.append(
            f"- **In-memory size (deep):** "
            f"{human_size(result['dimensions']['memory_bytes'])}"
        )
        lines.append(f"- **Detected encoding:** `{result['encoding']}`")
        lines.append("")

    # 3. Dimensions and integrity -----------------------------------------
    lines.append("## 3. Dimensions and file integrity")
    lines.append("")
    rows = []
    for result in results:
        fingerprint = result["fingerprint_before"]
        rows.append(
            [
                result["spec"].name,
                f"{result['dimensions']['rows']:,}",
                f"{result['dimensions']['columns']:,}",
                fingerprint["size_human"],
                f"`{fingerprint['sha256'][:16]}...`",
            ]
        )
    lines.append(
        markdown_table(
            ["Dataset", "Rows", "Columns", "File size", "SHA-256 prefix"],
            rows,
        )
    )
    lines.append("")
    lines.append(
        "**Measured:** the SHA-256 fingerprints recorded before and after the "
        "analysis are compared in Section 15. A match confirms that the raw files "
        "were not modified."
    )
    lines.append("")

    # 4. Column profiles ---------------------------------------------------
    lines.append("## 4. Column descriptions and data types")
    lines.append("")
    for result in results:
        lines.append(f"### {result['spec'].name}")
        lines.append("")
        profile = result["column_profile"]
        rows = [
            [
                row["Column"],
                row["Data type"],
                f"{int(row['Non-null count']):,}",
                f"{int(row['Missing count']):,}",
                f"{row['Missing %']:.2f}%",
                f"{int(row['Unique values']):,}",
                md_cell(row["Description (assumed)"], 90),
            ]
            for _, row in profile.iterrows()
        ]
        lines.append(
            markdown_table(
                [
                    "Column",
                    "Data type",
                    "Non-null",
                    "Missing",
                    "Missing %",
                    "Unique",
                    "Description (assumed)",
                ],
                rows,
            )
        )
        lines.append("")

    # 5. Missing-value analysis -------------------------------------------
    lines.append("## 5. Missing-value analysis")
    lines.append("")
    overall_rows = []
    for result in results:
        total_cells = result["dimensions"]["rows"] * result["dimensions"]["columns"]
        missing_cells = int(result["frame"].isna().sum().sum())
        overall_rows.append(
            [
                result["spec"].name,
                f"{total_cells:,}",
                f"{missing_cells:,}",
                f"{percent(missing_cells, total_cells):.2f}%",
            ]
        )
    lines.append(
        markdown_table(
            ["Dataset", "Total cells", "Missing cells", "Missing %"],
            overall_rows,
        )
    )
    lines.append("")
    for result in results:
        missing_cells = int(result["frame"].isna().sum().sum())
        if missing_cells == 0:
            lines.append(
                f"- **Measured:** `{result['spec'].name}` has **0 missing cells** "
                f"across {result['dimensions']['columns']} column(s)."
            )
        else:
            lines.append(
                f"- **Measured:** `{result['spec'].name}` has {missing_cells:,} "
                "missing cells (see column profile above)."
            )
    lines.append("")
    lines.append(
        "> **Assumption:** A whitespace-only text value is counted as present here "
        "because pandas does not treat it as `NaN`. Whitespace-only records are "
        "reported separately in the text analysis."
    )
    lines.append("")

    # 6. Duplicate analysis ------------------------------------------------
    lines.append("## 6. Duplicate analysis")
    lines.append("")
    duplicate_rows = []
    for result in results:
        duplicates = result["duplicates"]
        text_column, text_count = next(
            iter(duplicates["text_duplicates"].items()), ("-", 0)
        )
        duplicate_rows.append(
            [
                result["spec"].name,
                f"{duplicates['full_row_duplicates']:,}",
                f"`{text_column}`: {text_count:,}",
            ]
        )
    lines.append(
        markdown_table(
            ["Dataset", "Exact full-row duplicates", "Duplicate primary text values"],
            duplicate_rows,
        )
    )
    lines.append("")
    for result in results:
        duplicates = result["duplicates"]
        for column, ids in duplicates["duplicate_ids"].items():
            if ids:
                lines.append(
                    f"- **Measured:** `{result['spec'].name}` has duplicate values in "
                    f"`{column}`. Row identifiers for the duplicated group(s): "
                    f"{', '.join(str(item) for item in ids[:10])}."
                )
    if skills_result is not None:
        skill_duplicate_count = skills_result["duplicates"]["text_duplicates"].get(
            "Workplace Example", 0
        )
        if skill_duplicate_count > 20:
            example_counts = skills_result["frame"]["Workplace Example"].value_counts()
            top_example = example_counts.index[0] if not example_counts.empty else ""
            top_example_count = int(example_counts.iloc[0]) if not example_counts.empty else 0
            lines.append(
                f"- **Measured:** the {skill_duplicate_count:,} repeated "
                f"`Workplace Example` values in `software_skills_15.csv` are expected "
                "because the same technology can belong to multiple occupations. This "
                "is a many-to-many mapping, not duplicated rows. The most frequent "
                f"value is `{top_example}` ({top_example_count:,} rows)."
            )
    if all(
        result["duplicates"]["full_row_duplicates"] == 0 for result in results
    ):
        lines.append(
            "- **Measured:** no exact duplicate full rows were found in any dataset."
        )
    lines.append("")

    # 7. Text analysis -----------------------------------------------------
    lines.append("## 7. Text analysis")
    lines.append("")
    lines.append(
        "Text lengths are measured in **characters**, including whitespace and any "
        "HTML markup. Empty values are whitespace-only records. The 'short' threshold "
        "is a heuristic review flag, not a data-cleaning decision."
    )
    lines.append("")
    for result in results:
        if not result["text_summaries"]:
            continue
        lines.append(f"### {result['spec'].name}")
        lines.append("")
        stats_rows = []
        for item in result["text_summaries"]:
            stats = item["stats"]
            stats_rows.append(
                [
                    f"`{item['column']}`",
                    f"{stats['min']:,}",
                    f"{stats['median']:.0f}",
                    f"{stats['mean']:.0f}",
                    f"{stats['max']:,}",
                    f"{stats['p01']:.0f}",
                    f"{stats['p99']:.0f}",
                    f"{item['empty_count']:,}",
                    (
                        f"{item['short_count']:,} "
                        f"(< {item['short_threshold']})"
                        if item["short_threshold"] is not None
                        else "n/a"
                    ),
                ]
            )
        lines.append(
            markdown_table(
                [
                    "Column",
                    "Min",
                    "Median",
                    "Mean",
                    "Max",
                    "P01",
                    "P99",
                    "Empty",
                    "Short records",
                ],
                stats_rows,
            )
        )
        lines.append("")

        has_id = result["spec"].id_column is not None
        has_label = result["spec"].label_column is not None
        id_header = "Identifier" if has_id else "Row index"
        preview_headers = [id_header]
        if has_label:
            preview_headers.append("Label")
        preview_headers.extend(["Length", "Preview"])

        for item in result["text_summaries"]:
            lines.append(
                f"**`{item['column']}` content signals (measured):** "
                f"{item['non_ascii_pct']:.1f}% of records contain non-ASCII "
                f"characters; {item['multi_space_pct']:.1f}% contain runs of two or "
                f"more spaces; {item['markup_pct']:.1f}% contain HTML-like tags."
            )
            if item["empty_ids"]:
                lines.append("")
                lines.append(
                    "Whitespace-only record identifier(s): "
                    + ", ".join(f"`{value}`" for value in item["empty_ids"])
                    + "."
                )
            if item["shortest_rows"]:
                lines.append("")
                lines.append("Shortest non-empty records:")
                lines.append("")
                shortest_table_rows = []
                for row in item["shortest_rows"]:
                    cells = [md_cell(row["id"], 30)]
                    if has_label:
                        cells.append(md_cell(row["label"], 30))
                    cells.extend([f"{row['length']:,}", md_cell(row["preview"], 140)])
                    shortest_table_rows.append(cells)
                lines.append(markdown_table(preview_headers, shortest_table_rows))
            if item["representative_rows"]:
                lines.append("")
                lines.append("Representative records near the median length:")
                lines.append("")
                representative_headers = list(preview_headers)
                representative_headers[-1] = "Preview (truncated)"
                representative_table_rows = []
                for row in item["representative_rows"]:
                    cells = [md_cell(row["id"], 30)]
                    if has_label:
                        cells.append(md_cell(row["label"], 30))
                    cells.extend([f"{row['length']:,}", md_cell(row["preview"], 250)])
                    representative_table_rows.append(cells)
                lines.append(
                    markdown_table(representative_headers, representative_table_rows)
                )
            lines.append("")

    # 8. Categorical and unique distributions -----------------------------
    lines.append("## 8. Categorical and unique-value distributions")
    lines.append("")
    for result in results:
        if not result["categorical_summaries"]:
            continue
        lines.append(f"### {result['spec'].name}")
        lines.append("")
        for column, counts in result["categorical_summaries"].items():
            total_unique = len(counts)
            display = counts if total_unique <= 50 else counts.head(10)
            lines.append(
                f"**`{column}`** - {total_unique:,} unique value(s), "
                f"{len(counts):,} group(s) in the value-count table."
            )
            lines.append("")
            rows = [
                [
                    md_cell(row[column], 80),
                    f"{int(row['count']):,}",
                    f"{row['percentage']:.2f}%",
                ]
                for _, row in display.iterrows()
            ]
            lines.append(markdown_table(["Value", "Count", "Share"], rows))
            if total_unique > 50:
                lines.append("")
                lines.append(
                    f"> Showing the top 10 of {total_unique:,} values. The complete "
                    "unique count is shown in the column profile."
                )
            lines.append("")

    # 9. Numerical statistics ---------------------------------------------
    lines.append("## 9. Numerical descriptive statistics")
    lines.append("")
    numeric_found = False
    for result in results:
        numeric = result["numeric_summary"]
        if numeric.empty:
            continue
        numeric_found = True
        lines.append(f"### {result['spec'].name}")
        lines.append("")
        rows = []
        for index, row in numeric.iterrows():
            rows.append(
                [
                    f"`{index}`",
                    f"{row['count']:.0f}",
                    f"{row['mean']:.2f}",
                    f"{row['std']:.2f}",
                    f"{row['min']:.2f}",
                    f"{row['25%']:.2f}",
                    f"{row['50%']:.2f}",
                    f"{row['75%']:.2f}",
                    f"{row['max']:.2f}",
                ]
            )
        lines.append(
            markdown_table(
                [
                    "Column",
                    "Count",
                    "Mean",
                    "Std",
                    "Min",
                    "25%",
                    "50%",
                    "75%",
                    "Max",
                ],
                rows,
            )
        )
        lines.append("")
    if not numeric_found:
        lines.append("**Measured:** no numeric columns were found in any dataset.")
        lines.append("")
    else:
        lines.append(
            "> **Interpretation:** `Resume.csv::ID` is an identifier, not a "
            "meaningful numeric feature. Descriptive statistics for identifiers "
            "should not be used for modelling decisions."
        )
        lines.append("")

    # 10. Visualisations ---------------------------------------------------
    lines.append("## 10. Visualisations")
    lines.append("")
    lines.append(
        "All figures are saved in `ai-engine/data_analysis/outputs/figures/`."
    )
    lines.append("")
    for figure in figures:
        lines.append(f"### {figure['title']}")
        lines.append("")
        lines.append(f"![{figure['title']}](figures/{figure['filename']})")
        lines.append("")

    # 11. Important observations ------------------------------------------
    lines.append("## 11. Important observations (measured)")
    lines.append("")
    observations: list[str] = []
    for result in results:
        name = result["spec"].name
        observations.append(
            f"- `{name}`: {result['dimensions']['rows']:,} rows x "
            f"{result['dimensions']['columns']:,} columns; "
            f"{int(result['frame'].isna().sum().sum()):,} missing cells; "
            f"{result['duplicates']['full_row_duplicates']:,} exact full-row duplicates."
        )
        for item in result["text_summaries"]:
            observations.append(
                f"- `{name}::{item['column']}`: length min/median/mean/max = "
                f"{item['stats']['min']:,} / {item['stats']['median']:.0f} / "
                f"{item['stats']['mean']:.0f} / {item['stats']['max']:,} characters; "
                f"{item['empty_count']} empty and {item['short_count']} short "
                "record(s)."
            )
    if resume_result is not None:
        category_counts = resume_result["categorical_summaries"].get("Category")
        if category_counts is not None and not category_counts.empty:
            observations.append(
                f"- `Resume.csv::Category` has {len(category_counts):,} classes; the "
                f"largest is `{category_counts.iloc[0]['Category']}` with "
                f"{int(category_counts.iloc[0]['count']):,} rows "
                f"({category_counts.iloc[0]['percentage']:.1f}%), and the smallest is "
                f"`{category_counts.iloc[-1]['Category']}` with "
                f"{int(category_counts.iloc[-1]['count']):,} rows "
                f"({category_counts.iloc[-1]['percentage']:.1f}%)."
            )
    if job_result is not None:
        label_counts = job_result["categorical_summaries"].get("label")
        if label_counts is not None and not label_counts.empty:
            observations.append(
                f"- `job_roles_combined_ready_10461.csv::label` has "
                f"{len(label_counts):,} classes; the largest is "
                f"`{label_counts.iloc[0]['label']}` with "
                f"{int(label_counts.iloc[0]['count']):,} rows "
                f"({label_counts.iloc[0]['percentage']:.1f}%), and the smallest is "
                f"`{label_counts.iloc[-1]['label']}` with "
                f"{int(label_counts.iloc[-1]['count']):,} rows "
                f"({label_counts.iloc[-1]['percentage']:.1f}%)."
            )
    if skills_result is not None:
        skills_frame = skills_result["frame"]
        observations.append(
            f"- `software_skills_15.csv`: {skills_frame['Hot Technology'].eq('Y').sum():,} "
            f"rows are flagged Hot Technology ({percent(int(skills_frame['Hot Technology'].eq('Y').sum()), len(skills_frame)):.1f}%) "
            f"and {skills_frame['In Demand'].eq('Y').sum():,} rows are flagged In Demand "
            f"({percent(int(skills_frame['In Demand'].eq('Y').sum()), len(skills_frame)):.1f}%)."
        )
        observations.append(
            f"- `software_skills_15.csv::Workplace Example` has "
            f"{skills_frame['Workplace Example'].nunique():,} unique values across "
            f"{len(skills_frame):,} rows; {int(skills_frame['Workplace Example'].duplicated().sum()):,} "
            "rows repeat a previously seen workplace example because skills are "
            "mapped to multiple occupations."
        )
        very_short_mask = skills_frame["Workplace Example"].str.len().lt(3)
        short_examples = sorted(
            skills_frame.loc[very_short_mask, "Workplace Example"].unique().tolist()
        )
        if short_examples:
            observations.append(
                f"- `software_skills_15.csv::Workplace Example`: "
                f"{int(very_short_mask.sum()):,} rows contain one- or two-character "
                f"values ({', '.join(f'`{value}`' for value in short_examples)}). "
                "**Interpretation:** these look like valid programming-language names "
                "rather than truncated records."
            )
    lines.extend(observations)
    lines.append("")

    # 12. Data-quality problems -------------------------------------------
    lines.append("## 12. Potential data-quality problems")
    lines.append("")
    problems: list[str] = []
    for result in results:
        name = result["spec"].name
        for item in result["text_summaries"]:
            if item["empty_count"]:
                problems.append(
                    f"- **Measured:** `{name}::{item['column']}` contains "
                    f"{item['empty_count']} whitespace-only record(s) that provide no "
                    "content."
                )
            if item["short_count"]:
                problems.append(
                    f"- **Measured:** `{name}::{item['column']}` contains "
                    f"{item['short_count']} non-empty record(s) shorter than "
                    f"{item['short_threshold']} characters; these may be truncated or "
                    "low-information records."
                )
            if item["markup_pct"] > 0:
                problems.append(
                    f"- **Measured:** {item['markup_pct']:.1f}% of "
                    f"`{name}::{item['column']}` records contain HTML-like tags, so "
                    "plain-text extraction may be required before modelling."
                )
            if item["non_ascii_pct"] > 0:
                problems.append(
                    f"- **Measured:** {item['non_ascii_pct']:.1f}% of "
                    f"`{name}::{item['column']}` records contain non-ASCII characters "
                    "(for example typographic hyphens), which may require "
                    "normalisation."
                )
            if item["multi_space_pct"] > 0:
                problems.append(
                    f"- **Measured:** {item['multi_space_pct']:.1f}% of "
                    f"`{name}::{item['column']}` records contain runs of multiple "
                    "spaces, indicating layout artefacts from PDF/HTML conversion."
                )
        duplicates = result["duplicates"]
        if duplicates["text_duplicates"]:
            for column, count in duplicates["text_duplicates"].items():
                if count:
                    problems.append(
                        f"- **Measured:** `{name}::{column}` has {count:,} duplicate "
                        "value(s) beyond the first occurrence. This is an exact-match "
                        "count and does not detect near-duplicates."
                    )
    if job_result is not None:
        label_counts = job_result["categorical_summaries"].get("label")
        if label_counts is not None and not label_counts.empty:
            ratio = label_counts.iloc[0]["count"] / max(1, label_counts.iloc[-1]["count"])
            problems.append(
                f"- **Measured:** the job-role label distribution is highly imbalanced "
                f"(largest class is {ratio:.1f}x the smallest class)."
            )
            problems.append(
                "- **Interpretation:** without stratification or class weighting, a "
                "classifier may over-predict the dominant role."
            )
    if resume_result is not None:
        problems.append(
            "- **Interpretation:** `Resume.csv` covers 24 broad occupation "
            "categories, many outside IT (for example CHEF, AVIATION, FITNESS). "
            "Using all categories unchanged may add noise for an IT-focused job "
            "matcher."
        )
        problems.append(
            "- **Interpretation:** `Resume_html` is an HTML representation of the "
            "same résumés and should probably be treated as an alternative source "
            "rather than an independent feature, to avoid duplication."
        )
    if skills_result is not None:
        problems.append(
            "- **Interpretation:** `software_skills_15.csv` is a skill dictionary, "
            "not a job-posting corpus; it can support skill normalisation and "
            "enrichment but cannot train a job-role classifier by itself."
        )
    if not problems:
        problems.append("- No data-quality problems were detected by the measured checks.")
    lines.extend(problems)
    lines.append("")

    # 13. Assumptions and limitations -------------------------------------
    lines.append("## 13. Assumptions and limitations")
    lines.append("")
    lines.append(
        "- **Assumption:** `utf-8-sig` is the correct encoding; fallback encodings "
        "were only attempted on decode failure. The detected encoding for each "
        "dataset is reported in Section 2."
    )
    lines.append(
        "- **Assumption:** text length is measured in characters, including "
        "whitespace and HTML markup for `Resume_html`."
    )
    lines.append(
        "- **Assumption:** the 'short record' thresholds "
        "(`Resume_str` < 500, `Resume_html` < 1000, `jobdescription` < 300 "
        "characters) are heuristic review flags, not data-cleaning rules. No short "
        "threshold is applied to `Workplace Example` because one- and two-character "
        "technology names such as C, R, and J are valid."
    )
    lines.append(
        "- **Assumption:** duplicate detection uses exact string equality only; "
        "near-duplicates, whitespace-normalised duplicates, and semantic duplicates "
        "were not measured."
    )
    lines.append(
        "- **Assumption:** column descriptions are inferred from column names and "
        "dataset documentation because no formal data dictionary exists for "
        "`Resume.csv`."
    )
    lines.append(
        "- **Limitation:** this phase does not clean, normalise, deduplicate, or "
        "filter records, and does not evaluate text content quality semantically."
    )
    lines.append(
        "- **Limitation:** the software skills table has no numeric columns; its Y/N "
        "flags were analysed as categorical values."
    )
    lines.append("")

    # 14. Recommendations --------------------------------------------------
    lines.append("## 14. Recommendations for the next phase")
    lines.append("")
    lines.append(
        "1. **Handle the empty résumé:** inspect `Resume.csv` ID `12632728` and "
        "decide whether to repair, replace, or remove it during documented cleaning."
    )
    lines.append(
        "2. **Resolve duplicates:** review the two duplicate `Resume_str` groups "
        "(IDs `19147603`/`28398216` and `16850314`/`37473139`) and document a dedupe "
        "policy. Also check near-duplicates after whitespace normalisation."
    )
    lines.append(
        "3. **Normalise text:** remove/skip HTML markup for `Resume_html`, collapse "
        "repeated whitespace, normalise typographic characters, and preserve a "
        "cleaned plain-text field for modelling."
    )
    lines.append(
        "4. **Define the résumé scope:** decide whether to keep all 24 résumé "
        "categories or map/filter them to the six IT role classes used by the job "
        "dataset. Document the mapping before training."
    )
    lines.append(
        "5. **Address class imbalance:** use stratified splits, class weights, and "
        "imbalance-aware metrics (macro-F1, per-class recall) for the job-role "
        "classifier."
    )
    lines.append(
        "6. **Validate the skills dictionary:** confirm that `Workplace Example` "
        "values are suitable for skill extraction, and decide how to aggregate "
        "duplicate technologies across occupations and O*NET elements."
    )
    lines.append(
        "7. **Keep provenance:** record cleaning rules, dropped/edited row "
        "identifiers, and file hashes so that later phases remain reproducible."
    )
    lines.append(
        "8. **Proceed only after review:** do not start feature engineering or "
        "modelling until the cleaning decisions above are approved."
    )
    lines.append("")

    # 15. Validation -------------------------------------------------------
    lines.append("## 15. Validation")
    lines.append("")
    lines.append(f"- **Python:** `{run_metadata['python']}`")
    lines.append(f"- **pandas:** `{run_metadata['pandas']}`")
    lines.append(f"- **numpy:** `{run_metadata['numpy']}`")
    lines.append(f"- **matplotlib:** `{run_metadata['matplotlib']}`")
    lines.append(f"- **seaborn:** `{run_metadata['seaborn']}`")
    lines.append(f"- **Script:** `{run_metadata['script']}`")
    lines.append(f"- **Working directory at run time:** `{run_metadata['cwd']}`")
    lines.append("")

    integrity_rows = []
    for item in integrity:
        integrity_rows.append(
            [
                item["dataset"],
                item["size_before"],
                item["size_after"],
                f"`{item['sha256_before'][:16]}...`",
                "YES" if item["unchanged"] else "NO",
            ]
        )
    lines.append(
        markdown_table(
            [
                "Dataset",
                "Size before",
                "Size after",
                "SHA-256 prefix",
                "Unchanged",
            ],
            integrity_rows,
        )
    )
    lines.append("")
    all_unchanged = all(item["unchanged"] for item in integrity)
    lines.append(
        f"- **Measured:** dataset integrity check "
        f"{'PASSED' if all_unchanged else 'FAILED'} - "
        f"{'all raw files are unchanged' if all_unchanged else 'at least one file changed'}."
    )
    lines.append(f"- **Measured:** {len(results)} dataset(s) loaded and profiled.")
    lines.append(f"- **Measured:** {len(figures)} figure(s) generated.")
    if errors:
        lines.append("- **Errors encountered:**")
        for error in errors:
            lines.append(f"  - {error}")
    else:
        lines.append("- **Measured:** no errors were encountered during the run.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "*This report was generated automatically by "
        "`ai-engine/data_analysis/01_dataset_overview.py`. Measured values are "
        "reproducible by re-running the script; assumptions and interpretations are "
        "labelled explicitly.*"
    )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------


def ensure_output_directories() -> None:
    """Create output directories, failing with a clear error if impossible."""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Could not create output directories under {OUTPUT_DIR}: {exc}"
        ) from exc


def collect_run_metadata() -> dict[str, str]:
    """Collect environment versions and run context for reproducibility."""
    return {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "matplotlib": matplotlib.__version__,
        "seaborn": sns.__version__,
        "script": SCRIPT_PATH.relative_to(PROJECT_ROOT).as_posix(),
        "cwd": str(Path.cwd()),
    }


def main() -> int:
    """Run the complete EDA workflow and return a process exit code."""
    configure_logging()
    sns.set_theme(style="whitegrid", context="notebook")
    plt.rcParams["axes.titlesize"] = 11
    plt.rcParams["figure.autolayout"] = False

    LOGGER.info("Project root: %s", PROJECT_ROOT)
    LOGGER.info("Raw data directory: %s", RAW_DATA_DIR)
    LOGGER.info("Output directory: %s", OUTPUT_DIR)

    try:
        ensure_output_directories()
    except RuntimeError as exc:
        LOGGER.error("%s", exc)
        return 1

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for spec in DATASETS:
        try:
            results.append(analyse_dataset(spec))
        except Exception as exc:  # noqa: BLE001 - report and continue with other files
            message = f"{spec.name}: {type(exc).__name__}: {exc}"
            LOGGER.error("Failed to analyse %s: %s", spec.name, exc)
            errors.append(message)

    if not results:
        LOGGER.error("No datasets could be analysed; no report will be generated.")
        return 1

    # Figures -------------------------------------------------------------
    figures: list[dict[str, str]] = []
    figure_jobs: list[tuple[str, str, Any]] = [
        ("Missing-value overview", "missing_values_overview.png", plot_missing_values),
        ("Dataset dimensions comparison", "dataset_dimensions.png", plot_dimensions),
        (
            "Numerical distributions",
            "numeric_distributions.png",
            plot_numeric_distributions,
        ),
    ]

    for title, filename, plotter in figure_jobs:
        try:
            plotter(results, FIGURES_DIR / filename)
            figures.append({"title": title, "filename": filename})
        except Exception as exc:  # noqa: BLE001 - keep generating other figures
            message = f"Figure '{title}' failed: {type(exc).__name__}: {exc}"
            LOGGER.error("%s", message)
            errors.append(message)

    result_by_name = {result["spec"].name: result for result in results}
    resume_result = result_by_name.get("Resume.csv")
    job_result = result_by_name.get("job_roles_combined_ready_10461.csv")
    skills_result = result_by_name.get("software_skills_15.csv")

    if resume_result is not None:
        resume_primary = next(
            (
                item
                for item in resume_result["text_summaries"]
                if item["column"] == resume_result["spec"].primary_text_column
            ),
            None,
        )
        if resume_primary is not None:
            try:
                plot_text_length_distribution(
                    resume_result,
                    resume_primary,
                    FIGURES_DIR / "resume_text_length_distribution.png",
                )
                figures.append(
                    {
                        "title": "Résumé text-length distribution",
                        "filename": "resume_text_length_distribution.png",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                message = (
                    "Figure 'Résumé text-length distribution' failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                LOGGER.error("%s", message)
                errors.append(message)

    if job_result is not None:
        job_primary = next(
            (
                item
                for item in job_result["text_summaries"]
                if item["column"] == job_result["spec"].primary_text_column
            ),
            None,
        )
        if job_primary is not None:
            try:
                plot_text_length_distribution(
                    job_result,
                    job_primary,
                    FIGURES_DIR / "job_text_length_distribution.png",
                )
                figures.append(
                    {
                        "title": "Job description text-length distribution",
                        "filename": "job_text_length_distribution.png",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                message = (
                    "Figure 'Job description text-length distribution' failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                LOGGER.error("%s", message)
                errors.append(message)

    if resume_result is not None and job_result is not None:
        try:
            plot_category_distributions(
                resume_result,
                job_result,
                FIGURES_DIR / "category_distributions.png",
            )
            figures.append(
                {
                    "title": "Category distributions",
                    "filename": "category_distributions.png",
                }
            )
        except Exception as exc:  # noqa: BLE001
            message = (
                f"Figure 'Category distributions' failed: {type(exc).__name__}: {exc}"
            )
            LOGGER.error("%s", message)
            errors.append(message)

    if skills_result is not None:
        try:
            plot_software_skills_distributions(
                skills_result, FIGURES_DIR / "software_skills_distributions.png"
            )
            figures.append(
                {
                    "title": "Software skills distributions",
                    "filename": "software_skills_distributions.png",
                }
            )
        except Exception as exc:  # noqa: BLE001
            message = (
                "Figure 'Software skills distributions' failed: "
                f"{type(exc).__name__}: {exc}"
            )
            LOGGER.error("%s", message)
            errors.append(message)

    # Integrity check ------------------------------------------------------
    integrity: list[dict[str, Any]] = []
    for result in results:
        try:
            fingerprint_after = file_fingerprint(result["path"])
            result["fingerprint_after"] = fingerprint_after
            integrity.append(
                {
                    "dataset": result["spec"].name,
                    "size_before": result["fingerprint_before"]["size_human"],
                    "size_after": fingerprint_after["size_human"],
                    "sha256_before": result["fingerprint_before"]["sha256"],
                    "sha256_after": fingerprint_after["sha256"],
                    "unchanged": (
                        result["fingerprint_before"]["sha256"]
                        == fingerprint_after["sha256"]
                    ),
                }
            )
        except OSError as exc:
            message = (
                f"Integrity check failed for {result['spec'].name}: "
                f"{type(exc).__name__}: {exc}"
            )
            LOGGER.error("%s", message)
            errors.append(message)

    # Report ---------------------------------------------------------------
    report = build_report(
        results=results,
        figures=figures,
        integrity=integrity,
        errors=errors,
        run_metadata=collect_run_metadata(),
    )
    try:
        REPORT_PATH.write_text(report, encoding="utf-8")
        LOGGER.info("Report written: %s", REPORT_PATH)
    except OSError as exc:
        LOGGER.error("Could not write report %s: %s", REPORT_PATH, exc)
        return 1

    # Console summary ------------------------------------------------------
    print("\n" + "=" * 78)
    print("EDA SUMMARY")
    print("=" * 78)
    for result in results:
        print(
            f"- {result['spec'].name}: "
            f"{result['dimensions']['rows']:,} rows x "
            f"{result['dimensions']['columns']:,} columns | "
            f"{int(result['frame'].isna().sum().sum()):,} missing cells | "
            f"{result['duplicates']['full_row_duplicates']:,} duplicate rows"
        )
    print(f"- Figures generated: {len(figures)}")
    print(f"- Report: {REPORT_PATH}")
    print(
        "- Dataset integrity: "
        + ("PASSED (unchanged)" if integrity and all(item["unchanged"] for item in integrity) else "FAILED")
    )
    if errors:
        print(f"- Errors: {len(errors)} (see report Section 15)")
        for error in errors:
            print(f"    * {error}")
    else:
        print("- Errors: none")
    print("=" * 78)

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
