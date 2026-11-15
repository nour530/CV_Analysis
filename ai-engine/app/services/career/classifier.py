"""Career-field classifier (14 fields, English and French).

Training data:
- every ESCO occupation, as two documents (EN and FR): title, synonyms,
  description and essential skills, labelled with its career field
  (data/esco/domains.json);
- real CVs from the research dataset (data/raw/resumes/Resume.csv) whose job
  category maps to one field. ESCO describes jobs in dictionary style; the
  CVs teach the model how people actually write about their work;
- about 400 IT job descriptions (data/raw/job_descriptions/) for developer,
  data and cloud vocabulary that the CVs' IT category lacks.

data_analysis/06_train_domain_classifier.py evaluates the model and saves it
to data/models/domain_classifier.joblib, which the API loads. If that file is
missing, or was built for other ESCO data or another scikit-learn version, the
API trains an ESCO-only model on first use instead (less accurate on CVs).
"""

from __future__ import annotations

import csv
import hashlib
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import joblib
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

from app.services.career.esco import DATA_DIR, EscoCatalog, esco_data_dir, get_esco_catalog, normalize_phrase

logger = logging.getLogger(__name__)

AI_ENGINE_DIR = Path(__file__).resolve().parents[3]
MODEL_PATH = AI_ENGINE_DIR / "data" / "models" / "domain_classifier.joblib"
RESUMES_PATH = AI_ENGINE_DIR / "data" / "raw" / "resumes" / "Resume.csv"
IT_JOBS_PATH = AI_ENGINE_DIR / "data" / "raw" / "job_descriptions" / "job_roles_combined_ready_10461.csv"
IT_JOBS_PER_ROLE = 70  # six role categories -> about 400 documents
MAX_TEXT_CHARS = 20_000

# Resume.csv job category -> career field. Categories that span several
# fields (CONSULTANT, FITNESS, AUTOMOBILE, APPAREL, AVIATION) are left out.
CATEGORY_TO_DOMAIN = {
    "INFORMATION-TECHNOLOGY": "it",
    "ENGINEERING": "engineering",
    "HEALTHCARE": "health",
    "TEACHER": "education",
    "FINANCE": "finance",
    "ACCOUNTANT": "finance",
    "BANKING": "finance",
    "HR": "business",
    "SALES": "sales",
    "BUSINESS-DEVELOPMENT": "sales",
    "BPO": "sales",
    "PUBLIC-RELATIONS": "sales",
    "CHEF": "hospitality",
    "ADVOCATE": "legal_social",
    "AGRICULTURE": "agriculture",
    "DESIGNER": "creative",
    "DIGITAL-MEDIA": "creative",
    "ARTS": "creative",
    "CONSTRUCTION": "trades",
}


@dataclass(frozen=True, slots=True)
class DomainPrediction:
    domain_id: str
    probability: float


def training_documents(catalog: EscoCatalog) -> tuple[list[str], list[str], list[str]]:
    """Return (texts, labels, groups); the group keeps both language versions
    of one occupation together so evaluation splits cannot leak."""
    texts: list[str] = []
    labels: list[str] = []
    groups: list[str] = []
    for occupation in catalog.occupations:
        if not occupation.domain_id:
            continue
        for language in ("en", "fr"):
            document = catalog.occupation_document(occupation, language)
            if document:
                texts.append(document)
                labels.append(occupation.domain_id)
                groups.append(occupation.uri)
    return texts, labels, groups


def cv_training_documents(path: Path = RESUMES_PATH) -> tuple[list[str], list[str], list[str]]:
    """Labelled CV texts (texts, labels, original categories); empty if the
    research dataset is absent."""
    if not path.exists():
        return [], [], []
    from app.services.jobs.text import html_to_text

    csv.field_size_limit(10**9)
    texts: list[str] = []
    labels: list[str] = []
    categories: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            category = row.get("Category", "")
            domain = CATEGORY_TO_DOMAIN.get(category)
            text = html_to_text(row.get("Resume_html") or "") or row.get("Resume_str") or ""
            if domain and text.strip():
                texts.append(text[:MAX_TEXT_CHARS])
                labels.append(domain)
                categories.append(category)
    return texts, labels, categories


