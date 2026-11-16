"""Train and evaluate our CV quality scorer on Claude-graded CVs.

    python data_analysis/08_train_cv_scorer.py

Needs data/cv_quality/labels.jsonl (from 07_label_cv_quality.py). Compares two
model types with 5-fold cross-validation (each CV is scored by a model that
never saw it), keeps the better one, trains it on all labels and saves it to
data/models/cv_quality_scorer.joblib for the API.

Writes data_analysis/outputs/cv_scorer_evaluation.{json,md}.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

AI_ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(AI_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_DIR))

import numpy as np  # noqa: E402

from app.services.cv_quality.labelling import load_cvs, read_labels  # noqa: E402
from app.services.cv_quality.rubric import CRITERIA, RUBRIC_VERSION  # noqa: E402
from app.services.cv_quality.scorer import MODEL_PATH, TARGETS, CvQualityScorer, cross_validate  # noqa: E402

OUTPUT_DIR = AI_ENGINE_DIR / "data_analysis" / "outputs"
MIN_LABELS = 200


def main() -> int:
    labels = read_labels()
    if len(labels) < MIN_LABELS:
        print(f"Only {len(labels)} labelled CVs (need {MIN_LABELS}+). Run data_analysis/07_label_cv_quality.py first.")
        return 1
    cvs = [cv for cv in load_cvs() if cv.id in labels]
    texts = [cv.text for cv in cvs]
    y = np.array([[labels[cv.id]["labels"][t] for t in TARGETS] for cv in cvs], dtype=float)
    print(f"{len(cvs)} labelled CVs, rubric {RUBRIC_VERSION}")

    results = {}
    for kind in ("ridge", "boosting"):
        results[kind] = cross_validate(texts, y, kind=kind)
        mean_within = np.mean([results[kind][t]["within_tolerance"] for t in TARGETS])
        print(f"  {kind}: overall MAE {results[kind]['overall']['mae']}, mean within-tolerance {mean_within:.3f}", flush=True)
    # Lower mean error, each target scaled by its baseline error.
    best = min(results, key=lambda k: np.mean([results[k][t]["mae"] / max(results[k][t]["baseline_mae"], 1e-9) for t in TARGETS]))
    chosen = results[best]

    scorer = CvQualityScorer.train(texts, y, kind=best, metadata={"cvs": len(cvs), "trained_at": datetime.now(timezone.utc).isoformat()})
    scorer.save()
    print(f"Kept {best}; saved {MODEL_PATH.relative_to(AI_ENGINE_DIR)} ({MODEL_PATH.stat().st_size / 1e6:.1f} MB)")

    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "cvs": len(cvs), "rubric": RUBRIC_VERSION, "chosen": best, "results": results}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "cv_scorer_evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    names = {c.id: c.en for c in CRITERIA} | {"overall": "Overall (0-100)"}
    lines = [
        "# CV quality scorer evaluation",
        "",
        f"Generated: {report['generated_at']} - {len(cvs)} CVs graded by Claude (rubric {RUBRIC_VERSION}).",
        "",
        f"Model kept: **{best}** (TF-IDF wording + measured CV signals). 5-fold cross-validation:",
        "every CV is scored by a model that never saw it, then compared with Claude's grade.",
        "",
        "| Criterion | Mean error | Error if always guessing the average | Rank correlation | Within tolerance |",
        "| --- | ---: | ---: | ---: | ---: |",
        *[
            f"| {names[t]} | {chosen[t]['mae']} | {chosen[t]['baseline_mae']} | {chosen[t]['spearman']} | "
            f"{chosen[t]['within_tolerance']:.0%} (±{chosen[t]['tolerance']}) |"
            for t in TARGETS
        ],
        "",
        "Criteria are scored 0-10 (tolerance ±1 point), overall 0-100 (tolerance ±10).",
        "Rank correlation (Spearman): 1 = orders CVs exactly like Claude, 0 = no relation.",
        "",
        "Limits: the training CVs are anonymised English CVs from a US dataset; the grades",
        "are Claude's judgement against the rubric, not hiring outcomes.",
    ]
    (OUTPUT_DIR / "cv_scorer_evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Report written to data_analysis/outputs/cv_scorer_evaluation.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
