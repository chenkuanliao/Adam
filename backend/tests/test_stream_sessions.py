import asyncio
import gc
import json
import os
import tempfile
from io import BytesIO

import fitz
import httpx

os.environ["ADAM_DATA_DIR"] = tempfile.mkdtemp(prefix="adam-tests-")
os.environ.pop("ADAM_OPENCODE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from adam_server import main as main_module  # noqa: E402
from adam_server.database import engine  # noqa: E402


client = TestClient(main_module.app)


def upload_paper(text: str) -> dict:
    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    pdf = document.tobytes()
    document.close()
    response = client.post("/api/documents", files={"file": ("sessions.pdf", BytesIO(pdf), "application/pdf")})
    assert response.status_code == 201
    return response.json()


def sse_events(body: str) -> list[dict]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


class FakeProvider:
    def __init__(self, on_stream=None) -> None:
        self.on_stream = on_stream

    async def stream_answer(self, question, selected_text, images, page, history):
        if self.on_stream:
            await self.on_stream()
        yield "An answer."


def use_fake_models(monkeypatch, on_stream=None, on_title=None) -> None:
    monkeypatch.setattr(type(main_module.settings), "provider_api_key", lambda _self, _provider: "test-key")
    monkeypatch.setattr(main_module, "make_provider", lambda *_args, **_kwargs: FakeProvider(on_stream))

    async def fake_title(_transcript, _fallback):
        if on_title:
            await on_title()
        return "Generated Title"

    monkeypatch.setattr(main_module, "generate_configured_title", fake_title)


def test_sequential_streams_return_every_connection_without_gc(monkeypatch) -> None:
    observed: list[int] = []

    async def record_checked_out() -> None:
        observed.append(engine.pool.checkedout())

    use_fake_models(monkeypatch, on_stream=record_checked_out, on_title=record_checked_out)
    paper = upload_paper("Sequential streams release their database connections.")
    conversation = client.post(f"/api/documents/{paper['id']}/conversations", json={}).json()

    # Leaked sessions were only reclaimed by the cycle collector, so keep it off.
    gc.collect()
    gc.disable()
    try:
        for index in range(20):
            response = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": f"Question {index}?"})
            assert response.status_code == 200
            # The first turn may append a late `title` event after `completed`.
            assert [event["type"] for event in sse_events(response.text)][-2:] in (["delta", "completed"], ["completed", "title"])
            assert engine.pool.checkedout() == 0
            quick = client.post(f"/api/conversations/{conversation['id']}/quick-ask/stream", json={"question": "What is this?", "selected_text": "Sequential streams"})
            assert sse_events(quick.text)[-1]["type"] == "completed"
            assert engine.pool.checkedout() == 0
    finally:
        gc.enable()

    # No session is held while the provider or title model is awaited.
    assert observed and set(observed) == {0}
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assert detail["title"] == "Generated Title"
    assert len(detail["messages"]) == 40
    assert detail["messages"][-1]["role"] == "assistant"


def test_concurrent_streams_beyond_pool_size_complete_while_health_responds(monkeypatch) -> None:
    # More streams than the default pool (5 + 10 overflow) can hand out at once.
    stream_count = 20
    paper = upload_paper("Concurrent streams must not exhaust the connection pool.")
    conversations = [client.post(f"/api/documents/{paper['id']}/conversations", json={}).json() for _ in range(stream_count)]

    async def run() -> None:
        all_streaming = asyncio.Event()
        release = asyncio.Event()
        active = 0

        async def hold_stream() -> None:
            nonlocal active
            active += 1
            if active == stream_count:
                all_streaming.set()
            await release.wait()

        use_fake_models(monkeypatch, on_stream=hold_stream)
        transport = httpx.ASGITransport(app=main_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            streams = [asyncio.create_task(http.post(f"/api/conversations/{item['id']}/messages/stream", json={"question": "What is attention?"})) for item in conversations]
            await asyncio.wait_for(all_streaming.wait(), timeout=30)
            # Every stream is mid-answer; none of them owns a pooled connection.
            assert engine.pool.checkedout() == 0
            health = await http.get("/health")
            assert health.status_code == 200
            release.set()
            responses = await asyncio.wait_for(asyncio.gather(*streams), timeout=30)
        for response in responses:
            assert response.status_code == 200
            # Each new chat completes first, then delivers its generated title.
            assert [event["type"] for event in sse_events(response.text)][-2:] == ["completed", "title"]

    asyncio.run(run())
    assert engine.pool.checkedout() == 0
    for item in conversations:
        messages = client.get(f"/api/conversations/{item['id']}").json()["messages"]
        assert [message["role"] for message in messages] == ["user", "assistant"]


def test_regenerate_title_holds_no_session_while_awaiting_the_model(monkeypatch) -> None:
    observed: list[int] = []

    async def record_checked_out() -> None:
        observed.append(engine.pool.checkedout())

    use_fake_models(monkeypatch, on_title=record_checked_out)
    paper = upload_paper("Title regeneration uses short sessions.")
    conversation = client.post(f"/api/documents/{paper['id']}/conversations", json={}).json()
    assert client.post(f"/api/conversations/{conversation['id']}/regenerate-title").status_code == 409
    client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": "Why short sessions?"})
    client.patch(f"/api/conversations/{conversation['id']}", json={"title": "Manual"})

    regenerated = client.post(f"/api/conversations/{conversation['id']}/regenerate-title")
    assert regenerated.status_code == 200
    assert regenerated.json()["title"] == "Generated Title"
    assert regenerated.json()["id"] == conversation["id"]
    assert set(observed) == {0}
    assert engine.pool.checkedout() == 0
    assert client.post("/api/conversations/missing/regenerate-title").status_code == 404
