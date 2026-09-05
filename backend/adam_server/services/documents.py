import hashlib
import os
from pathlib import Path

import fitz
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Document, Page


async def ingest_pdf(upload: UploadFile, db: Session, settings: Settings) -> Document:
    safe_name = Path(upload.filename or "paper.pdf").name
    temp_path = settings.data_dir / "tmp" / f"upload-{os.urandom(12).hex()}.pdf"
    digest = hashlib.sha256()
    byte_size = 0
    limit = settings.max_upload_mb * 1024 * 1024

    try:
        with temp_path.open("wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                byte_size += len(chunk)
                if byte_size > limit:
                    raise HTTPException(413, f"PDF exceeds the {settings.max_upload_mb} MB limit.")
                digest.update(chunk)
                destination.write(chunk)

        with temp_path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise HTTPException(400, "The uploaded file is not a valid PDF.")

        sha256 = digest.hexdigest()
        existing = db.scalar(select(Document).where(Document.sha256 == sha256))
        if existing:
            return existing

        try:
            pdf = fitz.open(temp_path)
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
            storage_path=str(final_path),
            page_count=len(extracted),
            status="ready",
        )
        document.pages = [Page(**page) for page in extracted]
        db.add(document)
        db.commit()
        db.refresh(document)
        return document
    finally:
        await upload.close()
        if temp_path.exists():
            temp_path.unlink()
