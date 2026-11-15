# Career fields beyond IT (ESCO)

The original training data (Resume.csv and the job dataset) is mostly IT. To
cover the jobs most in demand in Tunisia (health, finance, sales, logistics,
hospitality, manufacturing, trades, education, agriculture and more), the app
uses **ESCO**, the European Commission's multilingual classification of
occupations and skills.

> This service uses the ESCO classification of the European Commission.
> The data in `data/esco/` is an adapted subset (English and French labels,
> grouped into 14 career fields); it is not an official ESCO version.

## Data

`data_analysis/05_import_esco.py` downloads all occupations and skills from the
public ESCO API (no key needed) and writes:

| File | Contents |
|---|---|
| `data/esco/esco_occupations.csv` | ~3,000 occupations: ISCO-based code, EN/FR titles and synonyms, descriptions, essential and optional skills |
| `data/esco/esco_skills.csv` | ~13,500 skills and knowledge items: EN/FR labels and synonyms, type, reuse level |
| `data/esco/domains.json` | 14 career fields defined by ISCO-08 code prefixes |
| `data/esco/ATTRIBUTION.md` | licence and attribution |

Raw API pages are cached in `data/raw/esco/` (git-ignored), so re-running is
quick. A few records the API cannot return (HTTP 500) are skipped.

```bash
venv/Scripts/python.exe data_analysis/05_import_esco.py
```

Each occupation goes to the field with the **most specific** matching ISCO
prefix (for example 431 "numerical clerks" goes to finance, not to business,
which covers 41; 3434 "chefs" goes to hospitality, not to creative, which covers 343).

## What it powers

**Field classifier** (`app/services/career/classifier.py`). TF-IDF on word
1–2-grams and character 3–5-grams, with logistic regression. It predicts the
top 3 fields for a CV. It is trained on two sources:

- every ESCO occupation (title, synonyms, description and essential skills),
  in English and French;
- about 2,000 labelled real CVs from the research dataset
  (`data/raw/resumes/Resume.csv`). Their categories are mapped to fields;
  categories that span several fields are left out;
- about 420 IT job descriptions (`data/raw/job_descriptions/`), a fixed sample
  balanced across 6 role types. They add developer, data and cloud vocabulary,
  because the CV dataset's IT profiles are mostly support and management roles.

Trained on ESCO alone, the model mistakes generic CV wording (manage, team,
clients, reports) for business roles. Adding real CVs fixes most of that:

| Test (14 fields) | Accuracy | Correct field in top 3 |
|---|---:|---:|
| ESCO occupations, held out (grouped 5-fold) | 83% | – |
| 2,001 real CVs, model trained on ESCO only | 34% | 71% |
| **2,001 real CVs, full model (5-fold, each CV unseen)** | **72%** | **92%** |
| 12 short French and English CVs for Tunisian roles, one per field | 12/12 | – |

Strongest fields: accounting 95%, IT 90%, finance 88%, chefs 81%. Weakest:
BPO / call centre 23%, arts 39%, agriculture 43% (agriculture has only 63 CVs).
Full table: `data_analysis/outputs/domain_classifier_evaluation.md`.

Retrain and re-evaluate after re-importing ESCO:

```bash
venv/Scripts/python.exe data_analysis/06_train_domain_classifier.py
```

This writes `data_analysis/outputs/domain_classifier_evaluation.md` and saves
the model to `data/models/domain_classifier.joblib`, which the API loads. If
the file is missing, or doesn't match the current ESCO data or scikit-learn
version, the API trains an ESCO-only model the first time it is needed instead.

**Career advisor** (`app/services/career/paths.py`).
- Suggested roles score 55% on text similarity between the CV and the
  occupation, and 45% on coverage of the occupation's essential skills. Coverage
  is counted against at least 8 skills, so roles that list only one or two skills
  don't win too easily.
- The skill gap for a target role lists the skills the CV shows and the ones it
  is missing: specific skills first, soft skills last.

**Career goal.** In Settings, users choose a field and a target role. Role
suggestions come from ESCO, in English or French. The dashboard's "Your career
direction" card and the assistant both use the goal.

## API

| Method | Path | Auth | |
|---|---|---|---|
| GET | `/career/domains?lang=en\|fr` | public | fields with occupation counts |
| GET | `/career/occupations?q=&domain=&lang=` | public | occupation search |
| GET | `/career/insights` | user | detected fields, target-role fit, suggested roles |

Tests: `tests/test_career.py` runs against a small fixture in `tests/fixtures/esco/`.
