"""Training labels for the CV scorer: Claude grades CVs against the rubric.

Used by data_analysis/07_label_cv_quality.py through the Message Batches API
(half price, results within 24 hours). Only the CV text from the research
dataset is sent; it is already anonymised. Labels are saved without the CV
text, as {"id", "category", "labels", "model", "rubric"} lines.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.services.cv_quality.rubric import GRADING_INSTRUCTIONS, RUBRIC_VERSION, clamp_label, label_schema

AI_ENGINE_DIR = Path(__file__).resolve().parents[3]
RESUMES_PATH = AI_ENGINE_DIR / "data" / "raw" / "resumes" / "Resume.csv"
LABELS_PATH = AI_ENGINE_DIR / "data" / "cv_quality" / "labels.jsonl"
MAX_CV_CHARS = 12_000

# Batch prices in USD per million tokens (input, output), 50% of standard.
BATCH_PRICES = {
    "claude-haiku-5-5": (0.05, 0.25),
    "claude-sonnet-5-5": (1.0, 5.0),
    "claude-opus-5-5": (2.0, 10.0),
}


@dataclass(frozen=True, slots=True)
class LabelCv:
    id: str
    category: str
    text: str


def load_cvs(path: Path = RESUMES_PATH) -> list[LabelCv]:
    from app.services.jobs.text import html_to_text

    csv.field_size_limit(10**9)
    cvs: list[LabelCv] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            text = html_to_text(row.get("Resume_html") or "") or row.get("Resume_str") or ""
            if text.strip():
                cvs.append(LabelCv(str(row["ID"]), row.get("Category", ""), text.strip()[:MAX_CV_CHARS]))
    return cvs


def sample_cvs(cvs: list[LabelCv], count: int, *, seed: int = 42) -> list[LabelCv]:
    """A deterministic sample spread evenly across job categories."""
    by_category: dict[str, list[LabelCv]] = defaultdict(list)
    for cv in cvs:
        by_category[cv.category].append(cv)
    rng = random.Random(seed)
    for items in by_category.values():
        rng.shuffle(items)
    chosen: list[LabelCv] = []
    while len(chosen) < min(count, len(cvs)):
        for category in sorted(by_category):
            if by_category[category] and len(chosen) < count:
                chosen.append(by_category[category].pop())
    return chosen


def request_params(cv: LabelCv, *, model: str, effort: str) -> dict:
    return {
        "model": model,
        "max_tokens": 6000,
        "system": GRADING_INSTRUCTIONS,
        "messages": [{"role": "user", "content": f"<cv>\n{cv.text}\n</cv>\n\nGrade this CV."}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": label_schema()}},
    }


def estimate_cost(cvs: list[LabelCv], model: str, *, output_tokens_per_cv: int = 1500) -> tuple[int, float]:
    """Rough (input tokens, USD) for a batch: ~3.5 characters per token,
    and output tokens include the model's thinking."""
    prompt_tokens = len(GRADING_INSTRUCTIONS) / 3.5 + 40
    input_tokens = int(sum(len(cv.text) / 3.5 + prompt_tokens for cv in cvs))
    price_in, price_out = BATCH_PRICES.get(model, BATCH_PRICES["claude-opus-5-5"])
    cost = input_tokens / 1e6 * price_in + len(cvs) * output_tokens_per_cv / 1e6 * price_out
    return input_tokens, round(cost, 2)


def parse_result_text(text: str) -> dict[str, int] | None:
    try:
        return clamp_label(json.loads(text))
    except (ValueError, KeyError, TypeError):
        return None


def label_line(cv: LabelCv, labels: dict[str, int], model: str) -> str:
    return json.dumps({"id": cv.id, "category": cv.category, "labels": labels, "model": model, "rubric": RUBRIC_VERSION})


def read_labels(path: Path = LABELS_PATH) -> dict[str, dict]:
    """Labels by CV id (only those graded with the current rubric)."""
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if item.get("rubric") == RUBRIC_VERSION:
                out[str(item["id"])] = item
    return out
