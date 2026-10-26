import asyncio
import contextvars
import json
import math
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
import httpx

from .config import Settings, get_settings
from .database import SessionLocal, get_db, run_migrations
from .models import Annotation, Conversation, Document, Folder, Message, ModelFavorite, Page, PaperNote, UsageEvent
from .schemas import AiNoteCreate, AiNoteUnlink, AnnotationIn, AnnotationOut, AnnotationUpdate, AppSettingsOut, AppSettingsUpdate, ChatRequest, ConversationCreate, ConversationDraftUpdate, ConversationDetail, ConversationOut, ConversationUpdate, DocumentArchive, DocumentMove, DocumentOut, DocumentUpdate, FolderIn, FolderOut, FolderUpdate, ModelFavoriteUpdate, PageTextOut, PaperNoteOut, PaperNoteUpdate, ProviderKeyUpdate, ProviderModelsOut, QuickAskImportRequest, QuickAskRequest, TitleModelUpdate
from .schemas import ChatTurnIn, ContextImageIn, PriceOverrideIn
from .services.context import build_paper_context
from .services.documents import document_file_path, ingest_pdf
from .services.thumbnails import THUMBNAIL_MEDIA_TYPE, THUMBNAIL_WIDTHS, ThumbnailBusy, ensure_thumbnail, remove_thumbnails, shutdown_thumbnail_workers, thumbnail_etag
from .services.llm import AnthropicProvider, ChatGPTSubscriptionProvider, GoogleProvider, OpenAICompatibleProvider, OpenAIResponsesProvider, TITLE_SYSTEM_PROMPT, clean_title, generate_zen_title, sse
from .services.chatgpt_auth import ChatGPTAuth, ChatGPTAuthError, friendly_api_error, error_code, parse_callback_url
from .services.web_search import asks_for_citing_papers, plan_search, previous_sources, search_citing_papers, search_web, web_context
from .schemas import ChatGPTCallbackIn, ChatGPTSignInOut, ChatGPTStatusOut
from .schemas import PaperCandidate, PaperImportIn, PaperSearchOut, PdfLinkIn, PdfLinkOut
from .services.paper_discovery import discover, download_pdf, existing_paper, resolve_pdf, verify_pdf_link
from .services.pricing import PriceBook, RefreshTooSoon, parse_overrides
from .services.usage import UsageTracker, record_usage_event
from .services.usage_summary import RANGE_DAYS, UsageRow, build_usage_summary, resolve_zone

settings = get_settings()
run_migrations()
logger = logging.getLogger(__name__)
price_book = PriceBook(settings.data_dir / "pricing")
# (document_id, conversation_id) that title-generation usage is attributed to.
title_usage_scope: contextvars.ContextVar[tuple[str | None, str | None]] = contextvars.ContextVar("title_usage_scope", default=(None, None))
chatgpt_auth = ChatGPTAuth(settings.data_dir, settings.chatgpt_callback_port)
chatgpt_listener: asyncio.base_events.Server | None = None
PROVIDER_NAMES = ("zen", "openrouter", "openai", "anthropic", "google", "chatgpt")


async def ensure_chatgpt_listener() -> None:
    """Start the loopback callback listener on first sign-in so a busy port never blocks startup."""
    global chatgpt_listener
    if chatgpt_listener is None or not chatgpt_listener.is_serving():
        chatgpt_listener = await chatgpt_auth.start_listener(settings.chatgpt_callback_listen_host, settings.chatgpt_callback_listen_port or settings.chatgpt_callback_port)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Keep the 30-day refresh token rotating while the server runs.
    refresher = asyncio.create_task(chatgpt_auth.background_refresh())
    yield
    refresher.cancel()
    if chatgpt_listener is not None:
        chatgpt_listener.close()
    # Stop thumbnail worker processes with the server instead of leaving them to interpreter exit.
    await run_in_threadpool(shutdown_thumbnail_workers)


app = FastAPI(title="Adam API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/api/paper-discovery/search", response_model=PaperSearchOut)
async def search_papers(q: str = Query(min_length=1, max_length=500), db: Session = Depends(get_db)):
    if not q.strip():
        raise HTTPException(422, "Enter a paper title, keywords, DOI, or arXiv link.")
    return await discover(q.strip(), settings.search_base_url, db)


@app.post("/api/paper-discovery/resolve", response_model=PaperCandidate)
async def resolve_paper(paper: PaperCandidate):
    try:
        async with asyncio.timeout(35):
            return await resolve_pdf(paper)
    except (httpx.HTTPError, OSError, TimeoutError):
        raise HTTPException(502, "The source could not be reached. Open its page or try another result.") from None


@app.post("/api/paper-discovery/verify-link", response_model=PdfLinkOut)
async def check_pdf_link(request: PdfLinkIn, db: Session = Depends(get_db)):
    try:
        result = await verify_pdf_link(request.url, settings.max_upload_mb * 1024 * 1024)
        if existing := existing_paper(db, result['paper']):
            result['paper'].existing_document_id = existing.id
        return result
    except (httpx.HTTPError, OSError, TimeoutError):
        raise HTTPException(502, 'The PDF could not be reached. Check the link and try again.') from None


