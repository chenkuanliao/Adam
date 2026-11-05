import asyncio
import base64
import hashlib
import json
import os
import stat
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

os.environ.setdefault("ADAM_DATA_DIR", tempfile.mkdtemp(prefix="adam-tests-"))

from adam_server.services import chatgpt_auth as auth_module  # noqa: E402
from adam_server.services.chatgpt_auth import PLAN_LIMIT, SIGN_IN_AGAIN, ChatGPTAuth, ChatGPTAuthError, parse_callback_url, pkce_pair  # noqa: E402
from adam_server.services.llm import ChatGPTRequestError, ChatGPTSubscriptionProvider  # noqa: E402

ISSUED = "oaiapp_test123"


def b64(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def id_token(nonce: str | None = None, aud: str = ISSUED, iss: str = "https://auth.openai.com", exp: float | None = None) -> str:
    claims = {"iss": iss, "aud": aud, "sub": "user-sub", "email": "owner@example.com", "name": "Owner", "exp": int(exp or time.time() + 3600)}
    if nonce is not None:
        claims["nonce"] = nonce
    return f"{b64({'alg': 'RS256'})}.{b64(claims)}.signature"


def token_body(access: str = "access-1", refresh: str = "refresh-1", nonce: str | None = None, scope: str = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct") -> dict:
    return {"access_token": access, "refresh_token": refresh, "id_token": id_token(nonce), "token_type": "Bearer", "expires_in": 3600, "scope": scope}


def form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


def authorize_params(url: str) -> dict[str, str]:
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://auth.openai.com/api/accounts/authorize"
    return {key: values[0] for key, values in parse_qs(parts.query).items()}


def signed_in_auth(tmp_path, handler, expires_in: float = 3600) -> ChatGPTAuth:
    auth = ChatGPTAuth(tmp_path, transport=httpx.MockTransport(handler))
    auth.save({"host_id": "urn:uuid:test", "client_id": ISSUED, "access_token": "access-1", "refresh_token": "refresh-1", "expires_at": int(time.time() + expires_in), "refreshed_at": int(time.time()), "plan_enabled": True, "scope": "chatgpt.tokens.use.direct"})
    return auth


# -- PKCE, authorize URL, state ---------------------------------------------------------------

def test_pkce_challenge_is_unpadded_base64url_sha256() -> None:
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and "=" not in challenge


def test_first_sign_in_uses_registration_client_and_exact_loopback(tmp_path) -> None:
    auth = ChatGPTAuth(tmp_path)
    params = authorize_params(auth.start())
    assert params["client_id"] == "dynamic_agent_client"
    assert params["agent_name_hint"] == "Adam"
    assert params["ext_agent_host_id"].startswith("urn:uuid:")
    assert params["redirect_uri"] == "http://127.0.0.1:1455/auth/callback"
    assert params["scope"].split() == ["openid", "profile", "email", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct"]
    assert params["resource"] == "https://api.openai.com/v1"
    assert params["response_type"] == "code" and params["code_challenge_method"] == "S256"
    assert "id_token_hint" not in params and "prompt" not in params
    pending = auth.pending[params["state"]]
    assert pending.nonce == params["nonce"]
    assert params["code_challenge"] == base64.urlsafe_b64encode(hashlib.sha256(pending.verifier.encode()).digest()).rstrip(b"=").decode()
    # The host ID is stable across attempts.
    assert authorize_params(auth.start())["ext_agent_host_id"] == params["ext_agent_host_id"]


def test_reauthorization_reuses_issued_client_without_name_hint(tmp_path) -> None:
    auth = ChatGPTAuth(tmp_path, callback_port=1460)
    auth.save({"host_id": "urn:uuid:x", "client_id": ISSUED, "id_token": "saved-id", "refresh_token": "r", "email": "owner@example.com"})
    params = authorize_params(auth.start())
    assert params["client_id"] == ISSUED and "agent_name_hint" not in params
    assert params["id_token_hint"] == "saved-id" and params["login_hint"] == "owner@example.com"
    assert params["redirect_uri"] == "http://127.0.0.1:1460/auth/callback"


def test_unknown_and_reused_state_is_rejected(tmp_path) -> None:
    calls = []
    auth = ChatGPTAuth(tmp_path, transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(500)))
    params = authorize_params(auth.start())
    with pytest.raises(ChatGPTAuthError, match="unknown or expired"):
        asyncio.run(auth.complete({"state": "forged", "code": "c", "client_id": ISSUED}))
    with pytest.raises(ChatGPTAuthError, match="access_denied|not enabled|failed"):
        asyncio.run(auth.complete({"state": params["state"], "error": "access_denied"}))
    # A state is single use even after an error.
    with pytest.raises(ChatGPTAuthError, match="unknown or expired"):
        asyncio.run(auth.complete({"state": params["state"], "code": "c", "client_id": ISSUED}))
    assert calls == []


def test_expired_pending_state_is_rejected(tmp_path, monkeypatch) -> None:
    auth = ChatGPTAuth(tmp_path)
    state = authorize_params(auth.start())["state"]
    auth.pending[state].created_at -= auth_module.PENDING_TTL + 1
    with pytest.raises(ChatGPTAuthError, match="unknown or expired"):
        asyncio.run(auth.complete({"state": state, "code": "c", "client_id": ISSUED}))


# -- callback URL parsing ----------------------------------------------------------------------

def test_parse_callback_url_accepts_loopback_and_bare_query() -> None:
    assert parse_callback_url("  http://127.0.0.1:1455/auth/callback?code=abc&scope=x+y&state=s1&client_id=oaiapp_1 ") == {"code": "abc", "scope": "x y", "state": "s1", "client_id": "oaiapp_1"}
    assert parse_callback_url("?code=abc&state=s1") == {"code": "abc", "state": "s1"}
    assert parse_callback_url("http://127.0.0.1:1455/auth/callback?error=access_denied&state=s1")["error"] == "access_denied"


@pytest.mark.parametrize("value", [
    "",
    "http://localhost:1455/auth/callback?code=a&state=b",
    "https://127.0.0.1:1455/auth/callback?code=a&state=b",
    "http://127.0.0.1:1455/callback?code=a&state=b",
    "http://127.0.0.1:1455/auth/callback?code=a",
    "http://127.0.0.1:1455/auth/callback?state=b",
])
def test_parse_callback_url_rejects_wrong_or_incomplete_urls(value) -> None:
    with pytest.raises(ChatGPTAuthError):
        parse_callback_url(value)


# -- code exchange and persistence -------------------------------------------------------------

def test_exchange_persists_private_credentials_and_hides_tokens(tmp_path) -> None:
    seen: list[dict] = []
    nonce_holder: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://auth.openai.com/api/accounts/oauth/token"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        seen.append(form(request))
        return httpx.Response(200, json=token_body(nonce=nonce_holder["nonce"]))

    auth = ChatGPTAuth(tmp_path, transport=httpx.MockTransport(handler))
    params = authorize_params(auth.start())
    nonce_holder["nonce"] = params["nonce"]
    verifier = auth.pending[params["state"]].verifier
    status = asyncio.run(auth.complete(parse_callback_url(f"http://127.0.0.1:1455/auth/callback?code=the-code&state={params['state']}&client_id={ISSUED}")))
    assert seen == [{"grant_type": "authorization_code", "client_id": ISSUED, "code": "the-code", "code_verifier": verifier, "redirect_uri": "http://127.0.0.1:1455/auth/callback", "resource": "https://api.openai.com/v1"}]
    assert stat.S_IMODE(os.stat(auth.path).st_mode) == 0o600
    stored = json.loads(auth.path.read_text())
    assert stored["client_id"] == ISSUED and stored["refresh_token"] == "refresh-1" and stored["plan_enabled"] is True
    assert status["signed_in"] and status["plan_enabled"] and status["email"] == "owner@example.com"
    serialized = json.dumps(status)
    assert "access-1" not in serialized and "refresh-1" not in serialized and "signature" not in serialized
    assert not list(tmp_path.glob(".*.tmp"))


def test_exchange_rejects_bad_nonce_and_missing_or_mismatched_client(tmp_path) -> None:
    auth = ChatGPTAuth(tmp_path, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=token_body(nonce="wrong"))))
    state = authorize_params(auth.start())["state"]
    with pytest.raises(ChatGPTAuthError, match="incomplete"):
        asyncio.run(auth.complete({"state": state, "code": "c"}))
    state = authorize_params(auth.start())["state"]
    with pytest.raises(ChatGPTAuthError, match="nonce"):
        asyncio.run(auth.complete({"state": state, "code": "c", "client_id": ISSUED}))
    # The issued client was saved before the exchange and is now bound.
    assert auth.load()["client_id"] == ISSUED
    state = authorize_params(auth.start())["state"]
    with pytest.raises(ChatGPTAuthError, match="different app registration"):
        asyncio.run(auth.complete({"state": state, "code": "c", "client_id": "oaiapp_other"}))


