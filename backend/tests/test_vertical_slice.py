import os
import tempfile
from io import BytesIO

import fitz

os.environ["ADAM_DATA_DIR"] = tempfile.mkdtemp(prefix="adam-tests-")
os.environ.pop("ADAM_OPENCODE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from adam_server.main import app  # noqa: E402


client = TestClient(app)


def sample_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Attention lets a model weigh relevant context.")
    value = document.tobytes()
    document.close()
    return value


PDF_BYTES = sample_pdf()


def test_upload_extract_reopen_and_missing_key() -> None:
    response = client.post("/api/documents", files={"file": ("paper.pdf", BytesIO(PDF_BYTES), "application/pdf")})
    assert response.status_code == 201, (response.text, [route.path for route in app.routes])
    uploaded = response.json()
    assert uploaded["page_count"] == 1

    repeated = client.post("/api/documents", files={"file": ("copy.pdf", BytesIO(PDF_BYTES), "application/pdf")})
    assert repeated.status_code == 201
    assert repeated.json()["id"] == uploaded["id"]

    page = client.get(f"/api/documents/{uploaded['id']}/pages/1/text")
    assert page.status_code == 200
    assert "weigh relevant context" in page.json()["text"]

    pdf = client.get(f"/api/documents/{uploaded['id']}/file")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"

    annotation_id = "11111111-1111-4111-8111-111111111111"
    highlight = client.post(
        f"/api/documents/{uploaded['id']}/annotations",
        json={"id": annotation_id, "page": 1, "text": "Attention", "color": "#f8e58c", "rects": [{"left": 0.1, "top": 0.1, "width": 0.2, "height": 0.03}]},
    )
    assert highlight.status_code == 201
    saved = client.get(f"/api/documents/{uploaded['id']}/annotations")
    assert saved.status_code == 200
    assert saved.json()[0]["text"] == "Attention"
    deleted = client.delete(f"/api/documents/{uploaded['id']}/annotations/{annotation_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/documents/{uploaded['id']}/annotations").json() == []

    chat = client.post(
        "/api/chat/stream",
        json={
            "document_id": uploaded["id"],
            "question": "Why is this useful?",
            "selected_text": "Attention lets a model weigh relevant context.",
            "page": 1,
        },
    )
    assert chat.status_code == 503
    assert "OpenCode API key secret" in chat.json()["detail"]
