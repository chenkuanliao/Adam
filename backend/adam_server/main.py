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
from .models import Annotation, Conversation, Document, Message, ModelFavorite, Page, PaperNote
from .schemas import AiNoteCreate, AiNoteUnlink, AnnotationIn, AnnotationOut, AnnotationUpdate, AppSettingsOut, AppSettingsUpdate, ChatRequest, ConversationCreate, ConversationDetail, ConversationOut, ConversationUpdate, DocumentOut, DocumentUpdate, ModelFavoriteUpdate, PageTextOut, PaperNoteOut, PaperNoteUpdate, ProviderKeyUpdate, ProviderModelsOut, QuickAskImportRequest, QuickAskRequest
from .services.context import build_paper_context
from .services.documents import document_file_path, ingest_pdf
from .services.llm import AnthropicProvider, GoogleProvider, OpenAICompatibleProvider, OpenAIResponsesProvider, generate_zen_title, sse
from .services.web_search import asks_for_citing_papers, plan_search, previous_sources, search_citing_papers, search_web, web_context

settings = get_settings()
run_migrations()

app = FastAPI(title="Adam API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


def make_provider(provider_name: str, api_key: str, model: str, system_prompt: str):
    if provider_name == "google": return GoogleProvider(api_key, model, "https://generativelanguage.googleapis.com/v1beta", system_prompt)
    if provider_name == "anthropic": return AnthropicProvider(api_key, model, system_prompt=system_prompt)
    if provider_name == "openai": return OpenAIResponsesProvider(api_key, model, "https://api.openai.com/v1", system_prompt)
    if provider_name == "zen" and model.startswith("gemini-"): return GoogleProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    if provider_name == "zen" and model.startswith(("claude-", "qwen")): return AnthropicProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    if provider_name == "zen" and model.startswith(("deepseek-", "minimax-", "glm-", "kimi-", "big-pickle", "mimo-", "ling-", "nemotron-")): return OpenAICompatibleProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    if provider_name == "zen": return OpenAIResponsesProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    return OpenAICompatibleProvider(api_key, model, {"openrouter": "https://openrouter.ai/api/v1"}[provider_name], system_prompt)


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
    return AppSettingsOut(provider=active_provider, model=app_settings.resolved_opencode_model, selected_models=selected_models, providers=providers, favorites=favorites, system_prompt=app_settings.system_prompt, quick_ask_prompt=app_settings.quick_ask_prompt)


@app.get("/api/settings", response_model=AppSettingsOut)
def get_app_settings(app_settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> AppSettingsOut:
    return settings_response(app_settings, db)


@app.put("/api/settings", response_model=AppSettingsOut)
def update_app_settings(request: AppSettingsUpdate, app_settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> AppSettingsOut:
    app_settings.save_runtime_settings(request.provider, request.model, request.api_keys, request.favorites, request.system_prompt, request.quick_ask_prompt)
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
    return list(db.scalars(select(Document).order_by(Document.updated_at.desc())))


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
    source = document_file_path(document.storage_path, app_settings)
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
        document.storage_path = str(destination.relative_to(app_settings.data_dir.resolve()))
        document.updated_at = datetime.now(timezone.utc)
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
    source = document_file_path(document.storage_path, app_settings)
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
def get_document_file(document_id: str, db: Session = Depends(get_db), app_settings: Settings = Depends(get_settings)) -> FileResponse:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document file not found.")
    source = document_file_path(document.storage_path, app_settings)
    if not source.is_relative_to((app_settings.data_dir / "documents").resolve()) or not source.is_file():
        raise HTTPException(404, "Document file not found.")
    return FileResponse(source, media_type="application/pdf", filename=document.original_name, content_disposition_type="inline")


@app.get("/api/documents/{document_id}/pages/{page_number}/text", response_model=PageTextOut)
def get_page_text(document_id: str, page_number: int, db: Session = Depends(get_db)) -> PageTextOut:
    page = db.scalar(select(Page).where(Page.document_id == document_id, Page.page_number == page_number))
    if not page:
        raise HTTPException(404, "Page not found.")
    return PageTextOut(page=page.page_number, text=page.native_text, width_pt=page.width_pt, height_pt=page.height_pt, rotation=page.rotation)


@app.get("/api/documents/{document_id}/paper-note", response_model=PaperNoteOut)
def get_paper_note(document_id: str, db: Session = Depends(get_db)) -> PaperNoteOut:
    if not db.get(Document, document_id):
        raise HTTPException(404, "Document not found.")
    note = db.get(PaperNote, document_id)
    if not note:
        return PaperNoteOut(document_id=document_id, content_html="", plain_text="", revision=0)
    return PaperNoteOut.model_validate(note, from_attributes=True)


@app.put("/api/documents/{document_id}/paper-note", response_model=PaperNoteOut)
def update_paper_note(document_id: str, request: PaperNoteUpdate, db: Session = Depends(get_db)) -> PaperNote:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found.")
    note = db.get(PaperNote, document_id)
    current_revision = note.revision if note else 0
    if request.revision != current_revision:
        raise HTTPException(409, "This note changed in another window. Reload it before saving again.")
    if not note:
        note = PaperNote(document_id=document_id)
        db.add(note)
    note.content_html = request.content_html
    note.plain_text = request.plain_text
    note.revision = current_revision + 1
    document.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(note)
    return note


@app.get("/api/documents/{document_id}/annotations", response_model=list[AnnotationOut])
def list_annotations(document_id: str, db: Session = Depends(get_db)) -> list[AnnotationOut]:
    if not db.get(Document, document_id):
        raise HTTPException(404, "Document not found.")
    annotations = db.scalars(select(Annotation).where(Annotation.document_id == document_id).order_by(Annotation.created_at)).all()
    return [annotation_out(item) for item in annotations]


def annotation_out(item: Annotation) -> AnnotationOut:
    return AnnotationOut(id=item.id, page=item.page_number, text=item.selected_text, color=item.color,
                         rects=json.loads(item.geometry_json), note_text=item.note_text,
                         ai_links=json.loads(item.ai_links_json or "[]"), created_at=item.created_at)


def rects_overlap(left: list[dict], right: list[dict]) -> bool:
    # A tiny tolerance makes separately captured versions of the same PDF text
    # resolve to one anchor despite browser rounding.
    tolerance = .002
    return any(
        a["left"] < b["left"] + b["width"] + tolerance
        and a["left"] + a["width"] + tolerance > b["left"]
        and a["top"] < b["top"] + b["height"] + tolerance
        and a["top"] + a["height"] + tolerance > b["top"]
        for a in left for b in right
    )


@app.post("/api/documents/{document_id}/ai-notes", response_model=AnnotationOut)
def link_ai_note(document_id: str, request: AiNoteCreate, db: Session = Depends(get_db)) -> AnnotationOut:
    document = db.get(Document, document_id)
    conversation = db.get(Conversation, request.link.conversation_id)
    if not document:
        raise HTTPException(404, "Document not found.")
    if not conversation or conversation.document_id != document_id:
        raise HTTPException(422, "The linked chat does not belong to this paper.")
    incoming_rects = [rect.model_dump() for rect in request.rects]
    candidates = db.scalars(select(Annotation).where(
        Annotation.document_id == document_id,
        Annotation.page_number == request.page,
        Annotation.ai_links_json.is_not(None),
    )).all()
    annotation = next((item for item in candidates if rects_overlap(json.loads(item.geometry_json), incoming_rects)), None)
    now = datetime.now(timezone.utc)
    link = {"conversation_id": conversation.id, "title": conversation.title, "question": request.link.question.strip(), "created_at": now.isoformat()}
    if annotation:
        links = json.loads(annotation.ai_links_json or "[]")
        duplicate = next((item for item in links if item["conversation_id"] == link["conversation_id"] and item["question"] == link["question"]), None)
        if not duplicate:
            links.append(link)
        existing_rects = json.loads(annotation.geometry_json)
        for rect in incoming_rects:
            if not any(all(abs(rect[key] - old[key]) < .002 for key in ("left", "top", "width", "height")) for old in existing_rects):
                existing_rects.append(rect)
        annotation.geometry_json = json.dumps(existing_rects)
        annotation.ai_links_json = json.dumps(links)
    else:
        annotation = Annotation(document_id=document_id, page_number=request.page, kind="ai_note",
                                selected_text=request.text, color="#7b61a8", geometry_json=json.dumps(incoming_rects),
                                ai_links_json=json.dumps([link]))
        db.add(annotation)
    document.updated_at = now
    db.commit()
    db.refresh(annotation)
    return annotation_out(annotation)


@app.post("/api/documents/{document_id}/ai-notes/unlink", status_code=204)
def unlink_ai_note(document_id: str, request: AiNoteUnlink, db: Session = Depends(get_db)) -> Response:
    if not db.get(Document, document_id):
        raise HTTPException(404, "Document not found.")
    for annotation in db.scalars(select(Annotation).where(Annotation.document_id == document_id, Annotation.ai_links_json.is_not(None))).all():
        links = json.loads(annotation.ai_links_json or "[]")
        remaining = [link for link in links if not (link.get("conversation_id") == request.conversation_id and link.get("question") == request.question)]
        if len(remaining) == len(links):
            continue
        if remaining:
            annotation.ai_links_json = json.dumps(remaining)
        elif annotation.kind == "ai_note":
            db.delete(annotation)
        else:
            annotation.ai_links_json = None
    db.commit()
    return Response(status_code=204)


@app.post("/api/documents/{document_id}/annotations", response_model=AnnotationOut, status_code=201)
def create_annotation(document_id: str, request: AnnotationIn, db: Session = Depends(get_db)) -> AnnotationOut:
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Document not found.")
    existing = db.get(Annotation, request.id)
    if existing:
        if existing.document_id != document_id:
            raise HTTPException(409, "Annotation id already exists.")
        return annotation_out(existing)
    annotation = Annotation(id=request.id, document_id=document_id, page_number=request.page, kind="note" if request.note_text is not None else "highlight", selected_text=request.text, color=request.color, geometry_json=json.dumps([rect.model_dump() for rect in request.rects]), note_text=request.note_text)
    db.add(annotation)
    document.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(annotation)
    return annotation_out(annotation)


@app.patch("/api/documents/{document_id}/annotations/{annotation_id}", response_model=AnnotationOut)
def update_annotation(document_id: str, annotation_id: str, request: AnnotationUpdate, db: Session = Depends(get_db)) -> AnnotationOut:
    annotation = db.get(Annotation, annotation_id)
    if not annotation or annotation.document_id != document_id:
        raise HTTPException(404, "Annotation not found.")
    annotation.note_text = request.note_text
    if request.color is not None:
        annotation.color = request.color
    annotation.kind = "note" if request.note_text else "highlight"
    document = db.get(Document, document_id)
    if document: document.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(annotation)
    return annotation_out(annotation)


@app.delete("/api/documents/{document_id}/annotations/{annotation_id}", status_code=204)
def delete_annotation(document_id: str, annotation_id: str, db: Session = Depends(get_db)) -> Response:
    annotation = db.get(Annotation, annotation_id)
    if not annotation or annotation.document_id != document_id:
        raise HTTPException(404, "Annotation not found.")
    db.delete(annotation)
    document = db.get(Document, document_id)
    if document: document.updated_at = datetime.now(timezone.utc)
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
    title = request.title.strip()
    if not title:
        raise HTTPException(422, "Chat title cannot be blank.")
    conversation.title = title
    db.commit()
    db.refresh(conversation)
    return conversation


@app.post("/api/conversations/{conversation_id}/regenerate-title", response_model=ConversationOut)
async def regenerate_conversation_title(conversation_id: str, db: Session = Depends(get_db)) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    if conversation.provider != "zen":
        raise HTTPException(409, "AI title generation is available for OpenCode Zen chats.")
    api_key = settings.provider_api_key("zen")
    if not api_key:
        raise HTTPException(503, "No OpenCode Zen API key is configured.")
    messages = list(db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)))
    if not messages:
        raise HTTPException(409, "Send a message before generating a title.")
    transcript = "\n\n".join(f"{message.role.title()}: {message.content}" for message in messages)
    fallback = next((message.content for message in messages if message.role == "user"), conversation.title)[:80]
    try:
        conversation.title = await generate_zen_title(api_key, transcript, fallback)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(502, "OpenCode Zen could not generate a title.") from exc
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
    for annotation in db.scalars(select(Annotation).where(Annotation.document_id == conversation.document_id, Annotation.ai_links_json.is_not(None))).all():
        links = [link for link in json.loads(annotation.ai_links_json or "[]") if link.get("conversation_id") != conversation_id]
        if links:
            annotation.ai_links_json = json.dumps(links)
        elif annotation.kind == "ai_note":
            db.delete(annotation)
        else:
            annotation.ai_links_json = None
    db.delete(conversation)
    db.commit()
    return Response(status_code=204)


@app.post("/api/conversations/{conversation_id}/messages/stream")
async def chat_stream(conversation_id: str, request: ChatRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    paper_title = Path(db.get(Document, conversation.document_id).original_name).stem
    provider_name = conversation.provider
    api_key = settings.provider_api_key(provider_name)
    if not api_key:
        raise HTTPException(503, f"No API key is configured for {provider_name}. Open Settings to enable chat.")

    if conversation.context_builder_version == "quick-ask-v1":
        paper_context, context_mode = "", "quick_ask_history"
    else:
        paper_context, context_mode = build_paper_context(db, conversation, request.question, request.selected_text)
    scope = "quick_ask_followup" if conversation.context_builder_version == "quick-ask-v1" else ("selection" if request.selected_text or request.images else "paper")
    context = {"scope": scope, "page": request.page, "selected_text": request.selected_text, "images": [image.model_dump() for image in request.images], "anchors": [anchor.model_dump() for anchor in request.anchors], "context_mode": context_mode, "search_requested": request.allow_web_search}
    user_message = Message(document_id=conversation.document_id, conversation_id=conversation.id, role="user", content=request.question, context_json=json.dumps(context))
    db.add(user_message)
    should_generate_title = conversation.title == "New chat" and provider_name == "zen"
    if conversation.title == "New chat":
        conversation.title = request.question.strip()[:80]
    db.commit()

    if should_generate_title:
        try:
            conversation.title = await generate_zen_title(api_key, f"User: {request.question.strip()}", conversation.title)
            db.commit()
        except (httpx.HTTPError, ValueError, KeyError):
            # A title must never prevent the actual chat request from succeeding.
            db.rollback()
            conversation = db.get(Conversation, conversation_id)

    model = conversation.model_id
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
            yield sse({"type": "started", "provider": provider_name, "model": model, "context_mode": context_mode, "conversation_id": conversation.id, "title": conversation.title})
            prior_sources = previous_sources(db, conversation.id)
            fresh_sources = []
            search_query = None
            search_performed = False
            if request.allow_web_search:
                yield sse({"type": "searching"})
                try:
                    planner = make_provider(provider_name, api_key, model, "")
                    search_query = await plan_search(planner, request.question, request.selected_text, paper_title)
                except Exception:
                    search_query = f'"{paper_title}" {request.question}'[:180]
                try:
                    if asks_for_citing_papers(request.question):
                        try:
                            fresh_sources = await search_citing_papers(paper_title)
                        except (httpx.HTTPError, ValueError, KeyError):
                            fresh_sources = []
                    if not fresh_sources:
                        fresh_sources = await search_web(settings.search_base_url, search_query)
                    search_performed = True
                except Exception:
                    yield sse({"type": "search_error", "message": "Web search is unavailable; answering with the paper and saved sources."})
            sources = (fresh_sources + [item for item in prior_sources if item["url"] not in {source["url"] for source in fresh_sources}])[:6]
            web = {"searched": search_performed, "query": search_query if search_performed else None, "reused": bool(prior_sources), "sources": sources}
            yield sse({"type": "web_sources", "web": web})
            system_prompt = conversation.system_prompt + paper_context + web_context(sources)
            provider = make_provider(provider_name, api_key, model, system_prompt)
            async for delta in provider.stream_answer(request.question, request.selected_text, request.images, request.page, history[-20:]):
                complete += delta
                yield sse({"type": "delta", "text": delta})
            with SessionLocal() as stream_db:
                stream_db.add(Message(document_id=conversation.document_id, conversation_id=conversation.id, role="assistant", content=complete, context_json=json.dumps({"web": web}) if search_performed or sources else None))
                stored = stream_db.get(Conversation, conversation.id)
                if stored: stored.updated_at = datetime.now(timezone.utc)
                stream_db.commit()
            yield sse({"type": "completed", "web": web})
        except Exception as exc:
            yield sse({"type": "error", "message": str(exc)})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/conversations/{conversation_id}/quick-ask/stream")
async def quick_ask_stream(conversation_id: str, request: QuickAskRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    history_has_selection = any(turn.selected_text or turn.images for turn in request.history)
    if not request.selected_text and not request.images and not history_has_selection:
        raise HTTPException(422, "Quick Ask requires selected text or a screenshot.")
    api_key = settings.provider_api_key(conversation.provider)
    if not api_key:
        raise HTTPException(503, f"No API key is configured for {conversation.provider}. Open Settings to enable Quick Ask.")
    provider = make_provider(conversation.provider, api_key, conversation.model_id, settings.quick_ask_prompt)

    async def events() -> AsyncIterator[str]:
        try:
            yield sse({"type": "started", "provider": conversation.provider, "model": conversation.model_id})
            async for delta in provider.stream_answer(request.question, request.selected_text, request.images, request.page, request.history):
                yield sse({"type": "delta", "text": delta})
            yield sse({"type": "completed"})
        except Exception as exc:
            yield sse({"type": "error", "message": str(exc)})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/conversations/{conversation_id}/quick-ask/import", response_model=ConversationOut, status_code=201)
def import_quick_ask(conversation_id: str, request: QuickAskImportRequest, db: Session = Depends(get_db)) -> ConversationOut:
    source = db.get(Conversation, conversation_id)
    if not source:
        raise HTTPException(404, "Conversation not found.")
    conversation = Conversation(document_id=source.document_id, title=f"Quick Ask · {request.turns[0].question.strip()[:70]}", provider=source.provider, model_id=source.model_id, system_prompt=settings.quick_ask_prompt, context_builder_version="quick-ask-v1")
    db.add(conversation)
    db.flush()
    for index, turn in enumerate(request.turns):
        context = json.dumps({"scope": "quick_ask_saved" if index == 0 else "quick_ask_followup", "page": request.page, "selected_text": request.selected_text if index == 0 else "", "images": [image.model_dump() for image in request.images] if index == 0 else [], "anchors": [anchor.model_dump() for anchor in request.anchors] if index == 0 else [], "label": "Saved Quick Ask · selection only" if index == 0 else "Quick Ask follow-up"})
        db.add(Message(document_id=conversation.document_id, conversation_id=conversation.id, role="user", content=turn.question, context_json=context))
        db.add(Message(document_id=conversation.document_id, conversation_id=conversation.id, role="assistant", content=turn.answer, context_json=None))
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conversation)
    result = ConversationOut.model_validate(conversation)
    return result.model_copy(update={"message_count": db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conversation.id)) or 0})