def test_missing_plan_scope_marks_plan_disabled_and_requests_consent(tmp_path) -> None:
    nonce_holder: dict = {}
    auth = ChatGPTAuth(tmp_path, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=token_body(nonce=nonce_holder["nonce"], scope="openid profile email offline_access"))))
    params = authorize_params(auth.start())
    nonce_holder["nonce"] = params["nonce"]
    with pytest.raises(ChatGPTAuthError, match="plan usage was not granted"):
        asyncio.run(auth.complete({"state": params["state"], "code": "c", "client_id": ISSUED}))
    assert auth.status()["signed_in"] and not auth.status()["plan_enabled"] and not auth.connected()
    assert authorize_params(auth.start())["prompt"] == "consent"


def test_atomic_write_replaces_file_with_0600(tmp_path) -> None:
    path = tmp_path / "creds.json"
    path.write_text("{}")
    os.chmod(path, 0o644)
    auth_module.write_private_json(path, {"a": 1})
    assert json.loads(path.read_text()) == {"a": 1}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# -- refresh -----------------------------------------------------------------------------------

def test_concurrent_requests_share_one_serialized_refresh_and_store_rotation(tmp_path) -> None:
    calls: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(form(request))
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=token_body(access=f"access-{len(calls) + 1}", refresh=f"refresh-{len(calls) + 1}"))

    auth = signed_in_auth(tmp_path, handler, expires_in=-10)

    async def run() -> list[str]:
        return await asyncio.gather(*(auth.access_token() for _ in range(5)))

    tokens = asyncio.run(run())
    assert tokens == ["access-2"] * 5
    assert calls == [{"grant_type": "refresh_token", "client_id": ISSUED, "refresh_token": "refresh-1", "resource": "https://api.openai.com/v1"}]
    stored = auth.load()
    assert stored["refresh_token"] == "refresh-2" and stored["expires_at"] > time.time() + 3000
    assert stat.S_IMODE(os.stat(auth.path).st_mode) == 0o600


