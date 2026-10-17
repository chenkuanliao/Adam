import asyncio
import json
import os
import tempfile
from io import BytesIO

import fitz

os.environ["ADAM_DATA_DIR"] = tempfile.mkdtemp(prefix="adam-tests-")
os.environ.pop("ADAM_OPENCODE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from adam_server.main import app  # noqa: E402
from adam_server.database import engine  # noqa: E402
from adam_server import main as main_module  # noqa: E402
from adam_server.services import web_search as web_search_module  # noqa: E402
from adam_server.services.web_search import plan_search  # noqa: E402


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

    # A library created in Docker keeps /data paths when opened locally.
    with main_module.SessionLocal() as db:
        document = db.get(main_module.Document, uploaded["id"])
        document.storage_path = f"/data/{document.storage_path}"
        db.commit()
    assert client.get(f"/api/documents/{uploaded['id']}/file").status_code == 200

    renamed_document = client.patch(f"/api/documents/{uploaded['id']}", json={"name": "Attention notes"})
    assert renamed_document.status_code == 200
    assert renamed_document.json()["original_name"] == "Attention notes.pdf"
    renamed_pdf = client.get(f"/api/documents/{uploaded['id']}/file")
    assert "Attention%20notes.pdf" in renamed_pdf.headers["content-disposition"]
    assert client.patch(f"/api/documents/{uploaded['id']}", json={"name": "../outside.pdf"}).status_code == 422

    empty_note = client.get(f"/api/documents/{uploaded['id']}/paper-note")
    assert empty_note.status_code == 200
    assert empty_note.json()["revision"] == 0
    saved_note = client.put(f"/api/documents/{uploaded['id']}/paper-note", json={
        "content_html": "<h1>Main finding</h1><ul><li>Attention helps</li></ul>",
        "plain_text": "Main finding\nAttention helps",
        "revision": 0,
    })
    assert saved_note.status_code == 200
    assert saved_note.json()["revision"] == 1
    stale_note = client.put(f"/api/documents/{uploaded['id']}/paper-note", json={
        "content_html": "stale", "plain_text": "stale", "revision": 0,
    })
    assert stale_note.status_code == 409
    assert client.get(f"/api/documents/{uploaded['id']}/paper-note").json()["content_html"].startswith("<h1>Main finding")

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
    assert conversation["model_id"] == "gpt-5.6-terra"
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
    assert initial.json()["model"] == "gpt-5.6-terra"
    assert initial.json()["provider"] == "zen"
    assert "You are Adam" in initial.json()["system_prompt"]
    assert "`$$...$$`" in initial.json()["system_prompt"]
    assert "Adam Quick Ask" in initial.json()["quick_ask_prompt"]
    assert "api_key" not in initial.json()

    saved = client.put("/api/settings", json={"provider": "openai", "model": "gpt-5.6-luna", "api_keys": {"openai": "local-test-secret"}, "favorites": {"openai": ["gpt-5.6-luna"]}, "system_prompt": "You are Adam. Use only the supplied context.", "quick_ask_prompt": "Explain only the selected item."})
    assert saved.status_code == 200
    assert saved.json()["provider"] == "openai"
    assert saved.json()["selected_models"]["openai"] == "gpt-5.6-luna"
    assert saved.json()["providers"]["openai"] is True
    assert saved.json()["favorites"]["openai"] == ["gpt-5.6-luna"]
    assert saved.json()["system_prompt"] == "You are Adam. Use only the supplied context."
    assert saved.json()["quick_ask_prompt"] == "Explain only the selected item."
    assert "local-test-secret" not in saved.text

    unstarred = client.post("/api/settings/favorite", json={"provider": "openai", "model": "gpt-5.6-luna", "starred": False})
    assert unstarred.status_code == 200
    assert unstarred.json()["favorites"].get("openai", []) == []
    starred = client.post("/api/settings/favorite", json={"provider": "openai", "model": "gpt-5.6-luna", "starred": True})
    assert starred.status_code == 200
    assert starred.json()["favorites"]["openai"] == ["gpt-5.6-luna"]

    cleared = client.put("/api/settings", json={"provider": "zen", "model": "gpt-5.6-terra", "api_keys": {"openai": None}})
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
    messages_before_quick_ask = len(client.get(f"/api/conversations/{conversation['id']}").json()["messages"])
    quick = client.post(f"/api/conversations/{conversation['id']}/quick-ask/stream", json={"question": "What does this mean?", "selected_text": "Attention lets a model weigh relevant context.", "page": 1})
    assert quick.status_code == 200
    assert captured[2][1] == 0
    assert "weigh relevant context" not in captured[2][0]
    assert captured[2][0] == "Explain only the selected item."
    assert len(client.get(f"/api/conversations/{conversation['id']}").json()["messages"]) == messages_before_quick_ask
    follow_up = client.post(f"/api/conversations/{conversation['id']}/quick-ask/stream", json={"question": "Can you simplify that?", "selected_text": "Attention lets a model weigh relevant context.", "page": 1, "history": [{"question": "What does this mean?", "answer": "It explains attention.", "page": 1}]})
    assert follow_up.status_code == 200
    assert captured[3][1] == 1
    context_only_in_history = client.post(f"/api/conversations/{conversation['id']}/quick-ask/stream", json={"question": "And why is that useful?", "page": 1, "history": [{"question": "What does this mean?", "answer": "It explains attention.", "selected_text": "Attention lets a model weigh relevant context.", "page": 1}]})
    assert context_only_in_history.status_code == 200
    assert captured[4][1] == 1
    imported = client.post(f"/api/conversations/{conversation['id']}/quick-ask/import", json={"selected_text": "Attention lets a model weigh relevant context.", "page": 1, "turns": [{"question": "What does this mean?", "answer": "It means relevant inputs receive more weight."}, {"question": "Can you simplify that?", "answer": "It focuses on useful inputs."}]})
    assert imported.status_code == 201
    saved_quick_chat = imported.json()
    assert saved_quick_chat["id"] != conversation["id"]
    assert saved_quick_chat["context_builder_version"] == "quick-ask-v1"
    assert client.get(f"/api/documents/{uploaded['id']}/annotations").json() == []
    saved_after_import = client.get(f"/api/conversations/{saved_quick_chat['id']}").json()
    assert len(saved_after_import["messages"]) == 4
    assert "quick_ask_saved" in saved_after_import["messages"][0]["context_json"]
    assert "weigh relevant context" not in saved_after_import["messages"][2]["context_json"]
    assert len(client.get(f"/api/conversations/{conversation['id']}").json()["messages"]) == messages_before_quick_ask
    continued = client.post(f"/api/conversations/{saved_quick_chat['id']}/messages/stream", json={"question": "One more follow-up."})
    assert continued.status_code == 200
    assert captured[5][0] == "Explain only the selected item."
    assert captured[5][1] == 2
    saved = client.get(f"/api/conversations/{conversation['id']}").json()
    assert saved["title"] == "What does the paper say?"
    assert [message["role"] for message in saved["messages"]] == ["user", "assistant", "user", "assistant"]
    client.put("/api/settings", json={"provider": "zen", "model": "gemini-other", "api_keys": {}})
    pinned = client.post(f"/api/conversations/{conversation['id']}/sync-defaults").json()
    assert pinned["provider"] == "google"
    assert pinned["model_id"] == "gemini-test"


def test_web_search_is_opt_in_saved_and_reused_only_in_its_chat(monkeypatch) -> None:
    first_paper = client.post("/api/documents", files={"file": ("web-one.pdf", BytesIO(sample_pdf("First paper on transformers.")), "application/pdf")}).json()
    second_paper = client.post("/api/documents", files={"file": ("web-two.pdf", BytesIO(sample_pdf("Second paper on kernels.")), "application/pdf")}).json()
    client.put("/api/settings", json={"provider": "google", "model": "gemini-test", "api_keys": {}})
    first_chat = client.post(f"/api/documents/{first_paper['id']}/conversations", json={}).json()
    second_chat = client.post(f"/api/documents/{second_paper['id']}/conversations", json={}).json()
    monkeypatch.setattr(type(main_module.settings), "provider_api_key", lambda _self, _provider: "test-key")
    prompts = []
    searches = []

    async def fake_stream(self, question, selected_text, images, page, history):
        prompts.append(self.system_prompt)
        yield "An external comparison [W1]." if "# Web reference" in self.system_prompt else "Only the paper is available."

    async def fake_plan(_provider, question, _selection, paper_title):
        assert paper_title == "web-one"
        return "transformer research comparison"

    async def fake_search(_base_url, query):
        searches.append(query)
        return [{"title": "Research source", "url": "https://example.org/research", "snippet": "External comparison evidence.", "retrieved_at": "2026-09-23T00:00:00+00:00"}]

    monkeypatch.setattr(main_module.GoogleProvider, "stream_answer", fake_stream)
    monkeypatch.setattr(main_module, "plan_search", fake_plan)
    monkeypatch.setattr(main_module, "search_web", fake_search)

    plain = client.post(f"/api/conversations/{first_chat['id']}/messages/stream", json={"question": "What does this paper say?"})
    assert plain.status_code == 200
    assert not searches
    assert "# Web reference" not in prompts[-1]

    searched = client.post(f"/api/conversations/{first_chat['id']}/messages/stream", json={"question": "How does it compare externally?", "allow_web_search": True})
    assert searched.status_code == 200
    assert '"type": "web_sources"' in searched.text
    assert searches == ["transformer research comparison"]
    saved = client.get(f"/api/conversations/{first_chat['id']}").json()["messages"][-1]
    assert json.loads(saved["context_json"])["web"]["searched"] is True
    assert "[W1]" in saved["content"]

    follow_up = client.post(f"/api/conversations/{first_chat['id']}/messages/stream", json={"question": "What else follows from that?"})
    assert follow_up.status_code == 200
    assert len(searches) == 1
    assert "https://example.org/research" in prompts[-1]
    reused = client.get(f"/api/conversations/{first_chat['id']}").json()["messages"][-1]
    assert json.loads(reused["context_json"])["web"]["reused"] is True
    assert json.loads(reused["context_json"])["web"]["searched"] is False

    other = client.post(f"/api/conversations/{second_chat['id']}/messages/stream", json={"question": "What is in this paper?"})
    assert other.status_code == 200
    assert "# Web reference" not in prompts[-1]


def test_quick_ask_search_sources_survive_followup_and_import(monkeypatch) -> None:
    paper = client.post("/api/documents", files={"file": ("quick-search.pdf", BytesIO(sample_pdf()), "application/pdf")}).json()
    client.put("/api/settings", json={"provider": "google", "model": "gemini-test", "api_keys": {}})
    conversation = client.post(f"/api/documents/{paper['id']}/conversations", json={}).json()
    monkeypatch.setattr(type(main_module.settings), "provider_api_key", lambda _self, _provider: "test-key")
    prompts = []
    searches = []
    source = {"title": "Related work", "url": "https://example.org/related", "snippet": "External evidence.", "retrieved_at": "2026-09-23T00:00:00+00:00"}

    async def fake_stream(self, question, selected_text, images, page, history):
        prompts.append(self.system_prompt)
        yield "Answer [W1]." if "# Web reference" in self.system_prompt else "Selection answer."

    async def fake_plan(_provider, _question, _selection, _paper_title):
        return "related work"

    async def fake_search(_base_url, query):
        searches.append(query)
        return [source]

    monkeypatch.setattr(main_module.GoogleProvider, "stream_answer", fake_stream)
    monkeypatch.setattr(main_module, "plan_search", fake_plan)
    monkeypatch.setattr(main_module, "search_web", fake_search)

    url = f"/api/conversations/{conversation['id']}/quick-ask/stream"
    first = client.post(url, json={"question": "Explain this", "selected_text": "Selected excerpt"})
    assert first.status_code == 200
    assert not searches
    assert "# Web reference" not in prompts[-1]

    searched = client.post(url, json={"question": "Find related work", "selected_text": "Selected excerpt", "allow_web_search": True})
    assert searched.status_code == 200
    assert searches == ["related work"]
    events = [json.loads(line[6:]) for line in searched.text.splitlines() if line.startswith("data: ")]
    web = next(event["web"] for event in events if event["type"] == "completed")
    assert web["searched"] is True
    assert web["sources"] == [source]

    turns = [{"question": "Find related work", "answer": "Answer [W1].", "selected_text": "Selected excerpt", "web": web}]
    followup = client.post(url, json={"question": "Explain its connection", "history": turns})
    assert followup.status_code == 200
    assert searches == ["related work"]
    assert source["url"] in prompts[-1]
    followup_events = [json.loads(line[6:]) for line in followup.text.splitlines() if line.startswith("data: ")]
    reused = next(event["web"] for event in followup_events if event["type"] == "completed")
    assert reused["reused"] is True
    assert reused["searched"] is False

    imported = client.post(f"/api/conversations/{conversation['id']}/quick-ask/import", json={
        "selected_text": "Selected excerpt", "turns": turns,
    })
    assert imported.status_code == 201
    saved_messages = client.get(f"/api/conversations/{imported.json()['id']}").json()["messages"]
    assert json.loads(saved_messages[-1]["context_json"])["web"]["sources"] == [source]


def test_search_planner_uses_paper_title_even_if_model_says_skip() -> None:
    class FakeProvider:
        def __init__(self, api_key="key", model="test", base_url="https://example.org", system_prompt=""):
            self.api_key = api_key
            self.model = model
            self.base_url = base_url
            self.system_prompt = system_prompt

        async def stream_answer(self, question, selected_text, images, page, history):
            assert "Paper title: Attention Is All You Need" in question
            yield '{"search": false, "query": "important papers citing the Transformer paper"}'

    query = asyncio.run(plan_search(FakeProvider(), "What important papers cited this paper?", "", "Attention Is All You Need"))
    assert query.startswith('"Attention Is All You Need"')
    assert "important papers citing" in query


def test_web_search_reads_all_five_results_with_larger_excerpts(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"title": f"Source {index}", "url": f"https://example.org/{index}", "content": "s" * 1000} for index in range(5)]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def get(self, *_args, **_kwargs):
            return FakeResponse()

    fetched = []

    async def fake_read_page(_client, url):
        fetched.append(url)
        return "p" * web_search_module.MAX_PAGE_TEXT_CHARS

    monkeypatch.setattr(web_search_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(web_search_module, "read_page", fake_read_page)
    sources = asyncio.run(web_search_module.search_web("http://search:8080", "example"))
    assert len(fetched) == len(sources) == 5
    assert all(len(source["snippet"]) > 3000 for source in sources)
    assert all(len(source["snippet"]) <= web_search_module.MAX_SOURCE_TEXT_CHARS for source in sources)


def test_citing_papers_question_uses_citation_index(monkeypatch) -> None:
    uploaded = client.post("/api/documents", files={"file": ("Attention is all you need.pdf", BytesIO(sample_pdf()), "application/pdf")}).json()
    client.put("/api/settings", json={"provider": "google", "model": "gemini-test", "api_keys": {}})
    conversation = client.post(f"/api/documents/{uploaded['id']}/conversations", json={}).json()
    monkeypatch.setattr(type(main_module.settings), "provider_api_key", lambda _self, _provider: "test-key")
    lookups = []

    async def fake_stream(self, question, selected_text, images, page, history):
        yield "A cited paper [W1]."

    async def fake_plan(_provider, _question, _selection, _title):
        return "papers citing Attention Is All You Need"

    async def fake_citations(title):
        lookups.append(title)
        return [{"title": "Later paper", "url": "https://openalex.org/W123", "snippet": "OpenAlex records the citation.", "retrieved_at": "2026-09-23T00:00:00+00:00"}]

    async def unexpected_web_search(_base_url, _query):
        raise AssertionError("Generic web search should be the fallback only")

    monkeypatch.setattr(main_module.GoogleProvider, "stream_answer", fake_stream)
    monkeypatch.setattr(main_module, "plan_search", fake_plan)
    monkeypatch.setattr(main_module, "search_citing_papers", fake_citations)
    monkeypatch.setattr(main_module, "search_web", unexpected_web_search)
    response = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": "What important papers cited this paper?", "allow_web_search": True})
    assert response.status_code == 200
    assert lookups == ["Attention is all you need"]
    assert '"searched": true' in response.text
    assert "https://openalex.org/W123" in response.text


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


def test_ai_notes_merge_overlapping_context_and_follow_chat_lifecycle() -> None:
    uploaded = client.post("/api/documents", files={"file": ("linked.pdf", BytesIO(sample_pdf("Linked AI note test.")), "application/pdf")}).json()
    first_chat = client.post(f"/api/documents/{uploaded['id']}/conversations", json={}).json()
    second_chat = client.post(f"/api/documents/{uploaded['id']}/conversations", json={}).json()
    note_id = "33333333-3333-4333-8333-333333333333"
    note = client.post(f"/api/documents/{uploaded['id']}/annotations", json={"id": note_id, "page": 1, "text": "Linked AI note", "color": "#d6a629", "rects": [{"left": .1, "top": .1, "width": .3, "height": .03}], "note_text": "Keep this regular note."})
    assert note.status_code == 201
    first = client.post(f"/api/documents/{uploaded['id']}/ai-notes", json={"page": 1, "text": "Linked AI", "rects": [{"left": .1, "top": .1, "width": .2, "height": .03}], "link": {"conversation_id": first_chat["id"], "question": "What is this?"}})
    assert first.status_code == 200
    overlapping = client.post(f"/api/documents/{uploaded['id']}/ai-notes", json={"page": 1, "text": "AI note", "rects": [{"left": .25, "top": .1, "width": .2, "height": .03}], "link": {"conversation_id": second_chat["id"], "question": "Why does it matter?"}})
    assert overlapping.status_code == 200
    assert overlapping.json()["id"] == first.json()["id"]
    assert len(overlapping.json()["ai_links"]) == 2
    annotations = client.get(f"/api/documents/{uploaded['id']}/annotations").json()
    assert len(annotations) == 2
    assert next(item for item in annotations if item["id"] == note_id)["note_text"] == "Keep this regular note."
    unlinked = client.post(f"/api/documents/{uploaded['id']}/ai-notes/unlink", json={"conversation_id": first_chat["id"], "question": "What is this?"})
    assert unlinked.status_code == 204
    assert len(next(item for item in client.get(f"/api/documents/{uploaded['id']}/annotations").json() if item["ai_links"])["ai_links"]) == 1
    assert client.delete(f"/api/conversations/{first_chat['id']}").status_code == 204
    assert len(next(item for item in client.get(f"/api/documents/{uploaded['id']}/annotations").json() if item["ai_links"])["ai_links"]) == 1
    assert client.delete(f"/api/conversations/{second_chat['id']}").status_code == 204
    remaining = client.get(f"/api/documents/{uploaded['id']}/annotations").json()
    assert len(remaining) == 1
    assert remaining[0]["id"] == note_id
    assert remaining[0]["note_text"] == "Keep this regular note."


def test_zen_auto_title_manual_rename_and_regeneration(monkeypatch) -> None:
    uploaded = client.post("/api/documents", files={"file": ("titles.pdf", BytesIO(sample_pdf("A unique title test paper.")), "application/pdf")}).json()
    client.put("/api/settings", json={"provider": "zen", "model": "gpt-5.6-luna", "api_keys": {}})
    monkeypatch.setattr(type(main_module.settings), "provider_api_key", lambda _self, _provider: "test-key")

    generated_from: list[str] = []
    async def fake_title(_key, transcript, _fallback, model):
        assert model == "gpt-6-luna"
        generated_from.append(transcript)
        return "Attention Mechanisms Explained" if len(generated_from) == 1 else "Attention Follow-up Analysis"

    async def fake_stream(self, question, selected_text, images, page, history):
        yield "A useful answer."

    monkeypatch.setattr(main_module, "generate_zen_title", fake_title)
    monkeypatch.setattr(main_module.OpenAIResponsesProvider, "stream_answer", fake_stream)
    conversation = client.post(f"/api/documents/{uploaded['id']}/conversations", json={}).json()
    response = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": "How does attention work?"})
    assert response.status_code == 200
    assert '"type": "completed"' in response.text
    assert "User: How does attention work?" in generated_from[0]
    assert "Assistant: A useful answer." in generated_from[0]
    assert client.get(f"/api/conversations/{conversation['id']}").json()["title"] == "Attention Mechanisms Explained"

    renamed = client.patch(f"/api/conversations/{conversation['id']}", json={"title": "My own title"})
    assert renamed.json()["title"] == "My own title"
    assert client.patch(f"/api/conversations/{conversation['id']}", json={"title": "   "}).status_code == 422

    regenerated = client.post(f"/api/conversations/{conversation['id']}/regenerate-title")
    assert regenerated.status_code == 200
    assert regenerated.json()["title"] == "Attention Follow-up Analysis"
    assert "User: How does attention work?" in generated_from[-1]
    assert "Assistant: A useful answer." in generated_from[-1]


def test_title_model_settings_are_independent_of_chat_provider(monkeypatch) -> None:
    uploaded = client.post("/api/documents", files={"file": ("title-provider.pdf", BytesIO(sample_pdf()), "application/pdf")}).json()
    client.put("/api/settings", json={"provider": "zen", "model": "gpt-5.6-terra", "api_keys": {}})
    monkeypatch.setattr(type(main_module.settings), "provider_api_key", lambda _self, _provider: "test-key")
    saved = client.put("/api/settings/title", json={"provider": "google", "model": "gemini-test"})
    assert saved.status_code == 200
    assert saved.json()["title_provider"] == "google"
    assert saved.json()["title_model"] == "gemini-test"
    assert saved.json()["provider"] == "zen"
    assert saved.json()["model"] == "gpt-5.6-terra"

    titles = []

    async def fake_google_stream(self, question, selected_text, images, page, history):
        titles.append((self.model, question))
        yield "Attention Research Chat" if len(titles) == 1 else "Updated Attention Chat"

    async def fake_zen_stream(self, question, selected_text, images, page, history):
        yield "The answer."

    monkeypatch.setattr(main_module.GoogleProvider, "stream_answer", fake_google_stream)
    monkeypatch.setattr(main_module.OpenAIResponsesProvider, "stream_answer", fake_zen_stream)
    conversation = client.post(f"/api/documents/{uploaded['id']}/conversations", json={}).json()
    assert conversation["provider"] == "zen"
    response = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": "How does attention work?"})
    assert response.status_code == 200
    assert '"title": "Attention Research Chat"' in response.text
    regenerated = client.post(f"/api/conversations/{conversation['id']}/regenerate-title")
    assert regenerated.status_code == 200
    assert regenerated.json()["title"] == "Updated Attention Chat"
    assert len(titles) == 2
    assert all(model == "gemini-test" for model, _ in titles)
    client.put("/api/settings/title", json={"provider": "zen", "model": "gpt-6-luna"})


def test_folder_organization_keeps_reader_data_and_moves_atomically() -> None:
    first = client.post('/api/folders', json={'name': '  Folder lifecycle  '})
    assert first.status_code == 201
    folder = first.json()
    assert folder['name'] == 'Folder lifecycle'
    assert client.post('/api/folders', json={'name': 'folder lifecycle'}).status_code == 409
    assert client.post('/api/folders', json={'name': '   '}).status_code == 422
    assert client.patch(f"/api/folders/{folder['id']}", json={'name': 'Reading group'}).status_code == 200
    pdf_bytes = sample_pdf('Folder organization preserves the paper.')
    paper = client.post('/api/documents', files={'file': ('folders.pdf', BytesIO(pdf_bytes), 'application/pdf')}).json()
    assert paper['folder_id'] is None
    note = client.put(f"/api/documents/{paper['id']}/paper-note", json={'content_html': '<p>Keep this note.</p>', 'plain_text': 'Keep this note.', 'revision': 0})
    assert note.status_code == 200
    move = {'document_ids': [paper['id']], 'folder_id': folder['id']}
    assert client.post('/api/document-moves', json={**move, 'document_ids': [paper['id'], 'missing']}).status_code == 404
    assert client.get(f"/api/documents/{paper['id']}").json()['folder_id'] is None
    assert client.post('/api/document-moves', json={**move, 'folder_id': 'missing'}).status_code == 404
    assert client.post('/api/document-moves', json=move).json()[0]['folder_id'] == folder['id']
    assert client.get(f"/api/documents/{paper['id']}").json()['folder_id'] == folder['id']
    assert client.post('/api/document-moves', json={**move, 'folder_id': None}).json()[0]['folder_id'] is None
    assert client.post('/api/document-moves', json=move).status_code == 200
    assert client.delete(f"/api/folders/{folder['id']}").status_code == 204
    assert client.get(f"/api/documents/{paper['id']}").json()['folder_id'] is None
    assert client.get(f"/api/documents/{paper['id']}/file").content == pdf_bytes
    assert 'Folder organization preserves' in client.get(f"/api/documents/{paper['id']}/pages/1/text").json()['text']
    assert client.get(f"/api/documents/{paper['id']}/paper-note").json()['plain_text'] == 'Keep this note.'
    assert folder['id'] not in [item['id'] for item in client.get('/api/folders').json()]
    client.delete(f"/api/documents/{paper['id']}")


def test_folder_migration_preserves_existing_papers_and_children(tmp_path) -> None:
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'ADAM_DATA_DIR': str(tmp_path)}
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', '0008_ai_note_links'], cwd=backend_root, env=env, check=True, capture_output=True)
    path = tmp_path / 'database' / 'adam.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO documents (id, sha256, original_name, byte_size, storage_path, page_count, status, created_at, updated_at) VALUES ('existing', 'hash', 'paper.pdf', 1, 'documents/paper.pdf', 1, 'ready', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO pages (id, document_id, page_number, width_pt, height_pt, rotation, native_text) VALUES ('page', 'existing', 1, 595, 842, 0, 'Keep extracted text')")
        db.execute("INSERT INTO paper_notes (document_id, content_html, plain_text, revision, created_at, updated_at) VALUES ('existing', '<p>Keep note</p>', 'Keep note', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO conversations (id, document_id, title, provider, model_id, system_prompt, context_builder_version, created_at, updated_at) VALUES ('chat', 'existing', 'Keep chat', 'zen', 'model', '', 'full-paper-v1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO messages (id, document_id, conversation_id, role, content, created_at) VALUES ('message', 'existing', 'chat', 'user', 'Keep message', CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO annotations (id, document_id, page_number, kind, selected_text, color, geometry_json, created_at) VALUES ('highlight', 'existing', 1, 'highlight', 'Keep highlight', '#f8e58c', '[]', CURRENT_TIMESTAMP)")
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', '0009_folders'], cwd=backend_root, env=env, check=True, capture_output=True)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO folders (id, name, created_at) VALUES ('old-folder', 'Existing folder', CURRENT_TIMESTAMP)")
        db.execute("UPDATE documents SET folder_id = 'old-folder' WHERE id = 'existing'")
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=backend_root, env=env, check=True, capture_output=True)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id, folder_id FROM documents').fetchall() == [('existing', 'old-folder')]
        for table in ['pages', 'paper_notes', 'conversations', 'messages', 'annotations']:
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 1
        assert db.execute('SELECT color, icon FROM folders WHERE id = ?', ('old-folder',)).fetchone() == ('#8c9d65', 'folder')
        assert 'discovery_json' in {column[1] for column in db.execute('PRAGMA table_info(documents)')}
        assert db.execute('SELECT version_num FROM alembic_version').fetchall() == [('0013_merge_discovery_folders',)]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_folder_colors_are_validated_and_persist_across_rename() -> None:
    created = client.post('/api/folders', json={'name': 'Palette lifecycle'})
    assert created.status_code == 201
    folder = created.json()
    assert folder['color'] == '#8c9d65'
    url = f"/api/folders/{folder['id']}"
    saved = client.patch(url, json={'name': folder['name'], 'color': '#A13BCD'})
    assert saved.status_code == 200
    assert saved.json()['color'] == '#a13bcd'
    renamed = client.patch(url, json={'name': 'Palette renamed'})
    assert renamed.json()['color'] == '#a13bcd'
    assert next(item for item in client.get('/api/folders').json() if item['id'] == folder['id'])['color'] == '#a13bcd'
    for invalid in ['red', '#fff', '#12345678', 'var(--color)']:
        assert client.patch(url, json={'name': 'Palette renamed', 'color': invalid}).status_code == 422
    assert client.delete(url).status_code == 204
    custom = client.post('/api/folders', json={'name': 'Custom palette', 'color': '#123456'})
    assert custom.status_code == 201
    assert custom.json()['color'] == '#123456'
    client.delete(f"/api/folders/{custom.json()['id']}")


