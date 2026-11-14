# JobMatch AI - Feature Engineering Report (Phase 2.4)

**Generated:** 2026-11-08T18:20:57+01:00

**Script:** `ai-engine/data_analysis/03_feature_engineering.py`

**Environment:** Python `3.12.8`, pandas `3.1.1`, NumPy `2.5.4`, scikit-learn `1.9.1`, spaCy `not_installed`

## 1. Executive summary

- **Measured:** built **1,541** canonical skills and **2,829** deterministic aliases from **6,217** skill-occupation rows.
- **Measured:** extracted features for **2,483** résumés and **10,461** job descriptions; source identifiers were preserved.
- **Measured:** **1,262** résumés (50.8%) and **8,029** jobs (76.8%) contain at least one dictionary skill.
- **Measured:** job titles were identified for **4,063** jobs (38.8%); the remaining titles were left empty instead of being invented.
- **Representation:** `clean_text` and `clean_description` are the semantic text fields; canonical skill columns and long-form match tables are the sparse/hybrid features.
- **Scope:** no embeddings, external API calls, model training, FastAPI endpoints, or Angular changes were made.
- **Validation:** all automated checks passed.

## 2. Inputs and outputs

| Input | Rows | Columns |
| --- | --- | --- |
| `resumes_cleaned.csv` | 2,483 | 14 |
| `job_roles_cleaned.csv` | 10,461 | 12 |
| `software_skills_cleaned.csv` | 6,217 | 17 |

| Output file | Size |
| --- | --- |
| `ai-engine/data/features/resume_features.csv` | 24.65 MB |
| `ai-engine/data/features/job_features.csv` | 38.55 MB |
| `ai-engine/data/features/skills_dictionary.csv` | 588.09 KB |
| `ai-engine/data/features/skill_aliases.csv` | 419.20 KB |
| `ai-engine/data/features/skill_occupation_map.csv` | 1.47 MB |
| `ai-engine/data/features/resume_skill_matches.csv` | 1.12 MB |
| `ai-engine/data/features/job_skill_matches.csv` | 10.50 MB |
| `ai-engine/data/features/resume_sections.csv` | 14.87 MB |
| `ai-engine/data/features/job_sections.csv` | 20.89 MB |
| `ai-engine/data/features/job_keywords.csv` | 5.32 MB |

## 3. Skills dictionary

| Metric | Value |
| --- | --- |
| Canonical skills | 1,541 |
| Aliases | 2,829 |
| Match-enabled aliases | 2,829 |
| Ambiguous aliases | 0 |
| O*NET occupations | 36 |
| Skill-occupation relationships | 6,217 |
| Hot-technology skills | 165 |
| In-demand skills | 156 |

| Skill category | Canonical skills |
| --- | --- |
| programming_language | 92 |
| framework_library | 100 |
| database | 172 |
| tool_technology | 1,177 |

Aliases are generated only from the dictionary entry itself: the cleaned surface form, a parenthetical expansion/acronym, and a punctuation-light form. Aliases that map to more than one canonical skill are marked ambiguous and excluded from matching.

| Alias type | Aliases |
| --- | --- |
| canonical_surface | 1,541 |
| compact_punctuation | 1,280 |
| expanded_parenthetical | 4 |
| parenthetical_acronym | 4 |

## 4. Résumé features

| Metric | Value |
| --- | --- |
| Rows | 2,483 |
| Rows with skills | 1,262 (50.8%) |
| Mean skills per résumé | 1.20 |
| Maximum skills in one résumé | 21 |
| Rows with programming languages | 434 |
| Rows with frameworks/libraries | 63 |
| Rows with databases | 158 |
| Rows with tools/technologies | 1,056 |
| Education coverage | 99.6% |
| Experience coverage | 100.0% |

Education and experience fields state their evidence source. `explicit_section` means the value came from a detected section; `keyword_scan` means a degree/institution or years phrase was found without a section; `experience_entry_count_inferred` is explicitly labelled as inferred.

