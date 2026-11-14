# Hybrid Matching Evaluation

Generated: 2026-11-14T15:07:14.092534+00:00

This report uses a synthetic, curated development set. It is useful for
regression checking and weight selection, not as a production benchmark.

## Default configuration

- Samples: 20
- Accuracy: 1.0000
- Macro-F1: 1.0000
- Weighted-F1: 1.0000

### Per-class metrics

| Label | Precision | Recall | F1 | Support |
| --- | ---: | ---: | ---: | ---: |
| strong_match | 1.0000 | 1.0000 | 1.0000 | 8 |
| moderate_match | 1.0000 | 1.0000 | 1.0000 | 6 |
| weak_match | 1.0000 | 1.0000 | 1.0000 | 6 |

### Confusion matrix

| Actual \ Predicted | strong_match | moderate_match | weak_match |
| --- | ---: | ---: | ---: |
| strong_match | 8 | 0 | 0 |
| moderate_match | 0 | 6 | 0 |
| weak_match | 0 | 0 | 6 |

### Incorrect predictions

| Case | Actual | Predicted | Score |
| --- | --- | --- | ---: |
| None | | | |

## Configuration comparison

| Configuration | Accuracy | Macro-F1 | Weighted-F1 |
| --- | ---: | ---: | ---: |
| default | 1.0000 | 1.0000 | 1.0000 |
| moderate_threshold_0_35 | 1.0000 | 1.0000 | 1.0000 |
| skill_heavy | 1.0000 | 1.0000 | 1.0000 |
| balanced | 1.0000 | 1.0000 | 1.0000 |
| moderate_threshold_0_45 | 0.9500 | 0.9441 | 0.9496 |
| semantic_heavy | 0.9500 | 0.9441 | 0.9496 |

## Limitations

- The evaluation set is small and synthetic; confidence intervals are wide.
- Labels describe lexical/résumé evidence, not job performance or hiring merit.
- Category compatibility is disabled because no validated cross-taxonomy
  mapping is available.
- Dictionary matching cannot understand every paraphrase or implicit skill.
- Thresholds and weights must be revalidated on an independently labelled
  production sample before deployment.
