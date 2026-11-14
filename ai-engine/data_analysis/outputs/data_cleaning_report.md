# JobMatch AI - Data Cleaning Report (Phase 2.3)

**Generated:** 2026-11-08T17:51:30+01:00

This report summarises the measured results of the cleaning pipeline. Detailed transformation records, flags, and review decisions are in `ai-engine/data/audit/cleaning_log.md`.

## 1. Executive summary

- **Measured:** 19,162 raw records were read and 19,161 modeling-ready records were written across the three datasets; 1 invalid résumé record was excluded.
- **Measured:** all 2,484 résumé HTML records were parsed; 0 clean résumé records still contain HTML markup.
- **Measured:** 125 job descriptions are shorter than 300 characters and were retained with review flags; all six job-role labels and their distribution are preserved.
- **Measured:** all 6,217 skill rows were retained, including 4,676 rows that repeat a workplace example across occupations.
- **Measured:** exact duplicate résumés were preserved and audited; no duplicate, short description, repeated skill, or category mapping was removed automatically.
- **Validation:** all automated checks passed.

## 2. Before/after dataset statistics

| Dataset | Raw rows | Processed rows | Excluded rows | Raw columns | Processed columns |
| --- | --- | --- | --- | --- | --- |
| Resume.csv | 2,484 | 2,483 | 1 | 4 | 14 |
| job_roles_combined_ready_10461.csv | 10,461 | 10,461 | 0 | 2 | 12 |
| software_skills_15.csv | 6,217 | 6,217 | 0 | 7 | 17 |

Résumé rows decrease by exactly the number of invalid empty records. Job-role and software-skills row counts are unchanged.

## 3. Cleaning operations

### Résumés

- Parsed `Resume_html` with a standard-library HTML parser, skipped script/style content, decoded HTML entities, and extracted visible text.
- Applied NFC, replaced non-breaking/Unicode spaces, removed zero-width/BOM/soft-hyphen artefacts, collapsed repeated whitespace, and trimmed.
- Added `clean_text` and related flags while preserving `ID`, `Resume_str`, `Resume_html`, and `Category`.
- Excluded 1 empty record from the modeling-ready file and wrote it to the excluded-records audit.
- Detected 2 exact `Resume_str` duplicate groups (4 rows) and retained them for human review.

### Job roles

- Normalised descriptions with NFC and whitespace rules while preserving punctuation, accents, and meaningful symbols.
- Canonicalised labels case-insensitively against the six documented role categories; original labels were retained.
- Flagged 125 descriptions shorter than 300 characters; none were deleted.
- Found 0 exact duplicate description groups; none were deleted.

### Software skills

- Normalised `Title`, `Workplace Example`, `Element Name`, and Y/N flags while retaining all original columns.
- Preserved all 6,217 rows and 6,217 unique occupation-example pairs.
- Found 0 genuinely identical full-row groups; none were removed.

## 4. Excluded records

| Record ID | Source row index | Category | Reason |
| --- | --- | --- | --- |
| 12632728 | 656 | BUSINESS-DEVELOPMENT | empty after HTML extraction and fallback |

The full excluded record, including original text fields, is in `ai-engine/data/audit/excluded_records.csv`.

## 5. Duplicate analysis

| Dataset/basis | Groups | Rows involved | Action |
| --- | --- | --- | --- |
| Résumé: exact `Resume_str` | 2 | 4 | Flagged and audited; retained |
| Résumé: exact `clean_text` | 2 | 4 | Flagged and audited; retained |
| Job descriptions: exact text | 0 | 0 | Audited; retained |
| Skills: genuinely identical full rows | 0 | 0 | Audited; retained |
| Skills: repeated workplace examples | Many-to-many | 4,676 | Valid relationships; retained |

Duplicate records are listed in `ai-engine/data/audit/duplicate_records.csv`. Repeated workplace examples are intentionally not treated as duplicate records.

## 6. Data-quality improvements

| Metric | Before | After |
| --- | --- | --- |
| Résumé records with HTML markup | 2,484 | 0 |
| Résumé records with non-breaking spaces | 866 | 0 |
| Résumé records with multiple spaces | 2,484 | 0 |
| Empty résumé records | 1 | 1 excluded |
| Job descriptions with multiple spaces | 0 | 0 |
| Skills text fields with multiple spaces | 0 | 0 |

- **Unicode preserved:** 1,574 résumé records and 3,779 job-description records still contain meaningful non-ASCII characters after cleaning.
- **Labels preserved:** six job-role labels remain with the same distribution; 0 label strings changed during canonicalisation.
- **Skills preserved:** 6,217 rows, 1,541 unique workplace examples, and 6,217 occupation-example pairs remain.

## 7. Validation results

