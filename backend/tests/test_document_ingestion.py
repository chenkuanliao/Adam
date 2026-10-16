import asyncio
import os
import tempfile
import threading
import time

import fitz
import httpx

os.environ.setdefault("ADAM_DATA_DIR", tempfile.mkdtemp(prefix="adam-tests-"))
os.environ.pop("ADAM_OPENCODE_API_KEY", None)

from sqlalchemy import func, select  # noqa: E402

from adam_server.config import get_settings  # noqa: E402
from adam_server.database import SessionLocal  # noqa: E402
from adam_server.main import app  # noqa: E402
from adam_server.models import Document, Page  # noqa: E402
from adam_server.services import documents as documents_module  # noqa: E402


def synthetic_pdf(label: str, pages: int = 1) -> bytes:
    document = fitz.open()
    for number in range(pages):
        page = document.new_page()
        # Columns out of reading order make sort=True observable in the stored text.
        page.insert_text((320, 90), f"{label} right column {number}")
        page.insert_text((72, 72), f"{label} left column {number}")
    value = document.tobytes()
    document.close()
    return value


def upload(client: httpx.AsyncClient, data: bytes, name: str = "paper.pdf"):
    return client.post("/api/documents", files={"file": (name, data, "application/pdf")})


def async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=30)


def blocking_open(monkeypatch, started: threading.Event, release: threading.Event):
    real_open = fitz.open

    def slow_open(*args, **kwargs):
        started.set()
        # Holds the worker like a large PDF would; on the event loop this would freeze /health.
        assert release.wait(10)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(documents_module.fitz, "open", slow_open)


def test_extraction_does_not_block_the_event_loop(monkeypatch) -> None:
    data = synthetic_pdf("responsive")
    started, release = threading.Event(), threading.Event()
    blocking_open(monkeypatch, started, release)

    async def scenario():
        async with async_client() as client:
            pending = asyncio.create_task(upload(client, data))
            while not started.is_set():
                await asyncio.sleep(0.01)
            began = time.perf_counter()
            health = await client.get("/health")
            elapsed = time.perf_counter() - began
            listed = await client.get("/api/documents")
            release.set()
            return health, elapsed, listed, await pending

    health, elapsed, listed, uploaded = asyncio.run(scenario())
    assert health.status_code == 200
    assert elapsed < 1
    assert listed.status_code == 200
    assert uploaded.status_code == 201


def test_concurrent_duplicate_uploads_share_one_document(monkeypatch) -> None:
    data = synthetic_pdf("concurrent duplicate", pages=3)
    opens = []
    real_open = fitz.open

    def counting_open(*args, **kwargs):
        opens.append(threading.current_thread().name)
        time.sleep(0.2)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(documents_module.fitz, "open", counting_open)

    async def scenario():
        async with async_client() as client:
            return await asyncio.gather(*(upload(client, data, f"copy-{index}.pdf") for index in range(4)))

    responses = asyncio.run(scenario())
    assert [response.status_code for response in responses] == [201] * 4
    assert len({response.json()["id"] for response in responses}) == 1
    assert len(opens) == 1
    document_id = responses[0].json()["id"]
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        assert db.scalar(select(func.count()).select_from(Document).where(Document.sha256 == document.sha256)) == 1
        assert db.scalar(select(func.count()).select_from(Page).where(Page.document_id == document_id)) == 3
    file_response = asyncio.run(fetch_file(document_id))
    assert file_response.status_code == 200
    assert file_response.content == data


async def fetch_file(document_id: str) -> httpx.Response:
    async with async_client() as client:
        return await client.get(f"/api/documents/{document_id}/file")


def test_duplicate_committed_elsewhere_returns_existing_document(monkeypatch) -> None:
    """Another server process can win the unique sha256 insert while this one extracts."""
    data = synthetic_pdf("cross process duplicate")
    real_open = fitz.open
    winner: dict[str, str] = {}

    def racing_open(path, *args, **kwargs):
        with open(path, "rb") as source:
            sha256 = documents_module.hashlib.sha256(source.read()).hexdigest()
        with SessionLocal() as db:
            document = Document(sha256=sha256, original_name="winner.pdf", byte_size=len(data), storage_path=f"documents/{sha256[:2]}/{sha256}.pdf", page_count=1, status="ready")
            db.add(document)
            db.commit()
            winner["id"] = document.id
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(documents_module.fitz, "open", racing_open)

    async def scenario():
        async with async_client() as client:
            return await upload(client, data, "loser.pdf")

    response = asyncio.run(scenario())
    assert response.status_code == 201, response.text
    assert response.json()["id"] == winner["id"]
    assert response.json()["original_name"] == "winner.pdf"


def test_stored_text_matches_sorted_pymupdf_extraction() -> None:
    data = synthetic_pdf("parity", pages=4)
    reference = fitz.open(stream=data, filetype="pdf")
    expected = [page.get_text("text", sort=True) for page in reference]
    reference.close()

    async def scenario():
        async with async_client() as client:
            uploaded = (await upload(client, data)).json()
            pages = [await client.get(f"/api/documents/{uploaded['id']}/pages/{number}/text") for number in range(1, 5)]
            return uploaded, pages

    uploaded, pages = asyncio.run(scenario())
    assert uploaded["page_count"] == 4
    assert [page.json()["text"] for page in pages] == expected


def test_rejected_uploads_leave_no_temp_files() -> None:
    async def scenario():
        async with async_client() as client:
            return await upload(client, b"not a pdf at all"), await upload(client, b"%PDF-1.7 truncated garbage")

    invalid, damaged = asyncio.run(scenario())
    assert invalid.status_code == 400
    assert damaged.status_code == 400
    temp_dir = get_settings().data_dir / "tmp"
    assert not list(temp_dir.glob("upload-*.pdf"))
