import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import SessionLocal, get_db, run_migrations
from .models import Annotation, Document, Message, Page
from .schemas import AnnotationIn, AnnotationOut, ChatRequest, DocumentOut, PageTextOut
from .services.documents import ingest_pdf
from .services.llm import OpenCodeGeminiProvider, sse

settings = get_settings()
run_migrations()

app = FastAPI(title="Adam API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)) -> list[Document]:
    return list(db.scalars(select(Document).order_by(Document.created_at.desc())))


@app.post("/api/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile,
    db: Session = Depends(get_db),
    app_settings: Settings = Depends(get_settings),
) -> Document:
    return await ingest_pdf(file, db, app_settings)


@app.get("/api/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: str, db: Session = Depends(get_db)) -> Document:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found.")
    return document


@app.get("/api/documents/{document_id}/file")
def get_document_file(document_id: str, db: Session = Depends(get_db)) -> FileResponse:
    document = db.get(Document, document_id)
    if not document or not Path(document.storage_path).is_file():
        raise HTTPException(404, "Document file not found.")
    return FileResponse(document.storage_path, media_type="application/pdf", filename=document.original_name, content_disposition_type="inline")


@app.get("/api/documents/{document_id}/pages/{page_number}/text", response_model=PageTextOut)
def get_page_text(document_id: str, page_number: int, db: Session = Depends(get_db)) -> PageTextOut:
    page = db.scalar(select(Page).where(Page.document_id == document_id, Page.page_number == page_number))
    if not page:
        raise HTTPException(404, "Page not found.")
    return PageTextOut(page=page.page_number, text=page.native_text, width_pt=page.width_pt, height_pt=page.height_pt, rotation=page.rotation)


@app.get("/api/documents/{document_id}/annotations", response_model=list[AnnotationOut])
def list_annotations(document_id: str, db: Session = Depends(get_db)) -> list[AnnotationOut]:
    if not db.get(Document, document_id):
        raise HTTPException(404, "Document not found.")
    annotations = db.scalars(select(Annotation).where(Annotation.document_id == document_id).order_by(Annotation.created_at)).all()
    return [AnnotationOut(id=item.id, page=item.page_number, text=item.selected_text, color=item.color, rects=json.loads(item.geometry_json), created_at=item.created_at) for item in annotations]


@app.post("/api/documents/{document_id}/annotations", response_model=AnnotationOut, status_code=201)
def create_annotation(document_id: str, request: AnnotationIn, db: Session = Depends(get_db)) -> AnnotationOut:
    if not db.get(Document, document_id):
        raise HTTPException(404, "Document not found.")
    existing = db.get(Annotation, request.id)
    if existing:
        if existing.document_id != document_id:
            raise HTTPException(409, "Annotation id already exists.")
        return AnnotationOut(id=existing.id, page=existing.page_number, text=existing.selected_text, color=existing.color, rects=json.loads(existing.geometry_json), created_at=existing.created_at)
    annotation = Annotation(id=request.id, document_id=document_id, page_number=request.page, kind="highlight", selected_text=request.text, color=request.color, geometry_json=json.dumps([rect.model_dump() for rect in request.rects]))
    db.add(annotation)
    db.commit()
    db.refresh(annotation)
    return AnnotationOut(id=annotation.id, page=annotation.page_number, text=annotation.selected_text, color=annotation.color, rects=request.rects, created_at=annotation.created_at)


@app.delete("/api/documents/{document_id}/annotations/{annotation_id}", status_code=204)
def delete_annotation(document_id: str, annotation_id: str, db: Session = Depends(get_db)) -> Response:
    annotation = db.get(Annotation, annotation_id)
    if not annotation or annotation.document_id != document_id:
        raise HTTPException(404, "Annotation not found.")
    db.delete(annotation)
    db.commit()
    return Response(status_code=204)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    if not db.get(Document, request.document_id):
        raise HTTPException(404, "Document not found.")
    api_key = settings.resolved_opencode_api_key
    if not api_key:
        raise HTTPException(503, "Configure the OpenCode API key secret to enable AI responses.")

    context = {"scope": "selection", "page": request.page, "selected_text": request.selected_text, "image_count": len(request.images)}
    db.add(Message(document_id=request.document_id, role="user", content=request.question, context_json=json.dumps(context)))
    db.commit()

    provider = OpenCodeGeminiProvider(api_key, settings.opencode_model, settings.opencode_base_url)

    async def events() -> AsyncIterator[str]:
        complete = ""
        try:
            yield sse({"type": "started", "provider": "opencode", "model": settings.opencode_model})
            async for delta in provider.stream_answer(request.question, request.selected_text, request.images, request.page, request.history):
                complete += delta
                yield sse({"type": "delta", "text": delta})
            with SessionLocal() as stream_db:
                stream_db.add(Message(document_id=request.document_id, role="assistant", content=complete, context_json=None))
                stream_db.commit()
            yield sse({"type": "completed"})
        except Exception as exc:
            yield sse({"type": "error", "message": str(exc)})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
