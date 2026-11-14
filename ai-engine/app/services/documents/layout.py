"""Layout signals that affect how applicant tracking systems parse a CV.

The inspection reads the uploaded bytes once, in memory, right after the text
was extracted. It never stores the file and never raises: if a signal cannot be
read it is simply left at its default.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import asdict, dataclass, field
from typing import Any

from pypdf import PdfReader

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_PATTERN = re.compile(r"(?:\+|00)?\d[\d\s().-]{7,}\d")
_XML_TAG = re.compile(r"<[^>]+>")
_DOCX_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")
_DOCX_TABLE = re.compile(r"<w:tbl>.*?</w:tbl>", re.DOTALL)
_DOCX_FONT = re.compile(r'<w:rFonts[^>]*w:ascii="([^"]+)"')
_DOCX_COLUMNS = re.compile(r'<w:cols[^>]*w:num="(\d+)"')
MAX_XML_BYTES = 20_000_000


@dataclass(frozen=True, slots=True)
class LayoutSignals:
    document_type: str
    page_count: int | None = None
    table_count: int = 0
    table_text_ratio: float = 0.0
    image_count: int = 0
    text_box_count: int = 0
    column_count: int = 1
    header_footer_text: bool = False
    header_footer_contact: bool = False
    font_count: int = 0
    chars_per_page: float | None = None
    garbled_ratio: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> LayoutSignals | None:
        if not payload:
            return None
        known = {name: payload[name] for name in cls.__dataclass_fields__ if name in payload}
        try:
            return cls(**known)
        except TypeError:
            return None


def garbled_ratio(text: str) -> float:
    """Share of characters that signal a broken text layer (e.g. custom fonts
    without a Unicode map, which ATS parsers read as gibberish)."""
    if not text:
        return 0.0
    bad = text.count("�") + 5 * text.count("(cid:")
    bad += sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF)
    return min(1.0, bad / max(1, len(text)))


def _contains_contact(text: str) -> bool:
    return bool(EMAIL_PATTERN.search(text) or PHONE_PATTERN.search(text))


def _inspect_docx(payload: bytes, text: str) -> LayoutSignals:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()

        def read(name: str) -> str:
            info = archive.getinfo(name)
            if info.file_size > MAX_XML_BYTES:
                return ""
            return archive.read(name).decode("utf-8", errors="ignore")

        body = read("word/document.xml") if "word/document.xml" in names else ""
        all_text = "".join(_DOCX_TEXT.findall(body))
        table_text = "".join(
            "".join(_DOCX_TEXT.findall(block)) for block in _DOCX_TABLE.findall(body)
        )
        header_footer = " ".join(
            _XML_TAG.sub(" ", read(name))
            for name in names
            if re.fullmatch(r"word/(header|footer)\d*\.xml", name)
        )
        header_footer = " ".join(header_footer.split())
        columns = [int(value) for value in _DOCX_COLUMNS.findall(body)]
        media = [name for name in names if name.startswith("word/media/")]
        return LayoutSignals(
            document_type="docx",
            table_count=body.count("<w:tbl>"),
            table_text_ratio=round(len(table_text) / len(all_text), 3) if all_text else 0.0,
            image_count=max(len(media), body.count("<w:drawing>") + body.count("<w:pict>")),
            text_box_count=body.count("<w:txbxContent>"),
            column_count=max(columns) if columns else 1,
            header_footer_text=bool(header_footer),
            header_footer_contact=_contains_contact(header_footer),
            font_count=len(set(_DOCX_FONT.findall(body))),
            garbled_ratio=round(garbled_ratio(text), 4),
        )


def _inspect_pdf(payload: bytes, text: str, page_count: int | None) -> LayoutSignals:
    reader = PdfReader(io.BytesIO(payload), strict=False)
    pages = list(reader.pages)
    images = 0
    fonts: set[str] = set()
    for page in pages[:20]:
        resources = page.get("/Resources") or {}
        try:
            resources = resources.get_object()
        except AttributeError:
            pass
        xobjects = resources.get("/XObject") if hasattr(resources, "get") else None
        if xobjects is not None:
            for ref in xobjects.get_object().values():
                try:
                    if ref.get_object().get("/Subtype") == "/Image":
                        images += 1
                except Exception:  # pragma: no cover - malformed objects
                    continue
        font_map = resources.get("/Font") if hasattr(resources, "get") else None
        if font_map is not None:
            for ref in font_map.get_object().values():
                try:
                    name = str(ref.get_object().get("/BaseFont") or "")
                except Exception:  # pragma: no cover
                    continue
                if name:
                    fonts.add(name.split("+")[-1].split("-")[0].split(",")[0])
    count = page_count or len(pages) or None
    return LayoutSignals(
        document_type="pdf",
        page_count=count,
        image_count=images,
        font_count=len(fonts),
        chars_per_page=round(len(text) / count, 1) if count else None,
        garbled_ratio=round(garbled_ratio(text), 4),
    )


def inspect_layout(
    payload: bytes,
    document_type: str,
    text: str,
    page_count: int | None = None,
) -> LayoutSignals:
    try:
        if document_type == "docx":
            return _inspect_docx(payload, text)
        if document_type == "pdf":
            return _inspect_pdf(payload, text, page_count)
    except Exception:  # never block an upload because inspection failed
        return LayoutSignals(
            document_type=document_type,
            page_count=page_count,
            garbled_ratio=round(garbled_ratio(text), 4),
            notes=["layout_inspection_failed"],
        )
    return LayoutSignals(document_type=document_type, garbled_ratio=round(garbled_ratio(text), 4))