@app.post("/api/paper-discovery/import", response_model=DocumentOut)
async def import_paper(request: PaperImportIn, db: Session = Depends(get_db)):
    if request.folder_id and not db.get(Folder, request.folder_id):
        raise HTTPException(404, "The destination folder no longer exists. Choose another folder.")
    if existing := existing_paper(db, request.paper):
        return existing
    try:
        upload = await download_pdf(request.pdf_url, request.paper.title, settings.max_upload_mb * 1024 * 1024)
        metadata = request.paper.model_dump(exclude={"existing_document_id", "pdf_urls"})
        metadata.update({"pdf_url": request.pdf_url, "imported_at": datetime.now(timezone.utc).isoformat()})
        return await ingest_pdf(upload, settings, folder_id=request.folder_id, discovery=metadata)
    except (httpx.HTTPError, OSError, TimeoutError):
        raise HTTPException(502, "The PDF could not be downloaded. Retry, choose another source, or upload it manually.") from None


def missing_credentials_message(provider: str, purpose: str = "chat") -> str:
    if provider == "chatgpt":
        return f"Sign in with ChatGPT in Settings → ChatGPT (subscription) to enable {purpose}."
    return f"No API key is configured for {provider}. Open Settings to enable {purpose}."


def make_provider(provider_name: str, api_key: str, model: str, system_prompt: str):
    if provider_name == "chatgpt": return ChatGPTSubscriptionProvider(chatgpt_auth, model, system_prompt)
    if provider_name == "google": return GoogleProvider(api_key, model, "https://generativelanguage.googleapis.com/v1beta", system_prompt)
    if provider_name == "anthropic": return AnthropicProvider(api_key, model, system_prompt=system_prompt)
    if provider_name == "openai": return OpenAIResponsesProvider(api_key, model, "https://api.openai.com/v1", system_prompt)
    if provider_name == "zen" and model.startswith("gemini-"): return GoogleProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    if provider_name == "zen" and model.startswith(("claude-", "qwen")): return AnthropicProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    if provider_name == "zen" and model.startswith(("deepseek-", "minimax-", "glm-", "kimi-", "big-pickle", "mimo-", "ling-", "nemotron-")): return OpenAICompatibleProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    if provider_name == "zen": return OpenAIResponsesProvider(api_key, model, "https://opencode.ai/zen/v1", system_prompt)
    return OpenAICompatibleProvider(api_key, model, {"openrouter": "https://openrouter.ai/api/v1"}[provider_name], system_prompt)


async def generate_configured_title(transcript: str, fallback: str) -> str:
    provider_name, model = settings.title_provider, settings.title_model
    api_key = settings.provider_api_key(provider_name)
    if not api_key:
        raise ValueError(missing_credentials_message(provider_name, "title generation"))
    document_id, conversation_id = title_usage_scope.get()
    tracker = UsageTracker(SessionLocal, "title", document_id, conversation_id)
    if provider_name == "zen" and model.startswith(("gpt-", "grok-", "muse-")):
        def record(usage) -> None:
            record_usage_event(SessionLocal, kind="title", provider=provider_name, model=model, usage=usage, status="completed",
                               document_id=document_id, conversation_id=conversation_id)
        return await generate_zen_title(api_key, transcript, fallback, model, on_usage=record)
    provider = tracker.track(provider_name, model, make_provider(provider_name, api_key, model, TITLE_SYSTEM_PROMPT))
    parts = []
    status = "partial"
    try:
        async for part in provider.stream_answer(transcript[:30000], "", [], None, []):
            parts.append(part)
            if sum(map(len, parts)) > 1000:
                break
        else:
            status = "completed"
    except Exception:
        status = "failed"
        raise
    finally:
        tracker.finish(status)
    return clean_title("".join(parts), fallback)


# One title job per conversation; strong references keep running tasks alive.
title_jobs: dict[str, asyncio.Task] = {}


async def persist_generated_title(conversation_id: str, provisional_title: str, transcript: str, document_id: str | None = None) -> str | None:
    try:
        title_usage_scope.set((document_id, conversation_id))
        title = await generate_configured_title(transcript, provisional_title)
        # Open the session only after the slow model call so no connection is held while waiting.
        return await run_in_threadpool(save_generated_title, conversation_id, provisional_title, title)
    except Exception:
        # A failed title must never turn a saved answer into a chat error.
        logger.warning("Automatic title generation failed for conversation %s", conversation_id, exc_info=True)
        return None


