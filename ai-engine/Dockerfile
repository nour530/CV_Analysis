# syntax=docker/dockerfile:1
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

RUN groupadd --system jobmatch \
    && useradd --system --gid jobmatch --create-home jobmatch

COPY requirements.txt ./
RUN pip install --no-cache-dir --retries 5 --require-hashes -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY data/features/job_features.csv ./data/features/job_features.csv
COPY data/features/skills_dictionary.csv ./data/features/skills_dictionary.csv
COPY data/features/skill_aliases.csv ./data/features/skill_aliases.csv
COPY data/features/skills_supplement.csv ./data/features/skills_supplement.csv
COPY data/job_sources.json ./data/job_sources.json
COPY data/esco ./data/esco
COPY data/models ./data/models

RUN mkdir -p /app/data \
    && chown -R jobmatch:jobmatch /app

USER jobmatch

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1} --no-access-log"]
