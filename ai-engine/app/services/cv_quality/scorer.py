"""Our own CV quality model: predicts the rubric scores for a CV.

Training (data_analysis/08_train_cv_scorer.py) learns from CVs graded by
Claude against the rubric in rubric.py. The model combines the CV's wording
(TF-IDF reduced to 120 dimensions) with ~25 measured signals (features.py) and
predicts 8 criterion scores (0-10) plus an overall score (0-100). It runs
locally: evaluating a CV sends nothing to any AI provider.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.decomposition import TruncatedSVD
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import Ridge
from sklearn.multioutput import MultiOutputRegressor
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.preprocessing import StandardScaler

from app.services.career.esco import normalize_phrase
from app.services.cv_quality.features import CvFeatures
from app.services.cv_quality.rubric import CRITERION_IDS, RUBRIC_VERSION

logger = logging.getLogger(__name__)

AI_ENGINE_DIR = Path(__file__).resolve().parents[3]
MODEL_PATH = AI_ENGINE_DIR / "data" / "models" / "cv_quality_scorer.joblib"
TARGETS = (*CRITERION_IDS, "overall")
MAX_TEXT_CHARS = 20_000


def build_pipeline(kind: str = "ridge", *, svd_components: int = 120) -> Pipeline:
    features = FeatureUnion(
        [
            (
                "wording",
                Pipeline(
                    [
                        ("tfidf", TfidfVectorizer(preprocessor=normalize_phrase, ngram_range=(1, 2), sublinear_tf=True, min_df=3, max_features=60_000)),
                        ("svd", TruncatedSVD(n_components=svd_components, random_state=42)),
                    ]
                ),
            ),
            ("signals", Pipeline([("measure", CvFeatures()), ("scale", StandardScaler())])),
        ]
    )
    if kind == "boosting":
        model = MultiOutputRegressor(
            HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, l2_regularization=1.0, random_state=42)
        )
    else:
        model = Ridge(alpha=3.0)
    return Pipeline([("features", features), ("model", model)])


def clip_predictions(values: np.ndarray) -> np.ndarray:
    """Criteria 0-10, overall 0-100 (the last column)."""
    clipped = np.clip(values, 0, 10)
    clipped[..., -1] = np.clip(values[..., -1], 0, 100)
    return clipped


@dataclass(frozen=True, slots=True)
class CvQualityScore:
    criteria: dict[str, float]
    overall: int


class CvQualityScorer:
    def __init__(self, pipeline: Pipeline, metadata: dict | None = None) -> None:
        self.pipeline = pipeline
        self.metadata = metadata or {}

    @classmethod
    def train(
        cls, texts: Sequence[str], labels: np.ndarray, *, kind: str = "ridge", metadata: dict | None = None, svd_components: int = 120
    ) -> CvQualityScorer:
        pipeline = build_pipeline(kind, svd_components=svd_components)
        pipeline.fit([text[:MAX_TEXT_CHARS] for text in texts], labels)
        return cls(pipeline, {"kind": kind, **(metadata or {})})

    def predict_many(self, texts: Sequence[str]) -> np.ndarray:
        return clip_predictions(np.asarray(self.pipeline.predict([text[:MAX_TEXT_CHARS] for text in texts]), dtype=float))

    def score(self, text: str) -> CvQualityScore:
        row = self.predict_many([text])[0]
        return CvQualityScore(
            criteria={cid: round(float(value), 1) for cid, value in zip(CRITERION_IDS, row[:-1])},
            overall=int(round(float(row[-1]))),
        )

    def save(self, path: Path = MODEL_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {"pipeline": self.pipeline, "sklearn": sklearn.__version__, "rubric": RUBRIC_VERSION, "metadata": self.metadata},
            path,
            compress=3,
        )

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> CvQualityScorer | None:
        """The saved model, or None if missing or built for another rubric
        or scikit-learn version."""
        if not path.exists():
            return None
        try:
            payload = joblib.load(path)
        except Exception:  # noqa: BLE001 - a broken file means "not trained"
            logger.warning("Could not load %s.", path)
            return None
        if not isinstance(payload, dict) or payload.get("sklearn") != sklearn.__version__ or payload.get("rubric") != RUBRIC_VERSION:
            logger.warning("%s was built for another rubric or scikit-learn version; retrain it.", path)
            return None
        return cls(payload["pipeline"], payload.get("metadata"))


def cross_validate(texts: Sequence[str], labels: np.ndarray, *, kind: str, folds: int = 5, svd_components: int = 120) -> dict:
    """K-fold evaluation against the labels: per target MAE, Spearman
    correlation, and share within 1 point (criteria) or 10 points (overall);
    plus the MAE of always predicting the training mean, as a baseline."""
    from scipy.stats import spearmanr
    from sklearn.model_selection import KFold

    texts = [text[:MAX_TEXT_CHARS] for text in texts]
    predicted = np.zeros_like(labels, dtype=float)
    baseline = np.zeros_like(labels, dtype=float)
    for train_idx, test_idx in KFold(n_splits=folds, shuffle=True, random_state=42).split(texts):
        pipeline = build_pipeline(kind, svd_components=svd_components)
        pipeline.fit([texts[i] for i in train_idx], labels[train_idx])
        predicted[test_idx] = clip_predictions(np.asarray(pipeline.predict([texts[i] for i in test_idx]), dtype=float))
        baseline[test_idx] = labels[train_idx].mean(axis=0)
    report: dict[str, dict] = {}
    for column, target in enumerate(TARGETS):
        tolerance = 10 if target == "overall" else 1
        error = np.abs(predicted[:, column] - labels[:, column])
        rho = spearmanr(predicted[:, column], labels[:, column]).statistic
        report[target] = {
            "mae": round(float(error.mean()), 3),
            "baseline_mae": round(float(np.abs(baseline[:, column] - labels[:, column]).mean()), 3),
            "spearman": round(float(0.0 if np.isnan(rho) else rho), 3),
            "within_tolerance": round(float((error <= tolerance).mean()), 3),
            "tolerance": tolerance,
        }
    return report


_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _loaded() -> CvQualityScorer | None:
    return CvQualityScorer.load()


def get_cv_scorer() -> CvQualityScorer | None:
    """The trained scorer, or None until data_analysis/08_train_cv_scorer.py has run."""
    with _LOCK:
        return _loaded()
