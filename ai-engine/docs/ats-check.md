# ATS check

Every CV analysis now returns an `ats` report: a 0–100 score, a grade, a
checklist with a fix for each problem, the five most important fixes, the
sections that were detected and, when a job is given, the keyword gaps.

The check is local and deterministic (no LLM call, nothing leaves the server)
and works for CVs written in English or French.

There is no single ATS standard. Taleo, Workday, SmartRecruiters, Lever and
others parse files differently. The checks encode rules that most of them share
plus content practices recruiters look for. The report says this in its
`disclaimer`; the score is an estimate, not a prediction of a hiring decision.

## API

- `POST /api/v1/cv/analyze` and `POST /api/v1/cv/analyze/upload` return the
  report in `ats` and store it with the analysis. `GET /api/v1/cv/latest` and
  `GET /api/v1/cv/history/{id}` return the stored report.
- `POST /api/v1/cv/ats` re-runs the check on a saved CV (latest by default, or
  `analysis_id`), optionally against a job: `job_id` for a catalog job, or
  `job_text` (plus optional `job_title`) for a pasted job ad. It returns `404
  cv_not_found` if the user has not analysed a CV yet.

## Checks and points

| Area | Check | Points | Fails when |
| --- | --- | ---: | --- |
| Readability | `text_layer` | 12 | fewer than 30 words, a PDF with under 250 characters per page (scan), or over 2% unreadable characters (broken fonts) |
| | `file_type` | 4 | plain text is a warning |
| | `layout` | 8 | text boxes, multiple columns or mostly-table layouts (uploads only) |
| | `images` | 3 | images present (warning) |
| | `header_footer` | 3 | email or phone only in a DOCX header/footer |
| | `length_pages` | 3 | more than 3 pages (warning) |
| Contact | `email`, `phone`, `linkedin`, `location` | 6, 4, 3, 2 | missing (LinkedIn and location are warnings) |
| Sections | experience, education, skills, summary, languages | 7, 5, 5, 3, 2 | heading not found (English or French names) |
| Content | `word_count` | 5 | under 150 words (fail), 150–250 or over 1300 (warning) |
| | `bullets` | 4 | fewer than 2 bullet points |
| | `quantified` | 5 | no experience bullet contains a number |
| | `action_verbs` | 4 | under half of experience bullets start with an action verb (English forms such as manage / manages / managed / managing, French infinitives, past participles and nouns such as *Pilotage*) |
| | `dates` | 3 | fewer than two dates |
| | `first_person` | 2 | more than four "I / my / je / mon" (warning) |
| | `paragraphs` | 2 | lines over 60 words (warning) |
| | `skills_detected` | 5 | fewer than 4 specific (non-soft) skills recognised |
| Keywords (with a job) | `job_keywords` | 25 | weighted skill coverage under 40% (warning under 70%) |
| | `job_title` | 5 | job title words not in the CV (warning) |

Pass earns all points, warning half, fail none; `info` items are not scored.
The score is the share of available points. Without a job, the keyword area is
left out. Keyword coverage weights required skills at 70% and preferred at 30%,
and, like matching, counts soft skills at 25% and spoken languages at 50%.
Missing skills are listed with specific tools and methods first.

## Layout signals

Uploads are inspected in memory right after text extraction
(`app/services/documents/layout.py`): DOCX tables and the share of text inside
them, text boxes, section columns, images, header/footer text, fonts; PDF
images, fonts and characters per page. Inspection never blocks an upload. Text
pasted into the form has no layout, so the report says layout was not checked.

## Storage

`cv_analyses` gained `resume_text` (the extracted text with line breaks, never
the uploaded file), `layout_json` and `ats_json` (migration `491a8818143d`).
They are deleted with the rest of the CV history. Analyses saved before this
change have no `resume_text`; re-checks then fall back to the profile text,
which has no line breaks, so section checks are less reliable. Analysing the CV
again fixes this.

## Calibration

On 300 randomly sampled CVs from the research dataset (contact details removed
by the dataset authors), scores ranged from 16 to 86 with a median of 74.
Section headings were found for experience in 295 and skills in 289 of them.
The checks were tuned against these CVs, but they have not been compared with
the outcome of a real ATS; treat thresholds as provisional.
