import hashlib
import json
import os
import threading
from pathlib import Path
from typing import BinaryIO

import anyio
import anyio.to_thread
import fitz
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import SessionLocal
from ..models import Document, Page


def document_file_path(storage_path: str, settings: Settings) -> Path:
    """Resolve stored PDFs after switching between Docker and local development."""
    path = Path(storage_path)
    docker_documents = Path("/data/documents")
    if path.is_relative_to(docker_documents):
        path = settings.data_dir / "documents" / path.relative_to(docker_documents)
    elif not path.is_absolute():
        path = settings.data_dir / path
    return path.resolve()


# PyMuPDF is not thread-safe, even across separate documents, so every fitz call in the
# server must hold this lock. Extraction still runs off the event loop because PyMuPDF
# periodically yields the GIL between pages.
PDF_ENGINE_LOCK = threading.RLock()
# Serializes the duplicate check through commit so concurrent uploads of one PDF extract once.
_INGEST_LOCK = threading.Lock()
# Bounds worker threads for uploads so queued ingestions cannot exhaust the shared threadpool.
_INGEST_LIMITER = anyio.CapacityLimiter(2)


async def ingest_pdf(upload: UploadFile, settings: Settings, *, folder_id: str | None = None, discovery: dict | None = None) -> Document:
    try:
        return await anyio.to_thread.run_sync(
            lambda: ingest_pdf_file(upload.file, upload.filename, settings, folder_id=folder_id, discovery=discovery),
            limiter=_INGEST_LIMITER,
        )
    finally:
        await upload.close()


def _existing_document(db: Session, existing: Document, discovery: dict | None) -> Document:
    if discovery and not existing.discovery:
        existing.discovery_json = json.dumps(discovery)
        db.commit()
        db.refresh(existing)
    return existing


def ingest_pdf_file(
    source_file: BinaryIO,
    filename: str | None,
    settings: Settings,
    *,
    folder_id: str | None = None,
    discovery: dict | None = None,
) -> Document:
    """Store and extract a PDF using its own database session; safe to run in a worker thread."""
    safe_name = Path(filename or "paper.pdf").name
    temp_path = settings.data_dir / "tmp" / f"upload-{os.urandom(12).hex()}.pdf"
    digest = hashlib.sha256()
    byte_size = 0
    limit = settings.max_upload_mb * 1024 * 1024

    try:
        source_file.seek(0)
        with temp_path.open("wb") as destination:
            while chunk := source_file.read(1024 * 1024):
                byte_size += len(chunk)
                if byte_size > limit:
                    raise HTTPException(413, f"PDF exceeds the {settings.max_upload_mb} MB limit.")
                digest.update(chunk)
                destination.write(chunk)

        with temp_path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise HTTPException(400, "The uploaded file is not a valid PDF.")

        sha256 = digest.hexdigest()
        with SessionLocal() as db:
            existing = db.scalar(select(Document).where(Document.sha256 == sha256))
            if existing:
                return _existing_document(db, existing, discovery)

            with _INGEST_LOCK:
                existing = db.scalar(select(Document).where(Document.sha256 == sha256))
                if existing:
                    return _existing_document(db, existing, discovery)

                try:
                    with PDF_ENGINE_LOCK:
                        pdf = fitz.open(temp_path)
                        try:
                            extracted = [
                                {
                                    "page_number": index + 1,
                                    "width_pt": page.rect.width,
                                    "height_pt": page.rect.height,
                                    "rotation": page.rotation,
                                    "native_text": page.get_text("text", sort=True),
                                }
                                for index, page in enumerate(pdf)
                            ]
                        finally:
                            pdf.close()
                except Exception as exc:
                    raise HTTPException(400, "The PDF could not be opened or is damaged.") from exc

                final_dir = settings.data_dir / "documents" / sha256[:2]
                final_dir.mkdir(parents=True, exist_ok=True)
                final_path = final_dir / f"{sha256}.pdf"
                os.replace(temp_path, final_path)

                document = Document(
                    sha256=sha256,
                    original_name=safe_name,
                    byte_size=byte_size,
                    storage_path=str(final_path.relative_to(settings.data_dir)),
                    page_count=len(extracted),
                    status="ready",
                    folder_id=folder_id,
                    discovery_json=json.dumps(discovery or {}),
                )
                document.pages = [Page(**page) for page in extracted]
                db.add(document)
                try:
                    db.commit()
                except IntegrityError:
                    # Another process stored the same bytes first; its file is identical to ours.
                    db.rollback()
                    existing = db.scalar(select(Document).where(Document.sha256 == sha256))
                    if existing:
                        return _existing_document(db, existing, discovery)
                    raise
                db.refresh(document)
                return document
    finally:
        if temp_path.exists():
            temp_path.unlink()
