"""Turn extracted CV text into an editable CvDocument (deterministic, local).

The parser is a starting point the user corrects in the editor, so it prefers
keeping text over guessing: anything it cannot place precisely stays in the
section where it was found, usually as a bullet.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.schemas.cv_document import (
    CvContact,
    CvDocument,
    CvEducation,
    CvExperience,
    CvLanguage,
    CvProject,
)
from app.services.ats.checker import (
    BULLET_PATTERN,
    DATE_PATTERN,
    LINKEDIN_PATTERN,
    PHONE_PATTERN,
    PRESENT_PATTERN,
    TUNISIAN_PLACES,
    detect_sections,
    detect_language,
)
from app.services.documents.layout import EMAIL_PATTERN
from app.services.nlp.skills import LocalSkillExtractor, strip_accents

URL_PATTERN = re.compile(r"(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:/[\w./%#-]*)?", re.IGNORECASE)
RANGE_SPLIT = re.compile(r"\s*(?:–|—|-|to|à|au|until|jusqu'à)\s*", re.IGNORECASE)
HEADER_SPLIT = re.compile(r"\s+(?:—|–|-|\||@|at|chez)\s+|\s*\|\s*|,\s+", re.IGNORECASE)
LIST_SPLIT = re.compile(r"\s*(?:[,;·•|/]|\s-\s)\s*")
DEGREE_HINT = re.compile(
    r"(?i)\b(?:master|bachelor|licen[cs]e|ing[ée]nieur|engineer(?:ing)?|dipl[oô]me|degree|"
    r"doctorat|phd|bac(?:calaur[ée]at)?|mba|bts|dut|msc|bsc|ma|ba|cycle|prépa|preparatory)\b"
)
SCHOOL_HINT = re.compile(
    r"(?i)\b(?:universit[ée]|university|[ée]cole|school|institut[e]?|faculty|facult[ée]|"
    r"college|coll[eè]ge|lyc[ée]e|ensi|enit|enis|enig|essec|ihec|isg|insat|esprit|polytech)\w*"
)


def _dates(line: str) -> tuple[str | None, str | None, bool]:
    matches = [m.group(0) for m in DATE_PATTERN.finditer(line)]
    current = bool(PRESENT_PATTERN.search(line))
    if not matches:
        return None, None, current
    start = matches[0]
    end = matches[1] if len(matches) > 1 else None
    if current:
        end = None
    elif end is None and len(matches) == 1:
        end = None
    return start, end, current


def _strip_dates(line: str) -> str:
    text = DATE_PATTERN.sub(" ", line)
    text = PRESENT_PATTERN.sub(" ", text)
    text = re.sub(r"[()\[\]]", " ", text)
    text = re.sub(r"\s*(?:–|—|-|to|à|au)\s*$", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"(?:\s*[|,–—-]\s*)+$", "", text)
    text = re.sub(r"^(?:\s*[|,–—-]\s*)+", "", text)
    return " ".join(text.split())


def _has_dates(line: str) -> bool:
    return bool(DATE_PATTERN.search(line) or PRESENT_PATTERN.search(line))


def _is_bullet(line: str) -> bool:
    return bool(BULLET_PATTERN.match(line))


def _bullet_text(line: str) -> str:
    return BULLET_PATTERN.sub("", line).strip()


def _is_location(text: str) -> bool:
    plain = strip_accents(text).casefold()
    return any(re.search(rf"\b{re.escape(place)}\b", plain) for place in TUNISIAN_PLACES) or bool(
        re.fullmatch(r"[A-Z][\w' -]+,\s*[A-Z][\w' -]+", text.strip())
    )


def _split_header(text: str) -> list[str]:
    return [part.strip() for part in HEADER_SPLIT.split(text) if part and part.strip()]


def _group_entries(lines: Sequence[str]) -> list[tuple[list[str], list[str]]]:
    """Group a section into (header lines, bullet lines) entries.

    A new entry starts at a non-bullet line that follows bullets, or at a line
    with dates when the current entry already has dates.
    """
    entries: list[tuple[list[str], list[str]]] = []
    header: list[str] = []
    bullets: list[str] = []
    for line in lines:
        if _is_bullet(line):
            bullets.append(_bullet_text(line))
            continue
        long_line = len(line.split()) > 14
        if long_line and (header or bullets):
            bullets.append(line)
            continue
        header_has_dates = any(_has_dates(item) for item in header)
        if bullets or (header_has_dates and _has_dates(line)) or len(header) >= 3:
            entries.append((header, bullets))
            header, bullets = [], []
        header.append(line)
    if header or bullets:
        entries.append((header, bullets))
    return entries


def _parse_experience(lines: Sequence[str]) -> list[CvExperience]:
    items: list[CvExperience] = []
    for header, bullets in _group_entries(lines):
        start = end = None
        current = False
        parts: list[str] = []
        for line in header:
            if _has_dates(line):
                start, end, current = _dates(line)
            remainder = _strip_dates(line)
            if remainder:
                parts.extend(_split_header(remainder))
        location = next((part for part in parts if _is_location(part)), None)
        names = [part for part in parts if part != location]
        items.append(
            CvExperience(
                title=names[0][:160] if names else None,
                company=names[1][:160] if len(names) > 1 else None,
                location=location[:120] if location else None,
                start=start,
                end=end,
                current=current,
                bullets=[*names[2:], *bullets][:20],
            )
        )
    return [item for item in items if item.title or item.bullets][:30]


def _parse_education(lines: Sequence[str]) -> list[CvEducation]:
    items: list[CvEducation] = []
    for header, details in _group_entries(lines):
        start = end = None
        parts: list[str] = []
        for line in header:
            if _has_dates(line):
                start, end, _ = _dates(line)
            remainder = _strip_dates(line)
            if remainder:
                parts.extend(_split_header(remainder))
        location = next((part for part in parts if _is_location(part) and not SCHOOL_HINT.search(part)), None)
        rest = [part for part in parts if part != location]
        degree = next((part for part in rest if DEGREE_HINT.search(part)), rest[0] if rest else None)
        school = next((part for part in rest if part != degree and SCHOOL_HINT.search(part)), None)
        if school is None:
            school = next((part for part in rest if part != degree), None)
        extra = [part for part in rest if part not in (degree, school)]
        items.append(
            CvEducation(
                degree=degree[:200] if degree else None,
                school=school[:200] if school else None,
                location=location[:120] if location else None,
                start=start,
                end=end,
                details=[*extra, *details][:10],
            )
        )
    return [item for item in items if item.degree or item.school][:15]


def _parse_projects(lines: Sequence[str]) -> list[CvProject]:
    items: list[CvProject] = []
    for header, bullets in _group_entries(lines):
        name = _strip_dates(header[0]) if header else None
        description = " ".join(header[1:]) or None
        items.append(CvProject(name=name[:160] if name else None, description=description[:600] if description else None, bullets=bullets[:10]))
    return [item for item in items if item.name or item.bullets][:15]


def _parse_list(lines: Sequence[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        text = _bullet_text(line) if _is_bullet(line) else line
        if ":" in text and len(text.split(":", 1)[0].split()) <= 4:
            text = text.split(":", 1)[1]
        values.extend(part.strip(" .") for part in LIST_SPLIT.split(text) if part.strip(" ."))
    return [value for value in values if 1 <= len(value) <= 60]


def _parse_languages(lines: Sequence[str]) -> list[CvLanguage]:
    result: list[CvLanguage] = []
    for line in lines:
        text = _bullet_text(line) if _is_bullet(line) else line
        for part in re.split(r"\s*[·,;|•]\s*", text):
            part = part.strip(" .")
            if not part:
                continue
            match = re.match(r"^([A-Za-zÀ-ÿ' ]{2,40}?)\s*(?:([:(–—-])\s*(.+))?$", part)
            if not match:
                continue
            name = match.group(1).strip()
            level = (match.group(3) or "").strip() or None
            if level and match.group(2) == "(" and level.endswith(")") and "(" not in level:
                level = level[:-1].strip() or None
            if len(name.split()) <= 3:
                result.append(CvLanguage(name=name[:60], level=level[:60] if level else None))
    return result[:12]


def _parse_contact(header_lines: Sequence[str], text: str) -> CvContact:
    email = EMAIL_PATTERN.search(text)
    linkedin = LINKEDIN_PATTERN.search(text)
    phone = next(
        (
            m.group(0).strip()
            for m in PHONE_PATTERN.finditer("\n".join(header_lines) or text[:600])
            if 8 <= len(re.sub(r"\D", "", m.group(0))) <= 15
        ),
        None,
    )
    name = headline = location = website = None
    for line in header_lines[:8]:
        parts = [part.strip() for part in re.split(r"\s*[|·•]\s*|\s{2,}", line) if part.strip()]
        for part in parts:
            if EMAIL_PATTERN.search(part) or LINKEDIN_PATTERN.search(part) or (phone and phone in part):
                continue
            if URL_PATTERN.fullmatch(part) and "." in part:
                website = website or part
                continue
            if _is_location(part) and len(part.split()) <= 5:
                location = location or part
                continue
            words = part.split()
            if name is None and 1 < len(words) <= 5 and not any(ch.isdigit() for ch in part):
                name = part
            elif headline is None and len(words) <= 12 and part != name:
                headline = part
    return CvContact(
        full_name=name.title() if name and name.isupper() else name,
        headline=headline,
        email=email.group(0) if email else None,
        phone=phone,
        location=location,
        linkedin=linkedin.group(0) if linkedin else None,
        website=website,
    )


def parse_cv_text(text: str, *, extractor: LocalSkillExtractor | None = None) -> CvDocument:
    lines = [line.strip() for line in (text or "").replace("\r", "").split("\n") if line.strip()]
    sections, content = detect_sections(lines)
    first_heading = next(
        (index for index, line in enumerate(lines) if line in sections.values()), len(lines)
    )
    header_lines = lines[:first_heading] if first_heading < len(lines) else lines[:6]

    skills = _parse_list(content.get("skills", []))
    if not skills and extractor is not None:
        skills = [item.skill for item in extractor.extract_skills(text)][:40]

    summary_lines = content.get("summary", [])
    summary = " ".join(_bullet_text(line) if _is_bullet(line) else line for line in summary_lines)

    return CvDocument(
        contact=_parse_contact(header_lines, text),
        summary=summary[:1500] or None,
        experience=_parse_experience(content.get("experience", [])),
        education=_parse_education(content.get("education", [])),
        skills=skills[:80],
        languages=_parse_languages(content.get("languages", [])),
        certifications=[
            (_bullet_text(line) if _is_bullet(line) else line)[:120]
            for line in content.get("certifications", [])
        ][:30],
        projects=_parse_projects(content.get("projects", [])),
    )


def document_language(text: str) -> str:
    language = detect_language(text)
    return "fr" if language == "fr" else "en"
