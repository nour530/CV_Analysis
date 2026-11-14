"""Render a CvDocument as plain text (for ATS checks) or an ATS-safe DOCX.

The DOCX uses a single column, real Word heading and bullet styles, standard
section names, contact details in the body (never in the page header), no
tables, text boxes or images, and a standard font.
"""

from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from app.schemas.cv_document import CvDocument

HEADINGS = {
    "en": {
        "summary": "Professional Summary",
        "experience": "Professional Experience",
        "education": "Education",
        "skills": "Skills",
        "languages": "Languages",
        "certifications": "Certifications",
        "projects": "Projects",
        "present": "Present",
    },
    "fr": {
        "summary": "Profil",
        "experience": "Expérience professionnelle",
        "education": "Formation",
        "skills": "Compétences",
        "languages": "Langues",
        "certifications": "Certifications",
        "projects": "Projets",
        "present": "Aujourd'hui",
    },
}
TEMPLATE_STYLES = {
    "classic": {"font": "Calibri", "size": 10.5, "accent": RGBColor(0x1F, 0x2A, 0x44)},
    "modern": {"font": "Arial", "size": 10, "accent": RGBColor(0x3B, 0x2F, 0xB8)},
    "compact": {"font": "Calibri", "size": 9.5, "accent": RGBColor(0x22, 0x22, 0x22)},
}


def _range(start: str | None, end: str | None, current: bool, present: str) -> str:
    finish = present if current else (end or "")
    if start and finish:
        return f"{start} – {finish}"
    return start or finish


def contact_line(doc: CvDocument) -> str:
    c = doc.contact
    return " | ".join(part for part in (c.location, c.phone, c.email, c.linkedin, c.website) if part)


def render_text(doc: CvDocument, language: str = "en") -> str:
    h = HEADINGS.get(language, HEADINGS["en"])
    lines: list[str] = []
    if doc.contact.full_name:
        lines.append(doc.contact.full_name)
    if doc.contact.headline:
        lines.append(doc.contact.headline)
    if contact_line(doc):
        lines.append(contact_line(doc))
    if doc.summary:
        lines += ["", h["summary"].upper(), doc.summary]
    if doc.experience:
        lines += ["", h["experience"].upper()]
        for item in doc.experience:
            head = ", ".join(part for part in (item.title, item.company, item.location) if part)
            dates = _range(item.start, item.end, item.current, h["present"])
            lines.append(f"{head} | {dates}" if dates else head)
            lines += [f"- {bullet}" for bullet in item.bullets]
    if doc.education:
        lines += ["", h["education"].upper()]
        for item in doc.education:
            head = ", ".join(part for part in (item.degree, item.school, item.location) if part)
            dates = _range(item.start, item.end, False, h["present"])
            lines.append(f"{head} | {dates}" if dates else head)
            lines += [f"- {detail}" for detail in item.details]
    if doc.projects:
        lines += ["", h["projects"].upper()]
        for item in doc.projects:
            lines.append(item.name or "")
            if item.description:
                lines.append(item.description)
            lines += [f"- {bullet}" for bullet in item.bullets]
    if doc.skills:
        lines += ["", h["skills"].upper(), ", ".join(doc.skills)]
    if doc.languages:
        lines += ["", h["languages"].upper()]
        lines.append(" · ".join(f"{l.name}: {l.level}" if l.level else l.name for l in doc.languages))
    if doc.certifications:
        lines += ["", h["certifications"].upper()]
        lines += [f"- {item}" for item in doc.certifications]
    return "\n".join(lines).strip() + "\n"


def _set_font(run, font: str, size: float, *, bold: bool = False, color: RGBColor | None = None) -> None:
    run.font.name = font
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font)
    run.font.size = Pt(size)
    run.bold = bold
    if color is not None:
        run.font.color.rgb = color


def render_docx(doc: CvDocument, *, language: str = "en", template: str = "classic") -> bytes:
    h = HEADINGS.get(language, HEADINGS["en"])
    style = TEMPLATE_STYLES.get(template, TEMPLATE_STYLES["classic"])
    font, size, accent = style["font"], style["size"], style["accent"]
    document = Document()
    for section in document.sections:
        section.top_margin = section.bottom_margin = Pt(42)
        section.left_margin = section.right_margin = Pt(48)
    normal = document.styles["Normal"]
    normal.font.name = font
    normal.font.size = Pt(size)
    normal.paragraph_format.space_after = Pt(2)

    def paragraph(text: str = "", *, bold: bool = False, size_delta: float = 0, color=None, align=None, space_after=2):
        para = document.add_paragraph()
        para.paragraph_format.space_after = Pt(space_after)
        if align is not None:
            para.alignment = align
        if text:
            _set_font(para.add_run(text), font, size + size_delta, bold=bold, color=color)
        return para

    def heading(text: str) -> None:
        para = document.add_heading(level=2)
        para.paragraph_format.space_before = Pt(10)
        para.paragraph_format.space_after = Pt(3)
        _set_font(para.add_run(text.upper()), font, size + 1, bold=True, color=accent)

    def bullet(text: str) -> None:
        para = document.add_paragraph(style="List Bullet")
        para.paragraph_format.space_after = Pt(1)
        _set_font(para.add_run(text), font, size)

    def entry(title: str, dates: str, subtitle: str | None) -> None:
        para = paragraph(space_after=0)
        para.paragraph_format.space_before = Pt(4)
        _set_font(para.add_run(title), font, size, bold=True)
        if dates:
            _set_font(para.add_run(f" | {dates}"), font, size)
        if subtitle:
            paragraph(subtitle, size_delta=-0.5, color=RGBColor(0x55, 0x55, 0x55), space_after=1)

    if doc.contact.full_name:
        paragraph(doc.contact.full_name, bold=True, size_delta=8, color=accent, space_after=0)
    if doc.contact.headline:
        paragraph(doc.contact.headline, size_delta=1.5, space_after=1)
    if contact_line(doc):
        paragraph(contact_line(doc), size_delta=-0.5, space_after=4)

    if doc.summary:
        heading(h["summary"])
        paragraph(doc.summary)
    if doc.experience:
        heading(h["experience"])
        for item in doc.experience:
            entry(
                " — ".join(part for part in (item.title, item.company) if part) or "",
                _range(item.start, item.end, item.current, h["present"]),
                item.location,
            )
            for text in item.bullets:
                bullet(text)
    if doc.education:
        heading(h["education"])
        for item in doc.education:
            entry(
                " — ".join(part for part in (item.degree, item.school) if part) or "",
                _range(item.start, item.end, False, h["present"]),
                item.location,
            )
            for text in item.details:
                bullet(text)
    if doc.projects:
        heading(h["projects"])
        for item in doc.projects:
            entry(item.name or "", "", item.description)
            for text in item.bullets:
                bullet(text)
    if doc.skills:
        heading(h["skills"])
        paragraph(", ".join(doc.skills))
    if doc.languages:
        heading(h["languages"])
        paragraph(" · ".join(f"{l.name}: {l.level}" if l.level else l.name for l in doc.languages))
    if doc.certifications:
        heading(h["certifications"])
        for text in doc.certifications:
            bullet(text)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def export_filename(doc: CvDocument, title: str, extension: str) -> str:
    base = doc.contact.full_name or title or "cv"
    safe = re.sub(r"[^\w-]+", "_", base, flags=re.UNICODE).strip("_")[:60] or "cv"
    return f"{safe}_CV.{extension}"