| Top résumé skills | Résumés containing |
| --- | --- |
| Microsoft Word | 242 |
| C | 163 |
| Microsoft Excel | 158 |
| Chef | 127 |
| Facebook | 112 |
| Linux | 86 |
| Adobe Photoshop | 71 |
| R | 70 |
| Microsoft Outlook | 65 |
| C++ | 64 |
| UNIX | 61 |
| LinkedIn | 59 |
| JavaScript | 56 |
| Route | 55 |
| Google Analytics | 52 |
| Microsoft Access | 49 |
| Microsoft PowerPoint | 49 |
| Microsoft Project | 48 |
| Microsoft Windows | 46 |
| PHP | 44 |

## 5. Job features

| Metric | Value |
| --- | --- |
| Rows | 10,461 |
| Job titles identified | 4,063 (38.8%) |
| Rows with required skills | 7,038 |
| Rows with preferred skills | 1,525 |
| Rows with other mentioned skills | 1,404 |
| Rows with any dictionary skill | 8,029 |
| Responsibilities coverage | 88.1% |
| Qualifications coverage | 93.1% |
| Keyword extraction method | `scikit-learn_tfidf` |
| Rows with keywords | 10,461 |

| Job title source | Rows |
| --- | --- |
| cue_phrase | 2,309 |
| explicit_field | 694 |
| leading_text | 1,060 |
| not_identified | 6,398 |

| Requirement type | Skill matches |
| --- | --- |
| mentioned | 2,445 |
| preferred | 2,772 |
| required | 20,758 |

| Top required skills | Job mentions |
| --- | --- |
| JavaScript | 2,358 |
| jQuery | 1,151 |
| C# | 1,080 |
| Linux | 1,074 |
| Python | 938 |
| Git | 798 |
| MySQL | 796 |
| C | 702 |
| PHP | 685 |
| UNIX | 639 |
| AJAX | 603 |
| C++ | 581 |
| Bootstrap | 423 |
| Node.js | 393 |
| NoSQL | 360 |
| React | 351 |
| Perl | 303 |
| Swift | 299 |
| JUnit | 255 |
| MongoDB | 255 |

## 6. Extraction methods

### Résumés

- Parsed the cleaned text with a heading dictionary and extracted section text with character offsets. Sections are emitted in `resume_sections.csv` for traceability.
- Matched skills against the reusable dictionary with case-insensitive, boundary-aware alias matching. Short/ambiguous aliases require nearby programming context.
- Aggregated canonical skills into technical skills, programming languages, frameworks/libraries, databases, and tools/technologies.
- Extracted degree and institution mentions only from explicit education text; when no education section existed, only strong degree keywords were scanned.
- Extracted explicitly stated experience years and counted date ranges as an explicitly labelled inferred entry count.

### Job descriptions

- Extracted titles only from explicit title/role fields, hiring cue phrases, or credible leading text; no title was invented when evidence was insufficient.
- Split descriptions into headings such as responsibilities, requirements, qualifications, preferred skills, education, and benefits, preserving offsets in `job_sections.csv`.
- Classified skill mentions as `required`, `preferred`, or `mentioned` using the nearest cue phrase and the containing section.
- Extracted responsibilities and qualifications from explicit sections; otherwise used cue-scored sentences as a labelled fallback.
- Generated lexical keywords with TF-IDF and stored term/score rows in `job_keywords.csv`.

## 7. Feature representation

- **Sentence Transformers / semantic similarity:** use `clean_text` for résumés and `clean_description` for jobs. These are unchanged cleaned text fields.
- **Scikit-learn sparse features:** use the long-form `resume_skill_matches.csv` and `job_skill_matches.csv` tables, or the semicolon-separated canonical skill columns in the feature tables.
- **Hybrid matching:** combine semantic similarity on clean text with skill overlap, required/preferred weighting, and dictionary categories.
- **Future LLM analysis:** `resume_features.csv` and `job_features.csv` retain source IDs, clean text, structured skills, sections, education/experience evidence, and keywords.
- **Not generated:** embeddings, vector indexes, model artefacts, or external API calls.

