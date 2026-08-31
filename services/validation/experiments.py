from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite


def _canonicalize(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("experiment manifests require finite numeric values")
        return 0.0 if value == 0.0 else value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("experiment timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("experiment manifest keys must be strings")
            normalized[key] = _canonicalize(item)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonicalize(item) for item in value]
    raise ValueError(
        f"unsupported experiment manifest value: {type(value).__name__}"
    )


def canonical_json(payload: Mapping[str, object]) -> str:
    """Return a deterministic JSON representation for research provenance."""
    normalized = _canonicalize(payload)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def stable_fingerprint(payload: Mapping[str, object]) -> str:
    """Fingerprint a manifest without relying on process-local hash state."""
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()