def test_auto_title_uses_answer_and_preserves_chat_on_title_failure(monkeypatch) -> None:
    uploaded = client.post("/api/documents", files={"file": ("specific-titles.pdf", BytesIO(sample_pdf("Residual connections preserve information.")), "application/pdf")}).json()
    monkeypatch.setattr(type(main_module.settings), "provider_api_key", lambda _self, _provider: "test-key")

    async def fake_stream(self, question, selected_text, images, page, history):
        yield "Residual connections preserve information through Transformer blocks."

    monkeypatch.setattr(main_module.OpenAIResponsesProvider, "stream_answer", fake_stream)
    for mode in ("failure", "rename"):
        conversation = client.post(f"/api/documents/{uploaded['id']}/conversations", json={}).json()

        async def fake_title(transcript, fallback):
            assert "User: What does this do?" in transcript
            assert "Selected passage: x + F(x)" in transcript
            assert "Assistant: Residual connections" in transcript
            # The answer must be durable before requesting its title.
            with main_module.SessionLocal() as db:
                saved = db.get(main_module.Conversation, conversation["id"])
                assert saved.messages[-1].role == "assistant"
                if mode == "rename":
                    saved.title = "My residual connection notes"
                    db.commit()
            if mode == "failure":
                raise ValueError("Title service unavailable")
            return "Residual Connections in Transformer Blocks"

        monkeypatch.setattr(main_module, "generate_configured_title", fake_title)
        response = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={"question": "What does this do?", "selected_text": "x + F(x)"})
        assert '\"type\": \"completed\"' in response.text
        assert '\"type\": \"error\"' not in response.text
        detail = client.get(f"/api/conversations/{conversation['id']}").json()
        assert detail["messages"][-1]["content"].startswith("Residual connections")
        assert detail["title"] == ("What does this do?" if mode == "failure" else "My residual connection notes")


