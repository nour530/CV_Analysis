# AI review: CV evaluation and full rewrite

The CV builder's **AI review** panel does two things:

1. **Evaluates the CV with our own trained model** (runs on our server, free,
   nothing sent to an AI provider): 8 recruiter criteria scored 0–10 and an
   overall score 0–100. Each criterion also lists facts measured in the CV
   (for example "3 of 8 bullet points show a figure"), and plain facts such as
   the email or dates are checked directly.
2. **Rewrites the whole CV with Claude**: rebuilds it into a clean ATS
   structure, fixing misplaced fields such as a job title read as the name,
   and rewrites it to the recruiter standard. The user compares before and
   after for each section and accepts sections one by one or all at once. If
   the scoring model is trained, the panel shows the score before and after.

## The rubric (`app/services/cv_quality/rubric.py`)

| Criterion | 0 | 10 |
|---|---|---|
| Structure | one block, sections unclear | standard sections, expected order |
| Professional summary | none | 2–3 specific sentences: title, years, skills, a result |
| Achievements and impact | duties only | most bullets show a measurable result |
| Action language | vague, first person, clichés | strong, varied action verbs |
| Skills | none | specific hard skills backed by the experience |
| Clarity and concision | hard to read | crisp, consistent, error-free |
| Completeness | titles, employers, dates missing | complete throughout |
| Focus and relevance | scattered | clearly aimed at a target role |
| **Overall (0–100)** | likely rejected | strong (80+) |

## Our CV scoring model

**Training data.** No public dataset has CVs scored by recruiters, so we make
one. Claude grades real CVs against the rubric:

- the CVs are about 1,500 anonymised CVs from `data/raw/resumes/Resume.csv`,
  spread evenly across the 24 job categories;
- Claude (by default `claude-sonnet-5-5`) grades them through the Message
  Batches API, which is half price;
- the grades are saved to `data/cv_quality/labels.jsonl`, without the CV text.

**Model.** The model uses two kinds of input:

- the CV's wording: TF-IDF on words and word pairs, reduced to 120 dimensions;
- 25 measured signals (`features.py`), such as the share of bullets with a
  figure, an action verb, the first person or a vague opening, repeated verbs,
  clichés, whether roles have dates, titles and employers, and the number of
  sections and skills.

Two model types are compared, ridge regression and gradient boosting. The one
with the lower error is kept.

**Accuracy.** It is measured with 5-fold cross-validation: every CV is scored by
a model that never saw it, then compared with Claude's grade. For each
criterion the report gives:

- the mean error, compared with always guessing the average;
- the rank correlation with Claude's grades;
- the share of CVs scored within ±1 point (criteria) or ±10 (overall).

The report is in `data_analysis/outputs/cv_scorer_evaluation.md`.

**Limits.** The model learns Claude's judgement against the rubric, not real
hiring outcomes. The training CVs are English and US-based.

### Train it (needs `ANTHROPIC_API_KEY`)

```bash
cd ai-engine
venv/Scripts/python.exe data_analysis/07_label_cv_quality.py --dry-run   # shows the cost (about $15 with Sonnet 5.5)
venv/Scripts/python.exe data_analysis/07_label_cv_quality.py             # submits the batch, waits, saves labels
venv/Scripts/python.exe data_analysis/08_train_cv_scorer.py              # trains, evaluates, saves the model
```

The batch usually finishes within an hour (24 h at most). You can stop the
script and collect the results later with `--resume`. The trained model is
saved to `data/models/cv_quality_scorer.joblib`, and the API loads it on the
next start. Until then, the panel shows the measured facts only.

## The AI rewrite (`app/services/cv_quality/rewriter.py`)

- **Input:** the CV's full text as the editor holds it, plus the target job's
  skills when the builder was opened from a job.
- **Privacy:**
  - Email, phone, LinkedIn and website links are replaced by `[EMAIL]`,
    `[PHONE]`, `[LINKEDIN]` and `[WEBSITE]` before sending. The real values are
    put back from the user's CV.
  - It needs `ALLOW_REMOTE_LLM_ANALYSIS=true` on the server, the user's AI
    consent in Settings, and an `ANTHROPIC_API_KEY`.
  - Each rewrite counts against the per-user remote AI quota.
- **Output:** a structured JSON CV (schema-enforced) and a note per changed
  section.
- **Guardrails (per section):** a warning for figures that are not in the
  original, `[X%]` placeholders to fill in, and employers, schools, skills or
  certifications that the original does not mention. Nothing changes until the
  user accepts the section.
- **Writing standard:** the same rules as the item-by-item tips:
  - action + task + result, strong varied verbs and one tense per role;
  - no vague openings or clichés;
  - standard searchable terms;
  - a summary that opens with the title and years of experience;
  - the job ad's exact wording for skills the CV already shows.

Settings: `CV_AI_MODEL` (default `claude-opus-5-5`), `CV_AI_EFFORT` (`medium`),
`CV_AI_TIMEOUT_SECONDS` (180).

## API

| Method | Path | |
|---|---|---|
| POST | `/resumes/{id}/evaluation` | our model's scores, evidence and fact checks |
| POST | `/resumes/{id}/ai-rewrite` | `{job_id?}` → improved CV, per-section changes and warnings, score before/after |

Tests: `tests/test_cv_quality.py` covers training on synthetic graded CVs,
saving and loading the model, the endpoints, the consent gates and the
rewriter. The rewriter tests use a fake Claude client, so they make no API
calls.
