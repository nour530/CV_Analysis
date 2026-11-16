"""Evaluate a CV: our model's scores plus the measured evidence behind them.

Scores come from the trained scorer (scorer.py) and run locally. Each
criterion also gets short, factual evidence measured in the CV ("3 of 8
bullets show a figure"), and plain facts (contact details, dates) are
checked directly rather than predicted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.cv_document import CvDocument
from app.services.ats.checker import NUMBER_PATTERN
from app.services.cv_builder.render import render_text
from app.services.cv_builder.suggestions import BUZZWORDS, FIRST_PERSON_START, WEAK_OPENING, _starts_with_verb
from app.services.cv_quality.rubric import CRITERIA
from app.services.cv_quality.scorer import CvQualityScorer


@dataclass(slots=True)
class CriterionResult:
    id: str
    label: str
    score: float | None
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FactCheck:
    id: str
    label: str
    ok: bool


@dataclass(slots=True)
class CvEvaluation:
    available: bool
    overall: int | None
    criteria: list[CriterionResult]
    checks: list[FactCheck]
    model: dict


T = {
    "en": {
        "summary_none": "No professional summary.",
        "summary_words": "Summary: {n} words (aim for 30-60).",
        "figures": "{n} of {total} bullet points show a figure.",
        "verbs": "{n} of {total} bullet points open with an action verb.",
        "weak": "{n} bullet points open with a vague phrase such as “Responsible for”.",
        "first_person": "{n} bullet points use the first person.",
        "buzzwords": "Clichés found: {items}.",
        "skills": "{n} skills listed.",
        "no_skills": "No skills section.",
        "long": "{n} bullet points are longer than 35 words.",
        "dates": "{n} of {total} roles have dates.",
        "titles": "{n} of {total} roles have a job title and an employer.",
        "no_education": "No education section.",
        "roles": "{n} roles; {bullets} bullet points in total.",
        "sections": "{n} sections: {items}.",
        "check_name": "Full name",
        "check_email": "Email address",
        "check_phone": "Phone number",
        "check_location": "City or location",
        "check_linkedin": "LinkedIn profile",
        "check_dates": "Dates on every role",
        "check_education": "Education section",
        "check_skills": "Skills section",
    },
    "fr": {
        "summary_none": "Pas de profil professionnel.",
        "summary_words": "Profil : {n} mots (visez 30 à 60).",
        "figures": "{n} points sur {total} contiennent un chiffre.",
        "verbs": "{n} points sur {total} commencent par un verbe d'action.",
        "weak": "{n} points commencent par une formule vague comme « Chargé de ».",
        "first_person": "{n} points utilisent la première personne.",
        "buzzwords": "Clichés trouvés : {items}.",
        "skills": "{n} compétences listées.",
        "no_skills": "Pas de section compétences.",
        "long": "{n} points dépassent 35 mots.",
        "dates": "{n} postes sur {total} ont des dates.",
        "titles": "{n} postes sur {total} ont un intitulé et un employeur.",
        "no_education": "Pas de section formation.",
        "roles": "{n} postes ; {bullets} points au total.",
        "sections": "{n} sections : {items}.",
        "check_name": "Nom complet",
        "check_email": "Adresse e-mail",
        "check_phone": "Téléphone",
        "check_location": "Ville ou localisation",
        "check_linkedin": "Profil LinkedIn",
        "check_dates": "Dates sur chaque poste",
        "check_education": "Section formation",
        "check_skills": "Section compétences",
    },
}
SECTION_NAMES = {
    "en": {"summary": "summary", "experience": "experience", "education": "education", "skills": "skills",
           "languages": "languages", "certifications": "certifications", "projects": "projects"},
    "fr": {"summary": "profil", "experience": "expérience", "education": "formation", "skills": "compétences",
           "languages": "langues", "certifications": "certifications", "projects": "projets"},
}


def _evidence(doc: CvDocument, text: str, language: str) -> dict[str, list[str]]:
    t = T.get(language, T["en"])
    bullets = [b for e in doc.experience for b in e.bullets] + [b for p in doc.projects for b in p.bullets]
    total = len(bullets)
    roles = doc.experience
    evidence: dict[str, list[str]] = {c.id: [] for c in CRITERIA}
    names = SECTION_NAMES.get(language, SECTION_NAMES["en"])
    present = [names[s] for s in names if getattr(doc, s)]
    evidence["structure"].append(t["sections"].format(n=len(present), items=", ".join(present) or "-"))
    words = len((doc.summary or "").split())
    evidence["summary"].append(t["summary_words"].format(n=words) if words else t["summary_none"])
    if total:
        evidence["achievements"].append(t["figures"].format(n=sum(bool(NUMBER_PATTERN.search(b)) for b in bullets), total=total))
        evidence["action_language"].append(t["verbs"].format(n=sum(_starts_with_verb(b) for b in bullets), total=total))
        if weak := sum(bool(WEAK_OPENING.match(b.strip())) for b in bullets):
            evidence["action_language"].append(t["weak"].format(n=weak))
        if first := sum(bool(FIRST_PERSON_START.match(b)) for b in bullets):
            evidence["action_language"].append(t["first_person"].format(n=first))
        if long := sum(len(b.split()) > 35 for b in bullets):
            evidence["clarity"].append(t["long"].format(n=long))
    found = list(dict.fromkeys(m.group(0).lower() for m in BUZZWORDS.finditer(text)))
    if found:
        evidence["action_language"].append(t["buzzwords"].format(items=", ".join(found[:4])))
    evidence["skills"].append(t["skills"].format(n=len(doc.skills)) if doc.skills else t["no_skills"])
    if roles:
        evidence["completeness"].append(t["dates"].format(n=sum(bool(e.start) for e in roles), total=len(roles)))
        evidence["completeness"].append(t["titles"].format(n=sum(bool(e.title and e.company) for e in roles), total=len(roles)))
        evidence["focus"].append(t["roles"].format(n=len(roles), bullets=sum(len(e.bullets) for e in roles)))
    if not doc.education:
        evidence["completeness"].append(t["no_education"])
    return evidence


def _checks(doc: CvDocument, language: str) -> list[FactCheck]:
    t = T.get(language, T["en"])
    c = doc.contact
    return [
        FactCheck("name", t["check_name"], bool(c.full_name)),
        FactCheck("email", t["check_email"], bool(c.email)),
        FactCheck("phone", t["check_phone"], bool(c.phone)),
        FactCheck("location", t["check_location"], bool(c.location)),
        FactCheck("linkedin", t["check_linkedin"], bool(c.linkedin)),
        FactCheck("dates", t["check_dates"], bool(doc.experience) and all(e.start for e in doc.experience)),
        FactCheck("education", t["check_education"], bool(doc.education)),
        FactCheck("skills", t["check_skills"], bool(doc.skills)),
    ]


def evaluate(doc: CvDocument, scorer: CvQualityScorer | None, *, language: str = "en") -> CvEvaluation:
    text = render_text(doc, language)
    evidence = _evidence(doc, text, language)
    score = scorer.score(text) if scorer is not None else None
    criteria = [
        CriterionResult(
            id=c.id,
            label=c.fr if language == "fr" else c.en,
            score=score.criteria[c.id] if score else None,
            evidence=evidence[c.id],
        )
        for c in CRITERIA
    ]
    model = {}
    if scorer is not None:
        model = {key: scorer.metadata.get(key) for key in ("kind", "cvs", "trained_at") if key in scorer.metadata}
    return CvEvaluation(
        available=score is not None,
        overall=score.overall if score else None,
        criteria=criteria,
        checks=_checks(doc, language),
        model=model,
    )
