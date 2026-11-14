# Real job postings (Tunisia)

The job list is filled with real postings published by employers, instead of
the research dataset. Postings are fetched from official job-board APIs that
employers publish for job distribution. No website is scraped.

## Sources

| Source | Endpoint | Key | What it gives |
| --- | --- | --- | --- |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/{company}/postings?country=tn` | none | Company postings filtered to Tunisia, full job ad |
| Recruitee | `{company}.recruitee.com/api/offers/` | none | Company careers site offers (filtered by `country_code`) |
| Lever | `api.lever.co/v0/postings/{company}?mode=json` | none | Company postings (filtered by `country` / location) |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{company}` | none | Company postings (filtered by address country) |
| Remotive | `remotive.com/api/remote-jobs` | none | Remote jobs open to candidates worldwide, Africa or EMEA |

The list of companies is in `data/job_sources.json`. At the first sync
(2026-11-14) it produced 361 active postings from 17 employers, including LEONI,
Devoteam, Inetum, Sopra Steria, Yassir, VEO Worldwide Services and Ozeol, plus
remote jobs.

Adzuna does not cover Tunisia, so it is not used. Paid aggregators (JSearch,
JobDataAPI, Careerjet with a publisher key) could be added later as further
adapters in `app/services/jobs/sources.py`.

Remotive asks API users to link back to the original posting and not to fetch
more than a few times a day; the apply link points to the Remotive posting and
the default sync interval is 6 hours.

### Adding an employer

1. Find the company's careers page. The application link reveals the platform
   and the board slug, for example `jobs.smartrecruiters.com/Devoteam/...`
   (slug `Devoteam`) or `jobs.lever.co/Yassir/...` (slug `Yassir`).
2. Add an entry to `data/job_sources.json`, for example
   `{"type": "smartrecruiters", "company": "Devoteam", "country": "tn"}`.
3. Run `python -m app.cli sync-jobs --only smartrecruiters:Devoteam --dry-run`
   to check how many Tunisian postings it returns, then run it without
   `--dry-run`.

## Sync

```bash
python -m app.cli sync-jobs              # fetch every source once
python -m app.cli sync-jobs --dry-run    # fetch and report, save nothing
python -m app.cli sync-jobs --only lever # one source type or type:company
python -m app.cli sync-jobs --refresh-skills  # re-extract skills after a dictionary change
python -m app.cli sync-jobs --loop-seconds 21600  # repeat every 6 hours
```

Docker Compose runs the `job-sync` service, which syncs at start-up and then
every `JOB_SYNC_INTERVAL_SECONDS` (default 21600).

What a sync does, per source:

- New postings are stored with skills extracted from the job text (without the
  employer's "about us" section, which is identical on every posting).
- Changed postings are updated; unchanged ones are only marked as seen.
  SmartRecruiters details are fetched only when a posting's version changed,
  so a repeat sync takes well under a minute.
- Postings that disappeared from a source are closed (`is_active = false`).
  If a source fails, none of its postings are closed.
- Closed postings older than `--closed-retention-days` (default 30) are deleted.

The output is one JSON line with per-source counts and errors. The exit code is
`0` on success, `3` when at least one source failed, `1` on a fatal error.

## Serving

`JOBS_CATALOG_SOURCE` controls where the API reads jobs from:

- `database` (Docker Compose default): only synced postings.
- `auto` (local default): synced postings, or the research dataset while none
  have been synced yet.
- `dataset`: the research dataset only (tests, offline work).

The API caches the catalog and checks the database every
`JOBS_CATALOG_REFRESH_SECONDS` (default 300). The catalog and its TF-IDF fit
are rebuilt only when the set of active postings changed.

New API fields on each job: `company`, `location`, `city`, `country_code`,
`workplace` (`onsite`, `hybrid`, `remote`, `unknown`), `employment_type`,
`experience_level`, `salary`, `apply_url`, `posted_at`, `source`,
`source_label`. `GET /api/v1/jobs` accepts `workplace`, `location` (whole-word
match on city or location) and `company` filters. `GET /api/v1/jobs/facets`
returns filter values with counts.

## Skills for non-IT and French postings

The original O*NET dictionary only covers IT software tools. It had no entry
for Java, AWS, Agile, SAP, accounting, quality methods or spoken languages, and
most Tunisian postings are in manufacturing, quality, sales, finance or HR and
are often written in French. `data/features/skills_supplement.csv` adds about
200 cross-industry skills with English and French aliases (accents optional).
After adding it, postings without any extracted skill dropped from 267 of 364 to
4 of 361.

Related extraction changes:

- `&` is a word boundary, so `R&D` no longer matches the R language.
- `chef`, `rapide` and `route` only match the tools of the same name in a
  DevOps context.
- French requirement cues (`exigé`, `requis`, `maîtrise`, `un plus`, ...) are
  recognised when classifying required and preferred skills.

Matching weights generic skills lower: soft skills count 25% and spoken
languages 50% of a technical or domain skill, and a job that lists only generic
skills is capped at that weight for its skill components. Without this, a
bilingual service-desk job was the top match for a Python developer's CV.
