"""Check original FinRobot upstream files without importing runtime or calling a network.

This test validates Git blob bytes against SHA-1 returned by GitHub at the pinned
upstream commit. It does not certify upstream behavior, licenses of dependencies,
or that any research agent is installed or active.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[2] / "third_party" / "finrobot"

_UPSTREAM_GIT_BLOBS = {
    "LICENSE": "6cc0d28c44e482825bba73c84761e3d65f138f42",
    "NOTICE": "4ad1671f14994630695c91b14ee7cfa14cacb6af",
    "agents/instructions/data_agent.md": "333559ee755fab7de14c05608cfaf16817144c57",
    "agents/instructions/modeling_agent.md": "10fa0907d88782d5f1396d734c95d9b10720bc4c",
    "agents/instructions/synthesis_agent.md": "ffe0c2e5e34d7785689869fa7fdea7d546d31e8b",
    "agents/instructions/report_agent.md": "37702beb5d00c6a1ff17bcb47cb51808d573e89c",
}


@pytest.mark.parametrize("relative_path,expected_sha", _UPSTREAM_GIT_BLOBS.items())
def test_finrobot_vendored_file_is_upstream_identical(
    relative_path: str, expected_sha: str
) -> None:
    path = _VENDOR / relative_path
    content = path.read_bytes()
    git_blob = b"blob " + str(len(content)).encode("ascii") + b"\0" + content
    assert hashlib.sha1(git_blob, usedforsecurity=False).hexdigest() == expected_sha
