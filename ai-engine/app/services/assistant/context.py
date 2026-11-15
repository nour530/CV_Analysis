"""Personal context for the career assistant.

Built from the user's own data on the site: career goal, latest CV analysis
(skills, ATS score and fixes, roles held), saved jobs and applications, and
best-matching current jobs. Contact details (name aside from the first name,
email, phone, LinkedIn, address) are never included.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CvDocumentRecord, JobApplication, User
from app.schemas.ats import AtsReport
from app.schemas.cv_document import CvDocument
from app.schemas.hybrid_match import ResumeProfile
from app.services.matching.application import MatchingApplicationService
from app.services.quick_match import score_jobs
from app.services.user_records import latest_cv_analysis

MAX_SKILLS = 40
MAX_APPLICATIONS = 15


def _first_name(user: User) -> str:
    name = (user.full_name or "").strip()
    return name.split()[0] if name else ""


def build_personal_context(
    db: Session,
    user: User,
    service: MatchingApplicationService,
    *,
    insights: dict | None = None,
) -> str:
    lines: list[str] = []
    first = _first_name(user)
    if first:
        lines.append(f"First name: {first}")

    if insights:
        if insights.get("target_domain"):
            lines.append(f"Target career field: {insights['target_domain']['label']}")
        if insights.get("target_role"):
            lines.append(f"Target role: {insights['target_role']}")
        detected = insights.get("detected_domains") or []
        if detected:
            lines.append(
                "Fields suggested by their CV: "
                + ", ".join(f"{d['label']} ({round(d['probability'] * 100)}%)" for d in detected)
            )
        fit = insights.get("target_role_fit")
        if fit:
            lines.append(
                f"Fit with target role '{fit['title']}': {round(fit['fit'] * 100)}%. "
                f"Has: {', '.join(fit['skills_you_have'][:8]) or 'none detected'}. "
                f"To build: {', '.join(fit['skills_to_build'][:8]) or 'none'}."
            )
        roles = insights.get("suggested_roles") or []
        if roles:
            lines.append(
                "Roles that fit the CV (ESCO): "
                + "; ".join(f"{r['title']} ({round(r['fit'] * 100)}%)" for r in roles[:5])
            )

    analysis = latest_cv_analysis(db, user_id=user.id)
    profile: ResumeProfile | None = None
    if analysis is not None:
        try:
            profile = ResumeProfile.model_validate(analysis.profile_json)
        except ValueError:
            profile = None
        if profile is not None:
            skills = [skill.skill for skill in profile.skills][:MAX_SKILLS]
            lines.append(f"CV skills ({len(profile.skills)}): {', '.join(skills) or 'none detected'}")
        if analysis.ats_json:
            try:
                ats = AtsReport.model_validate(analysis.ats_json)
                lines.append(f"ATS score: {ats.score}/100 ({ats.grade}). {ats.summary}")
                if ats.top_fixes:
                    lines.append("Top ATS fixes: " + " | ".join(ats.top_fixes[:3]))
            except ValueError:
                pass
        lines.append(f"CV last analysed: {analysis.created_at:%Y-%m-%d}")
    else:
        lines.append("No CV analysed yet.")

    resume = db.scalar(
        select(CvDocumentRecord)
        .where(CvDocumentRecord.user_id == user.id)
        .order_by(CvDocumentRecord.updated_at.desc())
        .limit(1)
    )
    if resume is not None:
        try:
            content = CvDocument.model_validate(resume.content_json)
            roles = [
                " at ".join(part for part in (item.title, item.company) if part)
                + (f" ({item.start}–{'present' if item.current else item.end or ''})" if item.start else "")
                for item in content.experience[:6]
                if item.title or item.company
            ]
            if roles:
                lines.append("Experience (from CV builder): " + "; ".join(roles))
            degrees = [", ".join(p for p in (e.degree, e.school) if p) for e in content.education[:3]]
            if any(degrees):
                lines.append("Education: " + "; ".join(d for d in degrees if d))
            if content.languages:
                lines.append("Languages: " + ", ".join(
                    f"{lang.name}{' (' + lang.level + ')' if lang.level else ''}" for lang in content.languages
                ))
        except ValueError:
            pass

    applications = db.scalars(
        select(JobApplication)
        .where(JobApplication.user_id == user.id)
        .order_by(JobApplication.updated_at.desc())
        .limit(MAX_APPLICATIONS)
    ).all()
    if applications:
        lines.append(
            "Tracked jobs: "
            + "; ".join(
                f"{a.title}{' at ' + a.company if a.company else ''} [{a.status}]" for a in applications
            )
        )

    if profile is not None and profile.skills:
        try:
            recommendations = service.recommend_jobs(
                resume_text=None,
                resume_profile=profile,
                resume_id=profile.resume_id,
                resume_category=profile.category,
                experience_evidence=None,
                job_category=None,
                limit=5,
                candidate_pool_size=40,
            )
            if recommendations.recommendations:
                lines.append(
                    "Best-matching current jobs: "
                    + "; ".join(
                        f"{r.job.title} at {r.job.company or 'n/a'} (id {r.job.job_id}, "
                        f"{round(r.match.overall_compatibility_score * 100)}% match)"
                        for r in recommendations.recommendations
                    )
                )
        except Exception:  # context is best-effort; the chat still works without it
            pass
    return "\n".join(f"- {line}" for line in lines)


def job_match_percent(service: MatchingApplicationService, profile: ResumeProfile | None, job_id: str) -> int | None:
    if profile is None:
        return None
    scores = score_jobs(service, profile, [job_id])
    return round(scores[0].score * 100) if scores else None
