"""OAuth 2.1 for the read-only MCP server, so ChatGPT's connector can sign in (chatgpt-mcp.md).

ChatGPT connectors reliably offer OAuth or no authentication; a static bearer field is not
available in every release. This is the minimum authorization server the MCP authorization
spec asks for, for one operator:

- discovery: ``/.well-known/oauth-protected-resource`` and
  ``/.well-known/oauth-authorization-server`` (issuer = ``PUBLIC_ORIGIN``);
- dynamic client registration (RFC 7591) for public clients whose redirect URIs are HTTPS on
  ChatGPT's own hosts only;
- ``/oauth/authorize``: a consent page where the operator approves by typing
  ``MCP_API_TOKEN``; the code is single-use, lives 60 s and is bound to client, redirect URI,
  PKCE S256 challenge and resource;
- ``/oauth/token``: authorization code (PKCE verified) and rotating refresh tokens.

The only scope is ``proto.read``: a token can call the same read-only tools as the static
token, nothing else. Tokens are random and kept only as SHA-256 digests, also on the
control-plane store so a restart keeps ChatGPT signed in (rotating MCP_API_TOKEN signs every
connector out), and are revocable at once with ``POST /v1/mcp/oauth/revoke`` (operator token).
Everything is closed unless ``MCP_ENABLED``, ``MCP_API_TOKEN`` and an HTTPS ``PUBLIC_ORIGIN``
are set. Every grant, refusal and revocation is journaled.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import hmac
import html
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .event_state import JournalUnavailable, record_operational_event
from .security import SlidingWindowRateLimiter, request_client_key
from .settings import settings

logger = logging.getLogger("proto.mcp.oauth")

SCOPE = "proto.read"
OAUTH_PREFIX = "/oauth/"
AUTHORIZE_PATH = "/oauth/authorize"
# ChatGPT's connector callbacks live on these hosts; nothing else may receive a code.
ALLOWED_REDIRECT_HOSTS = frozenset({"chatgpt.com", "chat.openai.com"})
CODE_TTL = timedelta(seconds=60)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
MAX_CLIENTS = 20
MAX_TOKENS = 200
_MAX_FORM_BYTES = 8 * 1024
_BOUNDARY = {"financial_connectivity": False, "real_money_execution": False}

router = APIRouter(tags=["mcp"])
approval_limiter = SlidingWindowRateLimiter(limit=5, window_seconds=60.0)
endpoint_limiter = SlidingWindowRateLimiter(limit=60, window_seconds=60.0)


@dataclass(frozen=True, slots=True)
class _Client:
    client_id: str
    redirect_uris: tuple[str, ...]
    name: str


@dataclass(frozen=True, slots=True)
class _Code:
    client_id: str
    redirect_uri: str
    challenge: str
    resource: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _Grant:
    client_id: str
    kind: str  # "access" | "refresh"
    expires_at: datetime


_clients: dict[str, _Client] = {}
_codes: dict[str, _Code] = {}
_grants: dict[str, _Grant] = {}  # sha256(token) -> grant


OAUTH_STATE_KEY = "mcp_oauth"
_persist_lock = asyncio.Lock()


_FINGERPRINT_SALT = b"proto-mcp-oauth-fingerprint/1"
_FINGERPRINT_ITERATIONS = 200_000


@functools.lru_cache(maxsize=4)
def _derive_fingerprint(secret: str) -> str:
    # A slow key derivation, not a plain hash: the value is stored, and must not be a cheap way
    # to test guesses of MCP_API_TOKEN.
    return hashlib.pbkdf2_hmac(
        "sha256", secret.encode(), _FINGERPRINT_SALT, _FINGERPRINT_ITERATIONS
    ).hex()


def _token_fingerprint() -> str:
    # Persisted grants belong to one MCP_API_TOKEN: rotating it signs every connector out.
    return _derive_fingerprint(settings.mcp_api_token or "")


def _document() -> dict[str, Any]:
    return {
        "version": 1,
        "token_fingerprint": _token_fingerprint(),
        "clients": [
            {"client_id": c.client_id, "redirect_uris": list(c.redirect_uris), "name": c.name}
            for c in _clients.values()
        ],
        # Only SHA-256 digests of tokens are stored, never a usable token.
        "grants": {
            key: {"client_id": g.client_id, "kind": g.kind, "expires_at": g.expires_at.isoformat()}
            for key, g in _grants.items()
        },
    }


async def _persist() -> None:
    """Save clients and token digests on the control-plane store, so a deploy's restart does not
    sign ChatGPT out. A store failure only loses that durability: it never blocks sign-in and
    never touches the risk path (the control store's fail-closed flag is left alone)."""

    from services.risk import SqlControlState

    from . import control_state

    store = control_state.current()
    if store.engine is None:
        return
    _prune()
    async with _persist_lock:
        try:
            await SqlControlState(store.engine).put(OAUTH_STATE_KEY, _document())
        except Exception:
            logger.warning("MCP OAuth state was not persisted", exc_info=True)


async def restore_oauth() -> None:
    """Load persisted clients and grants once at start-up (only for the current MCP token)."""

    from services.risk import SqlControlState

    from . import control_state

    store = control_state.current()
    if store.engine is None or not settings.mcp_enabled:
        return
    try:
        saved = await SqlControlState(store.engine).get(OAUTH_STATE_KEY)
    except Exception:
        logger.warning("MCP OAuth state could not be restored", exc_info=True)
        return
    if not saved or saved.get("token_fingerprint") != _token_fingerprint():
        return
    now = _now()
    for raw in saved.get("clients") or ():
        uris = tuple(u for u in raw.get("redirect_uris") or () if isinstance(u, str))
        if raw.get("client_id") and uris and all(_allowed_redirect(u) for u in uris):
            client = _Client(str(raw["client_id"]), uris, str(raw.get("name") or "ChatGPT")[:80])
            _clients.setdefault(client.client_id, client)
    for key, raw in (saved.get("grants") or {}).items():
        try:
            grant = _Grant(
                str(raw["client_id"]), str(raw["kind"]), datetime.fromisoformat(raw["expires_at"])
            )
        except (KeyError, TypeError, ValueError):
            continue
        if (
            grant.kind in ("access", "refresh")
            and grant.client_id in _clients
            and (grant.expires_at > now)
        ):
            _grants.setdefault(str(key), grant)


def reset_oauth_state() -> None:
    _clients.clear()
    _codes.clear()
    _grants.clear()
    approval_limiter.reset()
    endpoint_limiter.reset()


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issuer() -> str | None:
    """The HTTPS public origin, or ``None`` when OAuth cannot be offered."""

    origin = (settings.public_origin or "").rstrip("/")
    if not origin.startswith("https://") or urlsplit(origin).path not in ("", "/"):
        return None
    return origin


def available() -> bool:
    return bool(settings.mcp_enabled and settings.mcp_api_token and issuer())


def resource_url() -> str:
    return f"{issuer()}/mcp"


def resource_metadata_url() -> str:
    return f"{issuer()}/.well-known/oauth-protected-resource"


def valid_access_token(token: str) -> bool:
    if not available() or not token:
        return False
    grant = _grants.get(_digest(token))
    return grant is not None and grant.kind == "access" and grant.expires_at > _now()


async def _journal(event_type: str, payload: dict[str, Any]) -> None:
    try:
        await record_operational_event(
            event_type=event_type, source="mcp-oauth", payload={**payload, **_BOUNDARY}
        )
    except JournalUnavailable:
        logger.warning("%s was not journaled", event_type)


def _not_found() -> JSONResponse:
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _prune() -> None:
    now = _now()
    for store in (_codes, _grants):
        for key in [k for k, v in store.items() if v.expires_at <= now]:
            del store[key]


def _allowed_redirect(uri: str) -> bool:
    parts = urlsplit(uri)
    return (
        parts.scheme == "https"
        and parts.hostname in ALLOWED_REDIRECT_HOSTS
        and parts.port is None
        and not parts.username
        and not parts.password
        and not parts.fragment
        and len(uri) <= 512
    )


async def _form(request: Request) -> dict[str, str] | None:
    declared = request.headers.get("Content-Length")
    if declared is not None and (not declared.isdigit() or int(declared) > _MAX_FORM_BYTES):
        return None
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > _MAX_FORM_BYTES:
            return None
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=False)
    except UnicodeDecodeError:
        return None
    form: dict[str, str] = {}
    for key, value in pairs:
        if key in form:  # duplicated parameters are refused (RFC 6749 §3.1)
            return None
        form[key] = value
    return form


