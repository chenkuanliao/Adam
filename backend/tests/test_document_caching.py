import asyncio
import hashlib
import os
import tempfile
from io import BytesIO

import fitz
import pytest

os.environ.setdefault("ADAM_DATA_DIR", tempfile.mkdtemp(prefix="adam-tests-"))
os.environ.pop("ADAM_OPENCODE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from adam_server import main as main_module  # noqa: E402
from adam_server.main import app  # noqa: E402
from adam_server.services import thumbnails  # noqa: E402


client = TestClient(app)
DATA_DIR = main_module.settings.data_dir


@pytest.fixture(scope="module", autouse=True)
def stop_thumbnail_workers():
    yield
    thumbnails.shutdown_thumbnail_workers()


def synthetic_pdf(label: str, width: float = 600, height: float = 800) -> bytes:
    """A unique page with a black block in the top-left quarter and blank space elsewhere."""
    document = fitz.open()
    page = document.new_page(width=width, height=height)
    page.draw_rect(fitz.Rect(0, 0, width / 2, min(height, width) / 2), color=(0, 0, 0), fill=(0, 0, 0))
    page.insert_text((72, height - 72), f"Synthetic caching fixture {label}")
    value = document.tobytes()
    document.close()
    return value


def upload(label: str, **size: float) -> dict:
    content = synthetic_pdf(label, **size)
    response = client.post("/api/documents", files={"file": (f"{label}.pdf", BytesIO(content), "application/pdf")})
    assert response.status_code == 201, response.text
    return {**response.json(), "sha256": hashlib.sha256(content).hexdigest()}


def test_document_file_is_privately_cached_by_content_hash() -> None:
    paper = upload("file-cache")
    url = f"/api/documents/{paper['id']}/file"
    full = client.get(url)
    assert full.status_code == 200
    etag = full.headers["etag"]
    assert etag == f'"{paper["sha256"]}"'
    assert full.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert full.headers["accept-ranges"] == "bytes"

    for header in (etag, f"W/{etag}", f'"other", {etag}', "*"):
        cached = client.get(url, headers={"If-None-Match": header})
        assert cached.status_code == 304, header
        assert cached.content == b""
        assert cached.headers["etag"] == etag
        assert "immutable" in cached.headers["cache-control"]
    assert client.get(url, headers={"If-None-Match": '"stale"'}).status_code == 200

    partial = client.get(url, headers={"Range": "bytes=0-9"})
    assert partial.status_code == 206
    assert partial.content == full.content[:10]
    assert partial.headers["content-range"] == f"bytes 0-9/{len(full.content)}"
    assert partial.headers["etag"] == etag
    assert client.get(url, headers={"Range": "bytes=0-9", "If-Range": etag}).status_code == 206
    changed = client.get(url, headers={"Range": "bytes=0-9", "If-Range": '"older-copy"'})
    assert changed.status_code == 200
    assert changed.content == full.content
    assert client.get(url, headers={"Range": "bytes=0-9", "If-None-Match": etag}).status_code == 304

    # Renaming changes the filename but not the bytes, so the validator stays the same.
    renamed = client.patch(f"/api/documents/{paper['id']}", json={"name": "Renamed cache fixture"})
    assert renamed.status_code == 200
    after_rename = client.get(url)
    assert after_rename.headers["etag"] == etag
    assert "Renamed%20cache%20fixture.pdf" in after_rename.headers["content-disposition"]
    assert client.get("/api/documents/missing/file", headers={"If-None-Match": "*"}).status_code == 404


def test_thumbnail_renders_page_one_at_fixed_sizes_and_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    paper = upload("thumbnail")
    url = f"/api/documents/{paper['id']}/thumbnail"
    assert client.get(url, params={"width": 100}).status_code == 422

    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=604800"
    etag = response.headers["etag"]
    image = fitz.Pixmap(response.content)
    assert (image.width, image.height) == (270, 360)
    assert max(image.pixel(30, 30)) < 40  # inside the black block
    assert min(image.pixel(240, 200)) > 215  # blank page area

    cached_path = thumbnails.thumbnail_path(DATA_DIR, paper["sha256"], 270)
    assert cached_path.is_file()
    assert not [item for item in cached_path.parent.iterdir() if item.name.endswith(".tmp")]

    async def no_render(*_args):
        raise AssertionError("cached thumbnails must not be rendered again")

    monkeypatch.setattr(thumbnails, "_render", no_render)
    again = client.get(url, params={"width": 270})
    assert again.status_code == 200
    assert again.content == response.content
    assert client.get(url, headers={"If-None-Match": etag}).status_code == 304
    monkeypatch.undo()

    retina = client.get(url, params={"width": 540})
    assert retina.status_code == 200
    assert retina.headers["etag"] != etag
    assert fitz.Pixmap(retina.content).width == 540

    assert client.delete(f"/api/documents/{paper['id']}").status_code == 204
    assert not thumbnails.thumbnail_dir(DATA_DIR, paper["sha256"]).exists()
    assert client.get(url).status_code == 404


def test_tall_pages_are_cropped_to_a_bounded_aspect_ratio() -> None:
    paper = upload("tall", width=300, height=3000)
    response = client.get(f"/api/documents/{paper['id']}/thumbnail")
    assert response.status_code == 200
    image = fitz.Pixmap(response.content)
    assert (image.width, image.height) == (270, 540)


def test_damaged_pdf_reports_an_unrenderable_preview() -> None:
    paper = upload("damaged")
    with main_module.SessionLocal() as db:
        source = main_module.document_file_path(db.get(main_module.Document, paper["id"]).storage_path, main_module.settings)
    source.write_bytes(b"%PDF-1.7\nnot really a pdf")
    response = client.get(f"/api/documents/{paper['id']}/thumbnail")
    assert response.status_code == 422
    assert not thumbnails.thumbnail_path(DATA_DIR, paper["sha256"], 270).exists()


def test_concurrent_requests_share_one_render_and_queue_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    renders: list[int] = []
    release = asyncio.Event()

    async def fake_render(_source, destination, width):
        renders.append(width)
        await release.wait()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"jpeg")

    monkeypatch.setattr(thumbnails, "_render", fake_render)
    monkeypatch.setattr(thumbnails, "MAX_PENDING_RENDERS", 1)

    async def scenario() -> None:
        waiters = [asyncio.create_task(thumbnails.ensure_thumbnail(tmp_path, "ab" * 32, tmp_path / "paper.pdf", 270)) for _ in range(3)]
        await asyncio.sleep(0)
        with pytest.raises(thumbnails.ThumbnailBusy):
            await thumbnails.ensure_thumbnail(tmp_path, "ab" * 32, tmp_path / "paper.pdf", 540)
        # A cancelled client must not cancel the render the others are waiting on.
        waiters[0].cancel()
        await asyncio.sleep(0)
        release.set()
        paths = await asyncio.gather(*waiters[1:])
        assert paths == [thumbnails.thumbnail_path(tmp_path, "ab" * 32, 270)] * 2
        assert renders == [270]
        assert not thumbnails._inflight

    asyncio.run(scenario())
