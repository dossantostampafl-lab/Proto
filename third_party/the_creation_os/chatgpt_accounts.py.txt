from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.inference.chatgpt_connect import ISSUER, _config_root, _profile_credentials_path, _validate_profile_name

OIDC_CONFIGURATION_URL = f"{ISSUER}/.well-known/openid-configuration"


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid ChatGPT profile document: {path}")
    return value


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def profile_summaries() -> list[dict[str, object]]:
    profiles_root = _config_root() / "profiles"
    if not profiles_root.exists():
        return []
    results: list[dict[str, object]] = []
    for path in sorted(profiles_root.glob("*/credentials.json")):
        data = _load(path)
        scopes = data.get("scopes", [])
        plan_enabled = (
            "chatgpt.tokens.use.direct" in scopes
            if isinstance(scopes, list)
            else "chatgpt.tokens.use.direct" in str(scopes).split()
        )
        results.append(
            {
                "profile": path.parent.name,
                "account": str(data.get("email") or data.get("subject") or "unknown"),
                "registered": bool(data.get("client_id")),
                "plan_usage_enabled": bool(plan_enabled and data.get("access_token")),
            }
        )
    return results


def _clear_local_tokens(path: Path, data: dict[str, Any], *, revocation_confirmed: bool) -> None:
    retained = {
        key: data[key]
        for key in ("issuer", "client_id", "email", "subject", "ext_agent_host_id")
        if data.get(key)
    }
    retained.update(
        {
            "plan_usage_enabled": False,
            "signed_out_at": datetime.now(timezone.utc).isoformat(),
            "remote_revocation_confirmed": revocation_confirmed,
        }
    )
    _write(path, retained)


def _revoke_remote(data: dict[str, Any], *, timeout_seconds: float = 15.0) -> bool:
    refresh_token = str(data.get("refresh_token") or "")
    client_id = str(data.get("client_id") or "")
    if not refresh_token or not client_id:
        return False
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                discovery = client.get(OIDC_CONFIGURATION_URL)
                discovery.raise_for_status()
                payload = discovery.json()
                endpoint = payload.get("revocation_endpoint") if isinstance(payload, dict) else None
                if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
                    return False
                response = client.post(
                    endpoint,
                    data={
                        "token": refresh_token,
                        "token_type_hint": "refresh_token",
                        "client_id": client_id,
                    },
                )
            if response.status_code == 200:
                return True
            if response.status_code < 500:
                return False
        except (httpx.HTTPError, ValueError):
            pass
        if attempt < 2:
            time.sleep(0.5 * (2**attempt))
    return False


def sign_out(profile: str) -> bool:
    name = _validate_profile_name(profile)
    path = _profile_credentials_path(name)
    data = _load(path)
    if not data:
        raise RuntimeError(f"ChatGPT profile does not exist: {name}")
    confirmed = _revoke_remote(data)
    _clear_local_tokens(path, data, revocation_confirmed=confirmed)
    return confirmed


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage local Sign in with ChatGPT profiles.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List local profile labels without printing credentials.")
    signout = subparsers.add_parser("sign-out", help="Revoke the refresh token when possible and clear local bearer tokens.")
    signout.add_argument("--profile", default="default")
    args = parser.parse_args()

    if args.command == "list":
        summaries = profile_summaries()
        if not summaries:
            print("No local ChatGPT profiles.")
            return
        for item in summaries:
            state = "plan enabled" if item["plan_usage_enabled"] else "signed out / plan disabled"
            print(f'{item["profile"]}: {item["account"]} ({state})')
        return

    try:
        confirmed = sign_out(args.profile)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print("Local ChatGPT bearer credentials cleared.")
    if confirmed:
        print("Remote refresh-token revocation confirmed.")
    else:
        print(
            "Remote revocation could not be confirmed. "
            "The local credentials are cleared; use ChatGPT settings to disconnect the app remotely if needed."
        )


if __name__ == "__main__":
    main()
