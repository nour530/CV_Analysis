# JobMatch AI - Data Cleaning Log (Phase 2.3)

**Generated:** 2026-11-08T17:51:30+01:00

**Script:** `ai-engine/data_analysis/02_data_cleaning.py`

**Python:** `3.12.8` | **pandas:** `3.1.1`

## 1. Scope and data-safety decisions

- Raw files under `ai-engine/data/raw/` were opened read-only and never overwritten.
- SHA-256 fingerprints were recorded before and after the run and compared.
- Original columns and identifiers are retained in every processed file.
- No duplicate résumés were deleted; they are flagged and written to an audit file.
- No short job descriptions were deleted; they are flagged for review.
- No résumé category was mapped to the six job-role labels.
- Repeated workplace examples in the skills dictionary were retained.
- No machine-learning model, feature extraction, or job-matching logic was run.

## 2. Environment and inputs

| Raw dataset | Size | SHA-256 prefix |
| --- | --- | --- |
| Resume.csv | 53.73 MB | `d44a8f69a51b1240...` |
| job_roles_combined_ready_10461.csv | 19.45 MB | `2171dae4f298c86d...` |
| software_skills_15.csv | 666.09 KB | `d77e682526b78aa6...` |

## 3. Before/after record counts

| Dataset | Raw rows | Processed rows | Excluded rows | Raw columns | Processed columns |
| --- | --- | --- | --- | --- | --- |
| Resume.csv | 2,484 | 2,483 | 1 | 4 | 14 |
| job_roles_combined_ready_10461.csv | 10,461 | 10,461 | 0 | 2 | 12 |
| software_skills_15.csv | 6,217 | 6,217 | 0 | 7 | 17 |

**Résumé exclusion rule:** a record is excluded from the modeling-ready résumé file only when both HTML-derived text and the `Resume_str` fallback are empty after normalisation. The excluded record remains available in the raw file and in the excluded-records audit.

## 4. Résumé cleaning

### Transformations applied

1. **HTML parsing:** every `Resume_html` value was parsed with a standard-library `HTMLParser`. Script/style/noscript/template content was skipped, block elements were converted to word boundaries, and only visible text was collected.
2. **Entity decoding:** HTML entities were decoded with `html.unescape`, then parsed again so encoded markup did not leak into the output.
3. **Unicode normalisation:** all clean text was converted to Unicode NFC.
4. **Whitespace normalisation:** non-breaking/figure/narrow spaces were converted to ordinary spaces; zero-width space, BOM, and soft hyphen artefacts were removed; line breaks and control characters were converted to spaces; repeated whitespace was collapsed and the result trimmed.
5. **Fallback:** when HTML extraction produced no text but `Resume_str` did, the normalised `Resume_str` was used and `text_source` records the fallback.
6. **Empty-record handling:** records with no content after extraction and fallback were excluded from `resumes_cleaned.csv` and written to `excluded_records.csv`.
7. **Duplicate handling:** exact duplicates were detected on both raw `Resume_str` and `clean_text`, flagged in the processed file, and exported to `duplicate_records.csv`. No duplicate was removed.
8. **Preservation:** `ID`, `Resume_str`, `Resume_html`, and `Category` were kept unchanged; the cleaned representation is an added column.

### Measured results

| Metric | Value |
| --- | --- |
| Raw records | 2,484 |
| Processed records | 2,483 |
| Excluded records | 1 |
| HTML records parsed | 2,484 |
| HTML parse errors | 0 |
| Script/style tags found | 0 |
| HTML entity references found | 5,596 |
| Raw records with HTML markup | 2,484 |
| Clean records with HTML markup | 0 |
| Raw records with non-breaking spaces | 866 |
| Clean records with non-breaking spaces | 0 |
| Raw records with multiple spaces | 2,484 |
| Clean records with multiple spaces | 0 |
| Raw records with non-ASCII characters | 1,929 |
| Clean records with non-ASCII characters | 1,574 |
| Raw records with U+FFFD replacement char | 6 |
| Clean records with U+FFFD replacement char | 6 |
| Raw records with private-use chars | 5 |
| Clean records with private-use chars | 5 |
| Clean text min length | 688 |
| Clean text median length | 5543 |
| Clean text mean length | 5928 |
| Clean text max length | 35,215 |
| Text source counts | Resume_html: 2,483, empty: 1 |
| Category counts | BUSINESS-DEVELOPMENT: 120, INFORMATION-TECHNOLOGY: 120, ACCOUNTANT: 118, ADVOCATE: 118, CHEF: 118, ENGINEERING: 118, FINANCE: 118, AVIATION: 117, FITNESS: 117, SALES: 116, BANKING: 115, CONSULTANT: 115, HEALTHCARE: 115, CONSTRUCTION: 112, PUBLIC-RELATIONS: 111, HR: 110, DESIGNER: 107, ARTS: 103, TEACHER: 102, APPAREL: 97, DIGITAL-MEDIA: 96, AGRICULTURE: 63, AUTOMOBILE: 36, BPO: 22 |

