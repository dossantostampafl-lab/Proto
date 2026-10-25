from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import threading
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from jose import jwt

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
JWKS_URL = "https://auth.openai.com/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
AGENT_NAME = "THE CREATION OS"


def _config_root() -> Path:
    return Path.home() / ".config" / "the-creation-os" / "chatgpt"


def _validate_profile_name(value: str) -> str:
    normalized = value.strip()
    if not normalized or normalized in {".", ".."}:
        raise ValueError("ChatGPT profile name must not be empty")
    if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in normalized):
        raise ValueError("ChatGPT profile name may contain only letters, numbers, dot, underscore, and hyphen")
    return normalized


def _profile_credentials_path(profile: str = "default") -> Path:
    return _config_root() / "profiles" / _validate_profile_name(profile) / "credentials.json"


def _default_credentials_path() -> Path:
    return _profile_credentials_path("default")


def _inside_git_checkout(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    for directory in (resolved.parent, *resolved.parents):
        if (directory / ".git").exists():
            return True
    return False


def _storage_root_for(output: Path) -> Path:
    # Default profile layout: <root>/profiles/<profile>/credentials.json.
    for parent in output.parents:
        if parent.name == "profiles":
            return parent.parent
    return output.parent

def _b64url_sha256(value: str) -> str:
    digest = hashlib.sha256(value.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _random_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")


def _load_existing(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def _host_id_path(output: Path) -> Path:
    return _storage_root_for(output) / "host-id"


def _persist_host_id(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _load_or_create_host_id(output: Path, credentials: dict[str, object]) -> str:
    path = _host_id_path(output)
    credential_host_id = str(credentials.get("ext_agent_host_id") or "").strip()
    if credential_host_id:
        # A saved profile can intentionally represent a remote/self-hosted runtime.
        # Reuse its binding without overwriting this computer's own shared host identity.
        return credential_host_id
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = "urn:uuid:" + str(uuid.uuid4())
    _persist_host_id(path, value)
    return value


def _granted_scopes(tokens: dict[str, object]) -> set[str]:
    token_scope = tokens.get("scope")
    if not isinstance(token_scope, str) or not token_scope.strip():
        raise RuntimeError("ChatGPT token exchange did not return granted scopes")
    return {item for item in token_scope.split() if item}


def _validate_id_token(
    id_token: str, *, client_id: str, nonce: str, access_token: str
) -> dict[str, object]:
    with httpx.Client(timeout=15.0) as client:
        response = client.get(JWKS_URL)
        response.raise_for_status()
        jwks = response.json()
    header = jwt.get_unverified_header(id_token)
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not isinstance(keys, list):
        raise RuntimeError("OpenAI JWKS response did not contain keys")
    key = next(
        (
            item
            for item in keys
            if isinstance(item, dict) and item.get("kid") == header.get("kid")
        ),
        None,
    )
    if key is None:
        raise RuntimeError("OpenAI ID token signing key was not found")
    claims = jwt.decode(
        id_token,
        key,
        algorithms=[str(header.get("alg") or "RS256")],
        audience=client_id,
        issuer=ISSUER,
        access_token=access_token,
        options={"require_sub": True, "require_exp": True, "require_iat": True},
    )
    if claims.get("nonce") != nonce:
        raise RuntimeError("OpenAI ID token nonce did not match the authorization attempt")
    if not claims.get("sub"):
        raise RuntimeError("OpenAI ID token did not contain a subject")
    return dict(claims)


class _CallbackHandler(BaseHTTPRequestHandler):
    result: dict[str, str] | None = None
    event = threading.Event()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/auth/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = parse_qs(parsed.query, keep_blank_values=True)
        type(self).result = {
            key: values[-1]
            for key, values in query.items()
            if values
        }
        type(self).event.set()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>ChatGPT connected.</h2>"
            b"<p>You can close this browser tab and return to THE CREATION OS.</p>"
            b"</body></html>"
        )

    def log_message(self, format: str, *args: object) -> None:
        return


def _save(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def connect(
    *,
    output: Path,
    port: int,
    no_browser: bool,
    force_new: bool,
    enable_plan: bool = False,
    host_id_override: str | None = None,
) -> None:
    saved_credentials = _load_existing(output)
    if force_new and saved_credentials:
        raise RuntimeError(
            "Refusing to replace an existing ChatGPT registration. "
            "Use a different --profile for another account or workspace."
        )
    existing = saved_credentials
    saved_client_id = str(existing.get("client_id") or "").strip()
    client_id = saved_client_id or DYNAMIC_CLIENT_ID
    # The host identity belongs to this installation, not to one account/client.
    # Persist it before opening the browser so cancelled/failed first sign-ins
    # reuse the same identity. A self-hosted VM can supply its own persisted ID
    # when a later reauthorization is performed locally on the VM's behalf.
    if host_id_override:
        host_id = host_id_override.strip()
        if not (
            host_id.startswith("urn:uuid:")
            or host_id.startswith("urn:ietf:params:oauth:jwk-thumbprint:")
            or host_id.startswith("did:key:")
        ):
            raise RuntimeError("Unsupported ext_agent_host_id format")
        # An override can represent a remote VM. It belongs to this authorization/profile,
        # not to the browser computer's shared local host-id file.
    else:
        host_id = _load_or_create_host_id(output, saved_credentials)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = _random_verifier()
    redirect_uri = f"http://127.0.0.1:{port}/auth/callback"

    params = {
        "client_id": client_id,
        "ext_agent_host_id": host_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "resource": RESOURCE,
        "state": state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": _b64url_sha256(verifier),
    }
    contains_id_token_hint = False
    if client_id == DYNAMIC_CLIENT_ID:
        params["agent_name_hint"] = AGENT_NAME
    else:
        retained_id_token = str(existing.get("id_token") or "").strip()
        email = str(existing.get("email") or "").strip()
        # Returning authorization stays bound to the selected saved registration.
        # id_token_hint is credential-bearing, so only place it in a URL opened
        # directly by this process; never print that URL to a terminal.
        if retained_id_token and not no_browser:
            params["id_token_hint"] = retained_id_token
            contains_id_token_hint = True
        if email:
            params["login_hint"] = email
        if enable_plan:
            # The user explicitly asked to enable plan usage after an earlier decline.
            # OpenAI currently supports prompt=consent for this reauthorization path.
            params["prompt"] = "consent"

    url = AUTHORIZE_URL + "?" + urlencode(params)

    _CallbackHandler.result = None
    _CallbackHandler.event.clear()
    server = ThreadingHTTPServer(("127.0.0.1", port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if contains_id_token_hint:
            print(
                "Opening ChatGPT authorization in the browser. "
                "The authorization URL is intentionally not printed because it contains an ID-token hint."
            )
        else:
            print("Open this URL in the browser to connect ChatGPT:")
            print(url)
        if not no_browser:
            webbrowser.open(url)
        if not _CallbackHandler.event.wait(timeout=600):
            raise RuntimeError("Timed out waiting for the ChatGPT authorization callback")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    callback: dict[str, str] = _CallbackHandler.result or {}
    if callback.get("state") != state:
        raise RuntimeError("OAuth state did not match")
    if callback.get("error"):
        raise RuntimeError("ChatGPT authorization was not granted: " + callback["error"])
    code = callback.get("code")
    if not code:
        raise RuntimeError("ChatGPT authorization did not return a code")

    callback_client_id = callback.get("client_id")
    if client_id == DYNAMIC_CLIENT_ID:
        issued_client_id = str(callback_client_id or "").strip()
        if not issued_client_id or issued_client_id == DYNAMIC_CLIENT_ID:
            raise RuntimeError("Dynamic ChatGPT registration did not return an issued client_id")
    else:
        issued_client_id = client_id
        if callback_client_id and callback_client_id != issued_client_id:
            raise RuntimeError("ChatGPT callback returned a different client_id")

    # On first dynamic registration, retain the issued client ID before exchanging
    # the authorization code. If the code exchange fails (for example invalid_grant),
    # the next attempt must reuse this issued client rather than register another one.
    if client_id == DYNAMIC_CLIENT_ID:
        _save(
            output,
            {
                "issuer": ISSUER,
                "client_id": issued_client_id,
                "ext_agent_host_id": host_id,
            },
        )

    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": issued_client_id,
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
                "resource": RESOURCE,
            },
        )
        if response.status_code >= 400:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = {}
            error_code = (
                str(error_payload.get("error") or error_payload.get("code") or "oauth_error")
                if isinstance(error_payload, dict)
                else "oauth_error"
            )
            suffix = " Start authorization again with the retained issued client_id." if error_code == "invalid_grant" else ""
            raise RuntimeError(
                f"ChatGPT token exchange failed with HTTP {response.status_code}: {error_code}.{suffix}"
            )
        tokens = response.json()
        if not isinstance(tokens, dict):
            raise RuntimeError("ChatGPT token exchange returned an invalid response")

    id_token = str(tokens.get("id_token") or "")
    access_token = str(tokens.get("access_token") or "")
    refresh_token = str(tokens.get("refresh_token") or "")
    scopes = _granted_scopes(tokens)
    if not id_token or not access_token or not refresh_token:
        raise RuntimeError("ChatGPT token exchange returned incomplete credentials")

    claims = _validate_id_token(
        id_token, client_id=issued_client_id, nonce=nonce, access_token=access_token
    )
    existing_subject = str(existing.get("subject") or "").strip()
    if existing_subject and str(claims.get("sub") or "") != existing_subject:
        raise RuntimeError(
            "ChatGPT reauthorization returned a different account identity; "
            "the saved registration was not replaced"
        )
    document: dict[str, object] = {
        "email": claims.get("email"),
        "issuer": ISSUER,
        "subject": claims["sub"],
        "client_id": issued_client_id,
        "ext_agent_host_id": host_id,
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": str(tokens.get("token_type") or "Bearer"),
        "expires_in": int(tokens.get("expires_in") or 3600),
        "earliest_refresh_at": tokens.get("earliest_refresh_at"),
        "scopes": sorted(scopes),
        "plan_usage_enabled": "chatgpt.tokens.use.direct" in scopes,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    _save(output, document)
    plan_enabled = "chatgpt.tokens.use.direct" in scopes
    print(f"ChatGPT connection saved securely to: {output}")
    print(f"Plan usage permission: {'enabled' if plan_enabled else 'disabled'}")
    if not plan_enabled:
        print(
            "Sign-in was retained, but ChatGPT plan usage was not granted. "
            "Run this command again with --enable-plan to request consent."
        )
    print("No token value was printed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Connect THE CREATION OS to a ChatGPT account with OAuth.")
    parser.add_argument(
        "--profile",
        default="default",
        help="Local ChatGPT account/workspace profile name. Use a different name for another registration.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Advanced: protected credential file path. Defaults to the selected profile outside source control.",
    )
    parser.add_argument("--port", type=int, default=1455)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--new",
        action="store_true",
        help="Register a new ChatGPT client. Existing profiles are never overwritten; choose another --profile.",
    )
    parser.add_argument(
        "--enable-plan",
        action="store_true",
        help="Explicitly request consent again for ChatGPT plan usage on a saved registration.",
    )
    parser.add_argument(
        "--host-id",
        default=None,
        help="Advanced/self-hosted recovery: use an existing VM ext_agent_host_id for this authorization.",
    )
    args = parser.parse_args()
    try:
        profile = _validate_profile_name(args.profile)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    output = (args.output or _profile_credentials_path(profile)).expanduser().resolve()
    if _inside_git_checkout(output):
        raise SystemExit(
            "Refusing to store ChatGPT OAuth credentials inside a Git checkout. "
            "Choose a protected path outside source control."
        )
    connect(
        output=output,
        port=args.port,
        no_browser=args.no_browser,
        force_new=args.new,
        enable_plan=args.enable_plan,
        host_id_override=args.host_id,
    )


if __name__ == "__main__":
    main()
