# CV builder and AI improvements

Users edit their CV as structured sections (contact, summary, experience,
education, skills, languages, certifications, projects), see a live preview,
check it against ATS rules, review improvement suggestions one by one, and
download it as Word or PDF.

## Flow

1. **Create.** `POST /api/v1/resumes` parses the user's latest analysed CV (or
   `from_analysis_id`) into sections with a local, deterministic parser
   (`app/services/cv_builder/parser.py`). `{"blank": true}` starts empty. Each
   user can keep 20 CVs.
2. **Edit.** The editor (`/editor/:id`) autosaves 1.5 s after the last change
   with `PUT /api/v1/resumes/{id}`. Each save recomputes the ATS score of the CV
   as it will be exported.
3. **Check.** `POST /api/v1/resumes/{id}/ats` (optionally `{"job_id": ...}`)
   runs the ATS check on the rendered CV. Layout checks pass because the export
   is single-column with no tables, text boxes, images or header contact.
4. **Improve.** `POST /api/v1/resumes/{id}/suggestions` returns suggestions the
   user accepts or dismisses individually (see below). Opening the builder from
   a job page (`/editor?jobId=...`) tailors suggestions to that job.
5. **Download.** The PDF and Word buttons save the file straight to the
   user's computer. In Chrome and Edge a "Save as" dialog lets the user choose
   the folder; other browsers save to the usual download folder. Unsaved edits
   are saved first.
   - `GET /api/v1/resumes/{id}/export.pdf`: text-based PDF (A4, standard
     Helvetica font, real selectable text, automatic page breaks). It is built by
     `app/services/cv_builder/pdf.py` with no extra dependency.
   - `GET /api/v1/resumes/{id}/export.docx`: Word file with real heading and
     bullet styles, in Calibri or Arial.

   Both use standard section names in English or French, a single column and
   no tables, images or text boxes.

Three templates (classic, modern, compact) change font, accent colour and
density only; all are single-column for ATS safety. Section headings can be in
English or French independently of the content.

## Suggestions

| Source | When | What |
| --- | --- | --- |
| Rules | always | factual summary draft when there is none (title, years from the earliest start date, top skills), first-person clean-ups, tips for bullets without an action verb or a figure or over 35 words, vague openings ("Responsible for", "Worked on", "Chargé de"), clichés in the summary or bullets ("team player", "dynamique"), two bullets in one role starting with the same verb, roles with fewer than 2 bullets or no dates, skills shown in the experience but missing from the skills list, and the target job's specific skills the CV lacks ("add only if true") |
| AI | the request asks for it **and** `ALLOW_REMOTE_LLM_ANALYSIS=true` on the server **and** the user enabled remote analysis in Settings **and** an LLM provider is configured | rewrites of the summary and bullet points; counts against the per-user `USER_QUOTA_REMOTE_LLM_*` quota |

When AI cannot be used, the response falls back to rules and its `notice` says
why (not requested, server off, no user consent, no provider, provider error).

### What is sent to the AI provider

Only the summary, bullet points, role titles/companies as context and, when
tailoring, the job's skill names. Contact details are never included; emails and
phone numbers inside bullets are replaced with `[email]` / `[phone]` before
sending. The prompt (`cv_improvement_prompt`, version 1.1.0) treats CV text as
untrusted data, forbids adding employers, tools, skills, results or numbers that
the item does not state, and asks for placeholders such as `[X%]` where a figure
would help.

Rewrites follow the standard recruiters and ATS expect:
- bullets are structured as action + task + result (Google's XYZ formula),
  open with a strong verb, use one tense per role and stay under 30 words;
- vague duty phrases, clichés and repeated verbs are removed;
- job titles and tool names use standard, searchable wording, and uncommon
  acronyms are spelled out once;
- the summary opens with the title and years of experience, then core skills
  and the strongest result, with no objective statement;
- when tailoring to a job, a skill the CV already shows uses the job ad's exact
  wording, so ATS keyword matching succeeds.

### Checks on every AI suggestion

`SuggestionService.validate_ai` runs before anything reaches the user:

- dropped: unknown item references, duplicates, text identical to the original,
  and any suggestion containing an email address or phone number;
- warned: every AI suggestion ("check every word"), figures that are not in the
  original, placeholders to replace, and specific skills not found anywhere
  else in the CV.

The editor only replaces a bullet if it still has the text the suggestion was
made for; otherwise it tells the user the bullet has changed.

## Storage and privacy

`cv_documents` stores the structured content as JSON (no files), the title,
language, template, latest ATS score and the source analysis (migration
`3c5cc24007e5`). Rows are deleted with the account (`ON DELETE CASCADE`).
Deleting the CV analysis history keeps builder CVs but unlinks them from the
deleted analyses. Resume endpoints send `Cache-Control: no-store`.

## Parser limits

The parser expects the common layout: a header with name and contact details,
then sections with standard headings, roles introduced by a line with title,
company and dates, followed by bullet points. Unusual layouts still keep all
text, usually as bullets in the nearest section, for the user to tidy up.
Word bullet lists are now kept as "- " lines during DOCX extraction (they used
to lose their markers), which also improves the ATS bullet checks for uploads.