def start_title_job(conversation_id: str, provisional_title: str, transcript: str, document_id: str | None = None) -> asyncio.Task:
    job = title_jobs.get(conversation_id)
    if job and not job.done():
        return job
    job = asyncio.create_task(persist_generated_title(conversation_id, provisional_title, transcript, document_id))
    title_jobs[conversation_id] = job
    job.add_done_callback(lambda done: title_jobs.pop(conversation_id, None) if title_jobs.get(conversation_id) is done else None)
    return job


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def settings_response(app_settings: Settings, db: Session) -> AppSettingsOut:
    runtime = app_settings.runtime_settings
    active_provider = runtime.get("provider", "zen")
    selected_models = dict(runtime.get("selected_models", {}))
    if active_provider not in selected_models:
        selected_models[active_provider] = runtime.get("model", app_settings.opencode_model)
    providers = {name: bool(app_settings.provider_api_key(name)) for name in PROVIDER_NAMES}
    favorites: dict[str, list[str]] = {}
    for item in db.scalars(select(ModelFavorite).order_by(ModelFavorite.created_at)).all():
        favorites.setdefault(item.provider, []).append(item.model_id)
    return AppSettingsOut(provider=active_provider, model=app_settings.resolved_opencode_model, title_provider=app_settings.title_provider, title_model=app_settings.title_model, selected_models=selected_models, providers=providers, favorites=favorites, system_prompt=app_settings.system_prompt, quick_ask_prompt=app_settings.quick_ask_prompt)


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


