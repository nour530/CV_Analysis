"""Tools the career assistant may call. All read-only, scoped to public job
listings and the ESCO classification; results are capped in size."""

from __future__ import annotations

import json
from typing import Any

from app.schemas.hybrid_match import ResumeProfile
from app.services.career.esco import get_esco_catalog
from app.services.career.paths import get_career_advisor
from app.services.matching.application import MatchingApplicationService
from app.services.quick_match import score_jobs

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_jobs",
        "description": (
            "Search the current real job openings on JobMatch AI (Tunisia and remote). "
            "Use it whenever the user asks about available jobs, companies hiring, or "
            "jobs in a city. Returns up to 8 jobs with id, title, company, city, work mode, "
            "posting date, apply link and, when the user has a CV, a match percentage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords: job title, skill or company. Empty for all."},
                "city": {"type": "string", "description": "City name, e.g. Tunis, Sousse. Empty for any."},
                "workplace": {"type": "string", "enum": ["any", "onsite", "hybrid", "remote"]},
            },
            "required": ["query", "city", "workplace"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_job",
        "description": "Full details of one job from search_jobs (description, required skills, apply link, match).",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "role_skills",
        "description": (
            "Look up an occupation in the ESCO classification and return its field, description "
            "and essential skills, plus which of them the user's CV already shows. Use it for "
            "questions like 'what skills do I need to become X' or career-change advice."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"role": {"type": "string", "description": "Occupation name in English or French."}},
            "required": ["role"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class ToolExecutor:
    def __init__(
        self,
        service: MatchingApplicationService,
        *,
        profile: ResumeProfile | None,
        cv_text: str | None,
        language: str,
    ) -> None:
        self.service = service
        self.profile = profile
        self.cv_text = cv_text or ""
        self.language = language

    def run(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Return (JSON result, is_error)."""
        try:
            if name == "search_jobs":
                return json.dumps(self._search(arguments), ensure_ascii=False), False
            if name == "get_job":
                return json.dumps(self._job(arguments), ensure_ascii=False), False
            if name == "role_skills":
                return json.dumps(self._role(arguments), ensure_ascii=False), False
            return json.dumps({"error": f"Unknown tool {name}"}), True
        except Exception as exc:  # report, do not crash the conversation
            return json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:200]}"}), True

    def _match(self, job_ids: list[str]) -> dict[str, int]:
        if self.profile is None or not job_ids:
            return {}
        return {item.job_id: round(item.score * 100) for item in score_jobs(self.service, self.profile, job_ids)}

    def _search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        workplace = arguments.get("workplace") or "any"
        jobs, total = self.service.list_jobs(
            category=None,
            query=(arguments.get("query") or "").strip()[:100] or None,
            offset=0,
            limit=8,
            workplace=None if workplace == "any" else workplace,
            location=(arguments.get("city") or "").strip()[:60] or None,
        )
        matches = self._match([job.job_id for job in jobs])
        return {
            "total_matching": total,
            "jobs": [
                {
                    "id": job.job_id,
                    "title": job.title,
                    "company": job.company,
                    "city": job.city or job.location,
                    "workplace": job.workplace,
                    "posted": job.posted_at.date().isoformat() if job.posted_at else None,
                    "apply_url": job.apply_url,
                    "match_percent": matches.get(job.job_id),
                }
                for job in jobs
            ],
        }

    def _job(self, arguments: dict[str, Any]) -> dict[str, Any]:
        job = self.service.get_job(str(arguments.get("job_id") or "")[:120])
        return {
            "id": job.job_id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "workplace": job.workplace,
            "contract": job.employment_type,
            "salary": job.salary_text,
            "apply_url": job.apply_url,
            "required_skills": list(job.required_skills[:20]),
            "preferred_skills": list(job.preferred_skills[:15]),
            "match_percent": self._match([job.job_id]).get(job.job_id),
            "description_untrusted": job.description[:3000],
        }

    def _role(self, arguments: dict[str, Any]) -> dict[str, Any]:
        catalog = get_esco_catalog()
        advisor = get_career_advisor()
        if advisor is None:
            return {"error": "Occupation data is not available on this server."}
        occupation = advisor.find_occupation(str(arguments.get("role") or "")[:100])
        if occupation is None:
            return {"found": False, "message": "No matching occupation in ESCO; try a more common job title."}
        gap = advisor.skill_gap(occupation, self.cv_text, language=self.language, limit=25) if self.cv_text else None
        domain = catalog.domains.get(occupation.domain_id or "")
        return {
            "found": True,
            "occupation": occupation.label(self.language),
            "field": domain.label(self.language) if domain else None,
            "description": occupation.description(self.language)[:600],
            "essential_skills": [
                catalog.skills[uri].label(self.language) for uri in occupation.essential[:30] if uri in catalog.skills
            ],
            "cv_has": gap.have if gap else None,
            "cv_missing": gap.missing if gap else None,
            "source": "ESCO classification of the European Commission",
        }
