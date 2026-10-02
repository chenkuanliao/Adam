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
    assert '"title": "Attention Mechanisms Explained"' in response.text
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
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=backend_root, env=env, check=True, capture_output=True)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT id, folder_id FROM documents').fetchall() == [('existing', None)]
        for table in ['pages', 'paper_notes', 'conversations', 'messages', 'annotations']:
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 1
        assert db.execute('SELECT color FROM folders WHERE id = ?', ('old-folder',)).fetchone()[0] == '#8c9d65'
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