### Excluded résumé records

| Record ID | Source row index | Category | Reason | Clean length |
| --- | --- | --- | --- | --- |
| 12632728 | 656 | BUSINESS-DEVELOPMENT | empty after HTML extraction and fallback | 0 |

### Duplicate résumé analysis

| Basis | Groups | Rows involved | Deletion |
| --- | --- | --- | --- |
| Exact `Resume_str` | 2 | 4 | None (flagged for human review) |
| Exact `clean_text` | 2 | 4 | None (flagged for human review) |

| Basis | Group | Record ID | Source row | Category | Raw length | Clean length |
| --- | --- | --- | --- | --- | --- | --- |
| exact_Resume_str | RESUME_STR_DUP_001 | 19147603 | 1490 | FINANCE | 5210 | 4680 |
| exact_Resume_str | RESUME_STR_DUP_001 | 28398216 | 1509 | FINANCE | 5210 | 4680 |
| exact_Resume_str | RESUME_STR_DUP_002 | 16850314 | 2444 | AVIATION | 5236 | 4978 |
| exact_Resume_str | RESUME_STR_DUP_002 | 37473139 | 2483 | AVIATION | 5236 | 4978 |
| exact_clean_text | RESUME_CLEAN_DUP_001 | 19147603 | 1490 | FINANCE | 5210 | 4680 |
| exact_clean_text | RESUME_CLEAN_DUP_001 | 28398216 | 1509 | FINANCE | 5210 | 4680 |
| exact_clean_text | RESUME_CLEAN_DUP_002 | 16850314 | 2444 | AVIATION | 5236 | 4978 |
| exact_clean_text | RESUME_CLEAN_DUP_002 | 37473139 | 2483 | AVIATION | 5236 | 4978 |

### Columns added to the résumé output

| Column | Purpose |
| --- | --- |
| `source_row_index` | Zero-based position of the record in the raw CSV. |
| `clean_text` | Visible text extracted from Resume_html, NFC-normalised and whitespace-normalised. |
| `clean_text_length` | Character length of clean_text. |
| `text_source` | Resume_html, Resume_str_fallback, or empty. |
| `is_duplicate_resume_str` | True when Resume_str exactly matches another row. |
| `duplicate_group_id` | Group identifier for exact Resume_str duplicates. |
| `is_duplicate_clean_text` | True when clean_text exactly matches another row. |
| `clean_duplicate_group_id` | Group identifier for exact clean_text duplicates. |
| `has_replacement_char` | True when clean_text contains U+FFFD replacement characters. |
| `has_private_use_char` | True when clean_text contains private-use Unicode characters. |

## 5. Job-role cleaning

### Transformations applied

1. **Description normalisation:** NFC, non-breaking-space replacement, invisible character removal, whitespace collapsing, and trimming, using the same function as the résumé text.
2. **Label normalisation:** labels were whitespace-normalised, NFC-normalised, and mapped case-insensitively to the six documented role categories. The original label remains in `label`; the canonical value is in `label_clean`, and `label_changed` records whether the string changed.
3. **Review flags:** descriptions shorter than 300 characters are flagged in `short_description_flag`; empty descriptions are flagged in `is_empty_description`; labels outside the six categories are flagged in `is_valid_label`.
4. **Duplicate detection:** exact duplicate job descriptions were detected for audit but no description was removed.

### Measured results

| Metric | Raw | Clean |
| --- | --- | --- |
| Records | 10,461 | 10,461 |
| Empty descriptions | 0 | 0 |
| Descriptions under 300 characters | 125 | 125 |
| Records with multiple spaces | 0 | 0 |
| Records with non-ASCII characters | 3,804 | 3,779 |
| Records with U+FFFD replacement char | 5 | 5 |
| Records with private-use chars | 41 | 41 |
| Min description length | 200 | 200 |
| Median description length | 1604 | 1604 |
| Mean description length | 1921 | 1921 |
| Max description length | 16,908 | 16,908 |

