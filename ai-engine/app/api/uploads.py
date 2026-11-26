from __future__ import annotations

import tempfile

from fastapi import Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.api.errors import UploadValidationError
from app.core.audit import audit_event
from app.services.documents.extraction import (
    DocumentExtractionError,
    DocumentExtractionService,
    ExtractedDocument,
)

_EXTRACTOR = DocumentExtractionService()

_CHUNK_SIZE = 64 * 1024
_SPOOL_MEMORY_LIMIT = 1024 * 1024

_EXTRACTION_STATUS_CODES = {
    "empty_document": 400,
    "invalid_document": 400,
    "encrypted_document": 400,
    "unsupported_document_type": 415,
    "mime_type_mismatch": 415,
    "no_usable_text": 422,
    "text_too_large": 413,
    "pdf_page_limit_exceeded": 413,
    "docx_entry_limit_exceeded": 413,
    "docx_uncompressed_limit_exceeded": 413,
    "docx_compression_ratio_exceeded": 413,
}

_DOCX_LIMIT_CODES = frozenset(
    {
        "docx_entry_limit_exceeded",
        "docx_uncompressed_limit_exceeded",
        "docx_compression_ratio_exceeded",
    }
)


def _audit_rejection(
    request: Request | None,
    *,
    user_id: str | None,
    event_type: str,
    reason: str,
    metadata: dict[str, object],
) -> None:
    audit_event(
        event_type,
        "failure",
        request=request,
        user_id=user_id,
        metadata={"reason": reason, **metadata},
    )


async def _read_limited(
    upload: UploadFile,
    *,
    max_bytes: int,
) -> bytes:
    with tempfile.SpooledTemporaryFile(
        max_size=min(max_bytes, _SPOOL_MEMORY_LIMIT),
        mode="w+b",
    ) as spool:
        total = 0
        while True:
            chunk = await upload.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise UploadValidationError(
                    f"Uploaded file exceeds the {max_bytes} byte limit.",
                    code="upload_too_large",
                    status_code=413,
                )
            spool.write(chunk)
        spool.seek(0)
        return spool.read()


async def read_document_upload(
    upload: UploadFile,
    *,
    max_bytes: int,
    max_chars: int,
    max_pdf_pages: int | None = None,
    max_docx_entries: int | None = None,
    max_docx_uncompressed_bytes: int | None = None,
    max_docx_compression_ratio: int | None = None,
    request: Request | None = None,
    user_id: str | None = None,
) -> ExtractedDocument:
    try:
        payload = await _read_limited(upload, max_bytes=max_bytes)
    except UploadValidationError as exc:
        _audit_rejection(
            request,
            user_id=user_id,
            event_type="security.upload.rejected",
            reason=exc.code,
            metadata={"limit_bytes": max_bytes},
        )
        raise

    try:
        # Parsing a PDF or DOCX is CPU work: run it off the event loop so one
        # upload doesn't stall every other request on this worker.
        return await run_in_threadpool(
            _EXTRACTOR.extract,
            payload,
            filename=upload.filename or "",
            content_type=upload.content_type,
            max_chars=max_chars,
            max_pdf_pages=max_pdf_pages,
            max_docx_entries=max_docx_entries,
            max_docx_uncompressed_bytes=max_docx_uncompressed_bytes,
            max_docx_compression_ratio=max_docx_compression_ratio,
        )
    except DocumentExtractionError as exc:
        if exc.code == "pdf_page_limit_exceeded":
            _audit_rejection(
                request,
                user_id=user_id,
                event_type="security.upload.rejected",
                reason=exc.code,
                metadata={"limit_pages": max_pdf_pages},
            )
            _audit_rejection(
                request,
                user_id=user_id,
                event_type="security.upload.pdf_page_limit_exceeded",
                reason=exc.code,
                metadata={"limit_pages": max_pdf_pages},
            )
        elif exc.code in _DOCX_LIMIT_CODES:
            metadata = {
                "limit_entries": max_docx_entries,
                "limit_uncompressed_bytes": max_docx_uncompressed_bytes,
                "limit_compression_ratio": max_docx_compression_ratio,
            }
            _audit_rejection(
                request,
                user_id=user_id,
                event_type="security.upload.rejected",
                reason=exc.code,
                metadata=metadata,
            )
            _audit_rejection(
                request,
                user_id=user_id,
                event_type="security.upload.docx_limit_exceeded",
                reason=exc.code,
                metadata=metadata,
            )
        raise UploadValidationError(
            str(exc),
            code=exc.code,
            status_code=_EXTRACTION_STATUS_CODES.get(exc.code, 400),
        ) from exc
