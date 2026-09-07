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
    document_id: str
    question: str = Field(min_length=1, max_length=8000)
    selected_text: str = Field(default="", max_length=30000)
    page: int | None = Field(default=None, ge=1)
    images: list[ContextImageIn] = Field(default_factory=list, max_length=6)
    history: list[ChatTurnIn] = Field(default_factory=list, max_length=20)
