# IT Job Description Role Classification Dataset with O*NET Bright Outlook Sub-role and Skill Dictionary

This dataset accompanies the undergraduate thesis "Penstrukturan Deskripsi Pekerjaan Bidang Teknologi Informasi Menggunakan TF-IDF, Support Vector Machine, serta Pemetaan Okupasi dan Keterampilan Berbasis O*NET" (Computer Science Study Program, BINUS Online, Universitas Bina Nusantara, 2026) by Muhammad Alif Ramadhiska, Legyatna, and Muhammad Aria Muktadir.

## Files

| File | Rows | Description |
|---|---|---|
| `job_roles_combined_ready_10461.csv` | 10,461 | English IT job descriptions with a single role label. Columns: `jobdescription` (text), `label` (one of six role categories). |
| `Bright_Outlook_15_mapped.csv` | 30 | O*NET 30.3 occupations with SOC code 15- and Bright Outlook status, mapped manually to the six role categories. Columns: `Code`, `Occupation`, `Bright_Outlook_Category`, `role_category` (`DROP` = not mapped), `note`. |
| `occupation_data_15_filtered.csv` | 26 | Title and official description of the 26 occupations used as sub-roles. Columns: `O*NET-SOC Code`, `Title`, `Description`. |
| `software_skills_15.csv` | 6,217 | Technology skills of SOC 15- occupations with O*NET flags. Columns: `O*NET-SOC Code`, `Title`, `Workplace Example` (technology name), `Element ID`, `Element Name` (skill category), `Hot Technology` (Y/N), `In Demand` (Y/N). |
| `Anotasi_Subrole_60.xlsx` | 60 | Ground-truth annotation of the best-matching sub-role for 60 test-set job postings by two independent annotators, with the final agreed label and the system output (sheet "Kunci Sistem"). Instructions are in Indonesian. |
| `Evaluasi_Ekstraksi_Keterampilan.xlsx` | — | Skill-extraction evaluation instrument and results: precision (sheet "A Presisi"), recall on 20 postings (sheet "B Recall"), weight sensitivity (sheet "C Sensitivitas Bobot"), and summary. Instructions are in Indonesian. |

## Label distribution (`job_roles_combined_ready_10461.csv`)

| Role category | Rows |
|---|---|
| Software Development | 8,359 |
| Network Engineering | 796 |
| Cybersecurity | 630 |
| Data Analytics | 272 |
| Data Science | 254 |
| UI/UX Design | 150 |

## How the job dataset was built

1. Merged two public datasets: U.S. Technology Jobs on Dice.com (PromptCloud, Kaggle, 22,000 rows) and Job Dataset for IT Professionals (Rahman et al., Mendeley Data, cleaned file, 4,657 rows), giving 26,657 rows.
2. Removed 1,830 rows whose description text was identical after whitespace normalisation (24,827 rows left).
3. Selected and labelled rows by matching the job title against keyword rules for six role categories; rows whose title did not point to one category were removed (10,461 rows left).
4. Kept only the description text and the label; job titles are not included so that a model learns from the description, not from the title used for labelling.

## Sources and licences

- PromptCloud. (2017). *U.S. technology jobs on Dice.com* (Version 1) [Data set]. Kaggle. https://www.kaggle.com/datasets/PromptCloudHQ/us-technology-jobs-on-dicecom — CC BY-SA 4.0.
- Rahman, S., Habiba, K., Roy, S., & Nur, F. (2025). *Job dataset for IT professionals* (Version 1) [Data set]. Mendeley Data. https://doi.org/10.17632/8b62nm2gmv.1 — CC BY 4.0.
- This dataset includes information from the O*NET 30.3 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA). Used under the CC BY 4.0 license. O*NET® is a trademark of USDOL/ETA. The authors have modified all or some of this information (occupation selection, mapping to role categories, skill weighting). USDOL/ETA has not approved, endorsed, or tested these modifications.

Because one source is licensed CC BY-SA 4.0, this derived dataset is released under **CC BY-SA 4.0**.

## Related code

Training notebook and web application: https://github.com/malifrs/jobdesc-classifier
