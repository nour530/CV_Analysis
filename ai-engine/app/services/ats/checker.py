"""Deterministic ATS-readiness checks for CVs in English and French.

There is no single ATS standard: Taleo, Workday, SmartRecruiters, Lever and
others parse differently. These checks encode widely shared parsing rules
(machine-readable text, standard section headings, contact details in the body,
simple layout, keywords from the target job) and content practices recruiters
look for. The score is an estimate and the report says so.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.schemas.ats import (
    AtsCategoryId,
    AtsCategoryScore,
    AtsCheck,
    AtsKeywordMatch,
    AtsReport,
    AtsSection,
    AtsStats,
    AtsStatus,
)
from app.schemas.hybrid_match import JobRequirement, ProfileSkill
from app.services.matching.config import MatchingConfig
from app.services.documents.layout import EMAIL_PATTERN, LayoutSignals, garbled_ratio
from app.services.nlp.skills import LocalSkillExtractor, normalize_skill_key, strip_accents

DISCLAIMER = (
    "Applicant tracking systems differ from one employer to another. This score "
    "estimates how reliably common systems can read your CV and how well it "
    "presents your experience; it does not predict a hiring decision."
)

CATEGORY_LABELS: dict[AtsCategoryId, str] = {
    "parsing": "Readability by ATS",
    "contact": "Contact details",
    "sections": "Sections",
    "content": "Content quality",
    "keywords": "Job keywords",
}

PHONE_PATTERN = re.compile(r"(?<![\w])(?:\+|00)?\d{1,3}?[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?){2,5}\d{2,4}(?![\w])")
LINKEDIN_PATTERN = re.compile(r"linkedin\.com/(?:in|pub)/[\w%-]+", re.IGNORECASE)
PORTFOLIO_PATTERN = re.compile(r"(?:github\.com|gitlab\.com|behance\.net|dribbble\.com)/[\w-]+", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"(?<!\d)(?:19[6-9]\d|20[0-4]\d)(?!\d)")
MONTHS = (
    r"jan(?:uary|vier)?|feb(?:ruary)?|f[ée]v(?:rier)?|mar(?:ch|s)?|apr(?:il)?|avr(?:il)?|"
    r"may|mai|june?|juin|july?|juil(?:let)?|aug(?:ust)?|ao[uû]t|sep(?:t(?:ember|embre)?)?|"
    r"oct(?:ober|obre)?|nov(?:ember|embre)?|dec(?:ember)?|d[ée]c(?:embre)?"
)
DATE_PATTERN = re.compile(
    rf"(?i)\b(?:(?:{MONTHS})\.?\s+(?:19|20)\d{{2}}|(?:0?[1-9]|1[0-2])[/.-](?:19|20)\d{{2}}|(?:19|20)\d{{2}})\b"
)
PRESENT_PATTERN = re.compile(
    r"(?i)\b(?:present|current|now|today|ongoing|aujourd'hui|pr[ée]sent|actuel(?:lement)?|en\s+cours|à\s+ce\s+jour)\b"
)
BULLET_PATTERN = re.compile(r"^\s*(?:[-–—•●▪◦■□*·►▸➢➤✓✔o]|\d{1,2}[.)])\s+")
# A figure is a currency amount or a number followed by a unit or word
# ("40%", "12 dashboards", "4 lignes", "2,000 products"); bare years are not.
NUMBER_PATTERN = re.compile(
    r"(?i)(?:[€$£]\s?\d|(?<![\d/])(?!(?:19|20)\d{2})\d{1,3}(?:[,.\s]\d{3})*(?:[.,]\d+)?\s?(?:%|[a-zà-ÿ€$£]))"
)
FIRST_PERSON_PATTERN = re.compile(r"(?:(?<=\s)|^)(?:I|I'm|I've|my|me|je|j'ai|j'|mon|ma|mes)(?=\s)", re.MULTILINE)
WORD_PATTERN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9+#'’.-]*")

TUNISIAN_PLACES = (
    "tunis", "ariana", "ben arous", "manouba", "la marsa", "carthage", "le kram", "la goulette",
    "lac", "nabeul", "hammamet", "zaghouan", "bizerte", "beja", "jendouba", "le kef", "siliana",
    "sousse", "monastir", "mahdia", "sfax", "kairouan", "kasserine", "sidi bouzid", "gabes",
    "medenine", "djerba", "tataouine", "gafsa", "tozeur", "kebili", "mateur", "ennasr", "menzah",
    "tunisia", "tunisie",
)
LOCATION_HINT = re.compile(r"(?i)\b(?:address|adresse|location|localisation|ville|city)\s*:")

_ENGLISH_ACTION_BASES = """
achieve administer advise analyse analyze arrange assess assist audit automate build
calculate coach collaborate collect communicate compile complete conduct configure consolidate
contribute control convert coordinate create cut debug decrease define deliver deploy design
detect develop diagnose direct document draft drive edit educate eliminate enhance ensure
establish evaluate examine execute expand facilitate forecast formulate generate grow guide
handle identify implement improve increase influence initiate inspect install integrate
interview introduce investigate launch lead maintain manage market measure mentor merge
migrate minimise minimize model monitor motivate negotiate operate optimise optimize organise
organize oversee perform pilot plan prepare present prioritise prioritize process produce
program promote propose provide publish recommend recruit redesign reduce refactor resolve
restructure review revise run save scale schedule secure select sell set simplify solve
source spearhead standardise standardize streamline strengthen supervise supply support
teach test train transform translate troubleshoot update upgrade validate verify win write
"""
_IRREGULAR_ACTION_FORMS = "built led ran won wrote sold cut set taught oversaw drove grew made began brought"


def _english_forms(base: str) -> set[str]:
    forms = {base, base + "s", base + "ed", base + "ing"}
    if base.endswith("e"):
        forms |= {base + "d", base[:-1] + "ing"}
    if base.endswith("y") and base[-2:-1] not in "aeiou":
        forms |= {base[:-1] + "ies", base[:-1] + "ied"}
    if base.endswith(("s", "sh", "ch", "x", "z")):
        forms.add(base + "es")
    if len(base) <= 4 and base[-1] in "bdgmnprt" and base[-2:-1] in "aeiou" and base[-3:-2] not in "aeiou":
        forms |= {base + base[-1] + "ed", base + base[-1] + "ing"}
    return forms


_FRENCH_ACTION_FORMS = """
accompagne accompagné amélioré analysé animé assuré automatisé conçu concu conduit
contribué coordonné créé déployé développé dirigé effectué élaboré encadré
établi formé géré implémenté lancé mené mis négocié optimisé organisé participé
piloté planifié préparé produit réalisé réduit rédigé résolu supervisé suivi testé
accompagner améliorer analyser animer assurer automatiser concevoir conduire contribuer
coordonner créer déployer développer diriger effectuer élaborer encadrer former gérer
implémenter lancer mener négocier optimiser organiser participer piloter planifier
préparer réaliser réduire rédiger résoudre superviser suivre tester
gestion développement mise réalisation conception participation suivi élaboration
pilotage animation coordination encadrement amélioration optimisation analyse
"""

ACTION_VERBS = frozenset(
    strip_accents(word)
    for word in (
        {form for base in _ENGLISH_ACTION_BASES.split() for form in _english_forms(base)}
        | set(_IRREGULAR_ACTION_FORMS.split())
        | set(_FRENCH_ACTION_FORMS.split())
    )
)

SECTION_HEADINGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "summary": ("Summary / profile", (
        "summary", "professional summary", "profile", "professional profile", "about me", "about",
        "objective", "career objective", "personal statement", "profil", "profil professionnel",
        "résumé", "resume", "à propos", "a propos", "objectif", "objectif professionnel", "présentation",
    )),
    "experience": ("Work experience", (
        "experience", "experiences", "work experience", "professional experience", "employment",
        "employment history", "work history", "career history", "internships", "internship",
        "expérience", "expériences", "expérience professionnelle", "expériences professionnelles",
        "parcours professionnel", "stages", "stage", "historique professionnel",
    )),
    "education": ("Education", (
        "education", "academic background", "academic", "qualifications", "academic qualifications",
        "formation", "formations", "formation académique", "diplômes", "diplomes", "études",
        "etudes", "parcours académique", "cursus", "parcours universitaire",
    )),
    "skills": ("Skills", (
        "skills", "technical skills", "key skills", "core skills", "competencies",
        "core competencies", "skills and tools", "technologies", "tools", "expertise",
        "compétences", "competences", "compétences techniques", "compétences clés",
        "savoir-faire", "outils", "outils et technologies", "connaissances techniques",
    )),
    "languages": ("Languages", ("languages", "language skills", "langues", "langues parlées")),
    "certifications": ("Certifications", (
        "certifications", "certification", "certificates", "licenses", "certifications et formations",
    )),
    "projects": ("Projects", ("projects", "academic projects", "personal projects", "projets", "projets académiques", "réalisations")),
}
_HEADING_LOOKUP = {
    strip_accents(alias).casefold(): section
    for section, (_, aliases) in SECTION_HEADINGS.items()
    for alias in aliases
}
FRENCH_HINTS = frozenset("et de des du le la les une un pour avec dans sur en au aux est expérience compétences formation".split())
ENGLISH_HINTS = frozenset("and of the to with for in on at is experience skills education".split())


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(text).casefold().strip(" :-–—|•\t")).strip()


@dataclass(slots=True)
class _Doc:
    text: str
    lines: list[str]
    words: list[str]
    bullets: list[str]
    achievement_bullets: list[str]
    sections: dict[str, str]
    section_lines: dict[str, list[str]]


def _detect_sections(lines: Sequence[str]) -> tuple[dict[str, str], dict[str, list[str]]]:
    found: dict[str, str] = {}
    content: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        words = stripped.split()
        section = None
        if 0 < len(words) <= 6 and len(stripped) <= 60:
            key = _key(stripped)
            section = _HEADING_LOOKUP.get(key)
            if section is None and ":" in stripped:
                section = _HEADING_LOOKUP.get(_key(stripped.split(":", 1)[0]))
        if section is not None:
            found.setdefault(section, stripped[:160])
            current = section
            content.setdefault(section, [])
            remainder = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if remainder:
                content[section].append(remainder)
            continue
        if current is not None and stripped:
            content[current].append(stripped)
    return found, content


def _parse(text: str) -> _Doc:
    lines = [line.rstrip() for line in text.replace("\r", "").split("\n")]
    non_empty = [line for line in lines if line.strip()]
    words = WORD_PATTERN.findall(text)
    bullets = [BULLET_PATTERN.sub("", line).strip() for line in non_empty if BULLET_PATTERN.match(line)]
    sections, section_lines = _detect_sections(non_empty)
    if not bullets:
        # Many PDFs lose bullet glyphs: treat short action lines inside the
        # experience section as bullet points.
        bullets = [
            line
            for line in section_lines.get("experience", [])
            if 4 <= len(line.split()) <= 40 and _starts_with_action_verb(line)
        ]
    # Achievements are judged on experience/project bullets only; bullet
    # lists under Skills or Languages are not expected to start with verbs.
    achievement_lines = section_lines.get("experience", []) + section_lines.get("projects", [])
    achievement_bullets = [
        BULLET_PATTERN.sub("", line).strip() for line in achievement_lines if BULLET_PATTERN.match(line)
    ]
    if not achievement_bullets:
        achievement_bullets = [line for line in bullets if line not in set(section_lines.get("skills", []))]
    return _Doc(text, non_empty, words, bullets, achievement_bullets, sections, section_lines)


def _starts_with_action_verb(line: str) -> bool:
    words = WORD_PATTERN.findall(line)
    for word in words[:2]:
        if strip_accents(word).casefold().strip(".,:;") in ACTION_VERBS:
            return True
    return False


def _language(words: Sequence[str]) -> str:
    lowered = [word.casefold() for word in words[:3000]]
    fr = sum(1 for word in lowered if word in FRENCH_HINTS)
    en = sum(1 for word in lowered if word in ENGLISH_HINTS)
    if fr > en * 1.5:
        return "fr"
    if en > fr * 1.5:
        return "en"
    return "mixed"


class _Builder:
    def __init__(self) -> None:
        self.checks: list[AtsCheck] = []

    def add(
        self,
        check_id: str,
        category: AtsCategoryId,
        status: AtsStatus,
        title: str,
        message: str,
        max_points: float,
        fix: str | None = None,
    ) -> None:
        factor = {"pass": 1.0, "warning": 0.5, "fail": 0.0, "info": 0.0}[status]
        self.checks.append(
            AtsCheck(
                id=check_id,
                category=category,
                status=status,
                title=title,
                message=message,
                fix=fix if status in ("warning", "fail") else None,
                points=0.0 if status == "info" else round(max_points * factor, 2),
                max_points=0.0 if status == "info" else max_points,
            )
        )


class AtsChecker:
    def __init__(
        self,
        extractor: LocalSkillExtractor | None = None,
        config: MatchingConfig | None = None,
    ) -> None:
        self.extractor = extractor or LocalSkillExtractor()
        # Same generic-skill weights as matching: soft skills and spoken
        # languages count less than specific tools, methods and domains.
        self.config = config or MatchingConfig()

    def check(
        self,
        text: str,
        *,
        layout: LayoutSignals | None = None,
        document_type: str | None = None,
        page_count: int | None = None,
        skills: Sequence[ProfileSkill] | None = None,
        job_requirements: Sequence[JobRequirement] | None = None,
        job_id: str | None = None,
        job_title: str | None = None,
        company: str | None = None,
    ) -> AtsReport:
        doc = _parse(text or "")
        builder = _Builder()
        doc_type = document_type or (layout.document_type if layout else None)
        pages = page_count or (layout.page_count if layout else None)
        profile_skills = list(skills) if skills is not None else self.extractor.extract_skills(text)

        self._parsing(builder, doc, layout, doc_type, pages)
        self._contact(builder, doc, layout)
        self._sections(builder, doc)
        self._content(builder, doc, profile_skills, pages)
        keyword_match = None
        if job_requirements is not None:
            keyword_match = self._keywords(
                builder, doc, profile_skills, job_requirements, job_id, job_title, company
            )

        categories = self._categories(builder.checks, include_keywords=keyword_match is not None)
        total_max = sum(item.max_points for item in categories)
        total = sum(item.points for item in categories)
        score = round(100 * total / total_max) if total_max else 0
        grade = "excellent" if score >= 85 else "good" if score >= 70 else "fair" if score >= 50 else "poor"
        ranked = sorted(
            (check for check in builder.checks if check.status in ("fail", "warning") and check.fix),
            key=lambda check: (check.status != "fail", -(check.max_points - check.points)),
        )
        top_fixes = [check.fix for check in ranked[:5] if check.fix]
        failures = sum(1 for check in builder.checks if check.status == "fail")
        summary = {
            "excellent": "Your CV is well structured for applicant tracking systems.",
            "good": "Your CV should parse well. A few improvements would make it stronger.",
            "fair": "Some parts of your CV may be misread or overlooked by ATS software.",
            "poor": "ATS software is likely to misread important parts of this CV.",
        }[grade]
        if failures:
            summary += f" {failures} critical issue{'s' if failures > 1 else ''} to fix first."

        sections = [
            AtsSection(id=key, label=label, heading=doc.sections.get(key), found=key in doc.sections)
            for key, (label, _) in SECTION_HEADINGS.items()
        ]
        stats = AtsStats(
            word_count=len(doc.words),
            line_count=len(doc.lines),
            bullet_count=len(doc.bullets),
            quantified_bullets=sum(1 for line in doc.achievement_bullets if NUMBER_PATTERN.search(line)),
            action_verb_bullets=sum(1 for line in doc.achievement_bullets if _starts_with_action_verb(line)),
            skill_count=len(profile_skills),
            page_count=pages,
            document_type=doc_type,
        )
        return AtsReport(
            score=max(0, min(100, score)),
            grade=grade,
            summary=summary,
            language=_language(doc.words),
            categories=categories,
            checks=builder.checks,
            top_fixes=top_fixes,
            sections=sections,
            keyword_match=keyword_match,
            stats=stats,
            layout_checked=layout is not None,
            disclaimer=DISCLAIMER,
        )

    # -- categories -----------------------------------------------------------------

    @staticmethod
    def _categories(checks: Sequence[AtsCheck], *, include_keywords: bool) -> list[AtsCategoryScore]:
        results: list[AtsCategoryScore] = []
        for category, label in CATEGORY_LABELS.items():
            if category == "keywords" and not include_keywords:
                continue
            items = [check for check in checks if check.category == category and check.status != "info"]
            max_points = sum(check.max_points for check in items)
            points = sum(check.points for check in items)
            results.append(
                AtsCategoryScore(
                    id=category,
                    label=label,
                    score=round(100 * points / max_points) if max_points else 100,
                    points=round(points, 2),
                    max_points=round(max_points, 2),
                    passed=sum(1 for check in items if check.status == "pass"),
                    total=len(items),
                )
            )
        return results

    def _parsing(
        self,
        b: _Builder,
        doc: _Doc,
        layout: LayoutSignals | None,
        doc_type: str | None,
        pages: int | None,
    ) -> None:
        broken = layout.garbled_ratio if layout else garbled_ratio(doc.text)
        chars_per_page = layout.chars_per_page if layout else None
        if len(doc.words) < 30 or (chars_per_page is not None and chars_per_page < 250):
            b.add("text_layer", "parsing", "fail", "Text can be read by software",
                  "Very little text could be read from this file. It may be a scanned image or exported as pictures.",
                  12, "Export the CV from Word or Google Docs as a text-based PDF or DOCX, not a scan or image.")
        elif broken > 0.02:
            b.add("text_layer", "parsing", "fail", "Text can be read by software",
                  "Part of the text comes out as unreadable characters, usually because of decorative fonts.",
                  12, "Use a standard font (Arial, Calibri, Garamond, Helvetica) and export again.")
        else:
            b.add("text_layer", "parsing", "pass", "Text can be read by software",
                  "All text was extracted cleanly.", 12)

        if doc_type in ("pdf", "docx"):
            b.add("file_type", "parsing", "pass", "File format",
                  f"{doc_type.upper()} is accepted by virtually every ATS.", 4)
        elif doc_type == "text":
            b.add("file_type", "parsing", "warning", "File format",
                  "Plain text parses well but loses all formatting when a recruiter opens it.", 4,
                  "Send a DOCX or text-based PDF to employers.")

        if layout is None:
            b.add("layout", "parsing", "info", "Layout",
                  "Upload a PDF or DOCX file to check tables, columns, text boxes and images.", 0)
            return

        complex_parts = []
        if layout.text_box_count:
            complex_parts.append(f"{layout.text_box_count} text box(es)")
        if layout.column_count > 1:
            complex_parts.append(f"{layout.column_count} text columns")
        if layout.table_count and layout.table_text_ratio > 0.3:
            complex_parts.append("content laid out in tables")
        if complex_parts:
            b.add("layout", "parsing", "fail" if layout.text_box_count or layout.table_text_ratio > 0.6 else "warning",
                  "Simple layout", "Found " + ", ".join(complex_parts) + ". Many ATS read these out of order or skip them.",
                  8, "Use a single-column layout with plain paragraphs and bullet points instead of tables, columns or text boxes.")
        elif layout.table_count:
            b.add("layout", "parsing", "warning", "Simple layout",
                  "A small table was found. Most systems cope, but some read table cells out of order.",
                  8, "Replace tables with plain lines, e.g. 'Python · SQL · Docker'.")
        else:
            b.add("layout", "parsing", "pass", "Simple layout",
                  "Single-column layout without tables or text boxes.", 8)

        if layout.image_count:
            b.add("images", "parsing", "warning", "Images and graphics",
                  f"{layout.image_count} image(s) found. ATS ignore images, so text inside logos, icons or skill bars is lost.",
                  3, "Keep a photo only if the employer asks for one, and replace icons or skill bars with words.")
        else:
            b.add("images", "parsing", "pass", "Images and graphics", "No images that could hide information.", 3)

        if layout.header_footer_contact:
            b.add("header_footer", "parsing", "fail", "Contact details in the main body",
                  "Your email or phone number is in the page header or footer, which many ATS do not read.",
                  3, "Move your name, email and phone number into the first lines of the page body.")
        elif layout.document_type == "docx":
            b.add("header_footer", "parsing", "pass", "Contact details in the main body",
                  "No contact details hidden in headers or footers.", 3)

        if pages is not None and pages > 3:
            b.add("length_pages", "parsing", "warning", "Length",
                  f"{pages} pages. Recruiters spend seconds on a first read and some ATS truncate long files.",
                  3, "Aim for one page with under 5 years of experience, two pages at most otherwise.")
        elif pages is not None:
            b.add("length_pages", "parsing", "pass", "Length", f"{pages} page(s).", 3)

    def _contact(self, b: _Builder, doc: _Doc, layout: LayoutSignals | None) -> None:
        head = "\n".join(doc.lines[:25])
        email = EMAIL_PATTERN.search(doc.text)
        if email:
            b.add("email", "contact", "pass", "Email address", "An email address was found.", 6)
        elif layout and layout.header_footer_contact:
            b.add("email", "contact", "warning", "Email address",
                  "An email appears only in the page header or footer.", 6,
                  "Repeat your email address in the body, near your name.")
        else:
            b.add("email", "contact", "fail", "Email address", "No email address was found.", 6,
                  "Add a professional email address under your name.")

        phones = [m.group(0) for m in PHONE_PATTERN.finditer(doc.text)]
        phones = [p for p in phones if 8 <= len(re.sub(r"\D", "", p)) <= 15 and not YEAR_PATTERN.fullmatch(p.strip())]
        if phones:
            b.add("phone", "contact", "pass", "Phone number", "A phone number was found.", 4)
        else:
            b.add("phone", "contact", "fail", "Phone number", "No phone number was found.", 4,
                  "Add a phone number with the country code, e.g. +216 20 123 456.")

        if LINKEDIN_PATTERN.search(doc.text):
            b.add("linkedin", "contact", "pass", "LinkedIn profile", "A LinkedIn profile link was found.", 3)
        else:
            b.add("linkedin", "contact", "warning", "LinkedIn profile",
                  "No LinkedIn link. Recruiters in Tunisia and abroad routinely check LinkedIn.", 3,
                  "Add your LinkedIn URL, e.g. linkedin.com/in/your-name.")

        lowered = strip_accents(head).casefold()
        if LOCATION_HINT.search(head) or any(re.search(rf"\b{re.escape(place)}\b", lowered) for place in TUNISIAN_PLACES):
            b.add("location", "contact", "pass", "Location", "Your city or region is shown.", 2)
        else:
            b.add("location", "contact", "warning", "Location",
                  "No city found near the top. Many recruiters filter candidates by location.", 2,
                  "Add your city and country, e.g. 'Tunis, Tunisia'. A full street address is not needed.")

        if PORTFOLIO_PATTERN.search(doc.text):
            b.add("portfolio", "contact", "info", "Portfolio", "A GitHub, GitLab or portfolio link was found.", 0)

    def _sections(self, b: _Builder, doc: _Doc) -> None:
        def section(check_id: str, key: str, title: str, points: float, missing: str, fix: str) -> None:
            if key in doc.sections:
                b.add(check_id, "sections", "pass", title, f"Found as '{doc.sections[key]}'.", points)
            else:
                b.add(check_id, "sections", "fail" if points >= 5 else "warning", title, missing, points, fix)

        section("section_experience", "experience", "Work experience section", 7,
                "No clear 'Experience' heading. ATS use headings to know where your jobs are.",
                "Add a heading named 'Professional Experience' (or 'Expérience professionnelle') above your jobs, internships included.")
        section("section_education", "education", "Education section", 5,
                "No clear 'Education' heading was found.",
                "Add a heading named 'Education' (or 'Formation') with your degrees and dates.")
        section("section_skills", "skills", "Skills section", 5,
                "No dedicated 'Skills' heading was found.",
                "Add a 'Skills' (or 'Compétences') section listing tools, methods and languages.")
        section("section_summary", "summary", "Profile summary", 3,
                "No short summary at the top.",
                "Add 2–3 lines at the top stating your role, years of experience and main strengths.")
        if "languages" in doc.sections:
            b.add("section_languages", "sections", "pass", "Languages section",
                  f"Found as '{doc.sections['languages']}'.", 2)
        else:
            b.add("section_languages", "sections", "warning", "Languages section",
                  "No 'Languages' section. In Tunisia, French, English and Arabic levels are often screened.", 2,
                  "Add a 'Languages' section with levels, e.g. 'French: fluent · English: B2'.")
        unusual = [line for line in doc.lines if 0 < len(line.split()) <= 3 and line.isupper() and _key(line) not in _HEADING_LOOKUP]
        if len(unusual) >= 3:
            b.add("heading_names", "sections", "info", "Heading names",
                  "Some headings use non-standard names (" + ", ".join(sorted(set(unusual))[:4]) + "). Standard names parse more reliably.", 0)

    def _content(self, b: _Builder, doc: _Doc, skills: Sequence[ProfileSkill], pages: int | None) -> None:
        words = len(doc.words)
        if words < 150:
            b.add("word_count", "content", "fail", "Amount of detail", f"Only {words} words. The CV is too thin to show your experience.",
                  5, "Describe each role with 3–5 bullet points covering what you did and what changed because of it.")
        elif words < 250:
            b.add("word_count", "content", "warning", "Amount of detail", f"{words} words. There is room to show more of what you achieved.",
                  5, "Add 1–2 more achievement bullet points to your main roles and projects.")
        elif words > 1300:
            b.add("word_count", "content", "warning", "Amount of detail", f"{words} words is long for a first read.",
                  5, "Cut older or less relevant roles to one line each and keep the CV to two pages.")
        else:
            b.add("word_count", "content", "pass", "Amount of detail", f"{words} words, a good length.", 5)

        bullets = len(doc.bullets)
        if bullets >= 6:
            b.add("bullets", "content", "pass", "Bullet points", f"{bullets} bullet points make the CV easy to scan.", 4)
        elif bullets >= 2:
            b.add("bullets", "content", "warning", "Bullet points", f"Only {bullets} bullet points.", 4,
                  "Break long paragraphs into bullet points, one achievement per line.")
        else:
            b.add("bullets", "content", "fail", "Bullet points", "No bullet points were detected.", 4,
                  "List responsibilities and achievements as bullet points under each role.")

        achievements = doc.achievement_bullets
        quantified = sum(1 for line in achievements if NUMBER_PATTERN.search(line))
        if quantified >= 3:
            b.add("quantified", "content", "pass", "Measurable results", f"{quantified} bullet points include numbers.", 5)
        elif quantified >= 1:
            b.add("quantified", "content", "warning", "Measurable results", f"Only {quantified} bullet point(s) include numbers.", 5,
                  "Add figures to your achievements: percentages, amounts, volumes, team size or time saved.")
        else:
            b.add("quantified", "content", "fail", "Measurable results", "No achievements are backed by numbers.", 5,
                  "Quantify at least three achievements, e.g. 'Reduced defect rate by 18%' or 'Managed 40 client accounts'.")

        total = len(achievements)
        action = sum(1 for line in achievements if _starts_with_action_verb(line))
        share = action / total if total else 0.0
        if total and share >= 0.5:
            b.add("action_verbs", "content", "pass", "Action verbs", f"{action} of {total} experience bullet points start with an action verb.", 4)
        elif total:
            b.add("action_verbs", "content", "warning", "Action verbs", f"Only {action} of {total} experience bullet points start with an action verb.", 4,
                  "Start bullet points with verbs such as Led, Built, Reduced, Managed (or Piloté, Développé, Réduit, Géré).")
        else:
            b.add("action_verbs", "content", "fail", "Action verbs", "No bullet points to evaluate.", 4,
                  "Write achievements as bullet points that start with an action verb.")

        dates = len(DATE_PATTERN.findall(doc.text)) + len(PRESENT_PATTERN.findall(doc.text))
        experience_lines = " ".join(doc.section_lines.get("experience", []))
        if dates >= 2 and (not experience_lines or DATE_PATTERN.search(experience_lines) or PRESENT_PATTERN.search(experience_lines)):
            b.add("dates", "content", "pass", "Dates", "Roles and studies are dated.", 3)
        else:
            b.add("dates", "content", "fail", "Dates", "Few or no dates were found.", 3,
                  "Add start and end dates (month and year) to every role and degree, e.g. 'Mar 2023 – Present'.")

        first_person = len(FIRST_PERSON_PATTERN.findall(doc.text))
        if first_person > 4:
            b.add("first_person", "content", "warning", "Professional tone", f"First-person words ('I', 'my', 'je') appear {first_person} times.", 2,
                  "Drop pronouns: write 'Managed a team of 5' instead of 'I managed a team of 5'.")
        else:
            b.add("first_person", "content", "pass", "Professional tone", "Written in a concise, impersonal style.", 2)

        long_lines = sum(1 for line in doc.lines if len(line.split()) > 60)
        if long_lines:
            b.add("paragraphs", "content", "warning", "Readable paragraphs", f"{long_lines} paragraph(s) longer than 60 words.", 2,
                  "Split long paragraphs into bullet points of one or two lines.")
        else:
            b.add("paragraphs", "content", "pass", "Readable paragraphs", "No overly long paragraphs.", 2)

        count = len(skills)
        specific = [s for s in skills if s.category not in ("soft_skill",)]
        if len(specific) >= 8:
            b.add("skills_detected", "content", "pass", "Recognisable skills", f"{count} skills were recognised.", 5)
        elif len(specific) >= 4:
            b.add("skills_detected", "content", "warning", "Recognisable skills", f"Only {len(specific)} specific skills were recognised.", 5,
                  "Name your tools, methods and certifications explicitly (e.g. 'SAP MM', 'Power BI', 'ISO 9001'), as ATS search for exact terms.")
        else:
            b.add("skills_detected", "content", "fail", "Recognisable skills", f"Only {len(specific)} specific skills were recognised.", 5,
                  "Add a skills section naming the tools, software, methods and standards you know.")

    def _keywords(
        self,
        b: _Builder,
        doc: _Doc,
        skills: Sequence[ProfileSkill],
        requirements: Sequence[JobRequirement],
        job_id: str | None,
        job_title: str | None,
        company: str | None,
    ) -> AtsKeywordMatch:
        have = {normalize_skill_key(skill.skill) for skill in skills}

        def weight(item: JobRequirement) -> float:
            return self.config.skill_weight(item.category, item.skill)

        unique: dict[str, JobRequirement] = {}
        for item in requirements:
            unique.setdefault(normalize_skill_key(item.skill), item)
        ordered = sorted(unique.values(), key=lambda item: (-weight(item), item.skill.casefold()))
        required = [r for r in ordered if r.importance == "required"]
        preferred = [r for r in ordered if r.importance != "required"]
        matched = [r.skill for r in ordered if normalize_skill_key(r.skill) in have]
        missing_required = [r.skill for r in required if normalize_skill_key(r.skill) not in have]
        missing_preferred = [r.skill for r in preferred if normalize_skill_key(r.skill) not in have]

        def share(items: list[JobRequirement]) -> float | None:
            total = sum(weight(item) for item in items)
            if total <= 0:
                return None
            return sum(weight(item) for item in items if normalize_skill_key(item.skill) in have) / total

        parts = [(share(required), 0.7), (share(preferred), 0.3)]
        weight_sum = sum(w for value, w in parts if value is not None)
        coverage = sum(value * w for value, w in parts if value is not None) / weight_sum if weight_sum else 0.0

        specific_missing = [
            r.skill for r in required + preferred
            if normalize_skill_key(r.skill) not in have and weight(r) >= 1.0
        ]
        if not unique:
            b.add("job_keywords", "keywords", "info", "Job keywords",
                  "No recognisable skills were listed in this job posting, so keywords could not be compared.", 0)
        elif coverage >= 0.7:
            b.add("job_keywords", "keywords", "pass", "Job keywords",
                  f"Your CV mentions {len(matched)} of {len(unique)} skills from this job.", 25)
        else:
            shown = ", ".join((specific_missing or missing_required or missing_preferred)[:5])
            b.add("job_keywords", "keywords", "warning" if coverage >= 0.4 else "fail", "Job keywords",
                  f"Your CV mentions {len(matched)} of {len(unique)} skills from this job.", 25,
                  f"If you have them, name these skills using the job's wording: {shown}." if shown else
                  "Use the job posting's exact wording for skills you have.")

        title_in_cv = False
        if job_title:
            title_words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", job_title) if w.casefold() not in {"senior", "junior", "lead", "stagiaire", "intern"}]
            body = strip_accents(doc.text).casefold()
            title_in_cv = bool(title_words) and sum(strip_accents(w).casefold() in body for w in title_words) >= max(1, len(title_words) // 2)
            if title_in_cv:
                b.add("job_title", "keywords", "pass", "Job title", "Your CV uses wording close to the job title.", 5)
            else:
                b.add("job_title", "keywords", "warning", "Job title",
                      f"The job title '{job_title[:80]}' does not appear in your CV.", 5,
                      "If it reflects your experience, mirror the job title in your summary or headline.")

        return AtsKeywordMatch(
            job_id=job_id,
            job_title=job_title,
            company=company,
            coverage=round(coverage, 3),
            matched=matched[:200],
            missing_required=missing_required[:200],
            missing_preferred=missing_preferred[:200],
            title_in_cv=title_in_cv,
        )


# Public helpers shared with the CV builder.
detect_sections = _detect_sections


def detect_language(text: str) -> str:
    return _language(WORD_PATTERN.findall(text or ""))