@app.put("/api/settings/title", response_model=AppSettingsOut)
def update_title_model(request: TitleModelUpdate, app_settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> AppSettingsOut:
    if not app_settings.provider_api_key(request.provider):
        raise HTTPException(503, "Sign in with ChatGPT before selecting it for chat titles." if request.provider == "chatgpt" else f"Add a {request.provider} API key before selecting it for chat titles.")
    app_settings.save_title_settings(request.provider, request.model)
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
    if provider not in PROVIDER_NAMES:
        raise HTTPException(404, "Unknown provider.")
    if provider == "chatgpt":
        return ProviderModelsOut(provider=provider, models=await list_chatgpt_models())
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


async def list_chatgpt_models() -> list[str]:
    try:
        token = await chatgpt_auth.access_token()
        async with chatgpt_auth.client(timeout=20) as client:
            response = await client.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {token}"})
    except ChatGPTAuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Could not load models from ChatGPT.") from exc
    body = chatgpt_auth.json_body(response)
    if response.status_code != 200:
        code, param = error_code(body)
        raise HTTPException(response.status_code, friendly_api_error(response.status_code, code, param))
    # Keep server order; only models marked visibility == "list" are user-selectable.
    models = [item.get("slug") for item in body.get("models", []) if isinstance(item, dict) and item.get("visibility") == "list" and item.get("slug")]
    return list(dict.fromkeys(models))


def chatgpt_status(extra: dict | None = None) -> ChatGPTStatusOut:
    return ChatGPTStatusOut(**{**chatgpt_auth.status(), **(extra or {})})


@app.get("/api/chatgpt/status", response_model=ChatGPTStatusOut)
def get_chatgpt_status() -> ChatGPTStatusOut:
    return chatgpt_status()


@app.post("/api/chatgpt/sign-in", response_model=ChatGPTSignInOut)
async def start_chatgpt_sign_in() -> ChatGPTSignInOut:
    # The listener must be running before the browser opens the authorize URL.
    await ensure_chatgpt_listener()
    url = chatgpt_auth.start()
    return ChatGPTSignInOut(authorize_url=url, redirect_uri=chatgpt_auth.redirect_uri, listener_active=chatgpt_auth.listener_active, listener_error=chatgpt_auth.listener_error)


@app.post("/api/chatgpt/callback", response_model=ChatGPTStatusOut)
async def complete_chatgpt_sign_in(request: ChatGPTCallbackIn) -> ChatGPTStatusOut:
    try:
        await chatgpt_auth.complete(parse_callback_url(request.url))
    except ChatGPTAuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Adam could not reach ChatGPT to finish signing in. Try again.") from exc
    return chatgpt_status()


@app.post("/api/chatgpt/sign-out", response_model=ChatGPTStatusOut)
async def chatgpt_sign_out() -> ChatGPTStatusOut:
    result = await chatgpt_auth.sign_out()
    return ChatGPTStatusOut(**result)


@app.post("/api/chatgpt/resume", response_model=ChatGPTStatusOut)
def resume_chatgpt_usage() -> ChatGPTStatusOut:
    chatgpt_auth.resume_usage()
    return chatgpt_status()


FOLDER_COLORS = ["#8c9d65", "#618fbd", "#b8836d", "#9a80b8", "#c29d54", "#689f98"]


def validate_folder_name(name: str, db: Session, folder_id: str | None = None, parent_id: str | None = None) -> str:
    name = name.strip()
    if not name or any(ord(char) < 32 for char in name):
        raise HTTPException(422, "Enter a folder name.")
    existing = db.scalar(select(Folder).where(func.lower(Folder.name) == name.lower(), Folder.parent_id == parent_id, Folder.id != (folder_id or "")))
    if existing and existing.archived_at:
        raise HTTPException(409, "An archived folder with this name already exists here. Restore it or choose another name.")
    if existing:
        raise HTTPException(409, "A folder with this name already exists here.")
    return name


def folder_parent(db: Session, parent_id: str | None, folder_id: str | None = None) -> Folder | None:
    parent = db.get(Folder, parent_id) if parent_id else None
    if parent_id and not parent:
        raise HTTPException(404, "Destination folder not found.")
    ancestor = parent
    while ancestor:
        if ancestor.id == folder_id:
            raise HTTPException(422, "A folder cannot be moved into itself or its descendants.")
        ancestor = db.get(Folder, ancestor.parent_id) if ancestor.parent_id else None
    return parent


def automatic_folder_color(db: Session, previous: str | None = None) -> str:
    roots = list(db.scalars(select(Folder).where(Folder.parent_id.is_(None))))
    choices = [color for color in FOLDER_COLORS if color != previous]
    return min(choices, key=lambda color: sum(root.color == color for root in roots))


def color_branch(db: Session, folder: Folder, color: str) -> None:
    pending = [folder]
    children = list(db.scalars(select(Folder)))
    while pending:
        current = pending.pop()
        current.color = color
        pending.extend(child for child in children if child.parent_id == current.id)


@app.get("/api/folders", response_model=list[FolderOut])
def list_folders(db: Session = Depends(get_db)) -> list[Folder]:
    return list(db.scalars(select(Folder).order_by(func.lower(Folder.name))))


@app.post("/api/folders", response_model=FolderOut, status_code=201)
def create_folder(request: FolderIn, db: Session = Depends(get_db)) -> Folder:
    parent = folder_parent(db, request.parent_id)
    folder = Folder(name=validate_folder_name(request.name, db, parent_id=request.parent_id), parent_id=request.parent_id,
                    color=parent.color if parent else (request.color or automatic_folder_color(db)).lower(), icon=request.icon or "folder")
    db.add(folder)
    db.commit()
    return folder


@app.patch("/api/folders/{folder_id}", response_model=FolderOut)
def rename_folder(folder_id: str, request: FolderUpdate, db: Session = Depends(get_db)) -> Folder:
    folder = db.get(Folder, folder_id)
    if not folder:
        raise HTTPException(404, "Folder not found.")
    parent_id = request.parent_id if "parent_id" in request.model_fields_set else folder.parent_id
    parent = folder_parent(db, parent_id, folder_id)
    name = validate_folder_name(request.name if request.name is not None else folder.name, db, folder_id, parent_id)
    color = parent.color if parent else (automatic_folder_color(db, folder.color) if folder.parent_id else request.color or folder.color)
    folder.name = name
    folder.parent_id = parent_id
    color_branch(db, folder, color.lower())
    if request.icon is not None:
        folder.icon = request.icon
    if request.archived is not None:
        # Descendants stay as they are; clients hide everything below an archived folder.
        folder.archived_at = datetime.now(timezone.utc) if request.archived else None
    db.commit()
    return folder


@app.delete("/api/folders/{folder_id}", status_code=204)
def delete_folder(folder_id: str, db: Session = Depends(get_db)) -> Response:
    folder = db.get(Folder, folder_id)
    if not folder:
        raise HTTPException(404, "Folder not found.")
    # Keep children and their contents, promoting them to the library root.
    children = list(db.scalars(select(Folder).where(Folder.parent_id == folder_id)))
    for child in children:
        base = child.name
        suffix = 2
        while db.scalar(select(Folder).where(Folder.parent_id.is_(None), func.lower(Folder.name) == child.name.lower(), Folder.id != child.id)):
            child.name = f"{base[:70]} ({suffix})"
            suffix += 1
        child.parent_id = None
        color_branch(db, child, automatic_folder_color(db, child.color))
        db.flush()
    for document in db.scalars(select(Document).where(Document.folder_id == folder_id)):
        document.folder_id = None
    db.delete(folder)
    db.commit()
    return Response(status_code=204)


@app.post("/api/document-moves", response_model=list[DocumentOut])
def move_documents(request: DocumentMove, db: Session = Depends(get_db)) -> list[Document]:
    if request.folder_id is not None and not db.get(Folder, request.folder_id):
        raise HTTPException(404, "Destination folder not found.")
    ids = set(request.document_ids)
    documents = list(db.scalars(select(Document).where(Document.id.in_(ids))))
    if len(documents) != len(ids):
        raise HTTPException(404, "One or more papers no longer exist.")
    for document in documents:
        document.folder_id = request.folder_id
    db.commit()
    return documents


@app.post("/api/document-archive", response_model=list[DocumentOut])
def archive_documents(request: DocumentArchive, db: Session = Depends(get_db)) -> list[Document]:
    ids = set(request.document_ids)
    documents = list(db.scalars(select(Document).where(Document.id.in_(ids))))
    if len(documents) != len(ids):
        raise HTTPException(404, "One or more papers no longer exist.")
    archived_at = datetime.now(timezone.utc) if request.archived else None
    folders = {folder.id: folder for folder in db.scalars(select(Folder))} if not request.archived else {}
    for document in documents:
        # A restored paper must be visible even when an ancestor remains archived.
        folder = folders.get(document.folder_id)
        seen = set()
        while folder and folder.id not in seen:
            if folder.archived_at:
                document.folder_id = None
                break
            seen.add(folder.id)
            folder = folders.get(folder.parent_id)
        document.archived_at = archived_at
    db.commit()
    return documents


@app.get("/api/documents", response_model=list[DocumentOut])
def list_documents(db: Session = Depends(get_db)) -> list[Document]:
    return list(db.scalars(select(Document).order_by(Document.updated_at.desc())))


@app.post("/api/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile,
    app_settings: Settings = Depends(get_settings),
) -> Document:
    return await ingest_pdf(file, app_settings)


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
    sha256 = document.sha256
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
    remove_thumbnails(app_settings.data_dir, sha256)
    return Response(status_code=204)


# A document id always names the same bytes (its SHA-256), so browsers may keep the PDF.
# "private" keeps shared proxies from storing a user's papers.
DOCUMENT_FILE_CACHE_CONTROL = "private, max-age=31536000, immutable"
# Thumbnail URLs do not carry the renderer version, so they revalidate weekly instead.
THUMBNAIL_CACHE_CONTROL = "private, max-age=604800"


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Weak If-None-Match comparison, as RFC 9110 requires for GET."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    return any(candidate.strip().removeprefix("W/") == etag for candidate in if_none_match.split(","))


def managed_document_source(document: Document | None, app_settings: Settings) -> Path:
    if not document:
        raise HTTPException(404, "Document file not found.")
    source = document_file_path(document.storage_path, app_settings)
    if not source.is_relative_to((app_settings.data_dir / "documents").resolve()) or not source.is_file():
        raise HTTPException(404, "Document file not found.")
    return source


@app.get("/api/documents/{document_id}/file")
def get_document_file(document_id: str, request: Request, db: Session = Depends(get_db), app_settings: Settings = Depends(get_settings)) -> Response:
    document = db.get(Document, document_id)
    source = managed_document_source(document, app_settings)
    # The ETag is the content hash, so it stays stable across renames and Docker/local path changes.
    # Tradeoff: a browser holding the immutable copy keeps the filename from Content-Disposition
    # that was current when it cached the PDF; the in-app reader does not use that filename.
    headers = {"ETag": f'"{document.sha256}"', "Cache-Control": DOCUMENT_FILE_CACHE_CONTROL}
    if etag_matches(request.headers.get("if-none-match"), headers["ETag"]):
        return Response(status_code=304, headers=headers)
    # FileResponse keeps handling Range and If-Range (compared against this strong ETag).
    return FileResponse(source, media_type="application/pdf", filename=document.original_name, content_disposition_type="inline", headers=headers)


@app.get("/api/documents/{document_id}/thumbnail")
async def get_document_thumbnail(document_id: str, request: Request, width: int = THUMBNAIL_WIDTHS[0], db: Session = Depends(get_db), app_settings: Settings = Depends(get_settings)) -> Response:
    if width not in THUMBNAIL_WIDTHS:
        raise HTTPException(422, f"Thumbnail width must be one of {', '.join(map(str, THUMBNAIL_WIDTHS))}.")
    document = db.get(Document, document_id)
    source = managed_document_source(document, app_settings)
    sha256 = document.sha256
    # Release the connection before waiting on a render.
    db.close()
    headers = {"ETag": thumbnail_etag(sha256, width), "Cache-Control": THUMBNAIL_CACHE_CONTROL}
    if etag_matches(request.headers.get("if-none-match"), headers["ETag"]):
        return Response(status_code=304, headers=headers)
    try:
        path = await ensure_thumbnail(app_settings.data_dir, sha256, source, width)
    except ThumbnailBusy:
        raise HTTPException(503, "Previews are busy; try again shortly.", headers={"Retry-After": "2"})
    except Exception as exc:
        if not source.is_file():
            raise HTTPException(404, "Document file not found.") from exc
        raise HTTPException(422, "This PDF could not be rendered as a preview.") from exc
    if not source.is_file():
        # The paper was deleted while rendering; do not leave its thumbnail behind.
        remove_thumbnails(app_settings.data_dir, sha256)
        raise HTTPException(404, "Document file not found.")
    return FileResponse(path, media_type=THUMBNAIL_MEDIA_TYPE, headers=headers)


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
    return [{**ConversationOut.model_validate(conversation).model_dump(), "message_count": count} for conversation, count in rows if count or conversation.draft_text.strip()]


@app.get("/api/documents/{document_id}/conversation-search")
def search_conversations(document_id: str, q: str = Query(min_length=1, max_length=200), db: Session = Depends(get_db)) -> list[dict]:
    if not db.get(Document, document_id):
        raise HTTPException(404, "Document not found.")
    needle = q.strip().casefold()
    if not needle:
        return []
    # Read all message roles in one query; never fetch conversation details per card.
    rows = db.execute(select(Conversation, Message.content).outerjoin(Message, Message.conversation_id == Conversation.id).where(Conversation.document_id == document_id).order_by(Conversation.updated_at.desc(), Message.created_at)).all()
    matches = {}
    for conversation, content in rows:
        title_match = needle in conversation.title.casefold()
        offset = (content or "").casefold().find(needle)
        if not title_match and offset < 0:
            continue
        if conversation.id not in matches:
            matches[conversation.id] = {"id": conversation.id, "snippet": "", "title_match": title_match}
        if offset >= 0 and not matches[conversation.id]["snippet"]:
            start = max(0, offset - 60)
            end = min(len(content), offset + len(q) + 110)
            matches[conversation.id]["snippet"] = ("…" if start else "") + content[start:end] + ("…" if end < len(content) else "")
    return list(matches.values())


@app.post("/api/documents/{document_id}/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(document_id: str, request: ConversationCreate, db: Session = Depends(get_db)) -> Conversation:
    if not db.get(Document, document_id):
        raise HTTPException(404, "Document not found.")
    # Serialize creation so double clicks and multiple tabs reuse one blank workspace.
    # The document lookup already opened a transaction; restart it with a write lock.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    empty = db.scalar(select(Conversation).where(
        Conversation.document_id == document_id,
        func.trim(Conversation.draft_text) == "",
        ~select(Message.id).where(Message.conversation_id == Conversation.id).exists(),
    ).order_by(Conversation.updated_at.desc()).limit(1))
    if empty:
        runtime = settings.runtime_settings
        empty.provider = runtime.get("provider", "zen")
        empty.model_id = settings.resolved_opencode_model
        empty.system_prompt = settings.system_prompt
        db.commit()
        db.refresh(empty)
        return empty
    runtime = settings.runtime_settings
    provider = runtime.get("provider", "zen")
    conversation = Conversation(document_id=document_id, title=request.title or "New chat", provider=provider,
                                model_id=settings.resolved_opencode_model, system_prompt=settings.system_prompt)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)) -> ConversationDetail:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    return ConversationDetail.model_validate(conversation).model_copy(update={"message_count": len(conversation.messages)})


