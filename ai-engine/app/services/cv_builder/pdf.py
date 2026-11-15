"""Render a CvDocument as an ATS-safe PDF, with no third-party dependency.

The PDF is text-based (real, selectable text in the standard Helvetica font
with WinAnsi encoding), single column, with standard section names and no
images, tables or text boxes, so applicant tracking systems can read it.
Text is measured with the Adobe Helvetica metrics to wrap lines and break
pages.
"""

from __future__ import annotations

import unicodedata
import zlib
from dataclasses import dataclass

from app.schemas.cv_document import CvDocument
from app.services.cv_builder.render import HEADINGS, _range, contact_line

# Glyph widths (1/1000 em) for WinAnsi codes 32-255, from the Adobe Helvetica AFM metrics.
HELVETICA_WIDTHS = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584, 0,
    556, 0, 222, 556, 333, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0,
    0, 222, 222, 333, 333, 350, 556, 1000, 333, 1000, 500, 333, 944, 0, 500, 667,
    278, 333, 556, 556, 556, 556, 260, 556, 333, 737, 370, 556, 584, 333, 737, 333,
    400, 584, 333, 333, 333, 556, 537, 278, 333, 333, 365, 556, 834, 834, 834, 611,
    667, 667, 667, 667, 667, 667, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278,
    722, 722, 778, 778, 778, 778, 778, 584, 778, 722, 722, 722, 722, 667, 667, 611,
    556, 556, 556, 556, 556, 556, 889, 500, 556, 556, 556, 556, 278, 278, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 584, 611, 556, 556, 556, 556, 500, 556, 500,
)
HELVETICA_BOLD_WIDTHS = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
    975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
    333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
    611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584, 0,
    556, 0, 278, 556, 500, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0,
    0, 278, 278, 500, 500, 350, 556, 1000, 333, 1000, 556, 333, 944, 0, 500, 667,
    278, 333, 556, 556, 556, 556, 280, 556, 333, 737, 370, 556, 584, 333, 737, 333,
    400, 584, 333, 333, 333, 611, 556, 278, 333, 333, 365, 556, 834, 834, 834, 611,
    722, 722, 722, 722, 722, 722, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278,
    722, 722, 778, 778, 778, 778, 778, 584, 778, 722, 722, 722, 722, 667, 667, 611,
    556, 556, 556, 556, 556, 556, 889, 556, 556, 556, 556, 556, 278, 278, 278, 278,
    611, 611, 611, 611, 611, 611, 611, 584, 611, 611, 611, 611, 611, 556, 611, 556,
)

PAGE_WIDTH, PAGE_HEIGHT = 595.28, 841.89  # A4 in points
GREY = (0.33, 0.33, 0.33)
BLACK = (0.11, 0.11, 0.12)
REPLACEMENTS = {"\u2011": "-", "\u2212": "-", "\u2010": "-", "\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u200b": ""}


@dataclass(frozen=True, slots=True)
class PdfStyle:
    size: float
    accent: tuple[float, float, float]
    margin_x: float
    margin_y: float
    rule_width: float


PDF_STYLES = {
    "classic": PdfStyle(10.0, (0.122, 0.165, 0.267), 52, 46, 0.6),
    "modern": PdfStyle(10.0, (0.231, 0.184, 0.722), 52, 46, 1.4),
    "compact": PdfStyle(9.2, (0.133, 0.133, 0.133), 42, 36, 0.6),
}


def _encode(text: str) -> bytes:
    """Text as WinAnsi (cp1252) bytes; other characters lose their accents
    or become '?'."""
    out = bytearray()
    for char in text:
        char = REPLACEMENTS.get(char, char)
        if not char:
            continue
        try:
            out += char.encode("cp1252")
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", char).encode("cp1252", "ignore")
            out += base or b"?"
    return bytes(out)


def _literal(data: bytes) -> bytes:
    escaped = data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").replace(b"\r", b"\\r")
    return b"(" + escaped + b")"


def text_width(text: str, size: float, bold: bool = False) -> float:
    table = HELVETICA_BOLD_WIDTHS if bold else HELVETICA_WIDTHS
    return sum(table[c - 32] if c >= 32 else 0 for c in _encode(text)) * size / 1000


def wrap(text: str, size: float, max_width: float, bold: bool = False) -> list[str]:
    lines: list[str] = []
    for paragraph in (text or "").splitlines():
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}" if current else word
            if text_width(candidate, size, bold) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            # A single word wider than the line (a long URL) is split.
            while text_width(word, size, bold) > max_width:
                cut = len(word) - 1
                while cut > 1 and text_width(word[:cut], size, bold) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        if current:
            lines.append(current)
    return lines