## 8. Validation

**40/40** automated checks passed.

| Check | Result | Detail |
| --- | --- | --- |
| Raw file unchanged: Resume.csv | PASS | SHA-256 before=d44a8f69a51b1240..., after=d44a8f69a51b1240... |
| Raw file unchanged: job_roles_combined_ready_10461.csv | PASS | SHA-256 before=2171dae4f298c86d..., after=2171dae4f298c86d... |
| Raw file unchanged: software_skills_15.csv | PASS | SHA-256 before=d77e682526b78aa6..., after=d77e682526b78aa6... |
| Processed file unchanged: resumes_cleaned.csv | PASS | SHA-256 before=1a269aeb76bea71b..., after=1a269aeb76bea71b... |
| Processed file unchanged: job_roles_cleaned.csv | PASS | SHA-256 before=6a75dd5b829d02eb..., after=6a75dd5b829d02eb... |
| Processed file unchanged: software_skills_cleaned.csv | PASS | SHA-256 before=19f92d6e5bf5dcd9..., after=19f92d6e5bf5dcd9... |
| Résumé feature IDs exactly cover processed résumé IDs | PASS | processed=2483, features=2483, missing=0, extra=0 |
| Résumé feature IDs are unique | PASS | rows=2483, unique=2483 |
| Résumé feature count equals processed count | PASS | processed=2483, features=2483 |
| Résumé clean text is present | PASS | empty=0 |
| Job source row indices are preserved in order | PASS | processed=10461, features=10461 |
| Job feature IDs are unique | PASS | rows=10461, unique=10461 |
| Job feature count equals processed count | PASS | processed=10461, features=10461 |
| Job clean descriptions are present | PASS | empty=0 |
| Skills occupation map covers every processed skill row | PASS | processed=6217, mapped=6217 |
| Canonical skill IDs are unique | PASS | rows=1541, unique=1541 |
| Skill categories use the documented values | PASS | categories=['database', 'framework_library', 'programming_language', 'tool_technology'] |
| Ambiguous aliases are excluded from matching | PASS | ambiguous=0, ambiguous_enabled=0 |
| Every résumé skill match exists in the skills dictionary | PASS | matched=249, unknown=0 |
| Every job skill match exists in the skills dictionary | PASS | matched=363, unknown=0 |
| Requirement types use documented values | PASS | types=['mentioned', 'preferred', 'required'] |
| Alias normalization matches a compact punctuation alias | PASS | alias=24sevenofficeproject, canonical=24SevenOffice Project |
| Alias normalization matches a parenthetical acronym | PASS | alias=AEM, canonical=Adobe Experience Manager (AEM) |
| Alias normalization is case-insensitive | PASS | alias=24SevenOffice Project, canonical=24SevenOffice Project |
| Résumé section offsets are valid | PASS | invalid=0, sections=18568 |
| Job section offsets are valid | PASS | invalid=0, sections=33966 |
| Every résumé has at least one extracted section | PASS | covered=2483, processed=2483 |
| Every job has at least one extracted section | PASS | covered=10461, processed=10461 |
| Job keywords are traceable to job rows | PASS | keyword_ranges=10461, jobs=10461 |
| Output file exists: resume_features.csv | PASS | path=ai-engine/data/features/resume_features.csv, size=24.65 MB |
| Output file exists: job_features.csv | PASS | path=ai-engine/data/features/job_features.csv, size=38.55 MB |
| Output file exists: skills_dictionary.csv | PASS | path=ai-engine/data/features/skills_dictionary.csv, size=588.09 KB |
| Output file exists: skill_aliases.csv | PASS | path=ai-engine/data/features/skill_aliases.csv, size=419.20 KB |
| Output file exists: skill_occupation_map.csv | PASS | path=ai-engine/data/features/skill_occupation_map.csv, size=1.47 MB |
| Output file exists: resume_skill_matches.csv | PASS | path=ai-engine/data/features/resume_skill_matches.csv, size=1.12 MB |
| Output file exists: job_skill_matches.csv | PASS | path=ai-engine/data/features/job_skill_matches.csv, size=10.50 MB |
| Output file exists: resume_sections.csv | PASS | path=ai-engine/data/features/resume_sections.csv, size=14.87 MB |
| Output file exists: job_sections.csv | PASS | path=ai-engine/data/features/job_sections.csv, size=20.89 MB |
| Output file exists: job_keywords.csv | PASS | path=ai-engine/data/features/job_keywords.csv, size=5.32 MB |
| Alias normalization tests passed | PASS | tests=3, failed=0 |