@app.put("/api/conversations/{conversation_id}/draft", response_model=ConversationOut)
def save_conversation_draft(conversation_id: str, request: ConversationDraftUpdate, db: Session = Depends(get_db)) -> ConversationOut:
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found.")
    conversation.draft_text = request.draft_text if request.draft_text.strip() else ""
    db.commit()
    db.refresh(conversation)
    count = db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conversation.id)) or 0
    return ConversationOut.model_validate(conversation).model_copy(update={"message_count": count})


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


def load_title_transcript(conversation_id: str) -> tuple[str, str, str]:
    with SessionLocal() as db:
        conversation = db.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(404, "Conversation not found.")
        if not settings.provider_api_key(settings.title_provider):
            raise HTTPException(503, missing_credentials_message(settings.title_provider, "chat titles"))
        messages = list(db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)))
        if not messages:
            raise HTTPException(409, "Send a message before generating a title.")
        transcript = "\n\n".join(f"{message.role.title()}: {message.content}" for message in messages)
        fallback = next((message.content for message in messages if message.role == "user"), conversation.title)[:80]
        return transcript, fallback, conversation.document_id


def save_regenerated_title(conversation_id: str, title: str) -> ConversationOut:
    with SessionLocal() as db:
        conversation = db.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(404, "Conversation not found.")
        conversation.title = title
        db.commit()
        db.refresh(conversation)
        return ConversationOut.model_validate(conversation)