# -- discovery -------------------------------------------------------------------------------


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata() -> Response:
    if not available():
        return _not_found()
    return JSONResponse(
        {
            "resource": resource_url(),
            "authorization_servers": [issuer()],
            "scopes_supported": [SCOPE],
            "bearer_methods_supported": ["header"],
            "resource_name": "PROTO FinRobots (read-only)",
        }
    )


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata() -> Response:
    if not available():
        return _not_found()
    base = issuer()
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}{AUTHORIZE_PATH}",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [SCOPE],
        }
    )


# -- registration ----------------------------------------------------------------------------


@router.post("/oauth/register")
async def register_client(request: Request) -> Response:
    if not available():
        return _not_found()
    if not endpoint_limiter.allow(f"register:{request_client_key(request)}"):
        return _oauth_error("slow_down", "too many requests", 429)
    try:
        body = await request.json()
    except ValueError:
        return _oauth_error("invalid_client_metadata", "body must be JSON")
    if not isinstance(body, dict):
        return _oauth_error("invalid_client_metadata", "body must be an object")
    uris = body.get("redirect_uris")
    if (
        not isinstance(uris, list)
        or not 1 <= len(uris) <= 5
        or not all(isinstance(u, str) and _allowed_redirect(u) for u in uris)
    ):
        return _oauth_error(
            "invalid_redirect_uri", "redirect_uris must be HTTPS callbacks on ChatGPT's hosts"
        )
    method = body.get("token_endpoint_auth_method", "none")
    if method != "none":
        return _oauth_error("invalid_client_metadata", "only public clients (none) are supported")
    if len(_clients) >= MAX_CLIENTS:
        return _oauth_error("invalid_client_metadata", "client registry is full", 403)
    name = str(body.get("client_name") or "ChatGPT")[:80]
    client = _Client(secrets.token_urlsafe(24), tuple(uris), name)
    _clients[client.client_id] = client
    await _persist()
    await _journal("MCP_OAUTH_CLIENT_REGISTERED", {"client_id": client.client_id, "name": name})
    return JSONResponse(
        {
            "client_id": client.client_id,
            "client_id_issued_at": int(_now().timestamp()),
            "redirect_uris": list(client.redirect_uris),
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": name,
            "scope": SCOPE,
        },
        status_code=201,
    )