def test_fresh_token_is_used_without_refresh_and_background_rotates_daily(tmp_path) -> None:
    calls = []
    auth = signed_in_auth(tmp_path, lambda request: calls.append(request) or httpx.Response(200, json=token_body(access="access-2", refresh="refresh-2")))
    assert asyncio.run(auth.access_token()) == "access-1" and calls == []
    # Within the daily window the background job leaves a fresh token alone.
    asyncio.run(auth.refresh(min_valid=300, max_age=auth_module.BACKGROUND_REFRESH_INTERVAL))
    assert calls == []
    stored = auth.load()
    stored["refreshed_at"] = int(time.time() - auth_module.BACKGROUND_REFRESH_INTERVAL - 1)
    auth.save(stored)
    asyncio.run(auth.refresh(min_valid=300, max_age=auth_module.BACKGROUND_REFRESH_INTERVAL))
    assert len(calls) == 1 and auth.load()["refresh_token"] == "refresh-2"


def test_terminal_refresh_error_clears_tokens_but_keeps_registration(tmp_path) -> None:
    auth = signed_in_auth(tmp_path, lambda request: httpx.Response(400, json={"error": "invalid_grant"}), expires_in=-10)
    with pytest.raises(ChatGPTAuthError) as raised:
        asyncio.run(auth.access_token())
    assert str(raised.value) == SIGN_IN_AGAIN
    stored = auth.load()
    assert "refresh_token" not in stored and "access_token" not in stored
    assert stored["client_id"] == ISSUED and stored["host_id"] == "urn:uuid:test"


