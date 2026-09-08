from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    original_name: str
    byte_size: int
    page_count: int
    status: str
    created_at: datetime


class DocumentUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=512)


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


class AnnotationOut(AnnotationIn):
    created_at: datetime


class ContextImageIn(BaseModel):
    data_url: str = Field(min_length=32, max_length=12_000_000, pattern=r"^data:image/(png|jpeg|webp);base64,")
    page: int | None = Field(default=None, ge=1)


class ChatTurnIn(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    answer: str = Field(min_length=1, max_length=30000)
    selected_text: str = Field(default="", max_length=30000)
    page: int | None = Field(default=None, ge=1)
    images: list[ContextImageIn] = Field(default_factory=list, max_length=6)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    selected_text: str = Field(default="", max_length=30000)
    page: int | None = Field(default=None, ge=1)
    images: list[ContextImageIn] = Field(default_factory=list, max_length=6)


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
    selected_models: dict[str, str]
    providers: dict[str, bool]
    favorites: dict[str, list[str]]
    system_prompt: str


class AppSettingsUpdate(BaseModel):
    provider: str = Field(pattern=r"^(zen|openrouter|openai|anthropic|google)$")
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:/-]+$")
    api_keys: dict[str, str | None] = Field(default_factory=dict)
    favorites: dict[str, list[str]] = Field(default_factory=dict)
    system_prompt: str | None = Field(default=None, min_length=1, max_length=20000)


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
