import json
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
import httpx

from .config import Settings, get_settings
from .database import SessionLocal, get_db, run_migrations
from .models import Annotation, Conversation, Document, Message, ModelFavorite, Page
from .schemas import AnnotationIn, AnnotationOut, AppSettingsOut, AppSettingsUpdate, ChatRequest, ConversationCreate, ConversationDetail, ConversationOut, ConversationUpdate, DocumentOut, DocumentUpdate, ModelFavoriteUpdate, PageTextOut, ProviderKeyUpdate, ProviderModelsOut
from .services.context import build_paper_context
from .services.documents import ingest_pdf
from .services.llm import AnthropicProvider, GoogleProvider, OpenAICompatibleProvider, OpenAIResponsesProvider, sse

settings = get_settings()
run_migrations()

app = FastAPI(title="Adam API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def settings_response(app_settings: Settings, db: Session) -> AppSettingsOut:
    runtime = app_settings.runtime_settings
    active_provider = runtime.get("provider", "zen")
    selected_models = dict(runtime.get("selected_models", {}))
    if active_provider not in selected_models:
        selected_models[active_provider] = runtime.get("model", app_settings.opencode_model)
    providers = {name: bool(app_settings.provider_api_key(name)) for name in ("zen", "openrouter", "openai", "anthropic", "google")}
    favorites: dict[str, list[str]] = {}
    for item in db.scalars(select(ModelFavorite).order_by(ModelFavorite.created_at)).all():
        favorites.setdefault(item.provider, []).append(item.model_id)
    return AppSettingsOut(provider=active_provider, model=app_settings.resolved_opencode_model, selected_models=selected_models, providers=providers, favorites=favorites, system_prompt=app_settings.system_prompt)


@app.get("/api/settings", response_model=AppSettingsOut)
def get_app_settings(app_settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> AppSettingsOut:
    return settings_response(app_settings, db)


@app.put("/api/settings", response_model=AppSettingsOut)
def update_app_settings(request: AppSettingsUpdate, app_settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> AppSettingsOut:
    app_settings.save_runtime_settings(request.provider, request.model, request.api_keys, request.favorites, request.system_prompt)
    for provider, models in request.favorites.items():
        existing = {item.model_id: item for item in db.scalars(select(ModelFavorite).where(ModelFavorite.provider == provider)).all()}
        desired = set(models)
        for model_id in desired - existing.keys(): db.add(ModelFavorite(provider=provider, model_id=model_id))
        for model_id in existing.keys() - desired: db.delete(existing[model_id])
    db.commit()
    return settings_response(app_settings, db)


@app.post("/api/settings/favorite", response_model=AppSettingsOut)
def update_model_favorite(request: ModelFavoriteUpdate, app_settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> AppSettingsOut:
    favorite = db.scalar(select(ModelFavorite).where(ModelFavorite.provider == request.provider, ModelFavorite.model_id == request.model))
    if request.starred and not favorite: db.add(ModelFavorite(provider=request.provider, model_id=request.model))
    elif not request.starred and favorite: db.delete(favorite)
    db.commit()
    return settings_response(app_settings, db)


@app.post("/api/settings/key", response_model=AppSettingsOut)
def update_provider_key(request: ProviderKeyUpdate, app_settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> AppSettingsOut:
    app_settings.save_provider_api_key(request.provider, request.api_key)
    return settings_response(app_settings, db)


@app.get("/api/settings/models/{provider}", response_model=ProviderModelsOut)
async def list_provider_models(provider: str, app_settings: Settings = Depends(get_settings)) -> ProviderModelsOut:
    if provider not in {"zen", "openrouter", "openai", "anthropic", "google"}:
        raise HTTPException(404, "Unknown provider.")
    key = app_settings.provider_api_key(provider)
    if not key:
        raise HTTPException(503, f"Add a {provider} API key to load models.")
    urls = {"zen": "https://opencode.ai/zen/v1/models", "openrouter": "https://openrouter.ai/api/v1/models", "openai": "https://api.openai.com/v1/models", "anthropic": "https://api.anthropic.com/v1/models", "google": "https://generativelanguage.googleapis.com/v1beta/models"}
    headers = {"Authorization": f"Bearer {key}"}
    params = None
    if provider == "anthropic": headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if provider == "google": headers, params = {"x-goog-api-key": key}, {"pageSize": 1000}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(urls[provider], headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "The provider rejected the API key or model-list request.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Could not load models from the provider.") from exc
    data = payload.get("models", []) if provider == "google" else payload.get("data", [])
    models = []
    for item in data:
        model_id = item.get("name", "").removeprefix("models/") if provider == "google" else item.get("id", "")
        if provider == "google" and "generateContent" not in item.get("supportedGenerationMethods", []): continue
        if provider == "openai" and not model_id.startswith(("gpt-", "o1", "o3", "o4")): continue
        if model_id: models.append(model_id)
    return ProviderModelsOut(provider=provider, models=sorted(set(models)))


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


@app.patch("/api/documents/{document_id}", response_model=DocumentOut)
def update_document(document_id: str, request: DocumentUpdate, db: Session = Depends(get_db), app_settings: Settings = Depends(get_settings)) -> Document:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found.")
    name = request.name.strip()
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    if not name[:-4].strip() or Path(name).name != name or "/" in name or "\\" in name or any(ord(char) < 32 for char in name):
        raise HTTPException(422, "Enter a valid PDF filename without folders.")
    source = Path(document.storage_path).resolve()
    document_root = (app_settings.data_dir / "documents").resolve()
    if not source.is_file() or not source.is_relative_to(document_root):
        raise HTTPException(409, "The document file is outside managed storage and was not renamed.")
    destination = source.with_name(name)
    if destination != source and destination.exists():
        raise HTTPException(409, "A PDF with that filename already exists in this storage folder.")
    if destination != source:
        os.replace(source, destination)
    try:
        document.original_name = name
        document.storage_path = str(destination)
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        if destination != source and destination.exists():
            os.replace(destination, source)
        raise
    return document


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str, db: Session = Depends(get_db), app_settings: Settings = Depends(get_settings)) -> Response:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found.")
    source = Path(document.storage_path).resolve()
    document_root = (app_settings.data_dir / "documents").resolve()
    staged: Path | None = None
    if source.exists():
        if not source.is_file() or not source.is_relative_to(document_root):
            raise HTTPException(409, "The document file is outside managed storage and was not deleted.")
        staged = app_settings.data_dir / "tmp" / f"delete-{document.id}.pdf"
        os.replace(source, staged)
    try:
        db.delete(document)
        db.commit()
    except Exception:
        db.rollback()
        if staged and staged.exists():
            os.replace(staged, source)
        raise
    if staged and staged.exists():
        staged.unlink()
        try:
            source.parent.rmdir()
        except OSError:
            pass
    return Response(status_code=204)


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


@app.get("/api/documents/{document_id}/conversations", response_model=list[ConversationOut])
def list_conversations(document_id: str, db: Session = Depends(get_db)) -> list[dict]:
    if not db.get(Document, document_id):
        raise HTTPException(404, "Document not found.")
    rows = db.execute(select(Conversation, func.count(Message.id)).outerjoin(Message, Message.conversation_id == Conversation.id).where(Conversation.document_id == document_id).group_by(Conversation.id).order_by(Conversation.updated_at.desc())).all()
    return [{**ConversationOut.model_validate(conversation).model_dump(), "message_count": count} for conversation, count in rows]


@app.post("/api/documents/{document_id}/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(document_id: str, request: ConversationCreate, db: Session = Depends(get_db)) -> Conversation:
    if not db.get(Document, document_id):
        raise HTTPException(404, "Document not found.")
    runtime = settings.runtime_settings
    provider = runtime.get("provider", "zen")
    conversation = Conversation(document_id=document_id, title=request.title or "New chat", provider=provider,
                                model_id=settings.resolved_opencode_model, system_prompt=settings.system_prompt)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    return conversation


@app.patch("/api/conversations/{conversation_id}", response_model=ConversationOut)
def update_conversation(conversation_id: str, request: ConversationUpdate, db: Session = Depends(get_db)) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    conversation.title = request.title.strip()
    db.commit()
    db.refresh(conversation)
    return conversation


@app.post("/api/conversations/{conversation_id}/sync-defaults", response_model=ConversationOut)
def sync_conversation_defaults(conversation_id: str, db: Session = Depends(get_db)) -> ConversationOut:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    message_count = db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conversation.id)) or 0
    if message_count == 0:
        runtime = settings.runtime_settings
        conversation.provider = runtime.get("provider", "zen")
        conversation.model_id = settings.resolved_opencode_model
        conversation.system_prompt = settings.system_prompt
        db.commit()
        db.refresh(conversation)
    result = ConversationOut.model_validate(conversation)
    return result.model_copy(update={"message_count": message_count})


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)) -> Response:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    db.delete(conversation)
    db.commit()
    return Response(status_code=204)


@app.post("/api/conversations/{conversation_id}/messages/stream")
async def chat_stream(conversation_id: str, request: ChatRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    provider_name = conversation.provider
    api_key = settings.provider_api_key(provider_name)
    if not api_key:
        raise HTTPException(503, f"No API key is configured for {provider_name}. Open Settings to enable chat.")

    paper_context, context_mode = build_paper_context(db, conversation, request.question, request.selected_text)
    context = {"scope": "selection" if request.selected_text or request.images else "paper", "page": request.page, "selected_text": request.selected_text, "images": [image.model_dump() for image in request.images], "context_mode": context_mode}
    user_message = Message(document_id=conversation.document_id, conversation_id=conversation.id, role="user", content=request.question, context_json=json.dumps(context))
    db.add(user_message)
    if conversation.title == "New chat":
        conversation.title = request.question.strip()[:80]
    db.commit()

    model = conversation.model_id
    system_prompt = conversation.system_prompt + paper_context
    if provider_name == "google": provider = GoogleProvider(api_key, model, "https://generativelanguage.googleapis.com/v1beta", system_prompt)
    elif provider_name == "anthropic": provider = AnthropicProvider(api_key, model, system_prompt=system_prompt)
    elif provider_name == "openai": provider = OpenAIResponsesProvider(api_key, model, "https://api.openai.com/v1", system_prompt)
    elif provider_name == "zen" and model.startswith("gemini-"): provider = GoogleProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    elif provider_name == "zen" and model.startswith(("claude-", "qwen")): provider = AnthropicProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    elif provider_name == "zen" and model.startswith(("deepseek-", "minimax-", "glm-", "kimi-", "big-pickle", "mimo-", "ling-", "nemotron-")): provider = OpenAICompatibleProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    elif provider_name == "zen": provider = OpenAIResponsesProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    else:
        bases = {"openrouter": "https://openrouter.ai/api/v1"}
        provider = OpenAICompatibleProvider(api_key, model, bases[provider_name], system_prompt)

    async def events() -> AsyncIterator[str]:
        complete = ""
        try:
            history_messages = list(db.scalars(select(Message).where(Message.conversation_id == conversation.id, Message.id != user_message.id).order_by(Message.created_at)))
            history = []
            pending = None
            from .schemas import ChatTurnIn, ContextImageIn
            for message in history_messages:
                if message.role == "user":
                    saved = json.loads(message.context_json or "{}")
                    pending = (message, saved)
                elif message.role == "assistant" and pending:
                    prior, saved = pending
                    history.append(ChatTurnIn(question=prior.content, answer=message.content, selected_text=saved.get("selected_text", ""), page=saved.get("page"), images=[ContextImageIn(**item) for item in saved.get("images", [])]))
                    pending = None
            yield sse({"type": "started", "provider": provider_name, "model": model, "context_mode": context_mode, "conversation_id": conversation.id})
            async for delta in provider.stream_answer(request.question, request.selected_text, request.images, request.page, history[-20:]):
                complete += delta
                yield sse({"type": "delta", "text": delta})
            with SessionLocal() as stream_db:
                stream_db.add(Message(document_id=conversation.document_id, conversation_id=conversation.id, role="assistant", content=complete, context_json=None))
                stored = stream_db.get(Conversation, conversation.id)
                if stored: stored.updated_at = datetime.now(timezone.utc)
                stream_db.commit()
            yield sse({"type": "completed"})
        except Exception as exc:
            yield sse({"type": "error", "message": str(exc)})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
