from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.services.documents.layout import LayoutSignals, inspect_layout

DocumentType = Literal["text", "pdf", "docx"]
ExtractionStatus = Literal["success"]

TEXT_EXTENSIONS = frozenset({".txt", ".text", ".md", ".csv"})
PDF_EXTENSIONS = frozenset({".pdf"})
DOCX_EXTENSIONS = frozenset({".docx"})
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | PDF_EXTENSIONS | DOCX_EXTENSIONS

TEXT_MIME_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "text/csv",
        "text/x-csv",
        "application/csv",
    }
)
PDF_MIME_TYPES = frozenset({"application/pdf", "application/x-pdf"})
DOCX_MIME_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    }
)
GENERIC_MIME_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})

PDF_HEADER = b"%PDF-"
ZIP_HEADER = b"PK\x03\x04"
OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HORIZONTAL_WHITESPACE = re.compile(r"[ \t]+")


class DocumentExtractionError(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    text: str
    filename: str
    document_type: DocumentType
    page_count: int | None
    extraction_status: ExtractionStatus
    character_count: int
    layout: LayoutSignals | None = field(default=None, compare=False)


class DocumentExtractionService:
    def extract(
        self,
        payload: bytes,
        *,
        filename: str,
        content_type: str | None,
        max_chars: int,
        max_pdf_pages: int | None = None,
        max_docx_entries: int | None = None,
        max_docx_uncompressed_bytes: int | None = None,
        max_docx_compression_ratio: int | None = None,
    ) -> ExtractedDocument:
        if not payload:
            raise DocumentExtractionError(
                "Uploaded document is empty.",
                code="empty_document",
            )

        safe_filename = self._safe_filename(filename)
        suffix = Path(safe_filename).suffix.casefold()
        document_type = self._document_type(suffix)
        self._validate_mime_type(content_type, suffix)
        self._validate_signature(
            payload,
            document_type,
            safe_filename,
            max_docx_entries=max_docx_entries,
            max_docx_uncompressed_bytes=max_docx_uncompressed_bytes,
            max_docx_compression_ratio=max_docx_compression_ratio,
        )

        if document_type == "pdf":
            text, page_count = self._extract_pdf(payload, max_pdf_pages=max_pdf_pages)
        elif document_type == "docx":
            text = self._extract_docx(payload)
            page_count = None
        else:
            text = self._extract_text(payload)
            page_count = None

        normalized = normalize_extracted_text(text)
        if len(normalized) > max_chars:
            raise DocumentExtractionError(
                f"Extracted text exceeds the {max_chars} character limit.",
                code="text_too_large",
            )
        if not _has_meaningful_text(normalized):
            message = (
                "The PDF contains no extractable text. Scanned or image-only PDFs "
                "require OCR, which is not supported."
                if document_type == "pdf"
                else "The document does not contain meaningful text."
            )
            raise DocumentExtractionError(message, code="no_usable_text")

        return ExtractedDocument(
            text=normalized,
            filename=safe_filename,
            document_type=document_type,
            page_count=page_count,
            extraction_status="success",
            character_count=len(normalized),
            layout=inspect_layout(payload, document_type, normalized, page_count),
        )

    @staticmethod
    def _safe_filename(filename: str) -> str:
        safe = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not safe:
            raise DocumentExtractionError(
                "Uploaded document must have a filename with a supported extension.",
                code="unsupported_document_type",
            )
        return safe[:255]

    @staticmethod
    def _document_type(suffix: str) -> DocumentType:
        if suffix in TEXT_EXTENSIONS:
            return "text"
        if suffix in PDF_EXTENSIONS:
            return "pdf"
        if suffix in DOCX_EXTENSIONS:
            return "docx"
        raise DocumentExtractionError(
            "Unsupported document format. Upload a TXT, PDF, or DOCX CV.",
            code="unsupported_document_type",
        )

    @staticmethod
    def _validate_mime_type(content_type: str | None, suffix: str) -> None:
        normalized = (content_type or "").split(";", 1)[0].strip().casefold()
        if normalized in GENERIC_MIME_TYPES:
            return

        if suffix in PDF_EXTENSIONS:
            allowed = PDF_MIME_TYPES
        elif suffix in DOCX_EXTENSIONS:
            allowed = DOCX_MIME_TYPES
        else:
            allowed = TEXT_MIME_TYPES

        if normalized not in allowed:
            raise DocumentExtractionError(
                f"Declared content type '{normalized}' does not match the {suffix} "
                "extension.",
                code="mime_type_mismatch",
            )

    @staticmethod
    def _validate_signature(
        payload: bytes,
        document_type: DocumentType,
        filename: str,
        *,
        max_docx_entries: int | None = None,
        max_docx_uncompressed_bytes: int | None = None,
        max_docx_compression_ratio: int | None = None,
    ) -> None:
        if document_type == "pdf":
            if PDF_HEADER not in payload[:1024]:
                raise DocumentExtractionError(
                    f"'{filename}' is not a valid PDF document.",
                    code="invalid_document",
                )
            return

        if document_type == "docx":
            if payload.startswith(OLE_HEADER):
                raise DocumentExtractionError(
                    "Password-protected DOCX files are not supported.",
                    code="encrypted_document",
                )
            if not payload.startswith(ZIP_HEADER):
                raise DocumentExtractionError(
                    f"'{filename}' is not a valid DOCX document.",
                    code="invalid_document",
                )
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    entries = archive.infolist()
            except (zipfile.BadZipFile, OSError) as exc:
                raise DocumentExtractionError(
                    f"'{filename}' is not a valid DOCX document.",
                    code="invalid_document",
                ) from exc
            names = {entry.filename for entry in entries}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise DocumentExtractionError(
                    f"'{filename}' is not a valid DOCX document.",
                    code="invalid_document",
                )
            DocumentExtractionService._validate_docx_resources(
                entries,
                max_entries=max_docx_entries,
                max_uncompressed_bytes=max_docx_uncompressed_bytes,
                max_compression_ratio=max_docx_compression_ratio,
            )
            return

        if b"\x00" in payload:
            raise DocumentExtractionError(
                "The uploaded text file contains binary data.",
                code="invalid_document",
            )

    @staticmethod
    def _validate_docx_resources(
        entries: list[zipfile.ZipInfo],
        *,
        max_entries: int | None,
        max_uncompressed_bytes: int | None,
        max_compression_ratio: int | None,
    ) -> None:
        if max_entries is not None and len(entries) > max_entries:
            raise DocumentExtractionError(
                f"The DOCX archive contains more than the {max_entries} "
                "allowed entries.",
                code="docx_entry_limit_exceeded",
            )

        total_uncompressed = sum(max(0, entry.file_size) for entry in entries)
        if (
            max_uncompressed_bytes is not None
            and total_uncompressed > max_uncompressed_bytes
        ):
            raise DocumentExtractionError(
                "The DOCX archive expands beyond the "
                f"{max_uncompressed_bytes} byte limit.",
                code="docx_uncompressed_limit_exceeded",
            )

        if max_compression_ratio is None or max_compression_ratio <= 0:
            return

        total_compressed = sum(max(0, entry.compress_size) for entry in entries)
        if (
            total_compressed > 0
            and total_uncompressed / total_compressed > max_compression_ratio
        ):
            raise DocumentExtractionError(
                "The DOCX archive exceeds the allowed compression ratio.",
                code="docx_compression_ratio_exceeded",
            )

        for entry in entries:
            if entry.compress_size <= 0 or entry.file_size <= 0:
                continue
            if entry.file_size / entry.compress_size > max_compression_ratio:
                raise DocumentExtractionError(
                    "The DOCX archive exceeds the allowed compression ratio.",
                    code="docx_compression_ratio_exceeded",
                )

    @staticmethod
    def _extract_text(payload: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise DocumentExtractionError(
            "The uploaded text file could not be decoded.",
            code="invalid_document",
        )

    @staticmethod
    def _extract_pdf(
        payload: bytes,
        *,
        max_pdf_pages: int | None = None,
    ) -> tuple[str, int]:
        try:
            reader = PdfReader(io.BytesIO(payload), strict=False)
            if reader.is_encrypted:
                raise DocumentExtractionError(
                    "Password-protected PDF files are not supported.",
                    code="encrypted_document",
                )
            page_count = len(reader.pages)
            if max_pdf_pages is not None and page_count > max_pdf_pages:
                raise DocumentExtractionError(
                    f"The PDF contains more than the {max_pdf_pages} allowed pages.",
                    code="pdf_page_limit_exceeded",
                )
            pages = list(reader.pages)
            parts = [
                text
                for page in pages
                if (text := (page.extract_text() or "").strip())
            ]
        except DocumentExtractionError:
            raise
        except (PdfReadError, OSError, ValueError, TypeError, KeyError, IndexError, EOFError) as exc:
            raise DocumentExtractionError(
                "The PDF document could not be read. It may be corrupted.",
                code="invalid_document",
            ) from exc
        except Exception as exc:
            if isinstance(exc, MemoryError):
                raise
            raise DocumentExtractionError(
                "The PDF document could not be read. It may be corrupted.",
                code="invalid_document",
            ) from exc
        return "\n\n".join(parts), len(pages)

    @staticmethod
    def _extract_docx(payload: bytes) -> str:
        try:
            document = Document(io.BytesIO(payload))
        except (PackageNotFoundError, zipfile.BadZipFile, OSError, ValueError, KeyError) as exc:
            raise DocumentExtractionError(
                "The DOCX document could not be read. It may be corrupted.",
                code="invalid_document",
            ) from exc

        parts: list[str] = []
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, document)
                if paragraph.text.strip():
                    parts.append(
                        f"- {paragraph.text.strip()}" if _is_list_paragraph(child) else paragraph.text
                    )
            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                rows: list[str] = []
                for row in table.rows:
                    cells: list[str] = []
                    seen: set[str] = set()
                    for cell in row.cells:
                        value = cell.text.strip()
                        if value and value not in seen:
                            cells.append(value)
                            seen.add(value)
                    if cells:
                        rows.append(" | ".join(cells))
                if rows:
                    parts.append("\n".join(rows))
        return "\n\n".join(parts)


def _is_list_paragraph(element) -> bool:
    """True for Word bullet/numbered list items, whose markers are not part of
    the paragraph text. Keeping a "- " prefix preserves them for ATS checks."""
    properties = element.find(qn("w:pPr"))
    if properties is None:
        return False
    if properties.find(qn("w:numPr")) is not None:
        return True
    style = properties.find(qn("w:pStyle"))
    value = style.get(qn("w:val")) if style is not None else ""
    return bool(value) and value.replace(" ", "").casefold().startswith("list")


def normalize_extracted_text(value: str) -> str:
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for character in ("\u00a0", "\u2007", "\u202f"):
        text = text.replace(character, " ")
    for character in ("\u200b", "\ufeff", "\u00ad"):
        text = text.replace(character, "")
    text = CONTROL_CHARACTERS.sub("", text)

    lines: list[str] = []
    blank_lines = 0
    for raw_line in text.split("\n"):
        line = HORIZONTAL_WHITESPACE.sub(" ", raw_line).strip()
        if not line:
            blank_lines += 1
            if blank_lines <= 1 and lines:
                lines.append("")
            continue
        blank_lines = 0
        lines.append(line)
    return "\n".join(lines).strip()


def _has_meaningful_text(value: str) -> bool:
    return any(character.isalnum() for character in value)
