from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    folder_id: str | None
    original_name: str
    byte_size: int
    page_count: int
    status: str
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=512)


class PaperNoteUpdate(BaseModel):
    content_html: str = Field(max_length=1_000_000)
    plain_text: str = Field(max_length=300_000)
    revision: int = Field(ge=0)


class PaperNoteOut(BaseModel):
    document_id: str
    content_html: str
    plain_text: str
    revision: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PageTextOut(BaseModel):
    page: int
    text: str
    width_pt: float
    height_pt: float
    rotation: int


class HighlightRect(BaseModel):
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class AnnotationIn(BaseModel):
    id: str = Field(min_length=1, max_length=36)
    page: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=30000)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    rects: list[HighlightRect] = Field(min_length=1, max_length=500)
    note_text: str | None = Field(default=None, max_length=30000)


class AnnotationOut(AnnotationIn):
    created_at: datetime
    ai_links: list["AiNoteLinkOut"] = Field(default_factory=list)


class AnnotationUpdate(BaseModel):
    note_text: str = Field(max_length=30000)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class AiNoteLinkIn(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=36)
    question: str = Field(min_length=1, max_length=8000)


class AiNoteLinkOut(AiNoteLinkIn):
    title: str = Field(max_length=200)
    created_at: datetime


class AiNoteCreate(BaseModel):
    page: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=30000)
    rects: list[HighlightRect] = Field(min_length=1, max_length=500)
    link: AiNoteLinkIn


class ContextImageIn(BaseModel):
    data_url: str = Field(min_length=32, max_length=12_000_000, pattern=r"^data:image/(png|jpeg|webp);base64,")
    page: int | None = Field(default=None, ge=1)


class ContextAnchorIn(BaseModel):
    text: str = Field(min_length=1, max_length=30000)
    page: int = Field(ge=1)
    rects: list[HighlightRect] = Field(min_length=1, max_length=500)


class WebSourceIn(BaseModel):
    title: str = Field(max_length=200)
    url: str = Field(max_length=2000, pattern=r"^https?://")
    snippet: str = Field(max_length=7500)
    retrieved_at: str = Field(max_length=64)


class WebInfoIn(BaseModel):
    searched: bool = False
    query: str | None = Field(default=None, max_length=180)
    reused: bool = False
    sources: list[WebSourceIn] = Field(default_factory=list, max_length=6)


class ChatTurnIn(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    answer: str = Field(min_length=1, max_length=30000)
    selected_text: str = Field(default="", max_length=30000)
    page: int | None = Field(default=None, ge=1)
    images: list[ContextImageIn] = Field(default_factory=list, max_length=6)
    web: WebInfoIn | None = None


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    allow_web_search: bool = False
    selected_text: str = Field(default="", max_length=30000)
    page: int | None = Field(default=None, ge=1)
    images: list[ContextImageIn] = Field(default_factory=list, max_length=6)
    anchors: list[ContextAnchorIn] = Field(default_factory=list, max_length=20)


class QuickAskRequest(ChatRequest):
    history: list[ChatTurnIn] = Field(default_factory=list, max_length=30)


class QuickAskImportRequest(BaseModel):
    selected_text: str = Field(default="", max_length=30000)
    page: int | None = Field(default=None, ge=1)
    images: list[ContextImageIn] = Field(default_factory=list, max_length=6)
    turns: list[ChatTurnIn] = Field(min_length=1, max_length=30)
    anchors: list[ContextAnchorIn] = Field(default_factory=list, max_length=20)


class AiNoteUnlink(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=36)
    question: str = Field(min_length=1, max_length=8000)


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    role: str
    content: str
    context_json: str | None
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    document_id: str
    title: str
    provider: str
    model_id: str
    context_builder_version: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class AppSettingsOut(BaseModel):
    provider: str
    model: str
    title_provider: str
    title_model: str
    selected_models: dict[str, str]
    providers: dict[str, bool]
    favorites: dict[str, list[str]]
    system_prompt: str
    quick_ask_prompt: str


class AppSettingsUpdate(BaseModel):
    provider: str = Field(pattern=r"^(zen|openrouter|openai|anthropic|google)$")
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:/-]+$")
    api_keys: dict[str, str | None] = Field(default_factory=dict)
    favorites: dict[str, list[str]] = Field(default_factory=dict)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    quick_ask_prompt: str | None = Field(default=None, min_length=1, max_length=20000)


class TitleModelUpdate(BaseModel):
    provider: str = Field(pattern=r"^(zen|openrouter|openai|anthropic|google)$")
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:/-]+$")


class ProviderModelsOut(BaseModel):
    provider: str
    models: list[str]


class ModelFavoriteUpdate(BaseModel):
    provider: str = Field(pattern=r"^(zen|openrouter|openai|anthropic|google)$")
    model: str = Field(min_length=1, max_length=200)
    starred: bool


class ProviderKeyUpdate(BaseModel):
    provider: str = Field(pattern=r"^(zen|openrouter|openai|anthropic|google)$")
    api_key: str | None = Field(default=None, max_length=10000)


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    color: str
    created_at: datetime


class DocumentMove(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=500)
    folder_id: str | None = None
