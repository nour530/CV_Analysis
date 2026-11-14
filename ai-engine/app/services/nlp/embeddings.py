from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import Sequence

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.pipeline import FeatureUnion


class LocalEmbeddingError(RuntimeError):
    """Raised when local text embedding cannot be computed."""


class LocalTfidfEmbedder:
    """Deterministic local TF-IDF embedder for word and character n-grams.

    The embedder is lexical rather than transformer-based. It is intentionally
    local, CPU-only, and requires no model download or external service.

    Call :meth:`fit` with a reference corpus (for example the job catalog) so
    inverse document frequencies reflect how common each term really is. A
    fitted embedder only transforms texts at comparison time, so boilerplate
    terms shared by most documents carry little weight. An unfitted embedder
    falls back to fitting on the two compared texts, where IDF is nearly
    uninformative; use that mode only for ad-hoc comparisons and tests.
    """

    def __init__(
        self,
        *,
        word_ngram_range: tuple[int, int] = (1, 2),
        char_ngram_range: tuple[int, int] = (3, 5),
        max_features: int = 50_000,
    ) -> None:
        self.word_ngram_range = word_ngram_range
        self.char_ngram_range = char_ngram_range
        self.max_features = max_features
        self._fitted: FeatureUnion | None = None
        self._cache: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._cache_size = 4096
        self._lock = threading.Lock()

    @property
    def is_fitted(self) -> bool:
        return self._fitted is not None

    def fit(self, corpus: Sequence[str]) -> LocalTfidfEmbedder:
        """Fit the vocabulary and IDF weights on a reference corpus."""
        cleaned = [text for text in (self._normalize(t) for t in corpus) if text]
        if not cleaned:
            raise LocalEmbeddingError("At least one non-empty text is required.")
        try:
            vectorizer = self._vectorizer().fit(cleaned)
        except ValueError as exc:
            raise LocalEmbeddingError("Could not build TF-IDF features.") from exc
        with self._lock:
            self._fitted = vectorizer
            self._cache.clear()
        return self

    @staticmethod
    def _normalize(text: str) -> str:
        if not isinstance(text, str):
            return ""
        return re.sub(r"\s+", " ", text).strip().casefold()

    def _vectorizer(self) -> FeatureUnion:
        return FeatureUnion(
            [
                (
                    "word",
                    TfidfVectorizer(
                        analyzer="word",
                        ngram_range=self.word_ngram_range,
                        sublinear_tf=True,
                        stop_words="english",
                        max_features=self.max_features,
                    ),
                ),
                (
                    "char",
                    TfidfVectorizer(
                        analyzer="char_wb",
                        ngram_range=self.char_ngram_range,
                        sublinear_tf=True,
                        max_features=self.max_features,
                    ),
                ),
            ]
        )

    def fit_transform(self, texts: Sequence[str]):
        """Fit a local embedding over a corpus and return L2-normalised vectors."""
        cleaned = [self._normalize(text) for text in texts]
        if not cleaned or not any(cleaned):
            raise LocalEmbeddingError("At least one non-empty text is required.")
        try:
            return self._vectorizer().fit_transform(cleaned)
        except ValueError as exc:
            raise LocalEmbeddingError("Could not build TF-IDF features.") from exc

    def similarity_matrix(self, left: Sequence[str], right: Sequence[str]):
        """Return pairwise cosine similarity for two text collections."""
        documents = [self._normalize(item) for item in list(left) + list(right)]
        if not any(documents):
            raise LocalEmbeddingError("At least one non-empty text is required.")
        try:
            matrix = self._vectorizer().fit_transform(documents)
        except ValueError as exc:
            raise LocalEmbeddingError("Could not build TF-IDF features.") from exc
        left_size = len(left)
        return cosine_similarity(matrix[:left_size], matrix[left_size:])

    def similarity(self, text_a: str, text_b: str) -> float:
        """Return cosine similarity in [0, 1] between two individual texts."""
        left = self._normalize(text_a)
        right = self._normalize(text_b)
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        key = (left, right)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
            fitted = self._fitted
        try:
            if fitted is not None:
                matrix = fitted.transform([left, right])
            else:
                matrix = self._vectorizer().fit_transform([left, right])
        except ValueError:
            return 0.0
        score = float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0])
        score = max(0.0, min(1.0, score))
        with self._lock:
            self._cache[key] = score
            if len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return score
