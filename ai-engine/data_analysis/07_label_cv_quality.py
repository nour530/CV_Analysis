"""Grade training CVs with Claude (Message Batches API) for the CV scorer.

    python data_analysis/07_label_cv_quality.py --dry-run          # count and cost only
    python data_analysis/07_label_cv_quality.py                    # submit, wait, save
    python data_analysis/07_label_cv_quality.py --resume           # collect a submitted batch

Needs ANTHROPIC_API_KEY. Defaults: 1,500 CVs from data/raw/resumes/Resume.csv,
spread across job categories, graded by claude-sonnet-5-5 at medium effort.
Results usually arrive within an hour (at most 24 h). Labels are appended to
data/cv_quality/labels.jsonl; CVs already labelled are skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

AI_ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(AI_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(AI_ENGINE_DIR))

from app.services.cv_quality.labelling import (  # noqa: E402
    LABELS_PATH,
    estimate_cost,
    label_line,
    load_cvs,
    parse_result_text,
    read_labels,
    request_params,
    sample_cvs,
)

STATE_PATH = LABELS_PATH.parent / "label_batch.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=1500)
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true", help="collect the batch submitted earlier")
    args = parser.parse_args()

    cvs = sample_cvs(load_cvs(), args.count)
    done = read_labels()
    todo = [cv for cv in cvs if cv.id not in done]
    tokens, cost = estimate_cost(todo, args.model)
    print(f"{len(cvs)} CVs sampled, {len(done)} already labelled, {len(todo)} to grade with {args.model}.")
    print(f"Estimated: {tokens:,} input tokens, about ${cost} at batch prices.")
    if args.dry_run:
        return 0

    import anthropic

    client = anthropic.Anthropic()
    if args.resume:
        if not STATE_PATH.exists():
            print("No submitted batch found.")
            return 1
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    else:
        if not todo:
            print("Nothing to do.")
            return 0
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        batch = client.messages.batches.create(
            requests=[
                Request(custom_id=f"cv-{cv.id}", params=MessageCreateParamsNonStreaming(**request_params(cv, model=args.model, effort=args.effort)))
                for cv in todo
            ]
        )
        state = {"batch_id": batch.id, "model": args.model}
        LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
        print(f"Submitted batch {batch.id}. Safe to stop now and run with --resume later.")

    while True:
        batch = client.messages.batches.retrieve(state["batch_id"])
        counts = batch.request_counts
        print(f"  {batch.processing_status}: {counts.succeeded} done, {counts.processing} processing, {counts.errored} errors", flush=True)
        if batch.processing_status == "ended":
            break
        time.sleep(60)

    by_id = {f"cv-{cv.id}": cv for cv in cvs}
    saved = failed = 0
    with LABELS_PATH.open("a", encoding="utf-8") as handle:
        for result in client.messages.batches.results(state["batch_id"]):
            cv = by_id.get(result.custom_id)
            if cv is None or cv.id in done:
                continue
            labels = None
            if result.result.type == "succeeded" and result.result.message.stop_reason == "end_turn":
                text = next((b.text for b in result.result.message.content if b.type == "text"), "")
                labels = parse_result_text(text)
            if labels is None:
                failed += 1
                continue
            handle.write(label_line(cv, labels, state["model"]) + "\n")
            done[cv.id] = {}
            saved += 1
    STATE_PATH.unlink(missing_ok=True)
    print(f"Saved {saved} labels to {LABELS_PATH.relative_to(AI_ENGINE_DIR)} ({failed} failed; rerun to retry them).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
