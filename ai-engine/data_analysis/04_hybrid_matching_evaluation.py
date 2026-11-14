#!/usr/bin/env python
"""Evaluate the deterministic local matching layer on reviewed examples.

This script is intentionally local and deterministic. It does not call an LLM.
It reports accuracy, macro-F1, per-class metrics, a confusion matrix, and a
comparison of candidate weight/threshold configurations.

Run from the project root:

    ai-engine/venv/Scripts/python.exe ai-engine/data_analysis/04_hybrid_matching_evaluation.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AI_ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(AI_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_DIR))

from app.services.matching.config import (  # noqa: E402
    MatchingConfig,
    MatchingWeights,
    MatchThresholds,
    RequirementImportanceWeights,
)
from app.services.matching.evaluation import (  # noqa: E402
    compare_configs,
    evaluate_cases,
    load_reviewed_cases,
    write_evaluation_report,
)
from app.services.job_catalog import JobCatalog  # noqa: E402
from app.services.matching.local_matcher import LocalMatcher  # noqa: E402

OUTPUT_DIR = AI_ENGINE_DIR / "data_analysis" / "outputs"
JOB_FEATURES_PATH = AI_ENGINE_DIR / "data" / "features" / "job_features.csv"
JSON_PATH = OUTPUT_DIR / "hybrid_matching_evaluation.json"
REPORT_PATH = OUTPUT_DIR / "hybrid_matching_evaluation.md"


def configure_logging() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def candidate_configs() -> list[tuple[str, MatchingConfig]]:
    return [
        ("default", MatchingConfig()),
        ("moderate_threshold_0_35", MatchingConfig(thresholds=MatchThresholds(strong=0.75, moderate=0.35))),
        ("moderate_threshold_0_45", MatchingConfig(thresholds=MatchThresholds(strong=0.75, moderate=0.45))),
        (
            "skill_heavy",
            MatchingConfig(
                weights=MatchingWeights(
                    required_skill_coverage=0.55,
                    preferred_skill_coverage=0.15,
                    skill_overlap=0.15,
                    semantic_similarity=0.15,
                    category_compatibility=0.0,
                )
            ),
        ),
        (
            "semantic_heavy",
            MatchingConfig(
                weights=MatchingWeights(
                    required_skill_coverage=0.35,
                    preferred_skill_coverage=0.10,
                    skill_overlap=0.10,
                    semantic_similarity=0.45,
                    category_compatibility=0.0,
                )
            ),
        ),
        (
            "balanced",
            MatchingConfig(
                weights=MatchingWeights(
                    required_skill_coverage=0.30,
                    preferred_skill_coverage=0.15,
                    skill_overlap=0.15,
                    semantic_similarity=0.20,
                    category_compatibility=0.20,
                )
            ),
        ),
    ]


def build_report(metrics: dict[str, Any], comparison: list[dict[str, Any]]) -> str:
    lines = [
        "# Hybrid Matching Evaluation",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This report uses a synthetic, curated development set. It is useful for",
        "regression checking and weight selection, not as a production benchmark.",
        "",
        "## Default configuration",
        "",
        f"- Samples: {metrics['sample_count']}",
        f"- Accuracy: {metrics['accuracy']:.4f}",
        f"- Macro-F1: {metrics['macro_f1']:.4f}",
        f"- Weighted-F1: {metrics['weighted_f1']:.4f}",
        "",
        "### Per-class metrics",
        "",
        "| Label | Precision | Recall | F1 | Support |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, values in metrics["per_class"].items():
        lines.append(
            f"| {label} | {values.get('precision', 0):.4f} | "
            f"{values.get('recall', 0):.4f} | {values.get('f1-score', 0):.4f} | "
            f"{int(values.get('support', 0))} |"
        )

    lines.extend(
        [
            "",
            "### Confusion matrix",
            "",
            "| Actual \\ Predicted | " + " | ".join(metrics["confusion_matrix"]["labels"]) + " |",
            "| --- | " + " | ".join("---:" for _ in metrics["confusion_matrix"]["labels"]) + " |",
        ]
    )
    for label, row in zip(
        metrics["confusion_matrix"]["labels"],
        metrics["confusion_matrix"]["values"],
        strict=True,
    ):
        lines.append(f"| {label} | " + " | ".join(str(value) for value in row) + " |")

    lines.extend(
        [
            "",
            "### Incorrect predictions",
            "",
            "| Case | Actual | Predicted | Score |",
            "| --- | --- | --- | ---: |",
        ]
    )
    errors = [
        row
        for row in metrics["predictions"]
        if row["actual_label"] != row["predicted_label"]
    ]
    if errors:
        for row in errors:
            lines.append(
                f"| {row['case_id']} | {row['actual_label']} | "
                f"{row['predicted_label']} | {row['score']:.4f} |"
            )
    else:
        lines.append("| None | | | |")

    lines.extend(
        [
            "",
            "## Configuration comparison",
            "",
            "| Configuration | Accuracy | Macro-F1 | Weighted-F1 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in comparison:
        lines.append(
            f"| {row['config_name']} | {row['accuracy']:.4f} | "
            f"{row['macro_f1']:.4f} | {row['weighted_f1']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- The evaluation set is small and synthetic; confidence intervals are wide.",
            "- Labels describe lexical/résumé evidence, not job performance or hiring merit.",
            "- Category compatibility is disabled because no validated cross-taxonomy",
            "  mapping is available.",
            "- Dictionary matching cannot understand every paraphrase or implicit skill.",
            "- Thresholds and weights must be revalidated on an independently labelled",
            "  production sample before deployment.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    configure_logging()
    logger = logging.getLogger("jobmatch-hybrid-evaluation")
    logger.info("Loading reviewed evaluation cases.")
    cases = load_reviewed_cases()
    # Use the same catalog-fitted TF-IDF embedder as the API so semantic
    # similarity here matches production scoring.
    logger.info("Fitting TF-IDF weights on the job catalog sample.")
    embedder = JobCatalog.from_csv(JOB_FEATURES_PATH).embedder
    metrics = evaluate_cases(
        cases, matcher=LocalMatcher(MatchingConfig(), embedder=embedder)
    )
    comparison = compare_configs(cases, candidate_configs(), embedder=embedder)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_evaluation_report(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": "synthetic_curated_development_set",
            "default_metrics": metrics,
            "config_comparison": comparison,
        },
        JSON_PATH,
    )
    REPORT_PATH.write_text(
        build_report(metrics, comparison),
        encoding="utf-8",
    )

    logger.info("Accuracy: %.4f", metrics["accuracy"])
    logger.info("Macro-F1: %.4f", metrics["macro_f1"])
    logger.info("Wrote %s", JSON_PATH)
    logger.info("Wrote %s", REPORT_PATH)
    print(json.dumps({k: metrics[k] for k in ("sample_count", "accuracy", "macro_f1")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
