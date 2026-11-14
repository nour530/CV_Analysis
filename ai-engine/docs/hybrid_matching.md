# Hybrid Matching Engine

## Purpose

The hybrid matcher combines two clearly separated layers:

1. A deterministic, local matching layer that computes all scores from canonical
   skills, job requirement importance, local text similarity, and optional category
   compatibility.
2. An optional OpenAI explanation layer that adds contextual wording,
   recommendations, and uncertainty notes.

The deterministic layer is authoritative. OpenAI cannot change the score,
classification, matched skills, missing required skills, or component
contributions.

## Architecture

| Module | Responsibility |
| --- | --- |
| `app/services/nlp/skills.py` | Loads the local skills dictionary and alias table, performs boundary-aware extraction, classifies requirement importance, and canonicalises external skill names. |
| `app/services/nlp/embeddings.py` | Builds deterministic local TF-IDF vectors from word and character n-grams and computes cosine similarity. The API fits IDF weights once on a category-balanced sample of the job catalog (`JobCatalog.embedder`). No model download or external service is used. |
| `app/services/matching/profiles.py` | Converts résumé/job text or existing `ResumeAnalysis`/`JobAnalysis` objects into canonical `ResumeProfile`/`JobProfile` objects. |
| `app/services/matching/config.py` | Defines weights, thresholds, importance weights, category mapping, fairness notice, and disclaimer. |
| `app/services/matching/local_matcher.py` | Computes component scores, matched skills, missing requirements, experience evidence, recommendations, and local uncertainties. |
| `app/services/matching/hybrid_service.py` | Runs local matching first, then optionally calls the LLM for an explanation. Handles skips and failures without losing the local result. |
| `app/services/matching/evaluation.py` | Loads reviewed cases and computes accuracy, macro-F1, per-class metrics, and confusion matrices. |
| `app/schemas/hybrid_match.py` | Defines strict Pydantic schemas for profiles, requirements, score components, and the final result. |

## Matching flow

```text
résumé text / ResumeAnalysis ─┐
                              ├─> canonical profiles ─> local matcher ─> local result
job text / JobAnalysis ───────┘                                      │
                                                                     ├─> optional OpenAI explanation
                                                                     └─> final HybridMatchResult
```

The local matcher runs even when no LLM is configured. Remote analysis and
explanation require both an LLM provider and explicit authorisation:

```python
from app.services.llm.factory import create_llm_provider
from app.services.matching import HybridMatchingService

service = HybridMatchingService()
local = service.match_local(resume_text=resume_text, job_text=job_text)

hybrid = service.match_hybrid(
    resume_text=resume_text,
    job_text=job_text,
    provider=create_llm_provider(),
    allow_remote_analysis=True,
)
```

When structured analyses already exist, pass `resume_analysis=` and
`job_analysis=` and set `allow_remote_analysis=False`. The explanation call may
still use the provider, but no additional résumé/job extraction request is
required.

## Score components

All component values are in `[0, 1]`. Weights are normalised over the components
that are available for a specific comparison, so a missing component does not
silently reduce the score.

| Component | Default weight | Value |
| --- | ---: | --- |
| `required_skill_coverage` | 0.45 | Importance-weighted share of required job skills found in the résumé profile. |
| `preferred_skill_coverage` | 0.15 | Share of preferred job skills found in the résumé profile. Unavailable if the job lists none. |
| `skill_overlap` | 0.15 | Share of all job requirements present in the résumé profile. |
| `semantic_similarity` | 0.25 | Local TF-IDF cosine similarity over word `(1,2)` and character `(3,5)` n-grams, weighted by IDF from the job catalog reference corpus. |
| `category_compatibility` | 0.00 | `1.0` for an exact match in an explicitly validated mapping, otherwise `0.0`. |

Importance weights default to `required=1.0`, `preferred=0.6`, and
`mentioned=0.3`. Scores are classified as:

- `strong_match` at or above `0.75`
- `moderate_match` at or above `0.40`
- `weak_match` below `0.40`

These defaults are provisional. They must be revalidated on independently
labelled data before production use. Category compatibility is disabled by
default because the résumé and job datasets use different taxonomies and no
validated cross-taxonomy mapping is available.

## Evaluation