@app.post("/api/conversations/{conversation_id}/regenerate-title", response_model=ConversationOut)
async def regenerate_conversation_title(conversation_id: str) -> ConversationOut:
    # No session stays open while the title model is awaited.
    transcript, fallback, document_id = await run_in_threadpool(load_title_transcript, conversation_id)
    title_usage_scope.set((document_id, conversation_id))
    try:
        title = await generate_configured_title(transcript, fallback)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(502, "The title model could not generate a title.") from exc
    return await run_in_threadpool(save_regenerated_title, conversation_id, title)


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


@dataclass(frozen=True)
class ChatStreamSetup:
    conversation_id: str
    document_id: str
    title: str
    provider_name: str
    model: str
    api_key: str
    system_prompt: str
    paper_title: str
    paper_context: str
    context_mode: str
    history: list[ChatTurnIn]
    prior_sources: list[dict]
    should_generate_title: bool


def prepare_chat_stream(conversation_id: str, request: ChatRequest) -> ChatStreamSetup:
    # Streams outlive request dependencies, so they copy plain values out of a short session.
    with SessionLocal() as db:
        conversation = db.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(404, "Conversation not found.")
        paper_title = Path(db.get(Document, conversation.document_id).original_name).stem
        provider_name = conversation.provider
        api_key = settings.provider_api_key(provider_name)
        if not api_key:
            raise HTTPException(503, missing_credentials_message(provider_name))

        if conversation.context_builder_version == "quick-ask-v1":
            paper_context, context_mode = "", "quick_ask_history"
        else:
            paper_context, context_mode = build_paper_context(db, conversation, request.question, request.selected_text)
        history_messages = list(db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)))
        history = []
        pending = None
        for message in history_messages:
            if message.role == "user":
                saved = json.loads(message.context_json or "{}")
                pending = (message, saved)
            elif message.role == "assistant" and pending:
                prior, saved = pending
                history.append(ChatTurnIn(question=prior.content, answer=message.content, selected_text=saved.get("selected_text", ""), page=saved.get("page"), images=[ContextImageIn(**item) for item in saved.get("images", [])]))
                pending = None
        prior_sources = previous_sources(db, conversation.id)

        scope = "quick_ask_followup" if conversation.context_builder_version == "quick-ask-v1" else ("selection" if request.selected_text or request.images else "paper")
        context = {"scope": scope, "page": request.page, "selected_text": request.selected_text, "images": [image.model_dump() for image in request.images], "anchors": [anchor.model_dump() for anchor in request.anchors], "context_mode": context_mode, "search_requested": request.allow_web_search}
        db.add(Message(document_id=conversation.document_id, conversation_id=conversation.id, role="user", content=request.question, context_json=json.dumps(context)))
        conversation.draft_text = ""
        should_generate_title = conversation.title == "New chat" and bool(settings.provider_api_key(settings.title_provider))
        if conversation.title == "New chat":
            conversation.title = request.question.strip()[:80]
        db.commit()
        return ChatStreamSetup(conversation.id, conversation.document_id, conversation.title, provider_name, conversation.model_id, api_key,
                               conversation.system_prompt, paper_title, paper_context, context_mode, history, prior_sources, should_generate_title)