### Label preservation

| Label | Raw count | Clean count |
| --- | --- | --- |
| Cybersecurity | 630 | 630 |
| Data Analytics | 272 | 272 |
| Data Science | 254 | 254 |
| Network Engineering | 796 | 796 |
| Software Development | 8,359 | 8,359 |
| UI/UX Design | 150 | 150 |

- Labels changed by normalisation: **0**.
- Invalid labels after normalisation: **0**.
- Exact duplicate job-description groups: **0** (0 rows). None were deleted.

### Short descriptions flagged for review

**125** cleaned descriptions are shorter than 300 characters. They remain in the processed file. First 10 examples:

| Source row | Label | Raw length | Clean length | Preview |
| --- | --- | --- | --- | --- |
| 7699 | Software Development | 200 | 200 | Client is looking for someone with experience in SQL programming, SSRS and SQL server. Must have over 7-8 years of total experience.Experience with Al... |
| 1535 | Software Development | 207 | 207 | Person will modify and build tools to automate work that is completed manually today.Will perform enhancements that will improve speed to market and r... |
| 1678 | Software Development | 214 | 214 | Required Skills: 10+ years of Core Java7 Years of J2EE7 Years of Spring5 Years of Tomcat5 Years of Msg Frameworks5 Years of SchemasExperience in Multi... |
| 9977 | Software Development | 215 | 215 | Java Developer with Hadoop Location: Richmond, VA Duration: 6 months contract to hire 30% Hadoop + 70% Core Java Description: - Need Solid guys. - Jav... |
| 4818 | Software Development | 217 | 217 | i. .NET Framework 4.5/4.6 (using C#) ii. Strong hand-on knowledge on Windows Presentation Framework, PRISM/ MVVM / MEF Framework iii. Knowledge/ Exper... |
| 7610 | Software Development | 220 | 220 | The main function of a Database/DW Developer is to be responsible for designing and maintaining an efficient database system by overseeing operations... |
| 6171 | Software Development | 224 | 224 | Our client in Sacramento is looking for a .Net Developer. Must be able to work as a w2 employee for our client to be eligible for the position.Require... |
| 8816 | Software Development | 226 | 226 | RESUME & RATE to ershad at cloudpointamerica com BI Devo BI Dev (SQL Server) with experience with procedure etco BizTalk – Nice to haveo Transaction b... |
| 2858 | Software Development | 227 | 227 | - High proficience programming in C++- Quick learning with excellent analytical and problem solving skills- Confortable with rapid development cycles-... |
| 9019 | Network Engineering | 227 | 227 | Job Descriptionweekly payment Rs.7000. You can withdraw your earnings as soon as* your account reaches get EarnInterested can call us,*For info just s... |

### Columns added to the job-role output

| Column | Purpose |
| --- | --- |
| `source_row_index` | Zero-based position of the record in the raw CSV. |
| `clean_description` | NFC-normalised, whitespace-normalised job description. |
| `description_length` | Character length of clean_description. |
| `short_description_flag` | True when clean_description is shorter than 300 characters. |
| `is_empty_description` | True when clean_description is empty. |
| `label_clean` | Canonical label from the six documented job-role categories. |
| `label_changed` | True when canonicalisation changed the original label string. |
| `is_valid_label` | True when the label maps to one of the six documented categories. |
| `has_replacement_char` | True when clean_description contains U+FFFD replacement characters. |
| `has_private_use_char` | True when clean_description contains private-use Unicode characters. |

## 6. Software-skills cleaning

### Transformations applied

1. **Text normalisation:** `Title`, `Workplace Example`, and `Element Name` were NFC-normalised, whitespace-normalised, and trimmed. Original columns were retained.
2. **Flag normalisation:** `Hot Technology` and `In Demand` were whitespace/Unicode normalised and uppercased; original columns were retained.
3. **Occupation titles:** titles were standardised for Unicode and whitespace only. Case was not forced so that official O*NET capitalisation and acronyms are preserved.
4. **Many-to-many relationships:** repeated workplace examples were retained. They are valid because the same technology can be linked to multiple occupations.
5. **Genuine duplicate rows:** fully identical rows were detected for reporting only; none were removed.

### Measured results

| Metric | Raw | Clean |
| --- | --- | --- |
| Records | 6,217 | 6,217 |
| Unique occupation titles | 36 | 36 |
| Unique workplace examples | 1,541 | 1,541 |
| Rows repeating a workplace example | 4,676 | 4,676 |
| Unique occupation-example pairs | 6,217 | 6,217 |
| Rows with multiple spaces | 0 | 0 |
| Rows with non-ASCII characters | 0 | 0 |
| Rows with U+FFFD replacement char | 0 | 0 |
| Rows with private-use chars | 0 | 0 |

- Title values changed by normalisation: **0**.
- Workplace-example values changed: **0**.
- Element-name values changed: **0**.
- Genuine identical full-row groups: **0** (0 rows). None were removed.
- Invalid Hot Technology flags: **0**; invalid In Demand flags: **0**.

### Columns added to the skills output

| Column | Purpose |
| --- | --- |
| `source_row_index` | Zero-based position of the record in the raw CSV. |
| `Title_clean` | NFC-normalised, whitespace-normalised occupation title. |
| `Workplace Example_clean` | NFC-normalised, whitespace-normalised technology example. |
| `Element Name_clean` | NFC-normalised, whitespace-normalised O*NET skill category. |
| `Hot Technology_clean` | Normalised Y/N hot-technology flag. |
| `In Demand_clean` | Normalised Y/N in-demand flag. |
| `is_valid_hot_technology` | True when Hot Technology is Y or N after normalisation. |
| `is_valid_in_demand` | True when In Demand is Y or N after normalisation. |
| `has_replacement_char` | True when a cleaned text field contains U+FFFD. |
| `has_private_use_char` | True when a cleaned text field contains private-use Unicode. |

## 7. Output and audit files

| File | Size |
| --- | --- |
| `ai-engine/data/processed/resumes_cleaned.csv` | 67.90 MB |
| `ai-engine/data/processed/job_roles_cleaned.csv` | 39.33 MB |
| `ai-engine/data/processed/software_skills_cleaned.csv` | 1.33 MB |
| `ai-engine/data/audit/excluded_records.csv` | 1.40 KB |
| `ai-engine/data/audit/duplicate_records.csv` | 2.18 KB |

## 8. Validation results

The following checks were executed automatically by the script. A failure would make the script exit with a non-zero status.

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

## 9. Remaining data-quality issues

- **Exact duplicate résumés retained:** 2 groups (4 rows) still require a human deduplication decision.
- **U+FFFD replacement characters remain:** 6 résumé records and 5 job records contain replacement characters. They were preserved rather than guessed or deleted.
- **Private-use Unicode characters remain:** 5 résumé records and 41 job records contain private-use glyphs (often PDF bullet symbols).
- **Short job descriptions retained:** 125 cleaned descriptions are below 300 characters and may be low-information or truncated.
- **Résumé categories remain unmapped:** all 24 categories are preserved; no mapping to the six job-role labels was applied.
- **Repeated skill examples remain:** 4,676 rows repeat a workplace example across occupations. This is intentional many-to-many data.
- **No semantic deduplication, near-duplicate detection, language detection, spelling correction, or mojibake repair was performed.**

## 10. Decisions requiring human review

1. Confirm whether résumé ID `12632728` should remain excluded from the modeling-ready file or be repaired. The raw record is retained and the exclusion is audited.
2. Decide whether the exact duplicate résumé pairs should be removed, merged, or kept. They are currently retained and flagged in `duplicate_records.csv`.
3. Confirm that HTML-derived `clean_text` is the preferred résumé text source. `Resume_str` is retained as a fallback and original field.
4. Decide whether to map or filter the 24 résumé categories for the IT-focused job matcher.
5. Review short job descriptions before removing or down-weighting them.
6. Decide whether replacement characters and private-use glyphs should be repaired, normalised to standard symbols, or left as-is.
7. Decide how repeated skill/occupation relationships should be aggregated for feature engineering without destroying the many-to-many mapping.

## 11. Assumptions

- `utf-8-sig` is the correct encoding for the three raw files; fallback encodings are attempted only on decode failure.
- `Resume_html` is the primary résumé text source; `Resume_str` is used only when HTML extraction yields no text.
- A résumé is considered invalid for modelling only when both text sources are empty after normalisation.
- The six job labels are the documented categories from the Phase 2.2 inspection.
- Cleaning standardises representation; it does not correct content, resolve semantic errors, or make deduplication decisions.
