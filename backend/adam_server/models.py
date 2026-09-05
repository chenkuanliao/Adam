from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    original_name: Mapped[str] = mapped_column(String(512))
    byte_size: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    pages: Mapped[list["Page"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    width_pt: Mapped[float]
    height_pt: Mapped[float]
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    native_text: Mapped[str] = mapped_column(Text, default="")
    document: Mapped[Document] = relationship(back_populates="pages")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Annotation(Base):
    __tablename__ = "annotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24), default="highlight")
    selected_text: Mapped[str] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(16))
    geometry_json: Mapped[str] = mapped_column(Text)
    note_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