def it_job_documents(path: Path = IT_JOBS_PATH, *, per_role: int = IT_JOBS_PER_ROLE) -> tuple[list[str], list[str]]:
    """A fixed, balanced sample of IT job descriptions (per role category),
    labelled 'it'. The CV dataset's IT category is mostly support and
    management profiles; these add developer, data and cloud vocabulary."""
    if not path.exists():
        return [], []
    csv.field_size_limit(10**9)
    by_role: dict[str, list[str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            text = (row.get("jobdescription") or "").strip()
            if text:
                by_role.setdefault(row.get("label") or "", []).append(text[:MAX_TEXT_CHARS])
    texts: list[str] = []
    for role in sorted(by_role):
        rows = by_role[role]
        step = max(1, len(rows) // per_role)
        texts.extend(rows[::step][:per_role])
    return texts, ["it"] * len(texts)


def catalog_fingerprint(catalog: EscoCatalog) -> str:
    """Identifies the ESCO data and field mapping a model was trained on."""
    mapping = ",".join(f"{o.uri}={o.domain_id}" for o in catalog.occupations)
    return hashlib.sha256(mapping.encode("utf-8")).hexdigest()[:16]


def build_pipeline() -> Pipeline:
    features = FeatureUnion(
        [
            ("word", TfidfVectorizer(preprocessor=normalize_phrase, ngram_range=(1, 2), sublinear_tf=True, min_df=2, max_features=120_000)),
            ("char", TfidfVectorizer(preprocessor=normalize_phrase, analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=3, max_features=120_000)),
        ]
    )
    model = LogisticRegression(max_iter=2000, C=8.0, class_weight="balanced")
    return Pipeline([("features", features), ("model", model)])


class DomainClassifier:
    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline
        self.classes: list[str] = list(pipeline.named_steps["model"].classes_)

    @classmethod
    def train(
        cls,
        catalog: EscoCatalog,
        extra_texts: Sequence[str] = (),
        extra_labels: Sequence[str] = (),
    ) -> DomainClassifier:
        texts, labels, _ = training_documents(catalog)
        if len(set(labels)) < 2:
            raise ValueError("ESCO data is missing; run data_analysis/05_import_esco.py")
        pipeline = build_pipeline()
        pipeline.fit([*texts, *extra_texts], [*labels, *extra_labels])
        return cls(pipeline)

    def save(self, catalog: EscoCatalog, path: Path = MODEL_PATH, *, trained_on: dict | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "pipeline": self.pipeline,
                "sklearn": sklearn.__version__,
                "fingerprint": catalog_fingerprint(catalog),
                "trained_on": trained_on or {},
            },
            path,
            compress=3,
        )

    @classmethod
    def load(cls, catalog: EscoCatalog, path: Path = MODEL_PATH) -> DomainClassifier | None:
        """The saved model, or None if it is missing or does not match the
        current ESCO data and scikit-learn version."""
        if not path.exists():
            return None
        try:
            payload = joblib.load(path)
        except Exception:  # noqa: BLE001 - a broken file falls back to training
            logger.warning("Could not load %s; training the field classifier instead.", path)
            return None
        if not isinstance(payload, dict) or payload.get("sklearn") != sklearn.__version__ or payload.get("fingerprint") != catalog_fingerprint(catalog):
            logger.warning("%s was built for other data or scikit-learn; training instead.", path)
            return None
        return cls(payload["pipeline"])

    def predict(self, text: str, top: int = 3) -> list[DomainPrediction]:
        if not text or not text.strip():
            return []
        probabilities = self.pipeline.predict_proba([text[:MAX_TEXT_CHARS]])[0]
        ranked = sorted(zip(self.classes, probabilities), key=lambda item: -item[1])
        return [DomainPrediction(domain_id, round(float(p), 4)) for domain_id, p in ranked[:top]]

    def predict_many(self, texts: Sequence[str]) -> list[str]:
        return list(self.pipeline.predict([text[:MAX_TEXT_CHARS] for text in texts]))


_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _trained() -> DomainClassifier | None:
    catalog = get_esco_catalog()
    if not catalog.available:
        return None
    if esco_data_dir() == DATA_DIR:
        saved = DomainClassifier.load(catalog)
        if saved is not None:
            return saved
    return DomainClassifier.train(catalog)


def get_domain_classifier() -> DomainClassifier | None:
    """The trained classifier, or None when ESCO data has not been imported."""
    with _LOCK:
        return _trained()
