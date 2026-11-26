"""Check the quality of Claude's CV grades before training on them.

    python data_analysis/07b_check_label_quality.py              # spread of the saved grades only (free)
    python data_analysis/07b_check_label_quality.py --regrade 20 # also re-grade 20 CVs and compare

1. Spread: for each criterion, the mean, standard deviation and range of the
   saved grades. Grades bunched on one or two values teach the model little.
2. Consistency (--regrade N): N already-graded CVs are graded again with the
   same model and settings (standard API, about $0.02 per CV with Sonnet 5.5).
   It reports how often the two grades agree within 1 point (criteria) or
   10 points (overall). This is the ceiling for our own model: it cannot be
   more consistent with Claude than Claude is with itself.

Writes data_analysis/outputs/cv_label_quality.md.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

AI_ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(AI_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_DIR))

import numpy as np  # noqa: E402

from app.services.cv_quality.labelling import load_cvs, parse_result_text, read_labels, request_params  # noqa: E402
from app.services.cv_quality.scorer import TARGETS  # noqa: E402

OUTPUT = AI_ENGINE_DIR / "data_analysis" / "outputs" / "cv_label_quality.md"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regrade", type=int, default=0)
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    args = parser.parse_args()

    labels = read_labels()
    if not labels:
        print("No labels yet. Run data_analysis/07_label_cv_quality.py first.")
        return 1
    grades = np.array([[item["labels"][t] for t in TARGETS] for item in labels.values()], dtype=float)
    lines = ["# Quality of Claude's CV grades", "", f"{len(labels)} graded CVs.", "", "## Spread of the grades", "",
             "| Criterion | Mean | Std dev | Min | Max | Distinct values |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    print(f"{len(labels)} graded CVs\n{'criterion':<16}{'mean':>7}{'std':>7}{'min':>5}{'max':>5}{'distinct':>9}")
    for column, target in enumerate(TARGETS):
        values = grades[:, column]
        row = (values.mean(), values.std(), values.min(), values.max(), len(set(values)))
        print(f"{target:<16}{row[0]:7.1f}{row[1]:7.1f}{row[2]:5.0f}{row[3]:5.0f}{row[4]:9d}")
        lines.append(f"| {target} | {row[0]:.1f} | {row[1]:.1f} | {row[2]:.0f} | {row[3]:.0f} | {row[4]} |")

    if args.regrade:
        import anthropic

        client = anthropic.Anthropic(max_retries=3)
        by_id = {cv.id: cv for cv in load_cvs()}
        ids = [i for i in labels if i in by_id]
        random.Random(7).shuffle(ids)
        pairs = []
        for cv_id in ids[: args.regrade]:
            item = labels[cv_id]
            response = client.messages.create(**request_params(by_id[cv_id], model=item["model"], effort=args.effort))
            text = next((b.text for b in response.content if b.type == "text"), "")
            again = parse_result_text(text) if response.stop_reason == "end_turn" else None
            if again is None:
                print(f"  {cv_id}: no usable grade ({response.stop_reason})")
                continue
            pairs.append(([item["labels"][t] for t in TARGETS], [again[t] for t in TARGETS]))
            print(f"  re-graded {len(pairs)}/{args.regrade}", flush=True)
        if pairs:
            first = np.array([p[0] for p in pairs], dtype=float)
            second = np.array([p[1] for p in pairs], dtype=float)
            lines += ["", f"## Consistency: {len(pairs)} CVs graded twice", "",
                      "| Criterion | Mean difference | Agree within tolerance |", "| --- | ---: | ---: |"]
            print(f"\nConsistency on {len(pairs)} CVs graded twice\n{'criterion':<16}{'mean diff':>10}{'agree':>8}")
            for column, target in enumerate(TARGETS):
                tolerance = 10 if target == "overall" else 1
                diff = np.abs(first[:, column] - second[:, column])
                print(f"{target:<16}{diff.mean():10.2f}{(diff <= tolerance).mean():8.0%}")
                lines.append(f"| {target} | {diff.mean():.2f} | {(diff <= tolerance).mean():.0%} (±{tolerance}) |")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport written to {OUTPUT.relative_to(AI_ENGINE_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