The development evaluation set is
`data/evaluation/hybrid_matching_reviewed_examples.csv`: 20 curated examples
with 8 strong, 6 moderate, and 6 weak labels. Every case includes a review
rationale. The labels are synthetic and intended for regression checking and
weight selection, not as a production benchmark.

Run:

```bash
ai-engine/venv/Scripts/python.exe ai-engine/data_analysis/04_hybrid_matching_evaluation.py
```

Outputs:

- `data_analysis/outputs/hybrid_matching_evaluation.json`
- `data_analysis/outputs/hybrid_matching_evaluation.md`

Current default results on the curated set:

| Metric | Value |
| --- | ---: |
| Accuracy | 1.0000 |
| Macro-F1 | 1.0000 |
| Weighted-F1 | 1.0000 |
| Strong-match F1 | 1.0000 |
| Moderate-match F1 | 1.0000 |
| Weak-match F1 | 1.0000 |

Mean scores by reviewed label are approximately `0.920` (strong), `0.508`
(moderate), and `0.039` (weak). The lowest moderate case scores `0.420`; the
highest weak case scores `0.223`. The evaluation also compares alternative
configurations. Both a semantic-heavy configuration and a moderate threshold of
`0.45` reach `0.944` macro-F1, below the defaults.

Earlier versions fitted TF-IDF on only the two compared texts, which made IDF
almost uninformative: shared boilerplate weighed about as much as shared rare
skills. With that approach the semantic-heavy configuration reached only
`0.643` macro-F1. Similarity now uses IDF fitted on up to 2,000 catalog
descriptions sampled round-robin across categories, so the dominant Software
Development category does not decide which terms count as common. The fit runs
once per process on first use (a few seconds). Terms absent from the reference
corpus are ignored by the fitted vectoriser. Without a fitted corpus,
`LocalTfidfEmbedder` still falls back to the two-text fit. These results are highly sensitive to the small synthetic sample.

## Reliability and fallback

- The local matcher always runs first.
- Without a provider or without `allow_remote_analysis`, the LLM step is marked
  `skipped` and the local result is returned unchanged.
- Provider failures are caught. The explanation is left as `None`, deterministic
  local recommendations are retained, and an `openai_explanation` uncertainty is
  added.
- Component records expose `success`, `skipped`, or `failed` for
  `local_skill_matching`, `local_semantic_similarity`,
  `local_category_compatibility`, and `openai_explanation`.
- LLM-provided recommendations are used only if the response contains them;
  otherwise local recommendations remain.

## Fairness and responsible use

Every result includes a fairness notice and disclaimer. The matcher does not use
or infer protected characteristics such as age, gender, race, ethnicity,
religion, disability, or nationality. It must not be used as the sole basis for
hiring, rejection, ranking, or any other employment decision. A human reviewer
must verify the supporting evidence.

The LLM prompt receives structured analyses and the verified local result. It is
asked for an explanation, not a new score. The local score remains unchanged even
if the LLM returns a different score.

## Known limitations

- Skill matching is dictionary based. It cannot reliably understand every
  paraphrase or implicit skill.
- The dictionary does not contain every common surface form. For example, bare
  `SQL`, `AWS`, `Java`, `Angular`, and `Excel` are not canonical entries; use
  their canonical forms such as `Microsoft SQL Server`, `Oracle Java`,
  `Google Angular`, and `Microsoft Excel` when producing structured text.
- Generic aliases can create false positives; exact evidence is returned so a
  reviewer can check each match.
- Local TF-IDF similarity is lexical, not transformer-based semantic similarity.
- Category compatibility remains disabled without a validated mapping.
- Experience evidence is similarity-assisted and should be reviewed; it is not
  proof of hands-on experience.
- The evaluation set is small and synthetic. Production thresholds and weights
  require an independently labelled validation sample.

## Tests

```bash
cd ai-engine
venv/Scripts/python.exe -m pytest tests -q
```

The hybrid tests cover canonical alias extraction, short-alias context,
deterministic local similarity, component transparency, missing required versus
preferred skills, category mapping, LLM success/skip/failure handling,
score-override protection, schema strictness, and evaluation metrics.

## Angular integration

No Angular files were modified. The backend now exposes the engine through the
FastAPI service documented in `docs/api.md`. A future UI should consume
`HybridMatchResult` without recomputing or overriding the local score.