def test_slow_title_does_not_hold_answer_completion(monkeypatch) -> None:
    uploaded = client.post("/api/documents", files={"file": ("slow-titles.pdf", BytesIO(sample_pdf("Layer normalization stabilizes training.")), "application/pdf")}).json()
    monkeypatch.setattr(type(main_module.settings), "provider_api_key", lambda _self, _provider: "test-key")

    class FakeProvider:
        async def stream_answer(self, question, selected_text, images, page, history):
            yield f"Answer to {question}"

    monkeypatch.setattr(main_module, "make_provider", lambda *_args: FakeProvider())
    title_requests: list[str] = []
    release = {}
    outcome = {}

    async def slow_title(transcript, fallback):
        title_requests.append(transcript)
        await release[transcript.split("\n")[0]].wait()
        if outcome.get("raise"):
            raise RuntimeError("Title model crashed")
        return "Layer Normalization Explained"

    monkeypatch.setattr(main_module, "generate_configured_title", slow_title)

    def stored(conversation_id):
        with main_module.SessionLocal() as db:
            conversation = db.get(main_module.Conversation, conversation_id)
            return conversation.title, [message.role for message in conversation.messages]

    async def open_stream(conversation_id, question):
        response = await main_module.chat_stream(conversation_id, main_module.ChatRequest(question=question))
        return response.body_iterator

    async def next_event(stream):
        chunk = await asyncio.wait_for(anext(stream), timeout=2)
        return json.loads(chunk.removeprefix("data: "))

    async def until(stream, kind):
        while (event := await next_event(stream))["type"] != kind:
            assert event["type"] != "error", event
        return event

    async def scenario(mode):
        conversation_id = client.post(f"/api/documents/{uploaded['id']}/conversations", json={}).json()["id"]
        question = f"Why normalize ({mode})?"
        release[f"User: {question}"] = asyncio.Event()
        outcome["raise"] = mode == "failure"
        stream = await open_stream(conversation_id, question)
        completed = await until(stream, "completed")
        # The answer is durable and the composer can unlock while the title model is still running.
        assert completed["title_pending"] is True
        assert completed["title"] == question
        assert stored(conversation_id) == (question, ["user", "assistant"])
        assert conversation_id in main_module.title_jobs
        await asyncio.sleep(0)
        # Streams own short sessions only: none is checked out while the title model is pending.
        assert engine.pool.checkedout() == 0
        assert title_requests == [f"User: {question}\n\nAssistant: Answer to {question}"]

        if mode == "followup":
            # A next turn started before the title finishes completes on its own and never queues a second title job.
            followup = await open_stream(conversation_id, "And dropout?")
            events = [json.loads(chunk.removeprefix("data: ")) async for chunk in followup]
            assert [event["type"] for event in events][-1] == "completed"
            assert events[-1]["title_pending"] is False
            assert len(title_requests) == 1
            assert stored(conversation_id) == (question, ["user", "assistant", "user", "assistant"])
        if mode == "rename":
            with main_module.SessionLocal() as db_rename:
                db_rename.get(main_module.Conversation, conversation_id).title = "My normalization notes"
                db_rename.commit()
        if mode == "disconnect":
            await stream.aclose()

        release[f"User: {question}"].set()
        if mode == "disconnect":
            for _ in range(100):
                if conversation_id not in main_module.title_jobs:
                    break
                await asyncio.sleep(0.01)
            assert stored(conversation_id)[0] == "Layer Normalization Explained"
            return
        remaining = [json.loads(chunk.removeprefix("data: ")) async for chunk in stream]
        titles = [event["title"] for event in remaining if event["type"] == "title"]
        assert all(event["type"] != "error" for event in remaining)
        expected = {"followup": "Layer Normalization Explained", "rename": "My normalization notes", "failure": question}[mode]
        assert titles == ([] if mode == "failure" else [expected])
        assert stored(conversation_id)[0] == expected
        assert conversation_id not in main_module.title_jobs

    for mode in ("followup", "rename", "failure", "disconnect"):
        title_requests.clear()
        asyncio.run(scenario(mode))


