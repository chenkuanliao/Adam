import os
import tempfile
from io import BytesIO

import fitz

os.environ["ADAM_DATA_DIR"] = tempfile.mkdtemp(prefix="adam-tests-")
os.environ.pop("ADAM_OPENCODE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from adam_server.main import app  # noqa: E402
from adam_server import main as main_module  # noqa: E402


client = TestClient(app)


def sample_pdf(text: str = "Attention lets a model weigh relevant context.") -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
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

    created_chat = client.post(f"/api/documents/{uploaded['id']}/conversations", json={})
    assert created_chat.status_code == 201
    conversation = created_chat.json()
    assert conversation["model_id"] == "gemini-3.8-flash"
    assert client.get(f"/api/documents/{uploaded['id']}/conversations").json()[0]["id"] == conversation["id"]

    chat = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={
            "question": "Why is this useful?",
            "selected_text": "Attention lets a model weigh relevant context.",
            "page": 1,
        },
    )
    assert chat.status_code == 503
    assert "No API key is configured" in chat.json()["detail"]

    renamed = client.patch(f"/api/conversations/{conversation['id']}", json={"title": "Attention questions"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Attention questions"
    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204


def test_settings_are_persisted_without_exposing_the_key() -> None:
    initial = client.get("/api/settings")
    assert initial.status_code == 200
    assert initial.json()["model"] == "gemini-3.8-flash"
    assert initial.json()["provider"] == "zen"
    assert "You are Adam" in initial.json()["system_prompt"]
    assert "`$$...$$`" in initial.json()["system_prompt"]
    assert "api_key" not in initial.json()

    saved = client.put("/api/settings", json={"provider": "openai", "model": "gpt-5.6-luna", "api_keys": {"openai": "local-test-secret"}, "favorites": {"openai": ["gpt-5.6-luna"]}, "system_prompt": "You are Adam. Use only the supplied context."})
    assert saved.status_code == 200
    assert saved.json()["provider"] == "openai"
    assert saved.json()["selected_models"]["openai"] == "gpt-5.6-luna"
    assert saved.json()["providers"]["openai"] is True
    assert saved.json()["favorites"]["openai"] == ["gpt-5.6-luna"]
    assert saved.json()["system_prompt"] == "You are Adam. Use only the supplied context."
    assert "local-test-secret" not in saved.text

    unstarred = client.post("/api/settings/favorite", json={"provider": "openai", "model": "gpt-5.6-luna", "starred": False})
    assert unstarred.status_code == 200
    assert unstarred.json()["favorites"].get("openai", []) == []
    starred = client.post("/api/settings/favorite", json={"provider": "openai", "model": "gpt-5.6-luna", "starred": True})
    assert starred.status_code == 200
    assert starred.json()["favorites"]["openai"] == ["gpt-5.6-luna"]

    cleared = client.put("/api/settings", json={"provider": "zen", "model": "gemini-3.8-flash", "api_keys": {"openai": None}})
    assert cleared.status_code == 200
    assert cleared.json()["providers"]["openai"] is False
    assert cleared.json()["system_prompt"] == "You are Adam. Use only the supplied context."

    key_only = client.post("/api/settings/key", json={"provider": "anthropic", "api_key": "anthropic-test-secret"})
    assert key_only.status_code == 200
    assert key_only.json()["providers"]["anthropic"] is True
    assert "anthropic" not in key_only.json()["selected_models"]


def test_paper_context_and_history_are_owned_by_the_backend(monkeypatch) -> None:
    uploaded = client.post("/api/documents", files={"file": ("paper.pdf", BytesIO(PDF_BYTES), "application/pdf")}).json()
    conversation = client.post(f"/api/documents/{uploaded['id']}/conversations", json={}).json()
    captured: list[tuple[str, int]] = []

    client.put("/api/settings", json={"provider": "google", "model": "gemini-test", "api_keys": {}})
    conversation = client.post(f"/api/conversations/{conversation['id']}/sync-defaults").json()
    assert conversation["provider"] == "google"
    assert conversation["model_id"] == "gemini-test"

    monkeypatch.setattr(type(main_module.settings), "provider_api_key", lambda _self, _provider: "test-key")

    async def fake_stream(self, question, selected_text, images, page, history):
        captured.append((self.system_prompt, len(history)))
        yield "The paper explains relevant context."

    monkeypatch.setattr(main_module.GoogleProvider, "stream_answer", fake_stream)
    first = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": "What does the paper say?"})
    assert first.status_code == 200
    assert "weigh relevant context" in captured[0][0]
    assert captured[0][1] == 0

    second = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": "Explain that further."})
    assert second.status_code == 200
    assert captured[1][1] == 1
    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert saved["title"] == "What does the paper say?"
    assert [message["role"] for message in saved["messages"]] == ["user", "assistant", "user", "assistant"]
    client.put("/api/settings", json={"provider": "zen", "model": "gemini-other", "api_keys": {}})
    pinned = client.post(f"/api/conversations/{conversation['id']}/sync-defaults").json()
    assert pinned["provider"] == "google"
    assert pinned["model_id"] == "gemini-test"


def test_delete_paper_removes_file_and_related_records() -> None:
    pdf_bytes = sample_pdf("This paper exists only for deletion testing.")
    uploaded = client.post("/api/documents", files={"file": ("delete-me.pdf", BytesIO(pdf_bytes), "application/pdf")}).json()
    conversation = client.post(f"/api/documents/{uploaded['id']}/conversations", json={}).json()
    annotation = client.post(f"/api/documents/{uploaded['id']}/annotations", json={"id": "22222222-2222-4222-8222-222222222222", "page": 1, "text": "deletion", "color": "#f8e58c", "rects": [{"left": .1, "top": .1, "width": .2, "height": .03}]})
    assert annotation.status_code == 201
    assert client.get(f"/api/documents/{uploaded['id']}/file").status_code == 200

    deleted = client.delete(f"/api/documents/{uploaded['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/documents/{uploaded['id']}").status_code == 404
    assert client.get(f"/api/documents/{uploaded['id']}/file").status_code == 404
    assert client.get(f"/api/conversations/{conversation['id']}").status_code == 404
    assert all(item["id"] != uploaded["id"] for item in client.get("/api/documents").json())
