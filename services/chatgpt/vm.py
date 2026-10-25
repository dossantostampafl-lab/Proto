"""Prepare/import PROTO's own OAuth session on a private self-hosted VM.

OAuth runs on the browser computer. Import transfers that same user's PROTO
registration over SSH; it neither registers another app nor grants consent.
"""

from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path

from .chatgpt_connect import (
    DYNAMIC_CLIENT_ID,
    ISSUER,
    _load_existing,
    _load_or_create_host_id,
    _profile_credentials_path,
)
from .chatgpt_credentials import ChatGPTCredentialStore
from .storage import check_path, read_private, save_private


def prepare(profile: str) -> str:
    path = check_path(_profile_credentials_path(profile))
    # Never derive the VM's identity from an imported laptop record.
    return _load_or_create_host_id(path, {})


def import_session(profile: str, source: Path) -> None:
    target = check_path(_profile_credentials_path(profile))
    source = check_path(source)
    if source == target:
        raise ValueError("Use a separate private transfer file")
    incoming = read_private(source)
    if incoming.get("application") != "proto" or incoming.get("issuer") != ISSUER:
        raise ValueError("Only this user's PROTO registration can be imported")
    if incoming.get("client_id") in {None, "", DYNAMIC_CLIENT_ID}:
        raise ValueError("An issued client registration is required")
    # Validates structure, expiry metadata and granted plan permission. No network
    # claim is made here; the dashboard's model check verifies live access later.
    record = ChatGPTCredentialStore(str(source))._read()
    if not record.subject:
        raise ValueError("Verified account identity is missing")
    host_id = prepare(profile)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(
        target.with_suffix(target.suffix + ".lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        existing = _load_existing(target)
        if existing and (
            existing.get("application") != "proto"
            or existing.get("client_id") != record.client_id
            or existing.get("subject") != record.subject
        ):
            raise ValueError("Existing profile has another identity; use a separate profile")
        if existing.get("refresh_token"):
            raise ValueError("Disconnect the existing session before replacing it")
        save_private(target, {**incoming, "ext_agent_host_id": host_id})
    finally:
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "import"])
    parser.add_argument("--profile", default="default")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(prepare(args.profile))  # Public host identity, never a token.
        else:
            if args.source is None:
                parser.error("import requires --source (a protected file transferred over SSH)")
            import_session(args.profile, args.source)
            print("PROTO session imported. VM host identity preserved. Live access not yet tested.")
            print("Remove both transfer copies; let only the VM refresh this session.")
    except Exception:
        # Credential parser / filesystem errors must not echo secret contents.
        raise SystemExit(
            "VM setup failed; verify the private file, profile and permissions."
        ) from None


if __name__ == "__main__":
    main()
