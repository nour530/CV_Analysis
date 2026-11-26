# JobMatch AI (CV_Analysis)

ATS CV checker, CV builder, career assistant and job matching for job seekers in Tunisia.
A FastAPI backend (`ai-engine/`) and an Angular frontend (`frontend/`), run together with
PostgreSQL and Redis through `docker-compose.yml`.

## Commands

Run from the folder named. On Windows the venv's Python is `venv/Scripts/python.exe`; in CI
it is `python`.

| What | Where | Command |
|---|---|---|
| Install backend (exactly as CI) | `ai-engine/` | `python -m pip install --require-hashes -r requirements-dev.txt` |
| Backend tests (all ~300) | `ai-engine/` | `python -m pytest -q` |
| Lint and format check | `ai-engine/` | `python -m ruff check . && python -m ruff format --check .` |
| Dependency audit | `ai-engine/` | `python -m pip_audit -r requirements.txt` |
| Database migrations (local only) | `ai-engine/` | `python -m alembic upgrade head` |
| Run the API (local) | `ai-engine/` | `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload` |
| Install frontend | `frontend/` | `npm ci` |
| Frontend tests | `frontend/` | `npm test -- --watch=false` |
| Frontend production build | `frontend/` | `npm run build -- --configuration production` |

**Regenerating the Python lockfiles.** `requirements.txt` and `requirements-dev.txt` are
generated, with hashes, from `requirements.in` and `requirements-dev.in`. Pin to the tested
environment rather than resolving the newest releases. pip-tools runs from its own venv, never
the project's:

```bash
venv/Scripts/python.exe -m pip freeze > .lock-constraints.txt
pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url -c .lock-constraints.txt -o requirements.txt requirements.in
pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url -c .lock-constraints.txt -o requirements-dev.txt requirements-dev.in
rm .lock-constraints.txt
```

To upgrade a package on purpose, change its range in the `.in` file and compile with
`--upgrade-package <name>`. Then verify in a fresh venv installed with `--require-hashes`.

## Layout

- `ai-engine/app/`: the API. `api/routes` handles HTTP, `services` holds domain logic
  (matching, ATS, CV builder, career, assistant, CV quality, local LLM), `schemas` and
  `db/models` define the data, and `core` holds rate limits, audit and logging.
- `ai-engine/tests/`: backend tests. `ai-engine/migrations/`: Alembic migrations.
- `ai-engine/data/`: ESCO data, trained models, job sources and the research datasets.
  `data/raw/esco` is a re-creatable cache and is ignored.
- `ai-engine/data_analysis/`: numbered scripts that build data and train or evaluate models.
  Each writes its report to `data_analysis/outputs/`.
- `ai-engine/docs/`: feature and operations documentation.
- `frontend/src/app/`: `core` (services and models), `pages`, `shared/components`.
- `docs/decisions.md`: the decision record. Read it before changing anything it settles.

## Conventions

- **Tests cover every behaviour change.** Run both suites before saying work is done, and
  confirm the GitHub CI result after pushing; a local pass is not a CI pass. Checked by: CI.
- **AI providers are never called by tests.** Use the fake clients in
  `tests/test_assistant.py`, `tests/test_cv_quality.py` and `tests/test_local_llm.py`.
  Checked by: nothing; advice.
- **Contact details never go to an AI provider.** Email, phone and links are replaced by
  placeholders (`scrub_contacts` in `services/cv_quality/rewriter.py`). Checked by:
  `tests/test_cv_quality.py` and `tests/test_local_llm.py`.
- **A saved model loads only with the exact scikit-learn version it was trained with.** Keep
  `scikit-learn` pinned in the lock, and retrain (`data_analysis/06_*`, `08_*`) when you
  change it. Checked by: the model's own version check at load, which falls back silently.
  Run the career tests after any change.
- **Times are UTC**, written with their zone. Checked by: nothing; advice.
- **When you stop with work outstanding, say what you are waiting on and from whom.**
  Checked by: nothing; advice.

## Invariants

- Every new database change is an Alembic migration; tables are never created by hand in
  production (`AUTO_CREATE_TABLES` is false there). Checked by: nothing yet; no test runs the
  migrations (see `docs/decisions.md`).
- Secrets come from the environment only. `.env` is ignored and `.env.example` holds
  placeholders. Checked by: the CI secret scan.

## What not to do here

- Don't read or print `.env` files or any key or token. The enforced layer denies reading them.
- Don't run `alembic upgrade` against a production database, or deploy, from an agent session.
- Don't add a dependency with `pip install` alone. Add it to the `.in` file and regenerate the
  lock as above.
- Don't push to `main` directly when a pull request is possible.