# -- authorization ---------------------------------------------------------------------------


def _check_request(params: dict[str, str]) -> tuple[_Client | None, str | None]:
    """The client, or the reason the request cannot even be shown to the operator."""

    client = _clients.get(params.get("client_id", ""))
    if client is None:
        return None, "unknown client"
    if params.get("redirect_uri") not in client.redirect_uris:
        return None, "redirect_uri is not registered for this client"
    return client, None


def _request_problem(params: dict[str, str]) -> str | None:
    if params.get("response_type") != "code":
        return "unsupported_response_type"
    challenge = params.get("code_challenge", "")
    if params.get("code_challenge_method") != "S256" or not 43 <= len(challenge) <= 128:
        return "invalid_request"
    if any(scope != SCOPE for scope in params.get("scope", SCOPE).split()):
        return "invalid_scope"
    if params.get("resource", resource_url()).rstrip("/") != resource_url():
        return "invalid_target"
    return None


def _redirect(uri: str, **query: str) -> RedirectResponse:
    separator = "&" if urlsplit(uri).query else "?"
    return RedirectResponse(f"{uri}{separator}{urlencode(query)}", status_code=302)


_FIELDS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "state",
    "scope",
    "resource",
)


def _consent_page(client: _Client, params: dict[str, str], error: str = "") -> HTMLResponse:
    hidden = "".join(
        f'<input type="hidden" name="{name}" value="{html.escape(params[name])}">'
        for name in _FIELDS
        if name in params
    )
    note = f'<p class="err">{html.escape(error)}</p>' if error else ""
    page = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>PROTO · autorizar</title>