def test_temporary_refresh_failure_keeps_credentials(tmp_path) -> None:
    auth = signed_in_auth(tmp_path, lambda request: httpx.Response(502), expires_in=-10)
    with pytest.raises(ChatGPTAuthError, match="Try again"):
        asyncio.run(auth.access_token())
    assert auth.load()["refresh_token"] == "refresh-1"


def test_sign_out_revokes_refresh_token_and_keeps_client(tmp_path) -> None:
    calls: list[tuple[str, dict]] = []
    auth = signed_in_auth(tmp_path, lambda request: calls.append((str(request.url), form(request))) or httpx.Response(200))
    result = asyncio.run(auth.sign_out())
    assert calls == [("https://auth.openai.com/api/accounts/oauth/revoke", {"token": "refresh-1", "token_type_hint": "refresh_token", "client_id": ISSUED})]
    assert result["revoked"] is True and not result["signed_in"]
    assert auth.load()["client_id"] == ISSUED and "refresh_token" not in auth.load()


# -- inference request construction and error mapping ------------------------------------------

def sse_body(*events: dict) -> bytes:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()


def collect(provider: ChatGPTSubscriptionProvider) -> str:
    async def run() -> str:
        return "".join([part async for part in provider.stream_answer("What is attention?", "Selected", [], 2, [])])
    return asyncio.run(run())


def test_request_body_is_store_false_stream_true_without_forbidden_params(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=sse_body({"type": "response.output_text.delta", "delta": "Hello"}, {"type": "response.output_text.delta", "delta": " world"}, {"type": "response.completed", "response": {}}), headers={"content-type": "text/event-stream"})

    auth = signed_in_auth(tmp_path, handler)
    provider = ChatGPTSubscriptionProvider(auth, "gpt-test", "Be precise.", transport=httpx.MockTransport(handler))
    assert collect(provider) == "Hello world"
    request = requests[0]
    assert request.method == "POST" and str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == "Bearer access-1"
    body = json.loads(request.content)
    assert body["store"] is False and body["stream"] is True
    assert body["model"] == "gpt-test" and body["instructions"] == "Be precise."
    assert isinstance(body["input"], list) and body["input"][-1]["role"] == "user"
    assert not set(body) & ChatGPTSubscriptionProvider.FORBIDDEN_FIELDS
    assert not set(body) & {"temperature", "top_p", "max_output_tokens", "previous_response_id", "user"}