| Check | Result | Detail |
| --- | --- | --- |
| Raw file unchanged: Resume.csv | PASS | size=53.73 MB; SHA-256 before=d44a8f69a51b1240..., after=d44a8f69a51b1240... |
| Raw file unchanged: job_roles_combined_ready_10461.csv | PASS | size=19.45 MB; SHA-256 before=2171dae4f298c86d..., after=2171dae4f298c86d... |
| Raw file unchanged: software_skills_15.csv | PASS | size=666.09 KB; SHA-256 before=d77e682526b78aa6..., after=d77e682526b78aa6... |
| Résumé count equals raw minus excluded | PASS | raw=2484, excluded=1, processed=2483 |
| Résumé IDs are preserved | PASS | processed IDs=2483, excluded IDs=[12632728], missing=[] |
| Excluded résumés are exactly the empty records | PASS | excluded empty=1, processed non-empty=2483 |
| Résumé clean text contains no HTML markup | PASS | records with markup=0 |
| Résumé clean text is NFC-normalised | PASS | all processed clean_text values equal their NFC form |
| Meaningful accented Unicode characters remain | PASS | accented characters present: â, ç, è, é, ê, ï, ñ, ô, ö, ü |
| Résumé whitespace artefacts are removed | PASS | no double spaces and no non-breaking spaces in clean_text |
| Exact duplicate résumés are preserved, not deleted | PASS | raw duplicate rows=4, processed duplicate rows=4 |
| Job record count is preserved | PASS | raw=10461, processed=10461 |
| All six job-role labels are preserved | PASS | labels=['Cybersecurity', 'Data Analytics', 'Data Science', 'Network Engineering', 'Software Development', 'UI/UX Design'] |
| Job-role distribution is unchanged | PASS | raw={'Cybersecurity': 630, 'Data Analytics': 272, 'Data Science': 254, 'Network Engineering': 796, 'Software Development': 8359, 'UI/UX Design': 150}, clean={'Cybersecurity': 630,... |
| No invalid job labels | PASS | invalid labels=0 |
| Short job descriptions are retained | PASS | short descriptions flagged=125 |
| Job clean descriptions are NFC-normalised | PASS | all clean_description values equal their NFC form |
| Skills record count is preserved | PASS | raw=6217, processed=6217 |
| Valid many-to-many skill relationships remain intact | PASS | unique workplace examples=1541, repeated example rows=4676, unique occupation-example pairs=6217 |
| No genuine identical skill rows were removed | PASS | raw=0, processed=0 |
| Skill Y/N flags are valid | PASS | invalid Hot Technology=0, invalid In Demand=0 |
| Skills clean text columns are NFC-normalised | PASS | all cleaned skill text fields equal their NFC form |
| Output file exists: resumes_cleaned.csv | PASS | path=ai-engine/data/processed/resumes_cleaned.csv, size=67.90 MB |
| Output file exists: job_roles_cleaned.csv | PASS | path=ai-engine/data/processed/job_roles_cleaned.csv, size=39.33 MB |
| Output file exists: software_skills_cleaned.csv | PASS | path=ai-engine/data/processed/software_skills_cleaned.csv, size=1.33 MB |
| Output file exists: excluded_records.csv | PASS | path=ai-engine/data/audit/excluded_records.csv, size=1.40 KB |
| Output file exists: duplicate_records.csv | PASS | path=ai-engine/data/audit/duplicate_records.csv, size=2.18 KB |

**All automated validation checks passed.**

## 8. Remaining limitations

- 6 résumé records and 5 job records still contain U+FFFD replacement characters; these indicate source encoding loss and were not guessed.
- 5 résumé records and 41 job records still contain private-use Unicode glyphs, typically PDF bullet symbols.
- Exact duplicates are detected but not resolved; near-duplicates and semantic duplicates were not analysed.
- The 24 résumé categories remain unmapped to the six job-role labels.
- No stemming, lemmatisation, stopword removal, lowercasing of descriptions, language detection, spelling correction, or feature extraction was performed.
- HTML extraction preserves visible text but not layout; `Resume_html` and `Resume_str` remain available as original fields.

## 9. Recommendations for feature engineering

1. Use `clean_text` for résumé features and `clean_description` for job-description features; keep `Resume_str` and `Resume_html` only for traceability.
2. Decide the duplicate policy before train/test splitting to avoid leakage: duplicate résumés should be grouped into the same split or removed after human review.
3. Decide the résumé category scope before modelling. Do not map the 24 categories to the six job labels without an explicit, documented mapping.
4. Use `label_clean` for job-role targets and preserve the six-class distribution; plan for class weighting or stratified splits because Software Development is 79.9% of rows.
5. Review `short_description_flag` rows before removing or down-weighting them; some may still carry useful signal.
6. Use `Title_clean`, `Workplace Example_clean`, and `Element Name_clean` for skill normalisation, while preserving the many-to-many occupation-example structure.
7. Treat replacement characters and private-use glyphs as a documented limitation; decide whether to repair them at source or ignore them during tokenisation.
8. Do not use `ID` or `source_row_index` as model features; they are identifiers only.

## 10. Output files

| File | Size |
| --- | --- |
| `ai-engine/data/processed/resumes_cleaned.csv` | 67.90 MB |
| `ai-engine/data/processed/job_roles_cleaned.csv` | 39.33 MB |
| `ai-engine/data/processed/software_skills_cleaned.csv` | 1.33 MB |
| `ai-engine/data/audit/excluded_records.csv` | 1.40 KB |
| `ai-engine/data/audit/duplicate_records.csv` | 2.18 KB |

Detailed decisions and transformation records are in `ai-engine/data/audit/cleaning_log.md`.