class _Canvas:
    def __init__(self, style: PdfStyle) -> None:
        self.style = style
        self.pages: list[list[bytes]] = []
        self.left = style.margin_x
        self.right = PAGE_WIDTH - style.margin_x
        self.width = self.right - self.left
        self._new_page()

    def _new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_HEIGHT - self.style.margin_y

    def ensure(self, height: float) -> None:
        """Start a new page if `height` points do not fit on this one."""
        top = PAGE_HEIGHT - self.style.margin_y
        if self.y - height < self.style.margin_y and self.y < top:
            self._new_page()

    def text(self, x: float, text: str, size: float, *, bold: bool = False, color=BLACK) -> None:
        r, g, b = color
        self.pages[-1].append(
            b"BT /%s %.2f Tf %.3f %.3f %.3f rg %.2f %.2f Td %s Tj ET"
            % (b"F2" if bold else b"F1", size, r, g, b, x, self.y, _literal(_encode(text)))
        )

    def rule(self, y: float, color, width: float) -> None:
        r, g, b = color
        self.pages[-1].append(
            b"%.3f %.3f %.3f RG %.2f w %.2f %.2f m %.2f %.2f l S" % (r, g, b, width, self.left, y, self.right, y)
        )

    def advance(self, amount: float) -> None:
        self.y -= amount


def _pdf_string(text: str) -> bytes:
    return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode() + b">"


def _assemble(pages: list[list[bytes]], title: str) -> bytes:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in once the page ids are known
    pages_id = add(b"")
    regular = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    info = add(b"<< /Title %s /Producer (JobMatch AI) /Creator (JobMatch AI CV builder) >>" % _pdf_string(title))
    kids: list[int] = []
    for commands in pages:
        stream = zlib.compress(b"\n".join(commands))
        content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream" % (len(stream), stream))
        kids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
                b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_id, PAGE_WIDTH, PAGE_HEIGHT, regular, bold, content)
            )
        )
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
    objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % kid for kid in kids),
        len(kids),
    )
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(output)
    output += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    output += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    output += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        info,
        xref,
    )
    return bytes(output)


def render_pdf(doc: CvDocument, *, language: str = "en", template: str = "classic") -> bytes:
    h = HEADINGS.get(language, HEADINGS["en"])
    style = PDF_STYLES.get(template, PDF_STYLES["classic"])
    size = style.size
    leading = size * 1.35
    canvas = _Canvas(style)

    def lines(text: str, *, text_size: float = size, bold: bool = False, color=BLACK, indent: float = 0, gap: float = 1.5) -> None:
        for line in wrap(text, text_size, canvas.width - indent, bold):
            canvas.ensure(text_size * 1.35)
            canvas.advance(text_size * 1.35)
            canvas.text(canvas.left + indent, line, text_size, bold=bold, color=color)
        canvas.advance(gap)

    def heading(text: str) -> None:
        canvas.ensure(size * 6)  # keep a heading with the first lines under it
        canvas.advance(size * 1.2 + size + 1)
        canvas.text(canvas.left, text.upper(), size + 1, bold=True, color=style.accent)
        canvas.rule(canvas.y - 3.5, style.accent, style.rule_width)
        canvas.advance(5)

    def bullet(text: str) -> None:
        indent = size * 1.3
        for index, line in enumerate(wrap(text, size, canvas.width - indent)):
            canvas.ensure(leading)
            canvas.advance(leading)
            if index == 0:
                canvas.text(canvas.left + size * 0.25, "\u2022", size)
            canvas.text(canvas.left + indent, line, size)
        canvas.advance(0.8)

    def entry(title: str, dates: str, subtitle: str | None) -> None:
        canvas.ensure(leading * 3)
        canvas.advance(size * 0.5)
        date_width = text_width(dates, size) + size if dates else 0
        for index, line in enumerate(wrap(title, size, canvas.width - date_width, bold=True) or [""]):
            canvas.advance(leading)
            canvas.text(canvas.left, line, size, bold=True)
            if index == 0 and dates:
                canvas.text(canvas.right - text_width(dates, size), dates, size, color=GREY)
        if subtitle:
            lines(subtitle, text_size=size - 0.5, color=GREY, gap=0.5)

    if doc.contact.full_name:
        canvas.advance(size + 6)
        canvas.text(canvas.left, doc.contact.full_name, size + 9, bold=True, color=style.accent)
        canvas.advance(4)
    if doc.contact.headline:
        lines(doc.contact.headline, text_size=size + 1.5, gap=0.5)
    if contact_line(doc):
        lines(contact_line(doc), text_size=size - 0.5, color=GREY, gap=2)

    if doc.summary:
        heading(h["summary"])
        lines(doc.summary)
    if doc.experience:
        heading(h["experience"])
        for item in doc.experience:
            entry(
                " — ".join(part for part in (item.title, item.company) if part),
                _range(item.start, item.end, item.current, h["present"]),
                item.location,
            )
            for text in item.bullets:
                bullet(text)
    if doc.education:
        heading(h["education"])
        for item in doc.education:
            entry(
                " — ".join(part for part in (item.degree, item.school) if part),
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
        lines(", ".join(doc.skills))
    if doc.languages:
        heading(h["languages"])
        lines(" · ".join(f"{lang.name}: {lang.level}" if lang.level else lang.name for lang in doc.languages))
    if doc.certifications:
        heading(h["certifications"])
        for text in doc.certifications:
            bullet(text)

    title = f"{doc.contact.full_name} CV" if doc.contact.full_name else "CV"
    return _assemble(canvas.pages, title)
