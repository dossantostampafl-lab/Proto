"""Private, atomic storage; bearer records never enter a checkout."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def check_path(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("Credential symlinks are not accepted")
    resolved = path.expanduser().resolve()
    if any((parent / ".git").exists() for parent in resolved.parents):
        raise ValueError("Credentials cannot be stored in a Git checkout")
    return resolved


def save_private(path: Path, document: dict[str, Any]) -> None:
    path = check_path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, name = tempfile.mkstemp(prefix=".proto-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_private(path: Path) -> dict[str, Any]:
    path = check_path(path)
    if path.stat().st_mode & 0o077 or path.parent.stat().st_mode & 0o077:
        raise ValueError("Credential file/directory permissions are too broad")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid credential document")
    return value