def save_assistant_message(setup: ChatStreamSetup, content: str, web: dict, keep_web: bool) -> None:
    with SessionLocal() as db:
        db.add(Message(document_id=setup.document_id, conversation_id=setup.conversation_id, role="assistant", content=content, context_json=json.dumps({"web": web}) if keep_web else None))
        stored = db.get(Conversation, setup.conversation_id)
        if stored: stored.updated_at = datetime.now(timezone.utc)
        db.commit()


def save_generated_title(conversation_id: str, provisional_title: str, title: str) -> str | None:
    with SessionLocal() as db:
        stored = db.get(Conversation, conversation_id)
        # Preserve a title the user changed while the answer streamed.
        if stored and stored.title == provisional_title:
            stored.title = title
            db.commit()
        return stored.title if stored else None


@app.post("/api/conversations/{conversation_id}/messages/stream")
async def chat_stream(conversation_id: str, request: ChatRequest) -> StreamingResponse:
    setup = await run_in_threadpool(prepare_chat_stream, conversation_id, request)
    provider_name, api_key, model, paper_title = setup.provider_name, setup.api_key, setup.model, setup.paper_title

    async def events() -> AsyncIterator[str]:
        complete = ""
        usage = UsageTracker(SessionLocal, "chat", setup.document_id, setup.conversation_id)
        # Stays "partial" if the client disconnects mid-stream.
        status = "partial"
        try:
            yield sse({"type": "started", "provider": provider_name, "model": model, "context_mode": setup.context_mode, "conversation_id": setup.conversation_id, "title": setup.title})
            prior_sources = setup.prior_sources
            fresh_sources = []
            search_query = None
            search_performed = False
            if request.allow_web_search:
                yield sse({"type": "searching"})
                try:
                    planner = usage.track(provider_name, model, make_provider(provider_name, api_key, model, ""))
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
            system_prompt = setup.system_prompt + setup.paper_context + web_context(sources)
            provider = usage.track(provider_name, model, make_provider(provider_name, api_key, model, system_prompt))
            async for delta in provider.stream_answer(request.question, request.selected_text, request.images, request.page, setup.history[-20:]):
                complete += delta
                yield sse({"type": "delta", "text": delta})
            status = "completed"
            usage.finish(status)
            await run_in_threadpool(save_assistant_message, setup, complete, web, bool(search_performed or sources))
            title_job = None
            if setup.should_generate_title:
                # Resolve vague questions from the answer instead of naming the request.
                transcript = f"User: {request.question.strip()[:8000]}"
                if request.selected_text:
                    transcript += f"\n\nSelected passage: {request.selected_text[:6000]}"
                transcript += f"\n\nAssistant: {complete[:12000]}"
                title_job = start_title_job(setup.conversation_id, setup.title, transcript, setup.document_id)
            # The answer is saved: release the composer now instead of waiting on the title model.
            yield sse({"type": "completed", "web": web, "title": setup.title, "title_pending": title_job is not None})
            if title_job:
                # Shield the job so a client disconnect still lets the title finish and persist.
                title = await asyncio.shield(title_job)
                if title:
                    yield sse({"type": "title", "title": title})
        except Exception as exc:
            if status != "completed":
                status = "failed"
            yield sse({"type": "error", "message": str(exc)})
        finally:
            # Synchronous so it also runs when the stream is cancelled; never raises.
            usage.finish(status)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def prepare_quick_ask(conversation_id: str, request: QuickAskRequest) -> tuple[str, str, str, str, str]:
    with SessionLocal() as db:
        conversation = db.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(404, "Conversation not found.")
        history_has_selection = any(turn.selected_text or turn.images for turn in request.history)
        if not request.selected_text and not request.images and not history_has_selection:
            raise HTTPException(422, "Quick Ask requires selected text or a screenshot.")
        api_key = settings.provider_api_key(conversation.provider)
        if not api_key:
            raise HTTPException(503, missing_credentials_message(conversation.provider, "Quick Ask"))
        paper_title = Path(db.get(Document, conversation.document_id).original_name).stem
        return conversation.provider, conversation.model_id, api_key, paper_title, conversation.document_id


