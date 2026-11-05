"""Sign in with ChatGPT (open-source token-sharing path) for a single local owner.

Spec: https://developers.openai.com/siwc/token-sharing-open-source
Tokens never leave this module except as a bearer header to api.openai.com. Nothing here
logs or returns token values; public status is limited to identity and expiry metadata.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
REVOKE_URL = "https://auth.openai.com/api/accounts/oauth/revoke"
ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
REGISTRATION_CLIENT_ID = "dynamic_agent_client"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
CALLBACK_PATH = "/auth/callback"
AGENT_NAME = "Adam"
USAGE_URL = "https://chatgpt.com/settings/usage"

ACCESS_REFRESH_MARGIN = 5 * 60
BACKGROUND_REFRESH_INTERVAL = 20 * 60 * 60  # rotate the 30-day refresh token at least daily
REFRESH_TOKEN_LIFETIME = 30 * 24 * 60 * 60
PENDING_TTL = 15 * 60
USAGE_PAUSE_SECONDS = 15 * 60
TERMINAL_REFRESH_ERRORS = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired", "refresh_token_invalidated", "refresh_token_reused"}

SIGN_IN_AGAIN = "Sign in again: your ChatGPT session is no longer valid. Open Settings → ChatGPT (subscription)."
PLAN_LIMIT = f"ChatGPT plan limit reached. Adam paused ChatGPT requests; check your usage at {USAGE_URL} and resume from Settings when you're ready."


class ChatGPTAuthError(Exception):
    """A user-presentable sign-in or session problem. The message never contains secrets."""


@dataclass
class PendingSignIn:
    state: str
    verifier: str
    nonce: str
    redirect_uri: str
    client_id: str  # client used on the authorize request (registration or issued)
    created_at: float = field(default_factory=time.time)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode JWT claims without verifying the signature.

    The ID token is received directly from the token endpoint over TLS, so per OIDC Core
    §3.1.3.7 the TLS server validation stands in for signature checking; the issuer,
    audience, expiry, and nonce are still validated by the caller.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        value = json.loads(base64.urlsafe_b64decode(payload))
        return value if isinstance(value, dict) else {}
    except (IndexError, ValueError):
        return {}


def parse_callback_url(value: str) -> dict[str, str]:
    """Return the callback query parameters from a pasted URL (or bare query string)."""
    text = value.strip()
    if not text:
        raise ChatGPTAuthError("Paste the full callback URL from your browser's address bar.")
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.path != CALLBACK_PATH:
            raise ChatGPTAuthError(f"That doesn't look like the sign-in callback. It should start with http://127.0.0.1:<port>{CALLBACK_PATH}?")
        query = parts.query
    else:
        query = text.lstrip("?")
    params = {key: values[0] for key, values in parse_qs(query, keep_blank_values=True).items() if values}
    if "state" not in params or ("code" not in params and "error" not in params):
        raise ChatGPTAuthError("The pasted URL is missing the code or state. Copy the entire address after the sign-in redirect.")
    return params


def write_private_json(path: Path, value: dict) -> None:
    """Atomically replace `path` with JSON readable only by the owner (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def friendly_api_error(status: int, code: str | None, param: str | None = None) -> str:
    if code == "subscription_sharing_usage_limit_exceeded" or (status == 429 and not code):
        return PLAN_LIMIT
    if code == "subscription_sharing_user_not_eligible":
        return "ChatGPT plan usage isn't available for this account or workspace. Use another provider or an API key."
    if code in {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"} or status == 503:
        return "ChatGPT is temporarily unavailable. Try again in a moment."
    if code == "subscription_sharing_unsupported_capability":
        return f"ChatGPT plan access doesn't support part of this request{f' ({param})' if param else ''}."
    if code in {"chatpass_v2_scope_not_authorized", "chatpass_v2_invalid_authorization_context"}:
        return "ChatGPT plan access isn't authorized for this sign-in. Sign in with ChatGPT again in Settings."
    if code == "subscription_sharing_invalid_user" or status == 401:
        return SIGN_IN_AGAIN
    if code == "subscription_sharing_route_not_supported":
        return "ChatGPT rejected the request route. This is an Adam bug; please report it."
    if status == 403:
        return "ChatGPT refused this request (permission or region policy)."
    return f"ChatGPT request failed{f' ({code})' if code else ''} with HTTP {status}."


def error_code(body: Any) -> tuple[str | None, str | None]:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return error.get("code") or error.get("type"), error.get("param")
        if isinstance(error, str):
            return error, None
    return None, None


class ChatGPTAuth:
    """Owns the credential file, pending PKCE sessions, refresh lock, and usage pause."""

    def __init__(self, data_dir: Path, callback_port: int = 1455, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.path = Path(data_dir) / "chatgpt_auth.json"
        self.callback_port = callback_port
        self.transport = transport
        self.pending: dict[str, PendingSignIn] = {}
        self.refresh_lock = asyncio.Lock()
        self.exchange_lock = asyncio.Lock()
        self.listener_active = False
        self.listener_error: str | None = None

    # -- storage ---------------------------------------------------------------------------
    def load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def save(self, value: dict) -> None:
        write_private_json(self.path, value)

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.callback_port}{CALLBACK_PATH}"

    def client(self, **kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, timeout=kwargs.pop("timeout", 30), **kwargs)

    def connected(self) -> bool:
        stored = self.load()
        return bool(stored.get("refresh_token") and stored.get("client_id") and stored.get("plan_enabled"))

    # -- status ----------------------------------------------------------------------------
    def status(self) -> dict:
        stored = self.load()
        self.prune_pending()
        signed_in = bool(stored.get("refresh_token"))
        paused_until = stored.get("usage_paused_until") or 0
        paused = bool(paused_until and paused_until > time.time())
        return {
            "signed_in": signed_in,
            "plan_enabled": bool(signed_in and stored.get("plan_enabled")),
            "email": stored.get("email") if signed_in else None,
            "name": stored.get("name") if signed_in else None,
            "access_expires_at": stored.get("expires_at") if signed_in else None,
            "session_expires_at": stored.get("refresh_expires_at") if signed_in else None,
            "last_refreshed_at": stored.get("refreshed_at") if signed_in else None,
            "registered": bool(stored.get("client_id")),
            "usage_paused": paused,
            "usage_message": PLAN_LIMIT if paused else None,
            "usage_url": USAGE_URL,
            "pending": bool(self.pending),
            "redirect_uri": self.redirect_uri,
            "listener_active": self.listener_active,
            "listener_error": self.listener_error,
            "last_error": stored.get("last_error"),
        }

    # -- sign-in ---------------------------------------------------------------------------
    def prune_pending(self) -> None:
        cutoff = time.time() - PENDING_TTL
        for state in [key for key, item in self.pending.items() if item.created_at < cutoff]:
            self.pending.pop(state, None)

    def start(self) -> str:
        stored = self.load()
        if not stored.get("host_id"):
            # Opaque, stable per install; never derived from the user's identity.
            stored["host_id"] = f"urn:uuid:{uuid.uuid4()}"
            self.save(stored)
        self.prune_pending()
        verifier, challenge = pkce_pair()
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        issued = stored.get("client_id")
        client_id = issued or REGISTRATION_CLIENT_ID
        params = {"client_id": client_id}
        if not issued:
            params["agent_name_hint"] = AGENT_NAME
        params["ext_agent_host_id"] = stored["host_id"]
        if issued and stored.get("id_token") and stored.get("refresh_token"):
            params["id_token_hint"] = stored["id_token"]
        if issued and stored.get("email"):
            params["login_hint"] = stored["email"]
        params.update({
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        })
        if issued and stored.get("plan_declined"):
            # Re-request plan consent only when the user previously declined it.
            params["prompt"] = "consent"
        self.pending[state] = PendingSignIn(state, verifier, nonce, self.redirect_uri, client_id)
        # Keep only the newest few attempts so stale tabs cannot accumulate.
        for old in list(self.pending)[:-3]:
            self.pending.pop(old, None)
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def complete(self, params: dict[str, str]) -> dict:
        """Validate a callback (listener or pasted) and exchange its code."""
        async with self.exchange_lock:
            self.prune_pending()
            state = params.get("state", "")
            pending = self.pending.get(state) if state else None
            if not pending or not secrets.compare_digest(pending.state, state):
                raise ChatGPTAuthError("This sign-in link is unknown or expired. Click “Sign in with ChatGPT” again and use the newest callback URL.")
            # One use per state, whether it succeeds or not.
            self.pending.pop(state, None)
            stored = self.load()
            if error := params.get("error"):
                if error == "access_denied" and stored.get("client_id"):
                    stored["plan_declined"] = True
                    self.save(stored)
                    raise ChatGPTAuthError("ChatGPT plan usage was not enabled. Sign in again and approve plan access, or use another provider.")
                raise ChatGPTAuthError(f"ChatGPT sign-in failed ({error}).")
            code = params.get("code")
            if not code:
                raise ChatGPTAuthError("The callback did not include an authorization code.")
            returned_client = params.get("client_id")
            issued = stored.get("client_id")
            if issued and returned_client and returned_client != issued:
                raise ChatGPTAuthError("ChatGPT returned a different app registration than the one saved. Sign out and sign in again.")
            if not issued:
                if not returned_client:
                    raise ChatGPTAuthError("ChatGPT registration was incomplete (no client ID returned). Try signing in again.")
                # Save the issued client before exchanging so a retry can reuse it.
                stored["client_id"] = issued = returned_client
                self.save(stored)
            async with self.client() as client:
                response = await client.post(TOKEN_URL, data={
                    "grant_type": "authorization_code",
                    "client_id": issued,
                    "code": code,
                    "code_verifier": pending.verifier,
                    "redirect_uri": pending.redirect_uri,
                    "resource": RESOURCE,
                })
            body = self.json_body(response)
            if response.status_code != 200:
                code_name, _ = error_code(body)
                if code_name == "invalid_grant":
                    raise ChatGPTAuthError("The sign-in code expired or was already used. Click “Sign in with ChatGPT” again.")
                raise ChatGPTAuthError(f"ChatGPT token exchange failed{f' ({code_name})' if code_name else ''}.")
            claims = self.validate_id_token(body.get("id_token", ""), issued, pending.nonce)
            self.store_tokens(stored, body, claims)
            if stored.get("plan_enabled"):
                stored.pop("plan_declined", None)
            else:
                stored["plan_declined"] = True
            self.save(stored)
            if not stored.get("plan_enabled"):
                raise ChatGPTAuthError("Signed in, but ChatGPT plan usage was not granted. Sign in again and approve plan access.")
            return self.status()

    @staticmethod
    def json_body(response: httpx.Response) -> dict:
        try:
            value = response.json()
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    @staticmethod
    def validate_id_token(id_token: str, client_id: str, nonce: str | None) -> dict:
        claims = decode_jwt_claims(id_token) if id_token else {}
        if not claims:
            raise ChatGPTAuthError("ChatGPT did not return a valid ID token.")
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if claims.get("iss") != ISSUER or client_id not in audiences:
            raise ChatGPTAuthError("ChatGPT returned an ID token for a different issuer or app.")
        if nonce is not None and claims.get("nonce") != nonce:
            raise ChatGPTAuthError("ChatGPT sign-in could not be verified (nonce mismatch). Try again.")
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < time.time() - 300:
            raise ChatGPTAuthError("ChatGPT returned an expired ID token. Try signing in again.")
        return claims

    def store_tokens(self, stored: dict, body: dict, claims: dict | None) -> None:
        now = time.time()
        if not body.get("access_token") or not body.get("refresh_token"):
            raise ChatGPTAuthError("ChatGPT did not return a complete token set.")
        scope = body.get("scope")
        if scope is not None:
            stored["scope"] = scope
        # Replace the access token, expiry, scopes, and rotated refresh token together.
        stored.update({
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "expires_at": int(now + int(body.get("expires_in") or 3600)),
            "refresh_expires_at": int(now + REFRESH_TOKEN_LIFETIME),
            "refreshed_at": int(now),
            "plan_enabled": PLAN_SCOPE in str(stored.get("scope", "")).split(),
        })
        if body.get("earliest_refresh_at"):
            stored["earliest_refresh_at"] = body["earliest_refresh_at"]
        if body.get("id_token"):
            stored["id_token"] = body["id_token"]
        if claims:
            stored.update({"sub": claims.get("sub"), "email": claims.get("email") or stored.get("email"), "name": claims.get("name") or stored.get("name")})
        stored.pop("last_error", None)

    # -- tokens ----------------------------------------------------------------------------
    async def access_token(self, force_refresh: bool = False) -> str:
        stored = self.load()
        if not stored.get("refresh_token") or not stored.get("client_id"):
            raise ChatGPTAuthError("Not signed in with ChatGPT. Open Settings → ChatGPT (subscription) to sign in.")
        if not stored.get("plan_enabled"):
            raise ChatGPTAuthError("ChatGPT plan usage isn't enabled for this sign-in. Sign in again and approve plan access.")
        if not force_refresh and stored.get("access_token") and stored.get("expires_at", 0) - ACCESS_REFRESH_MARGIN > time.time():
            return stored["access_token"]
        return await self.refresh(min_valid=ACCESS_REFRESH_MARGIN, previous=stored.get("access_token") if force_refresh else None)

    async def refresh(self, min_valid: float = 0, previous: str | None = None, max_age: float | None = None) -> str:
        """Refresh under a lock. Waiters reuse a refresh that completed while they waited."""
        async with self.refresh_lock:
            stored = self.load()
            refresh_token, client_id = stored.get("refresh_token"), stored.get("client_id")
            if not refresh_token or not client_id:
                raise ChatGPTAuthError(SIGN_IN_AGAIN)
            fresh = stored.get("access_token") and stored.get("expires_at", 0) - min_valid > time.time()
            recent = max_age is None or time.time() - stored.get("refreshed_at", 0) < max_age
            if fresh and recent and stored.get("access_token") != previous:
                return stored["access_token"]
            async with self.client() as client:
                response = await client.post(TOKEN_URL, data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "resource": RESOURCE,
                })
            body = self.json_body(response)
            if response.status_code != 200:
                code, _ = error_code(body)
                if code in TERMINAL_REFRESH_ERRORS:
                    self.clear_tokens(stored, "Your ChatGPT session ended. Sign in again.")
                    raise ChatGPTAuthError(SIGN_IN_AGAIN)
                if code == "invalid_client":
                    stored["last_error"] = "ChatGPT rejected Adam's app registration (invalid_client)."
                    self.save(stored)
                    raise ChatGPTAuthError("ChatGPT rejected Adam's app registration. Sign out, then sign in again.")
                # Temporary failures keep credentials so a later attempt can succeed.
                raise ChatGPTAuthError(f"Could not refresh the ChatGPT session (HTTP {response.status_code}). Try again shortly.")
            claims = decode_jwt_claims(body["id_token"]) if body.get("id_token") else None
            self.store_tokens(stored, body, claims)
            self.save(stored)
            return stored["access_token"]

    def clear_tokens(self, stored: dict, reason: str | None = None) -> None:
        for key in ("access_token", "refresh_token", "id_token", "expires_at", "refresh_expires_at", "refreshed_at", "earliest_refresh_at", "scope", "plan_enabled", "usage_paused_until"):
            stored.pop(key, None)
        if reason:
            stored["last_error"] = reason
        self.save(stored)

    async def sign_out(self) -> dict:
        async with self.refresh_lock:
            stored = self.load()
            refresh_token, client_id = stored.get("refresh_token"), stored.get("client_id")
            revoked = None
            if refresh_token and client_id:
                revoked = False
                for delay in (0, 1, 2):
                    await asyncio.sleep(delay)
                    try:
                        async with self.client(timeout=15) as client:
                            response = await client.post(REVOKE_URL, data={"token": refresh_token, "token_type_hint": "refresh_token", "client_id": client_id})
                        if response.status_code == 200:
                            revoked = True
                            break
                        if response.status_code < 500:
                            break
                    except httpx.HTTPError:
                        continue
            # Keep the issued client and host ID for a later sign-in; drop identity and hints.
            for key in ("email", "name", "sub", "last_error"):
                stored.pop(key, None)
            self.clear_tokens(stored)
        result = self.status()
        result["revoked"] = revoked
        return result

    # -- usage pause -----------------------------------------------------------------------
    def pause_for_usage_limit(self) -> None:
        stored = self.load()
        stored["usage_paused_until"] = int(time.time() + USAGE_PAUSE_SECONDS)
        self.save(stored)

    def resume_usage(self) -> dict:
        stored = self.load()
        stored.pop("usage_paused_until", None)
        self.save(stored)
        return self.status()

    def ensure_not_paused(self) -> None:
        if (self.load().get("usage_paused_until") or 0) > time.time():
            raise ChatGPTAuthError(PLAN_LIMIT)

    # -- background refresh ----------------------------------------------------------------
    async def background_refresh(self, check_interval: float = 60 * 60) -> None:
        while True:
            try:
                stored = self.load()
                if stored.get("refresh_token") and stored.get("client_id"):
                    await self.refresh(min_valid=ACCESS_REFRESH_MARGIN, max_age=BACKGROUND_REFRESH_INTERVAL)
            except ChatGPTAuthError as exc:
                logger.warning("ChatGPT background refresh failed: %s", exc)
            except Exception:
                logger.warning("ChatGPT background refresh failed", exc_info=True)
            await asyncio.sleep(check_interval)

    # -- loopback callback listener --------------------------------------------------------
    async def start_listener(self, host: str, port: int) -> asyncio.base_events.Server | None:
        try:
            server = await asyncio.start_server(self.handle_callback_connection, host, port)
        except OSError as exc:
            self.listener_active = False
            self.listener_error = f"Port {port} is unavailable ({exc.strerror or exc}). Use the paste-the-callback-URL option."
            logger.warning("ChatGPT callback listener could not bind %s:%s: %s", host, port, exc)
            return None
        self.listener_active, self.listener_error = True, None
        return server

    async def handle_callback_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, title, message = 404, "Not found", "This address only handles Adam's ChatGPT sign-in callback."
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
            method, target, _ = request_line.split(" ", 2)
            parts = urlsplit(target)
            if method == "GET" and parts.path == CALLBACK_PATH:
                try:
                    params = {key: values[0] for key, values in parse_qs(parts.query, keep_blank_values=True).items() if values}
                    result = await self.complete(params)
                    who = f" as {result['email']}" if result.get("email") else ""
                    status, title, message = 200, "Signed in", f"Adam is connected to ChatGPT{who}. You can close this tab and return to Adam."
                except ChatGPTAuthError as exc:
                    status, title, message = 400, "Sign-in not completed", str(exc)
                except httpx.HTTPError:
                    status, title, message = 502, "Sign-in not completed", "Adam could not reach ChatGPT to finish signing in. Try again."
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            status, title, message = 400, "Bad request", "Malformed request."
        page = (f"<!doctype html><html><head><meta charset='utf-8'><title>Adam · {title}</title></head>"
                f"<body style=\"font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#2b2a27\">"
                f"<h1 style='font-size:1.4rem'>{title}</h1><p>{_escape(message)}</p></body></html>").encode("utf-8")
        reasons = {200: "OK", 400: "Bad Request", 404: "Not Found", 502: "Bad Gateway"}
        writer.write(f"HTTP/1.1 {status} {reasons[status]}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(page)}\r\nCache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n".encode("ascii") + page)
        try:
            await writer.drain()
        finally:
            writer.close()


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