# Discovery/import regressions: external I/O is mocked; the real ingestion and DB run.
def test_discovery_partial_failure_and_versions(monkeypatch) -> None:
    from adam_server.services import paper_discovery as discovery
    from adam_server.schemas import PaperCandidate

    paper = PaperCandidate(title="Discovery test paper", source="arXiv", source_url="https://arxiv.org/abs/2601.12345v2", arxiv_id="2601.12345v2", pdf_urls=["https://arxiv.org/pdf/2601.12345v2"])

    async def arxiv(_query): return [paper]
    async def unavailable(*_args): raise OSError("offline")
    monkeypatch.setattr(discovery, "search_arxiv", arxiv)
    monkeypatch.setattr(discovery, "search_general", unavailable)
    result = client.get('/api/paper-discovery/search', params={'q': 'test paper'})
    assert result.status_code == 200
    assert result.json()['results'][0]['title'] == paper.title
    assert len(result.json()['warnings']) == 1
    assert client.get('/api/paper-discovery/search', params={'q': '   '}).status_code == 422

    async def general(*_args):
        return [paper.model_copy(update={'pdf_urls': ['https://example.org/alternative.pdf']}), paper.model_copy(update={'arxiv_id': '2601.12345v1', 'source_url': 'https://arxiv.org/abs/2601.12345v1'})]
    monkeypatch.setattr(discovery, "search_general", general)
    data = client.get('/api/paper-discovery/search', params={'q': 'test paper'}).json()
    assert len(data['results']) == 2  # versions must remain distinct
    assert len(data['results'][0]['pdf_urls']) == 2


