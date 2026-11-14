"""Improvement suggestions for a structured CV.

Two sources, always reviewed by the user before anything changes:

* Rules (local, always available): structural tips, a factual summary draft,
  first-person clean-ups, and skills that the CV's own experience demonstrates.
* AI (optional): rewrites of the summary and bullet points by the configured
  LLM, only when the server, the user's settings and the request all allow it.

AI output is never trusted blindly. Suggestions that reference unknown items,
repeat the original or contain contact details are dropped; suggestions that
introduce figures or skills that are not in the CV, or that contain
placeholders, carry warnings so the user can check them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.schemas.cv_document import CvContact, CvDocument, CvSuggestion
from app.schemas.hybrid_match import JobRequirement
from app.services.ats.checker import ACTION_VERBS, NUMBER_PATTERN, PHONE_PATTERN, WORD_PATTERN
from app.services.cv_builder.render import render_text
from app.services.documents.layout import EMAIL_PATTERN
from app.services.matching.config import MatchingConfig
from app.services.nlp.skills import LocalSkillExtractor, normalize_skill_key, strip_accents

FIRST_PERSON_START = re.compile(r"^(?:I\s+|I've\s+|I'm\s+|My\s+|Je\s+|J'ai\s+|J’ai\s+)", re.IGNORECASE)
PLACEHOLDER = re.compile(r"\[[^\]]{1,40}\]")
FIGURE = re.compile(r"\d+(?:[.,]\d+)?")
MAX_BULLETS_FOR_AI = 40

TEXT = {
    "en": {
        "add_summary": "Add a 2–3 line summary at the top. This draft uses only facts from your CV; adjust it.",
        "summary_short": "A summary of 2–3 sentences helps recruiters and ATS place you quickly.",
        "first_person": "Drop the pronoun: CVs are written without 'I'.",
        "verb": "Start with an action verb (Led, Built, Reduced, Managed…).",
        "figure": "Add a figure if you can: %, amount, volume, team size or time saved.",
        "long": "Keep bullet points to one or two lines; split or shorten this one.",
        "few_bullets": "Add 3–5 bullet points describing what you achieved in this role.",
        "no_dates": "Add start and end dates (month and year) to this role.",
        "skills_from_cv": "These skills appear in your experience but not in your skills list.",
        "skills_from_job": "This job asks for these skills. Add them only if you really have them.",
        "check_ai": "AI suggestion: check every word before accepting.",
        "new_figures": "Adds figures that are not in your original ({items}). Keep them only if they are true.",
        "placeholder": "Replace {items} with your real figure, or remove it.",
        "new_skills": "Mentions {items}, which is not elsewhere in your CV. Keep it only if it is true.",
        "years": "{title} with {years}+ years of experience",
        "skills_clause": " in {skills}",
        "summary_end": ".",
    },
    "fr": {
        "add_summary": "Ajoutez un profil de 2–3 lignes en haut. Ce brouillon n'utilise que des faits de votre CV ; adaptez-le.",
        "summary_short": "Un profil de 2–3 phrases aide les recruteurs et les ATS à vous situer rapidement.",
        "first_person": "Supprimez le pronom : un CV s'écrit sans « je ».",
        "verb": "Commencez par un verbe d'action (Piloté, Développé, Réduit, Géré…).",
        "figure": "Ajoutez un chiffre si possible : %, montant, volume, taille d'équipe ou temps gagné.",
        "long": "Limitez chaque point à une ou deux lignes ; coupez ou raccourcissez celui-ci.",
        "few_bullets": "Ajoutez 3 à 5 points décrivant vos réalisations dans ce poste.",
        "no_dates": "Ajoutez les dates de début et de fin (mois et année) de ce poste.",
        "skills_from_cv": "Ces compétences apparaissent dans votre expérience mais pas dans votre liste de compétences.",
        "skills_from_job": "Cette offre demande ces compétences. Ajoutez-les seulement si vous les avez vraiment.",
        "check_ai": "Suggestion de l'IA : vérifiez chaque mot avant d'accepter.",
        "new_figures": "Ajoute des chiffres absents de l'original ({items}). Gardez-les seulement s'ils sont vrais.",
        "placeholder": "Remplacez {items} par votre vrai chiffre, ou supprimez-le.",
        "new_skills": "Mentionne {items}, absent du reste de votre CV. Gardez-le seulement si c'est vrai.",
        "years": "{title} avec plus de {years} ans d'expérience",
        "skills_clause": " en {skills}",
        "summary_end": ".",
    },
}


@dataclass(slots=True)
class _Item:
    ref: str
    field: str
    target_id: str | None
    index: int | None
    text: str


def _starts_with_verb(text: str) -> bool:
    words = WORD_PATTERN.findall(text)
    return any(strip_accents(word).casefold().strip(".,:;") in ACTION_VERBS for word in words[:2])


def _scrub_contact(text: str) -> str:
    text = EMAIL_PATTERN.sub("[email]", text)
    return PHONE_PATTERN.sub(lambda m: "[phone]" if len(re.sub(r"\D", "", m.group(0))) >= 8 else m.group(0), text)


def _year(value: str | None) -> int | None:
    match = re.search(r"(19|20)\d{2}", value or "")
    return int(match.group(0)) if match else None


def collect_items(doc: CvDocument, *, scope: str = "all", experience_id: str | None = None) -> list[_Item]:
    items: list[_Item] = []
    if scope in ("all", "summary") and doc.summary:
        items.append(_Item("summary", "summary", None, None, doc.summary))
    if scope in ("all", "experience"):
        for entry in doc.experience:
            if experience_id and entry.id != experience_id:
                continue
            for index, bullet in enumerate(entry.bullets):
                items.append(_Item(f"exp:{entry.id}:{index}", "bullet", entry.id, index, bullet))
        if not experience_id:
            for project in doc.projects:
                for index, bullet in enumerate(project.bullets):
                    items.append(_Item(f"proj:{project.id}:{index}", "bullet", project.id, index, bullet))
    return items[: MAX_BULLETS_FOR_AI + 1]


class SuggestionService:
    def __init__(
        self,
        extractor: LocalSkillExtractor | None = None,
        config: MatchingConfig | None = None,
    ) -> None:
        self.extractor = extractor or LocalSkillExtractor()
        self.config = config or MatchingConfig()

    def _skill_keys(self, skills: Sequence[str]) -> set[str]:
        """Keys for listed skills under both their own and canonical names
        ("Power BI" also covers "Microsoft Power BI")."""
        keys: set[str] = set()
        for skill in skills:
            keys.add(normalize_skill_key(skill))
            keys.add(normalize_skill_key(self.extractor.canonicalize_skill(skill)))
            keys.update(normalize_skill_key(found.skill) for found in self.extractor.extract_skills(f"Skills: {skill}"))
        return keys

    # -- rules ------------------------------------------------------------------------

    def rules(
        self,
        doc: CvDocument,
        *,
        language: str,
        scope: str = "all",
        experience_id: str | None = None,
        job_requirements: Sequence[JobRequirement] | None = None,
    ) -> list[CvSuggestion]:
        t = TEXT.get(language, TEXT["en"])
        out: list[CvSuggestion] = []

        if scope in ("all", "summary"):
            if not doc.summary:
                out.append(CvSuggestion(field="summary", suggestion=self._summary_draft(doc, language), reason=t["add_summary"]))
            elif len(doc.summary.split()) < 12:
                out.append(CvSuggestion(field="tip", original=doc.summary, reason=t["summary_short"]))

        if scope in ("all", "experience"):
            for entry in doc.experience:
                if experience_id and entry.id != experience_id:
                    continue
                if len(entry.bullets) < 2:
                    out.append(CvSuggestion(field="tip", target_id=entry.id, reason=t["few_bullets"]))
                if not entry.start:
                    out.append(CvSuggestion(field="tip", target_id=entry.id, reason=t["no_dates"]))
                for index, bullet in enumerate(entry.bullets):
                    out.extend(self._bullet_rules(entry.id, index, bullet, t))

        if scope in ("all", "skills"):
            listed = self._skill_keys(doc.skills)
            # Contact details (e.g. a LinkedIn URL) are not skills.
            body = render_text(doc.model_copy(update={"skills": [], "contact": CvContact()}), language)
            shown = []
            for skill in self.extractor.extract_skills(body):
                key = normalize_skill_key(skill.skill)
                if key not in listed and skill.category != "soft_skill" and self.config.skill_weight(skill.category, skill.skill) >= 1.0:
                    shown.append(skill.skill)
                    listed.add(key)
            if shown:
                out.append(CvSuggestion(field="skills", add_skills=shown[:20], reason=t["skills_from_cv"]))
            if job_requirements:
                have = self._skill_keys(doc.skills) | {
                    normalize_skill_key(skill.skill) for skill in self.extractor.extract_skills(render_text(doc, language))
                }
                missing = [
                    req.skill
                    for req in sorted(job_requirements, key=lambda r: (r.importance != "required", -self.config.skill_weight(r.category, r.skill)))
                    if normalize_skill_key(req.skill) not in have
                    and self.config.skill_weight(req.category, req.skill) >= 1.0
                ]
                missing = list(dict.fromkeys(missing))
                if missing:
                    out.append(CvSuggestion(field="skills", add_skills=missing[:20], reason=t["skills_from_job"], warnings=[t["skills_from_job"]]))
        return out

    @staticmethod
    def _bullet_rules(target_id: str, index: int, bullet: str, t: dict[str, str]) -> list[CvSuggestion]:
        out: list[CvSuggestion] = []
        if FIRST_PERSON_START.match(bullet):
            cleaned = FIRST_PERSON_START.sub("", bullet).strip()
            if cleaned:
                cleaned = cleaned[0].upper() + cleaned[1:]
                out.append(CvSuggestion(field="bullet", target_id=target_id, index=index, original=bullet, suggestion=cleaned, reason=t["first_person"]))
                return out
        tips = []
        if not _starts_with_verb(bullet):
            tips.append(t["verb"])
        if not NUMBER_PATTERN.search(bullet):
            tips.append(t["figure"])
        if len(bullet.split()) > 35:
            tips.append(t["long"])
        if tips:
            out.append(CvSuggestion(field="tip", target_id=target_id, index=index, original=bullet, reason=" ".join(tips)[:400]))
        return out

    def _summary_draft(self, doc: CvDocument, language: str) -> str:
        t = TEXT.get(language, TEXT["en"])
        title = doc.contact.headline or next((e.title for e in doc.experience if e.title), None)
        years_seen = [_year(e.start) for e in doc.experience if _year(e.start)]
        years = (date.today().year - min(years_seen)) if years_seen else None
        skills = [s for s in doc.skills if self.config.skill_weight(self.extractor.category_for_skill(s), s) >= 1.0][:3]
        if not title:
            title = "Professional" if language == "en" else "Professionnel"
        sentence = t["years"].format(title=title, years=years) if years and years > 0 else title
        if skills:
            sentence += t["skills_clause"].format(skills=", ".join(skills))
        return (sentence + t["summary_end"])[:600]

    # -- AI ---------------------------------------------------------------------------

    def build_ai_input(
        self,
        doc: CvDocument,
        *,
        scope: str,
        experience_id: str | None,
        job_requirements: Sequence[JobRequirement] | None,
        job_title: str | None,
    ) -> tuple[str, dict[str, _Item]]:
        items = collect_items(doc, scope=scope, experience_id=experience_id)[:MAX_BULLETS_FOR_AI]
        lines = ["CV items:"]
        context = []
        for entry in doc.experience:
            context.append(f"- role: {entry.title or '?'} at {entry.company or '?'}{' (current)' if entry.current else ''}")
        if context:
            lines = ["Roles (context only, do not rewrite):", *context, "", "CV items:"]
        lines += [f"[{item.ref}] {_scrub_contact(item.text)}" for item in items]
        if job_requirements:
            skills = ", ".join(dict.fromkeys(req.skill for req in job_requirements))[:1500]
            lines += ["", f"Target job: {job_title or 'not named'}", f"Target-job skills: {skills}"]
        return "\n".join(lines), {item.ref: item for item in items}

    def validate_ai(
        self,
        doc: CvDocument,
        raw: Sequence[tuple[str, str, str]],
        items: dict[str, _Item],
        *,
        language: str,
    ) -> list[CvSuggestion]:
        t = TEXT.get(language, TEXT["en"])
        cv_text = render_text(doc, language)
        cv_skills = {normalize_skill_key(s.skill) for s in self.extractor.extract_skills(cv_text)} | self._skill_keys(doc.skills)
        out: list[CvSuggestion] = []
        seen: set[str] = set()
        for ref, text, reason in raw:
            item = items.get(ref.strip())
            suggestion = " ".join((text or "").split()).rstrip()
            if item is None or ref in seen or not suggestion:
                continue
            if item.field == "bullet":
                suggestion = suggestion.lstrip("-•* ").rstrip(".")
            if suggestion.casefold() == item.text.casefold().rstrip("."):
                continue
            if EMAIL_PATTERN.search(suggestion) or any(
                len(re.sub(r"\D", "", m.group(0))) >= 8 for m in PHONE_PATTERN.finditer(suggestion)
            ):
                continue
            seen.add(ref)
            warnings = [t["check_ai"]]
            original_figures = set(FIGURE.findall(item.text))
            new_figures = [f for f in dict.fromkeys(FIGURE.findall(PLACEHOLDER.sub("", suggestion))) if f not in original_figures]
            if new_figures:
                warnings.append(t["new_figures"].format(items=", ".join(new_figures[:5])))
            placeholders = list(dict.fromkeys(PLACEHOLDER.findall(suggestion)))
            if placeholders:
                warnings.append(t["placeholder"].format(items=", ".join(placeholders[:3])))
            new_skills = [
                s.skill for s in self.extractor.extract_skills(suggestion)
                if normalize_skill_key(s.skill) not in cv_skills and s.category != "soft_skill"
            ]
            if new_skills:
                warnings.append(t["new_skills"].format(items=", ".join(dict.fromkeys(new_skills[:5]))))
            out.append(
                CvSuggestion(
                    field=item.field,  # type: ignore[arg-type]
                    target_id=item.target_id,
                    index=item.index,
                    original=item.text[:1500],
                    suggestion=suggestion[:1500],
                    reason=(reason or "").strip()[:400] or t["check_ai"],
                    warnings=warnings[:5],
                )
            )
        return out


def merge_with_rules(ai: list[CvSuggestion], rules: list[CvSuggestion]) -> list[CvSuggestion]:
    """Keep AI rewrites and drop rule tips for the same item, which they cover."""
    covered = {(s.target_id, s.index, s.field) for s in ai}
    covered_summary = any(s.field == "summary" for s in ai)
    kept = [
        rule
        for rule in rules
        if not (rule.field in ("tip", "bullet") and rule.index is not None and (rule.target_id, rule.index, "bullet") in covered)
        and not (covered_summary and rule.field == "tip" and rule.target_id is None and rule.original is not None)
    ]
    return [*ai, *kept][:60]
