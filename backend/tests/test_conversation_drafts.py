from concurrent.futures import ThreadPoolExecutor
from test_vertical_slice import client, sample_pdf
from adam_server import main as main_module


def test_temporary_chats_drafts_and_sent_history():
    paper = client.post('/api/documents', files={'file': ('drafts.pdf', sample_pdf('Draft lifecycle test.'), 'application/pdf')}).json()
    base = f"/api/documents/{paper['id']}/conversations"
    first = client.post(base, json={}).json()
    assert client.get(base).json() == []
    for _ in range(3):
        assert client.post(base, json={}).json()['id'] == first['id']
    draft_url = f"/api/conversations/{first['id']}/draft"
    assert client.put(draft_url, json={'draft_text': 'Explain the methods, please'}).status_code == 200
    assert client.get(base).json()[0]['draft_text'] == 'Explain the methods, please'
    assert client.get(f"/api/conversations/{first['id']}").json()['messages'] == []
    second = client.post(base, json={}).json()
    assert second['id'] != first['id']
    assert len(client.get(base).json()) == 1
    assert client.put(draft_url, json={'draft_text': '   '}).status_code == 200
    assert client.get(base).json() == []
    # A sent conversation remains visible after clearing the next unsent draft.
    with main_module.SessionLocal() as db:
        db.add(main_module.Message(document_id=paper['id'], conversation_id=first['id'], role='user', content='A sent question'))
        db.commit()
    assert client.put(draft_url, json={'draft_text': ''}).json()['message_count'] == 1
    assert client.get(base).json()[0]['message_count'] == 1
    assert client.get(f"/api/conversations/{first['id']}").json()['message_count'] == 1
    assert client.put(draft_url, json={'draft_text': 'x' * 8001}).status_code == 422
    assert client.delete(f"/api/conversations/{first['id']}").status_code == 204
    assert client.get(base).json() == []
    assert client.put(draft_url, json={'draft_text': 'Deleted draft'}).status_code == 404


def test_concurrent_new_chat_reuses_one_temporary_chat():
    paper = client.post('/api/documents', files={'file': ('concurrent.pdf', sample_pdf('Concurrent draft creation.'), 'application/pdf')}).json()
    base = f"/api/documents/{paper['id']}/conversations"
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: client.post(base, json={}).json()['id'], range(8)))
    assert len(set(ids)) == 1
    assert client.get(base).json() == []


def test_sending_a_draft_consumes_it_without_losing_history(monkeypatch):
    from test_stream_sessions import use_fake_models
    use_fake_models(monkeypatch)
    paper = client.post('/api/documents', files={'file': ('send-draft.pdf', sample_pdf('Sending a draft.'), 'application/pdf')}).json()
    base = f"/api/documents/{paper['id']}/conversations"
    conversation = client.post(base, json={}).json()
    draft_url = f"/api/conversations/{conversation['id']}/draft"
    client.put(draft_url, json={'draft_text': 'Explain the paper'})
    response = client.post(f"/api/conversations/{conversation['id']}/messages/stream", json={'question': 'Explain the paper'})
    assert response.status_code == 200
    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assert detail['draft_text'] == ''
    assert detail['message_count'] == 2
    assert client.get(base).json()[0]['id'] == conversation['id']
