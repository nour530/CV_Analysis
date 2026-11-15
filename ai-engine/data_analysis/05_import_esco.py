"""Import the ESCO classification (skills and occupations) in English and French.

ESCO is the European Commission's multilingual classification of skills,
competences and occupations. Reuse is free for any purpose with attribution:
"This service uses the ESCO classification of the European Commission."
This project keeps an adapted subset (English and French labels only).

The script pages through the public ESCO API (no key needed), caches every raw
page under data/raw/esco/ (git-ignored) so it can resume, and writes compact
files to data/esco/:

- esco_skills.csv        skills/knowledge with EN/FR labels, synonyms, type,
                         reuse level (transversal, sector-specific, ...)
- esco_occupations.csv   occupations with ISCO code, EN/FR labels, synonyms,
                         short description and essential/optional skill URIs

Run:  python data_analysis/05_import_esco.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import httpx

AI_ENGINE_DIR = Path(__file__).resolve().parents[1]
CACHE_DIR = AI_ENGINE_DIR / "data" / "raw" / "esco"
OUT_DIR = AI_ENGINE_DIR / "data" / "esco"
API = "https://ec.europa.eu/esco/api/search"
PAGE = {"skill": 100, "occupation": 50}
PAUSE_SECONDS = 0.5
USER_AGENT = "JobMatchAI-ESCO-import/1.0"


def _labels(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _alts(record: dict, lang: str) -> list[str]:
    values = _labels(record.get("alternativeLabel")).get(lang) or []
    return [v.strip() for v in values if isinstance(v, str) and v.strip()]


def _description(record: dict, lang: str) -> str:
    value = _labels(record.get("description")).get(lang)
    if isinstance(value, dict):
        value = value.get("literal")
    return " ".join(str(value or "").split())


def _link_title(record: dict, name: str) -> str:
    links = (record.get("_links") or {}).get(name) or []
    if isinstance(links, dict):
        links = [links]
    return str(links[0].get("title") or "") if links else ""


def _get_page(client: httpx.Client, kind: str, size: int, index: int, *, attempts: int = 4) -> dict | None:
    """One API page, cached on disk. Returns None if the server keeps failing."""
    cache = CACHE_DIR / f"{kind}_s{size}_p{index:05d}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    params = {"type": kind, "language": "en", "limit": size, "offset": index, "full": "true"}
    for attempt in range(attempts):
        try:
            response = client.get(API, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            wait = 2 ** attempt
            print(f"  {kind} size {size} page {index}: {type(exc).__name__}, retrying in {wait}s", flush=True)
            time.sleep(wait)
            continue
        cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        time.sleep(PAUSE_SECONDS)
        return payload
    return None


def _records(payload: dict | None) -> list[dict]:
    return ((payload or {}).get("_embedded") or {}).get("results") or []


def _fetch_range(client: httpx.Client, kind: str, start: int, count: int, size: int) -> list[dict]:
    """Records [start, start+count) using pages of `size`; a failing page is
    split into smaller pages, down to single records, which are skipped."""
    results: list[dict] = []
    for index in range(start // size, (start + count + size - 1) // size):
        payload = _get_page(client, kind, size, index)
        if payload is not None:
            results.extend(_records(payload))
        elif size > 1:
            smaller = 10 if size > 10 else 1
            print(f"  {kind}: splitting records {index * size}-{index * size + size - 1} into pages of {smaller}", flush=True)
            results.extend(_fetch_range(client, kind, index * size, size, smaller))
        else:
            print(f"  {kind}: skipping record {index} (server error)", flush=True)
    return results


def fetch_all(client: httpx.Client, kind: str) -> list[dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    size = PAGE[kind]
    first = _get_page(client, kind, size, 0, attempts=6)
    if first is None:
        raise SystemExit(f"ESCO API unavailable for {kind}")
    total = int(first.get("total") or 0)
    results = list(_records(first))
    # The ESCO API's `offset` is a page index, not a record offset.
    pages = (total + size - 1) // size
    for index in range(1, pages):
        results.extend(_fetch_range(client, kind, index * size, size, size))
        if (index + 1) % 10 == 0 or index + 1 == pages:
            print(f"  {kind}: {min((index + 1) * size, total)}/{total}", flush=True)
    return results


def write_skills(records: list[dict]) -> int:
    path = OUT_DIR / "esco_skills.csv"
    seen: set[str] = set()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["uri", "label_en", "label_fr", "alt_en", "alt_fr", "skill_type", "reuse_level", "group_en"])
        for record in records:
            uri = record.get("uri")
            labels = _labels(record.get("preferredLabel"))
            if not uri or uri in seen or not labels.get("en"):
                continue
            seen.add(uri)
            writer.writerow([
                uri,
                labels.get("en", ""),
                labels.get("fr", ""),
                ";".join(_alts(record, "en")),
                ";".join(_alts(record, "fr")),
                _link_title(record, "hasSkillType"),
                _link_title(record, "hasReuseLevel"),
                _link_title(record, "broaderHierarchyConcept") or _link_title(record, "broaderSkill"),
            ])
    return len(seen)


def write_occupations(records: list[dict]) -> int:
    path = OUT_DIR / "esco_occupations.csv"
    seen: set[str] = set()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "uri", "code", "label_en", "label_fr", "alt_en", "alt_fr",
            "description_en", "description_fr", "essential_skills", "optional_skills",
        ])
        for record in records:
            uri = record.get("uri")
            labels = _labels(record.get("preferredLabel"))
            if not uri or uri in seen or not labels.get("en"):
                continue
            seen.add(uri)
            links = record.get("_links") or {}

            def uris(name: str) -> str:
                items = links.get(name) or []
                if isinstance(items, dict):
                    items = [items]
                return ";".join(item.get("uri", "") for item in items if item.get("uri"))

            writer.writerow([
                uri,
                record.get("code") or "",
                labels.get("en", ""),
                labels.get("fr", ""),
                ";".join(_alts(record, "en")[:15]),
                ";".join(_alts(record, "fr")[:15]),
                _description(record, "en")[:600],
                _description(record, "fr")[:600],
                uris("hasEssentialSkill"),
                uris("hasOptionalSkill"),
            ])
    return len(seen)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as client:
        print("Fetching ESCO skills…", flush=True)
        skills = fetch_all(client, "skill")
        print("Fetching ESCO occupations…", flush=True)
        occupations = fetch_all(client, "occupation")
    n_skills = write_skills(skills)
    n_occupations = write_occupations(occupations)
    (OUT_DIR / "ATTRIBUTION.md").write_text(
        "This service uses the [ESCO classification](https://ec.europa.eu/esco) of the European Commission.\n\n"
        "The files in this folder are an adapted subset of ESCO (English and French labels, "
        "short descriptions and occupation-skill relations), imported with "
        "`data_analysis/05_import_esco.py` from the public ESCO API.\n",
        encoding="utf-8",
    )
    print(f"Done in {time.time() - started:.0f}s: {n_skills} skills, {n_occupations} occupations -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
