"""Oracle side of the free XP path: the API reads the PC node from an optional root-only file."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

OCI = Path(__file__).resolve().parents[2] / "infra" / "oci"
TOKEN = "t" * 40
POSIX = os.name != "nt"


def set_node(tmp_path: Path, *args: str, token: str = TOKEN) -> subprocess.CompletedProcess:
    env = {**os.environ, "PROTO_ETC": str(tmp_path), "PROTO_SKIP_RESTART": "1"}
    return subprocess.run(
        ["bash", str(OCI / "deploy/set-xp-node.sh"), *args],
        input=f"{token}\n".encode(),
        env=env,
        capture_output=True,
    )


@pytest.mark.parametrize("ip", ["100.101.102.103", "100.64.0.1", "100.127.255.254", "10.0.2.20"])
def test_a_tailscale_or_private_address_is_written_with_the_token(tmp_path, ip) -> None:
    run = set_node(tmp_path, ip)
    assert run.returncode == 0, run.stderr
    written = (tmp_path / "xp.env").read_text()
    assert written == f"XP_EXECUTOR_URL=http://{ip}:8101\nXP_EXECUTOR_API_TOKEN={TOKEN}\n"
    assert TOKEN.encode() not in run.stdout + run.stderr  # never echoed
    if POSIX:
        assert (tmp_path / "xp.env").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "ip", ["8.8.8.8", "155.181.40.136", "100.63.0.1", "100.128.0.1", "192.169.0.1", "example.com"]
)
def test_a_public_address_is_refused_so_the_token_never_crosses_the_internet(
    tmp_path, ip
) -> None:
    run = set_node(tmp_path, ip)
    assert run.returncode == 2 and not (tmp_path / "xp.env").exists()


@pytest.mark.parametrize("token", ["short", "has space " + "x" * 40, ""])
def test_a_weak_token_is_refused(tmp_path, token) -> None:
    run = set_node(tmp_path, "100.100.100.100", token=token)
    assert run.returncode == 2 and not (tmp_path / "xp.env").exists()


def test_a_bad_port_is_refused_and_a_custom_port_is_used(tmp_path) -> None:
    assert set_node(tmp_path, "100.100.100.100", "80x").returncode == 2
    assert set_node(tmp_path, "100.100.100.100", "9000").returncode == 0
    assert "http://100.100.100.100:9000" in (tmp_path / "xp.env").read_text()


def test_remove_turns_xp_routing_off_again(tmp_path) -> None:
    assert set_node(tmp_path, "100.100.100.100").returncode == 0
    assert set_node(tmp_path, "--remove").returncode == 0
    assert not (tmp_path / "xp.env").exists()


def xp_env_args(
    tmp_path: Path, content: str | None, role: str = "api"
) -> subprocess.CompletedProcess:
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    function = re.search(r"xp_env_args\(\) \{\n(.*?)\n\}\n", deploy, re.S)
    assert function, "xp_env_args() not found"
    file = tmp_path / "xp.env"
    if content is not None:
        file.write_text(content)
    script = (
        f'role="{role}"\n'
        'log() { echo "proto-deploy[$role]: $*"; }\n'
        f'xp_env_file="{file.as_posix()}"\n'
        f"xp_env_args() {{\n{function.group(1)}\n}}\nxp_env_args\n"
    )
    return subprocess.run(["bash", "-c", script], capture_output=True)


def test_no_file_leaves_the_api_exactly_as_it_was(tmp_path) -> None:
    run = xp_env_args(tmp_path, None)
    assert run.returncode == 0 and run.stdout == b"" and run.stderr == b""


def test_a_valid_file_adds_exactly_one_env_file_argument(tmp_path) -> None:
    run = xp_env_args(tmp_path, f"XP_EXECUTOR_URL=http://100.100.100.100:8101\nXP_EXECUTOR_API_TOKEN={TOKEN}\n")
    lines = run.stdout.decode().split()
    assert run.returncode == 0 and lines[0] == "--env-file" and lines[1].endswith("xp.env")
    assert len(lines) == 2


@pytest.mark.parametrize(
    "content",
    [
        "XP_EXECUTOR_URL=http://100.100.100.100:8101\nXP_EXECUTOR_API_TOKEN=short\n",
        f"XP_EXECUTOR_API_TOKEN={TOKEN}\n",
        f"XP_EXECUTOR_URL=ftp://x\nXP_EXECUTOR_API_TOKEN={TOKEN}\n",
        "garbage\n",
    ],
)
def test_a_malformed_file_is_ignored_and_never_leaks_text_into_the_podman_arguments(
    tmp_path, content
) -> None:
    run = xp_env_args(tmp_path, content)
    assert run.returncode == 0
    assert run.stdout == b""  # a warning on stdout would become a podman argument
    assert b"ignoring" in run.stderr


def test_the_api_and_the_watchdog_read_the_xp_file_and_the_executor_never() -> None:
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    for role in ("api", "watchdog"):
        block = deploy.split(f"    {role})\n      mapfile", 1)[1].split(" ;;", 1)[0]
        assert 'xp_args[@]+"${xp_args[@]}"' in block
    executor = deploy.split("    executor)\n      podman run", 1)[1].split(" ;;", 1)[0]
    assert "xp_args" not in executor


def test_on_the_watchdog_instance_the_halt_only_token_is_written(tmp_path) -> None:
    (tmp_path / "role").write_text("watchdog\n")
    run = set_node(tmp_path, "100.101.102.103")
    assert run.returncode == 0, run.stderr
    assert (tmp_path / "xp.env").read_text() == (
        "WATCHDOG_XP_EXECUTOR_URL=http://100.101.102.103:8101\n"
        f"WATCHDOG_XP_EXECUTOR_TOKEN={TOKEN}\n"
    )


def test_the_watchdog_reads_its_own_keys_only(tmp_path) -> None:
    api_file = f"XP_EXECUTOR_URL=http://100.100.100.100:8101\nXP_EXECUTOR_API_TOKEN={TOKEN}\n"
    assert xp_env_args(tmp_path, api_file, role="watchdog").stdout == b""
    watchdog_file = (
        "WATCHDOG_XP_EXECUTOR_URL=http://100.100.100.100:8101\n"
        f"WATCHDOG_XP_EXECUTOR_TOKEN={TOKEN}\n"
    )
    lines = xp_env_args(tmp_path, watchdog_file, role="watchdog").stdout.decode().split()
    assert lines[0] == "--env-file" and len(lines) == 2