def test_usage_limit_429_maps_to_friendly_message_and_pauses(tmp_path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429, json={"error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}})

    auth = signed_in_auth(tmp_path, handler)
    provider = ChatGPTSubscriptionProvider(auth, "gpt-test", transport=httpx.MockTransport(handler))
    with pytest.raises(ChatGPTRequestError) as raised:
        collect(provider)
    assert str(raised.value) == PLAN_LIMIT and raised.value.status == 429
    assert "chatgpt.com/settings/usage" in PLAN_LIMIT and "reset" not in PLAN_LIMIT.lower()
    assert auth.status()["usage_paused"] is True
    # While paused, no request is sent.
    with pytest.raises(ChatGPTAuthError, match="plan limit reached"):
        collect(provider)
    assert len(calls) == 1
    assert auth.resume_usage()["usage_paused"] is False


def test_usage_limit_mid_stream_failure_also_pauses(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body({"type": "response.output_text.delta", "delta": "Par"}, {"type": "response.failed", "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}}))

    auth = signed_in_auth(tmp_path, handler)
    with pytest.raises(ChatGPTRequestError, match="plan limit reached"):
        collect(ChatGPTSubscriptionProvider(auth, "gpt-test", transport=httpx.MockTransport(handler)))
    assert auth.status()["usage_paused"]


def test_401_refreshes_once_then_asks_to_sign_in_again(tmp_path) -> None:
    hits = {"responses": 0, "token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/token"):
            hits["token"] += 1
            return httpx.Response(200, json=token_body(access="access-2", refresh="refresh-2"))
        hits["responses"] += 1
        return httpx.Response(401, json={"error": {"code": "subscription_sharing_invalid_user"}})

    auth = signed_in_auth(tmp_path, handler)
    with pytest.raises(ChatGPTRequestError) as raised:
        collect(ChatGPTSubscriptionProvider(auth, "gpt-test", transport=httpx.MockTransport(handler)))
    assert str(raised.value) == SIGN_IN_AGAIN
    assert hits == {"responses": 2, "token": 1}


def test_stream_without_completed_event_is_an_error(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse_body({"type": "response.output_text.delta", "delta": "Half"}))

    auth = signed_in_auth(tmp_path, handler)
    with pytest.raises(ChatGPTRequestError, match="ended before"):
        collect(ChatGPTSubscriptionProvider(auth, "gpt-test", transport=httpx.MockTransport(handler)))


# -- loopback listener -------------------------------------------------------------------------

def test_loopback_listener_completes_sign_in(tmp_path) -> None:
    nonce_holder: dict = {}
    auth = ChatGPTAuth(tmp_path, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=token_body(nonce=nonce_holder["nonce"]))))

    async def run() -> tuple[int, str, int, str]:
        server = await auth.start_listener("127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        params = authorize_params(auth.start())
        nonce_holder["nonce"] = params["nonce"]
        async with httpx.AsyncClient() as client:
            ok = await client.get(f"http://127.0.0.1:{port}/auth/callback", params={"code": "c", "state": params["state"], "client_id": ISSUED})
            missing = await client.get(f"http://127.0.0.1:{port}/other")
        server.close()
        return ok.status_code, ok.text, missing.status_code, auth.load().get("refresh_token")

    status, text, missing, refresh = asyncio.run(run())
    assert status == 200 and "owner@example.com" in text and "refresh-1" not in text
    assert missing == 404 and refresh == "refresh-1"


# -- API surface -------------------------------------------------------------------------------

def test_api_status_and_sign_in_never_expose_tokens(monkeypatch, tmp_path) -> None:
    from fastapi.testclient import TestClient
    from adam_server import main as main_module

    auth = signed_in_auth(tmp_path, lambda request: httpx.Response(200))
    monkeypatch.setattr(main_module, "chatgpt_auth", auth)

    async def no_listener() -> None:
        return None

    monkeypatch.setattr(main_module, "ensure_chatgpt_listener", no_listener)
    client = TestClient(main_module.app)
    status = client.get("/api/chatgpt/status")
    assert status.status_code == 200 and status.json()["signed_in"] is True
    assert "access-1" not in status.text and "refresh-1" not in status.text
    started = client.post("/api/chatgpt/sign-in")
    assert started.status_code == 200
    params = authorize_params(started.json()["authorize_url"])
    assert params["client_id"] == ISSUED and params["redirect_uri"] == "http://127.0.0.1:1455/auth/callback"
    bad = client.post("/api/chatgpt/callback", json={"url": "http://127.0.0.1:1455/auth/callback?code=x&state=nope"})
    assert bad.status_code == 400 and "unknown or expired" in bad.json()["detail"]