| Alias test | Alias | Canonical | Result |
| --- | --- | --- | --- |
| compact punctuation alias | 24sevenofficeproject | 24SevenOffice Project | PASS |
| parenthetical acronym alias | AEM | Adobe Experience Manager (AEM) | PASS |
| case-insensitive canonical alias | 24SevenOffice Project | 24SevenOffice Project | PASS |

**All automated validation checks passed.**

## 9. Limitations

- Skill matching is limited to dictionary names and deterministic aliases. Vendor-qualified entries (for example, vendor-prefixed product names) can reduce recall for shorter common names. No arbitrary synonym was added.
- Skill-category assignment is a documented heuristic based on dictionary names, O*NET element names, and explicit language/framework/database rules. It is not an official O*NET classification.
- Section, title, requirement, education, and experience extraction are rule-based and may miss unheaded or unusually formatted content.
- Education and experience values are only extracted when stated; the date-range count is inferred and explicitly labelled.
- TF-IDF keywords are lexical, not semantic. They do not replace embeddings or LLM analysis.
- Duplicate résumés and the class imbalance documented in earlier phases remain unresolved and must be handled before model training and train/test splitting.
- spaCy was not used in this phase because no trained pipeline/model was required for the deterministic rules; the cleaned text is ready for a spaCy pipeline later.

## 10. Recommendations for NLP and embeddings

1. Use `clean_text` and `clean_description` as the canonical semantic text inputs for Sentence Transformers; do not embed raw HTML or duplicated feature text.
2. Build a sparse skill matrix by pivoting the long-form match tables; weight required skills above preferred/mentioned skills for job matching.
3. Start with a hybrid score combining semantic similarity and dictionary-skill overlap, then evaluate it against a human-labelled relevance set.
4. Review the ambiguous alias report and add only human-approved aliases; do not use ambiguous aliases automatically.
5. Resolve duplicate résumés and define the résumé-category scope before train/test split to avoid leakage and label noise.
6. Plan for class weighting or stratified evaluation because job-role labels are highly imbalanced.
7. Use the section and keyword tables as retrieval context for a future LLM, but keep source IDs and evidence fields for traceability.
8. Do not use `resume_id`, `job_id`, or `source_row_index` as model features; they are identifiers only.

## 11. Output files

| File | Size |
| --- | --- |
| `ai-engine/data/features/resume_features.csv` | 24.65 MB |
| `ai-engine/data/features/job_features.csv` | 38.55 MB |
| `ai-engine/data/features/skills_dictionary.csv` | 588.09 KB |
| `ai-engine/data/features/skill_aliases.csv` | 419.20 KB |
| `ai-engine/data/features/skill_occupation_map.csv` | 1.47 MB |
| `ai-engine/data/features/resume_skill_matches.csv` | 1.12 MB |
| `ai-engine/data/features/job_skill_matches.csv` | 10.50 MB |
| `ai-engine/data/features/resume_sections.csv` | 14.87 MB |
| `ai-engine/data/features/job_sections.csv` | 20.89 MB |
| `ai-engine/data/features/job_keywords.csv` | 5.32 MB |

Machine-readable metadata, hashes, settings, and validation results are in `ai-engine/data/features/feature_manifest.json`.
