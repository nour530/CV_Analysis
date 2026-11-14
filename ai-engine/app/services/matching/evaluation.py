from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from pydantic import Field

from app.schemas.common import StrictSchema
from app.services.matching.config import MatchingConfig
from app.services.matching.local_matcher import LocalMatcher
from app.services.nlp.embeddings import LocalTfidfEmbedder

DEFAULT_EVALUATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "evaluation"
    / "hybrid_matching_reviewed_examples.csv"
)
LABEL_ORDER = ["strong_match", "moderate_match", "weak_match"]
ReviewedLabel = Literal["strong_match", "moderate_match", "weak_match"]


class ReviewedMatchCase(StrictSchema):
    case_id: str = Field(min_length=1, max_length=120)
    resume_id: str | None = Field(default=None, max_length=120)
    job_id: str | None = Field(default=None, max_length=120)
    resume_text: str = Field(min_length=1, max_length=200_000)
    job_text: str = Field(min_length=1, max_length=200_000)
    resume_category: str | None = Field(default=None, max_length=120)
    job_category: str | None = Field(default=None, max_length=120)
    actual_label: ReviewedLabel
    review_rationale: str = Field(default="", max_length=2000)
    reviewer_notes: str = Field(default="", max_length=2000)


def load_reviewed_cases(path: str | Path | None = None) -> list[ReviewedMatchCase]:
    source = Path(path) if path is not None else DEFAULT_EVALUATION_PATH
    if not source.exists():
        raise FileNotFoundError(f"Evaluation dataset not found: {source}")
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with source.open("r", encoding=encoding, newline="") as handle:
                return [
                    ReviewedMatchCase.model_validate(row)
                    for row in csv.DictReader(handle)
                ]
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RuntimeError(f"Could not decode evaluation dataset: {last_error}")


def evaluate_cases(
    cases: Iterable[ReviewedMatchCase],
    *,
    matcher: LocalMatcher | None = None,
) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

    engine = matcher or LocalMatcher()
    actual: list[str] = []
    predicted: list[str] = []
    rows: list[dict[str, Any]] = []

    for case in cases:
        result = engine.match_texts(
            case.resume_text,
            case.job_text,
            resume_id=case.resume_id or case.case_id,
            job_id=case.job_id,
            resume_category=case.resume_category,
            job_category=case.job_category,
        )
        actual.append(case.actual_label)
        predicted.append(result.classification)
        rows.append(
            {
                "case_id": case.case_id,
                "actual_label": case.actual_label,
                "predicted_label": result.classification,
                "score": result.overall_compatibility_score,
                "matched_skills": [item.skill for item in result.matched_skills],
                "missing_required_skills": [
                    item.skill for item in result.missing_required_skills
                ],
                "review_rationale": case.review_rationale,
            }
        )

    labels = [label for label in LABEL_ORDER if label in set(actual) | set(predicted)]
    report = classification_report(
        actual,
        predicted,
        labels=labels,
        output_dict=True,
        zero_division=0,
    )
    matrix = confusion_matrix(actual, predicted, labels=labels)
    scores_by_label: dict[str, list[float]] = {label: [] for label in LABEL_ORDER}
    for row in rows:
        scores_by_label[row["actual_label"]].append(row["score"])

    return {
        "sample_count": len(rows),
        "accuracy": round(float(accuracy_score(actual, predicted)), 6),
        "macro_f1": round(
            float(f1_score(actual, predicted, labels=labels, average="macro", zero_division=0)),
            6,
        ),
        "weighted_f1": round(
            float(
                f1_score(
                    actual,
                    predicted,
                    labels=labels,
                    average="weighted",
                    zero_division=0,
                )
            ),
            6,
        ),
        "labels": labels,
        "per_class": {
            label: {
                key: round(float(value), 6)
                for key, value in report.get(label, {}).items()
            }
            for label in labels
        },
        "confusion_matrix": {
            "labels": labels,
            "values": matrix.tolist(),
        },
        "mean_score_by_actual_label": {
            label: round(sum(values) / len(values), 6) if values else None
            for label, values in scores_by_label.items()
        },
        "predictions": rows,
    }


def compare_configs(
    cases: Iterable[ReviewedMatchCase],
    configs: Iterable[tuple[str, MatchingConfig]],
    *,
    embedder: LocalTfidfEmbedder | None = None,
) -> list[dict[str, Any]]:
    case_list = list(cases)
    results: list[dict[str, Any]] = []
    for name, config in configs:
        metrics = evaluate_cases(
            case_list, matcher=LocalMatcher(config, embedder=embedder)
        )
        results.append(
            {
                "config_name": name,
                "macro_f1": metrics["macro_f1"],
                "accuracy": metrics["accuracy"],
                "weighted_f1": metrics["weighted_f1"],
                "weights": config.weights.as_dict(),
                "thresholds": config.thresholds.as_dict(),
            }
        )
    return sorted(results, key=lambda item: item["macro_f1"], reverse=True)


def write_evaluation_report(metrics: Mapping[str, Any], output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    return target