def test_discovery_import_provenance_duplicate_and_folder(monkeypatch) -> None:
    from adam_server import main
    from fastapi import UploadFile
    import httpx

    content = sample_pdf('Unique remote import paper for discovery tests.')
    async def download(*_args): return UploadFile(file=BytesIO(content), filename='Discovered.pdf')
    monkeypatch.setattr(main, 'download_pdf', download)
    folder = client.post('/api/folders', json={'name': 'Discovery imports'}).json()
    paper = {'title': 'Discovered paper', 'source': 'arXiv', 'source_url': 'https://arxiv.org/abs/2601.54321v1', 'arxiv_id': '2601.54321v1', 'doi': '10.1234/discovery'}
    request = {'paper': paper, 'pdf_url': 'https://arxiv.org/pdf/2601.54321v1', 'folder_id': folder['id']}
    result = client.post('/api/paper-discovery/import', json=request)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved['folder_id'] == folder['id']
    assert saved['discovery']['arxiv_id'] == paper['arxiv_id']
    assert saved['discovery']['pdf_url'] == request['pdf_url']
    assert client.get(f"/api/documents/{saved['id']}/file").content == content
    assert 'Unique remote' in client.get(f"/api/documents/{saved['id']}/pages/1/text").json()['text']
    client.put(f"/api/documents/{saved['id']}/paper-note", json={'content_html': '<p>Keep me</p>', 'plain_text': 'Keep me', 'revision': 0})

    async def should_not_download(*_args): raise AssertionError('Duplicate must not download')
    monkeypatch.setattr(main, 'download_pdf', should_not_download)
    repeated = client.post('/api/paper-discovery/import', json={**request, 'folder_id': None})
    assert repeated.json()['id'] == saved['id']
    assert repeated.json()['folder_id'] == folder['id']
    assert client.get(f"/api/documents/{saved['id']}/paper-note").json()['plain_text'] == 'Keep me'

    from adam_server.services.paper_discovery import existing_paper
    from adam_server.schemas import PaperCandidate
    with main.SessionLocal() as db:
        assert existing_paper(db, PaperCandidate(**{**paper, 'arxiv_id': '2601.54321v2', 'source_url': 'https://arxiv.org/abs/2601.54321v2'})) is None

    # Identical local uploads are reused by hash and retain their destination/name.
    monkeypatch.setattr(main, 'download_pdf', download)
    by_hash = client.post('/api/paper-discovery/import', json={**request, 'paper': {**paper, 'arxiv_id': None, 'doi': None, 'source_url': 'https://other.example/paper'}})
    assert by_hash.json()['id'] == saved['id']
    assert by_hash.json()['folder_id'] == folder['id']
    assert client.post('/api/paper-discovery/import', json={**request, 'folder_id': 'missing'}).status_code == 404

    async def offline(*_args): raise httpx.ConnectError('offline')
    monkeypatch.setattr(main, 'download_pdf', offline)
    assert client.post('/api/paper-discovery/import', json={**request, 'paper': {**paper, 'arxiv_id': '2601.99999v1'}}).status_code == 502