@app.post("/api/conversations/{conversation_id}/quick-ask/stream")
async def quick_ask_stream(conversation_id: str, request: QuickAskRequest) -> StreamingResponse:
    provider_name, model, api_key, paper_title, document_id = await run_in_threadpool(prepare_quick_ask, conversation_id, request)

    async def events() -> AsyncIterator[str]:
        usage = UsageTracker(SessionLocal, "quick_ask", document_id, conversation_id)
        status = "partial"
        try:
            yield sse({"type": "started", "provider": provider_name, "model": model})
            prior_sources = []
            seen_urls = set()
            for turn in reversed(request.history):
                for source in turn.web.sources if turn.web else []:
                    if source.url not in seen_urls:
                        prior_sources.append(source.model_dump())
                        seen_urls.add(source.url)
                    if len(prior_sources) >= 4:
                        break
                if len(prior_sources) >= 4:
                    break
            fresh_sources = []
            search_query = None
            search_performed = False
            if request.allow_web_search:
                yield sse({"type": "searching"})
                try:
                    planner = usage.track(provider_name, model, make_provider(provider_name, api_key, model, ""))
                    search_selection = request.selected_text or (request.history[0].selected_text if request.history else "")
                    search_query = await plan_search(planner, request.question, search_selection, paper_title)
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
                    yield sse({"type": "search_error", "message": "Web search is unavailable; answering with the selection and earlier sources."})
            sources = (fresh_sources + [item for item in prior_sources if item["url"] not in {source["url"] for source in fresh_sources}])[:6]
            web = {"searched": search_performed, "query": search_query if search_performed else None, "reused": bool(prior_sources), "sources": sources}
            yield sse({"type": "web_sources", "web": web})
            provider = usage.track(provider_name, model, make_provider(provider_name, api_key, model, settings.quick_ask_prompt + web_context(sources)))
            async for delta in provider.stream_answer(request.question, request.selected_text, request.images, request.page, request.history):
                yield sse({"type": "delta", "text": delta})
            status = "completed"
            yield sse({"type": "completed", "web": web})
        except Exception as exc:
            status = "failed"
            yield sse({"type": "error", "message": str(exc)})
        finally:
            usage.finish(status)

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
        db.add(Message(document_id=conversation.document_id, conversation_id=conversation.id, role="assistant", content=turn.answer, context_json=json.dumps({"web": turn.web.model_dump()}) if turn.web else None))
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conversation)
    result = ConversationOut.model_validate(conversation)
    return result.model_copy(update={"message_count": db.scalar(select(func.count(Message.id)).where(Message.conversation_id == conversation.id)) or 0})


def load_usage_rows(since: datetime, kind: str | None) -> list[UsageRow]:
    with SessionLocal() as db:
        query = select(UsageEvent).where(UsageEvent.created_at >= since)
        if kind:
            query = query.where(UsageEvent.kind == kind)
        return [UsageRow(event.created_at, event.kind, event.provider, event.model_id, event.uncached_input_tokens, event.cache_read_tokens,
                         event.cache_write_tokens, event.output_tokens, event.reasoning_tokens, event.provider_reported_cost_usd, event.status)
                for event in db.scalars(query)]


@app.get("/api/usage/summary")
async def usage_summary(days: int = 7, provider: str | None = None, kind: str | None = None, tz: str | None = Query(default=None, max_length=64)) -> dict:
    if days not in RANGE_DAYS:
        raise HTTPException(422, f"Range must be one of {', '.join(map(str, RANGE_DAYS))} days.")
    if kind not in (None, "", "chat", "quick_ask", "title"):
        raise HTTPException(422, "Kind must be chat, quick_ask, or title.")
    try:
        await price_book.ensure_fresh()
    except Exception:
        logger.warning("Price refresh failed; using cached prices", exc_info=True)
    zone = resolve_zone(tz)
    now = datetime.now(timezone.utc)
    # Query a day early; the summary trims to the exact local window.
    since = now - timedelta(days=days + 1)
    rows = await run_in_threadpool(load_usage_rows, since, kind or None)
    return build_usage_summary(rows, book=price_book, overrides=parse_overrides(settings.price_overrides), days=days, now=now,
                               zone=zone, provider=provider or None, kind=kind or None)


@app.post("/api/usage/prices/refresh")
async def refresh_usage_prices() -> dict:
    try:
        await price_book.ensure_fresh(force=True)
    except RefreshTooSoon as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(exc.retry_after)}) from exc
    return price_book.status()


@app.get("/api/usage/price-overrides")
def list_price_overrides() -> dict:
    return settings.price_overrides


@app.put("/api/usage/price-overrides")
def save_price_override(request: PriceOverrideIn) -> dict:
    values = [request.input, request.output, request.cache_read, request.cache_write]
    if any(value is not None and not math.isfinite(value) for value in values):
        raise HTTPException(422, "Prices must be finite numbers.")
    settings.save_price_override(request.model.strip(), {"input": request.input, "output": request.output,
                                                         "cache_read": request.cache_read, "cache_write": request.cache_write})
    return settings.price_overrides


@app.delete("/api/usage/price-overrides")
def delete_price_override(model: str = Query(min_length=1, max_length=200)) -> dict:
    settings.save_price_override(model.strip(), None)
    return settings.price_overrides