<style>body{{font:16px system-ui;background:#0b0f14;color:#e6e9ee;max-width:32rem;margin:2rem auto;
padding:0 16px}}input[type=password]{{width:100%;padding:.6rem;margin:.5rem 0}}
button{{padding:.6rem 1rem;margin-right:.5rem}}.err{{color:#f87171}}small{{color:#94a3b8}}</style>
</head><body><h1>Autorizar acesso de leitura</h1>
<p><b>{html.escape(client.name)}</b> pede acesso <b>somente leitura</b> ao PROTO: FinRobots,
council, decisões dos agentes e estado do Risk Engine.</p>
<p>Nenhuma ferramenta pode operar, mudar limites ou kill switches. Dinheiro real não está
conectado (PAPER/TESTNET).</p>{note}
<form method="post" action="{AUTHORIZE_PATH}">{hidden}
<label>MCP_API_TOKEN
<input type="password" name="approval_token" autocomplete="off" required></label>
<button type="submit" name="decision" value="approve">Autorizar</button>
<button type="submit" name="decision" value="deny">Recusar</button></form>
<small>Retorno para {html.escape(urlsplit(params["redirect_uri"]).hostname or "")}.</small>
</body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@router.get(AUTHORIZE_PATH)
async def authorize_page(request: Request) -> Response:
    if not available():
        return _not_found()
    params = {k: v for k, v in request.query_params.items() if k in _FIELDS}
    if len(request.query_params.getlist("client_id")) > 1:
        return _oauth_error("invalid_request", "duplicated parameters")
    client, reason = _check_request(params)
    if client is None:
        # Never redirect to an unverified URI: show the error here instead.
        return _oauth_error("invalid_request", reason or "invalid request")
    problem = _request_problem(params)
    if problem:
        return _redirect(params["redirect_uri"], error=problem, state=params.get("state", ""))
    return _consent_page(client, params)


@router.post(AUTHORIZE_PATH)
async def authorize_decision(request: Request) -> Response:
    if not available():
        return _not_found()
    form = await _form(request)
    if form is None:
        return _oauth_error("invalid_request", "malformed form")
    params = {k: v for k, v in form.items() if k in _FIELDS}
    client, reason = _check_request(params)
    if client is None:
        return _oauth_error("invalid_request", reason or "invalid request")
    problem = _request_problem(params)
    state = params.get("state", "")
    if problem:
        return _redirect(params["redirect_uri"], error=problem, state=state)
    if form.get("decision") != "approve":
        await _journal("MCP_OAUTH_DENIED", {"client_id": client.client_id, "reason": "operator"})
        return _redirect(params["redirect_uri"], error="access_denied", state=state)
    if not approval_limiter.allow(f"approve:{request_client_key(request)}"):
        return _consent_page(client, params, "Muitas tentativas. Aguarde um minuto.")
    supplied = form.get("approval_token", "")
    expected = settings.mcp_api_token or ""
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        await _journal("MCP_OAUTH_DENIED", {"client_id": client.client_id, "reason": "token"})
        return _consent_page(client, params, "Token incorreto.")
    _prune()
    code = secrets.token_urlsafe(32)
    _codes[_digest(code)] = _Code(
        client_id=client.client_id,
        redirect_uri=params["redirect_uri"],
        challenge=params["code_challenge"],
        resource=resource_url(),
        expires_at=_now() + CODE_TTL,
    )
    await _journal("MCP_OAUTH_APPROVED", {"client_id": client.client_id})
    return _redirect(params["redirect_uri"], code=code, state=state)


# -- tokens ----------------------------------------------------------------------------------


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def _issue(client_id: str) -> JSONResponse:
    _prune()
    if len(_grants) >= MAX_TOKENS:
        return _oauth_error("temporarily_unavailable", "too many active tokens", 503)
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = _now()
    _grants[_digest(access)] = _Grant(client_id, "access", now + ACCESS_TTL)
    _grants[_digest(refresh)] = _Grant(client_id, "refresh", now + REFRESH_TTL)
    await _persist()
    await _journal("MCP_OAUTH_TOKEN_ISSUED", {"client_id": client_id})
    return JSONResponse(
        {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": int(ACCESS_TTL.total_seconds()),
            "refresh_token": refresh,
            "scope": SCOPE,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/oauth/token")
async def token(request: Request) -> Response:
    if not available():
        return _not_found()
    if not endpoint_limiter.allow(f"token:{request_client_key(request)}"):
        return _oauth_error("slow_down", "too many requests", 429)
    form = await _form(request)
    if form is None:
        return _oauth_error("invalid_request", "malformed form")
    client = _clients.get(form.get("client_id", ""))
    if client is None:
        return _oauth_error("invalid_client", "unknown client", 401)
    grant_type = form.get("grant_type")
    if grant_type == "authorization_code":
        code = _codes.pop(_digest(form.get("code", "")), None)  # single use, even on failure
        verifier = form.get("code_verifier", "")
        if (
            code is None
            or code.expires_at <= _now()
            or code.client_id != client.client_id
            or code.redirect_uri != form.get("redirect_uri")
            or not 43 <= len(verifier) <= 128
            or not verifier.isascii()
            or not hmac.compare_digest(_s256(verifier), code.challenge)
            or form.get("resource", code.resource).rstrip("/") != code.resource
        ):
            return _oauth_error("invalid_grant", "code is invalid, expired or already used")
        return await _issue(client.client_id)
    if grant_type == "refresh_token":
        key = _digest(form.get("refresh_token", ""))
        grant = _grants.pop(key, None)  # rotation: the old refresh token dies now
        if (
            grant is None
            or grant.kind != "refresh"
            or grant.client_id != client.client_id
            or grant.expires_at <= _now()
        ):
            return _oauth_error("invalid_grant", "refresh token is invalid or expired")
        return await _issue(client.client_id)
    return _oauth_error("unsupported_grant_type", "use authorization_code or refresh_token")


@router.post("/v1/mcp/oauth/revoke")
async def revoke_all() -> dict[str, object]:
    """Operator route (operator token): sign every ChatGPT connector out and forget clients."""

    counts = {"clients": len(_clients), "tokens": len(_grants), "codes": len(_codes)}
    _clients.clear()
    _codes.clear()
    _grants.clear()
    await _persist()
    await _journal("MCP_OAUTH_REVOKED", counts)
    return {"revoked": counts, **_BOUNDARY}


@router.get("/v1/mcp/oauth/status")
def oauth_status() -> dict[str, object]:
    _prune()
    return {
        "mcp_enabled": settings.mcp_enabled,
        "static_token_configured": settings.mcp_api_token is not None,
        "available": available(),
        "issuer": issuer(),
        "clients": len(_clients),
        "active_access_tokens": sum(1 for g in _grants.values() if g.kind == "access"),
        "scope": SCOPE,
        **_BOUNDARY,
    }