def test_discovery_public_url_and_dns_guard(monkeypatch) -> None:
    from adam_server.services import paper_discovery as discovery
    from fastapi import HTTPException
    import pytest

    for url in ('file:///etc/passwd', 'https://user:pass@example.org/paper.pdf', 'http://example.org:8000/p.pdf', 'javascript:alert(1)'):
        with pytest.raises(HTTPException): discovery.public_url(url)
    for address in ('127.0.0.1', '10.1.2.3', '169.254.169.254', '::1', '::ffff:127.0.0.1'):
        monkeypatch.setattr(discovery.socket, 'getaddrinfo', lambda *_args, **_kwargs: [(2, 1, 6, '', (address, 443))])
        with pytest.raises(HTTPException): asyncio.run(discovery.public_address('example.org', 443))
    # A mixed public/private DNS answer is also rejected.
    monkeypatch.setattr(discovery.socket, 'getaddrinfo', lambda *_args, **_kwargs: [(2, 1, 6, '', (ip, 443)) for ip in ('8.8.8.8', '127.0.0.1')])
    with pytest.raises(HTTPException): asyncio.run(discovery.public_address('example.org', 443))


def test_discovery_pinned_download_redirect_limits_and_invalid_content(monkeypatch) -> None:
    from adam_server.services import paper_discovery as discovery
    from fastapi import HTTPException
    import httpx
    import pytest

    real_client = httpx.AsyncClient
    calls = []
    content = sample_pdf('Public PDF download test.')
    def handler(request):
        calls.append(request)
        assert request.url.host == '8.8.8.8'  # no second hostname DNS lookup
        assert request.headers['host'] == 'papers.example'
        assert request.extensions['sni_hostname'] == 'papers.example'
        if request.url.path == '/redirect': return httpx.Response(302, headers={'location': 'http://127.0.0.1/internal'})
        if request.url.path == '/pdf': return httpx.Response(200, content=content)
        if request.url.path == '/large': return httpx.Response(200, content=content, headers={'content-length': '99999999'})
        return httpx.Response(200, content=b'<html>Login required</html>')
    monkeypatch.setattr(discovery.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    async def address(host, _port):
        if host == '127.0.0.1': raise HTTPException(400, 'Private address')
        return '8.8.8.8'
    monkeypatch.setattr(discovery, 'public_address', address)

    async def run():
        upload = await discovery.download_pdf('https://papers.example/pdf', '../Unsafe title', 100000)
        assert await upload.read() == content
        assert '/' not in upload.filename
        await upload.close()
        for path, limit, expected in [('/redirect', 100000, 400), ('/html', 100000, 400), ('/large', 100000, 413), ('/pdf', 10, 413)]:
            with pytest.raises(HTTPException) as error:
                await discovery.download_pdf('https://papers.example' + path, 'Paper', limit)
            assert error.value.status_code == expected
        assert len(calls) == 5  # private redirect was never fetched
    asyncio.run(run())


def test_discovery_resolves_relative_pdf_and_rejects_unsafe_links() -> None:
    from adam_server.services.paper_discovery import PdfLinks, arxiv_id
    parser = PdfLinks('https://papers.example/article/1')
    parser.feed('<meta name="citation_pdf_url" content="/files/paper.pdf"><a href="javascript:alert(1)">PDF</a><link type="application/pdf" href="/other.pdf">')
    assert parser.urls == ['https://papers.example/files/paper.pdf', 'https://papers.example/other.pdf']
    assert arxiv_id('https://arxiv.org/pdf/1706.03762v7.pdf') == '1706.03762v7'
    assert arxiv_id('arXiv:hep-th/9901001v2') == 'hep-th/9901001v2'
    assert arxiv_id('https://example.org/1706.03762') is None


def test_discovery_arxiv_metadata_cache_and_resolver(monkeypatch) -> None:
    from adam_server.services import paper_discovery as discovery
    from adam_server.schemas import PaperCandidate
    import httpx

    atom = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry><id>http://arxiv.org/abs/2610.11111v3</id><title> A test\n title </title><author><name>A. Author</name></author><published>2026-10-01</published><summary>An abstract</summary><arxiv:doi>10.1234/example</arxiv:doi></entry></feed>'''
    real_client = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=atom)
    monkeypatch.setattr(discovery.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(discovery, '_cache', {})
    monkeypatch.setattr(discovery, '_arxiv_last', 0)
    async def run():
        papers = await discovery.search_arxiv('2610.11111v3')
        assert papers[0].title == 'A test title'
        assert papers[0].doi == '10.1234/example'
        assert papers[0].pdf_urls == ['https://arxiv.org/pdf/2610.11111v3']
        papers[0].title = 'Mutated response'
        cached = await discovery.search_arxiv('2610.11111v3')
        assert cached[0].title == 'A test title'
        assert len(calls) == 1
        assert calls[0].url.params['id_list'] == '2610.11111v3'
    asyncio.run(run())

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def source(_url):
        yield httpx.Response(200, text='<meta name="citation_title" content="Actual title"><meta name="citation_author" content="A. Researcher"><meta name="citation_publication_date" content="2026/10/01"><meta name="citation_doi" content="10.1234/source"><meta name="citation_pdf_url" content="/download.pdf">', headers={'content-type': 'text/html'}), 'https://papers.example/landing'
    monkeypatch.setattr(discovery, 'public_stream', source)
    paper = PaperCandidate(title='Search snippet title', source_url='https://papers.example/landing')
    resolved = client.post('/api/paper-discovery/resolve', json=paper.model_dump())
    assert resolved.status_code == 200
    assert resolved.json()['title'] == 'Actual title'
    assert resolved.json()['authors'] == ['A. Researcher']
    assert resolved.json()['year'] == '2026'
    assert resolved.json()['pdf_urls'] == ['https://papers.example/download.pdf']


def test_discovery_invalid_pdf_leaves_no_library_entry(monkeypatch) -> None:
    from adam_server import main
    from fastapi import UploadFile
    before = {paper['id'] for paper in client.get('/api/documents').json()}
    async def invalid(*_args): return UploadFile(file=BytesIO(b'%PDF-not-a-real-pdf'), filename='Broken.pdf')
    monkeypatch.setattr(main, 'download_pdf', invalid)
    response = client.post('/api/paper-discovery/import', json={'paper': {'title': 'Damaged remote PDF', 'source_url': 'https://papers.example/broken'}, 'pdf_url': 'https://papers.example/broken.pdf'})
    assert response.status_code == 400
    assert {paper['id'] for paper in client.get('/api/documents').json()} == before
    assert not list((main.settings.data_dir / 'tmp').glob('upload-*'))


def test_nested_folders_inherit_colors_move_safely_and_keep_contents() -> None:
    root = client.post('/api/folders', json={'name': 'Nested root', 'color': '#123456'}).json()
    other = client.post('/api/folders', json={'name': 'Other root', 'color': '#654321'}).json()
    child = client.post('/api/folders', json={'name': 'Child', 'parent_id': root['id'], 'color': '#ffffff', 'icon': 'book'}).json()
    leaf = client.post('/api/folders', json={'name': 'Leaf', 'parent_id': child['id']}).json()
    assert child['color'] == leaf['color'] == root['color']
    assert client.post('/api/folders', json={'name': 'child', 'parent_id': root['id']}).status_code == 409
    twin = client.post('/api/folders', json={'name': 'Child', 'parent_id': other['id']})
    assert twin.status_code == 201
    assert client.patch(f"/api/folders/{root['id']}", json={'parent_id': leaf['id']}).status_code == 422
    assert client.patch(f"/api/folders/{child['id']}", json={'parent_id': child['id']}).status_code == 422
    assert client.patch(f"/api/folders/{child['id']}", json={'parent_id': 'missing'}).status_code == 404
    # Conflicting moves leave the whole branch unchanged.
    assert client.patch(f"/api/folders/{child['id']}", json={'parent_id': other['id']}).status_code == 409
    client.delete(f"/api/folders/{twin.json()['id']}")
    moved = client.patch(f"/api/folders/{child['id']}", json={'parent_id': other['id']})
    assert moved.json()['color'] == other['color']
    assert moved.json()['icon'] == 'book'
    icon_update = client.patch(f"/api/folders/{child['id']}", json={'icon': 'star'})
    assert icon_update.json()['icon'] == 'star'
    assert icon_update.json()['parent_id'] == other['id']
    assert icon_update.json()['color'] == other['color']
    client.patch(f"/api/folders/{other['id']}", json={'color': '#abcdef'})
    folders = {folder['id']: folder for folder in client.get('/api/folders').json()}
    assert folders[leaf['id']]['color'] == '#abcdef'
    detached = client.patch(f"/api/folders/{child['id']}", json={'parent_id': None}).json()
    assert detached['parent_id'] is None and detached['color'] != '#abcdef'
    paper = client.post('/api/documents', files={'file': ('nested.pdf', BytesIO(sample_pdf('Nested contents survive.')), 'application/pdf')}).json()
    assert client.post('/api/document-moves', json={'document_ids': [paper['id']], 'folder_id': leaf['id']}).status_code == 200
    # Reopening reads the persisted hierarchy; removing a parent keeps its children and papers.
    assert client.delete(f"/api/folders/{child['id']}").status_code == 204
    folders = {folder['id']: folder for folder in client.get('/api/folders').json()}
    assert folders[leaf['id']]['parent_id'] is None
    assert client.get(f"/api/documents/{paper['id']}").json()['folder_id'] == leaf['id']
    for folder in [root, other, leaf]:
        client.delete(f"/api/folders/{folder['id']}")

def test_folder_icons_are_validated_and_persist_across_updates() -> None:
    folder = client.post('/api/folders', json={'name': 'Icon lifecycle'}).json()
    assert folder['icon'] == 'folder'
    url = f"/api/folders/{folder['id']}"
    icons = ['folder', 'book', 'bookmark', 'star', 'heart', 'lightbulb', 'flask', 'code', 'globe', 'leaf', 'briefcase', 'graduation']
    for icon in icons:
        saved = client.patch(url, json={'name': folder['name'], 'icon': icon})
        assert saved.status_code == 200
        assert saved.json()['icon'] == icon
    renamed = client.patch(url, json={'name': 'Icon renamed', 'color': '#618fbd'})
    assert renamed.json()['icon'] == 'graduation'
    assert next(item for item in client.get('/api/folders').json() if item['id'] == folder['id'])['icon'] == 'graduation'
    for invalid in ['', 'unknown', '<svg>', 5]:
        assert client.patch(url, json={'name': 'Icon renamed', 'icon': invalid}).status_code == 422
        assert client.post('/api/folders', json={'name': 'Invalid icon', 'icon': invalid}).status_code == 422
    assert client.patch(url, json={'name': 'Icon renamed', 'icon': None}).json()['icon'] == 'graduation'
    client.delete(url)
    custom = client.post('/api/folders', json={'name': 'Book collection', 'icon': 'book', 'color': '#9275bc'})
    assert custom.status_code == 201
    assert custom.json()['icon'] == 'book'
    client.delete(f"/api/folders/{custom.json()['id']}")


def test_folder_and_discovery_migrations_upgrade_all_feature_branches(tmp_path) -> None:
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1]
    for revision in ['0011_folder_icons', '0011_nested_folders', '0011_paper_discovery', '0012_merge_folder_features']:
        data_dir = tmp_path / revision
        env = {**os.environ, 'ADAM_DATA_DIR': str(data_dir)}
        def upgrade(target):
            subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', target], cwd=backend_root, env=env, check=True, capture_output=True)
        upgrade(revision)
        path = data_dir / 'database' / 'adam.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO folders (id, name, color, created_at) VALUES ('root', 'Root', '#123456', CURRENT_TIMESTAMP)")
            if revision == '0011_folder_icons':
                db.execute("UPDATE folders SET icon = 'book' WHERE id = 'root'")
            elif revision in ['0011_nested_folders', '0012_merge_folder_features']:
                db.execute("INSERT INTO folders (id, name, color, parent_id, created_at) VALUES ('child', 'Child', '#123456', 'root', CURRENT_TIMESTAMP)")
        upgrade('head')
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT color, icon, parent_id FROM folders WHERE id = 'root'").fetchone() == ('#123456', 'book' if revision == '0011_folder_icons' else 'folder', None)
            if revision in ['0011_nested_folders', '0012_merge_folder_features']:
                assert db.execute("SELECT parent_id, icon FROM folders WHERE id = 'child'").fetchone() == ('root', 'folder')
            assert 'discovery_json' in {column[1] for column in db.execute('PRAGMA table_info(documents)')}
            assert db.execute('SELECT version_num FROM alembic_version').fetchall() == [('0013_merge_discovery_folders',)]
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_conversation_search_titles_messages_and_scope() -> None:
    from adam_server.database import SessionLocal
    from adam_server.models import Message
    paper = client.post('/api/documents', files={'file': ('search.pdf', BytesIO(sample_pdf('Search fixture')), 'application/pdf')}).json()
    other = client.post('/api/documents', files={'file': ('other.pdf', BytesIO(sample_pdf('Other fixture')), 'application/pdf')}).json()
    first = client.post(f"/api/documents/{paper['id']}/conversations", json={'title': 'Attention notes'}).json()
    second = client.post(f"/api/documents/{paper['id']}/conversations", json={'title': 'Another topic'}).json()
    third = client.post(f"/api/documents/{other['id']}/conversations", json={'title': 'Attention elsewhere'}).json()
    with SessionLocal() as db:
        for conversation in [second, third]:
            db.add(Message(document_id=conversation['document_id'], conversation_id=conversation['id'], role='assistant', content='Sparse ATTENTION supports retrieval. Literal 100% and a_b.', context_json=None))
        db.commit()
    url = f"/api/documents/{paper['id']}/conversation-search"
    results = client.get(url, params={'q': 'attention'}).json()
    assert {item['id'] for item in results} == {first['id'], second['id']}
    assert next(item for item in results if item['id'] == second['id'])['snippet'].startswith('Sparse ATTENTION')
    assert client.get(url, params={'q': '100%'}).json()[0]['id'] == second['id']
    assert client.get(url, params={'q': 'a_b'}).json()[0]['id'] == second['id']
    assert client.get(url, params={'q': 'absent'}).json() == []
    assert client.get(url, params={'q': '   '}).json() == []
    assert client.get('/api/documents/missing/conversation-search', params={'q': 'test'}).status_code == 404
