"""Offline contract tests for the isolated, research-only original FinRobot SDK adapter."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_RUNNER_PATH = Path(__file__).resolve().parents[2] / "apps" / "research_worker" / "__main__.py"
_SPEC = importlib.util.spec_from_file_location("proto_research_adapter", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


def test_original_pipeline_mapping_uses_verified_sdk_names() -> None:
    assert runner.PIPELINES == {
        "research": "aresearch",
        "dcf": "adcf",
        "comps": "acomps",
        "earnings": "aearnings",
        "ic-memo": "aic_memo",
        "lbo": "albo",
    }


@pytest.mark.parametrize(
    ("ticker", "pipeline", "model"),
    [
        ("AAPL;rm -rf", "research", "anthropic:claude"),
        ("AAPL", "send-order", "anthropic:claude"),
        ("AAPL", "earnings", "http://localhost:8000"),
        ("AAPL", "earnings", "test"),
        ("../etc/passwd", "dcf", "openai:gpt-4o"),
    ],
)
def test_invalid_or_execution_like_requests_are_rejected(
    ticker: str, pipeline: str, model: str
) -> None:
    with pytest.raises(ValueError):
        runner.validate_request(ticker, pipeline, model)


def test_validate_only_never_reads_key_or_imports_upstream(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def inaccessible_secret(*args: object, **kwargs: object) -> str:
        raise AssertionError("No secrets should be opened in validation mode")

    monkeypatch.setattr(runner, "read_secret", inaccessible_secret)
    rc = runner.main([
        "--ticker", "aapl", "--pipeline", "earnings",
        "--model", "anthropic:claude-sonnet-4-6", "--validate-only",
    ])
    result = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert result["status"] == "VALIDATED_ONLY"
    assert result["ticker"] == "AAPL"
    assert result["execution_authority"] is False


@pytest.mark.asyncio
async def test_without_provider_key_abstains_without_importing_vendor(
    tmp_path: Path,
) -> None:
    response = await runner.run_research(
        ticker="AAPL",
        pipeline="research",
        model="anthropic:claude-sonnet-4-6",
        timeout_seconds=10,
        llm_key_file=tmp_path / "missing",
    )
    assert response["status"] == "ABSTAIN"
    assert response["reason"] == "CREDENTIAL_UNAVAILABLE"
    assert response["report"] is None


@pytest.mark.asyncio
async def test_sdk_pipeline_invocation_and_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called: list[object] = []
    llm = tmp_path / "llm"
    llm.write_text("fake-do-not-emit", encoding="utf-8")

    class Result:
        steps = {"earnings": "verified from upstream fake"}
        has_failures = False

        def collect_warnings(self) -> list[str]:
            return []

        def format_summary(self) -> str:
            return "FinRobot test report"

    class FakeFinRobot:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["provider_keys"] == {"anthropic": "fake-do-not-emit"}
            called.append(kwargs["model"])

        async def __aenter__(self) -> FakeFinRobot:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def aearnings(self, ticker: str) -> Result:
            called.append(ticker)
            return Result()

    fake = ModuleType("finrobot")
    fake.FinRobot = FakeFinRobot  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "finrobot", fake)
    response = await runner.run_research(
        ticker="AAPL",
        pipeline="earnings",
        model="anthropic:claude-sonnet-4-6",
        timeout_seconds=20,
        llm_key_file=llm,
        fmp_key_file=tmp_path / "no-fmp",
    )
    assert response["status"] == "RESEARCH_REPORT"
    assert response["execution_authority"] is False
    assert response["finrobot_commit"] == runner.ORIGINAL_FINROBOT_COMMIT
    assert called == ["anthropic:claude-sonnet-4-6", "AAPL"]
    assert "fake-do-not-emit" not in json.dumps(response)


@pytest.mark.asyncio
async def test_upstream_partial_failure_abstains(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = tmp_path / "llm"
    key.write_text("fake", encoding="utf-8")

    class Failed:
        has_failures = True
        steps = {"partial": "not trusted"}

        def collect_warnings(self) -> list[str]:
            return ["stale provider"]

        def format_summary(self) -> str:
            return "unreliable report"

    class FakeFinRobot:
        def __init__(self, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> FakeFinRobot:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def adcf(self, ticker: str) -> Failed:
            return Failed()

    fake = ModuleType("finrobot")
    fake.FinRobot = FakeFinRobot  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "finrobot", fake)
    response = await runner.run_research(
        ticker="AAPL",
        pipeline="dcf",
        model="openai:gpt-4o",
        timeout_seconds=20,
        llm_key_file=key,
        fmp_key_file=tmp_path / "no-fmp",
    )
    assert response["status"] == "ABSTAIN"
    assert response["reason"] == "UPSTREAM_EVIDENCE_INCOMPLETE"
    assert response["report"] is None


@pytest.mark.asyncio
async def test_upstream_raises_without_leaking_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    key = tmp_path / "llm"
    key.write_text("secret-cannot-leak", encoding="utf-8")

    class FakeFinRobot:
        def __init__(self, **kwargs: object) -> None:
            raise RuntimeError("secret-cannot-leak")

    fake = ModuleType("finrobot")
    fake.FinRobot = FakeFinRobot  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "finrobot", fake)
    response = await runner.run_research(
        ticker="AAPL",
        pipeline="earnings",
        model="openai:gpt-4o",
        timeout_seconds=20,
        llm_key_file=key,
        fmp_key_file=tmp_path / "no-fmp",
    )
    assert response["status"] == "ABSTAIN"
    assert "secret-cannot-leak" not in json.dumps(response)


@pytest.mark.asyncio
async def test_a_client_that_hangs_while_opening_abstains_within_the_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The timeout covers the whole SDK lifecycle, not only the pipeline call."""

    import asyncio

    key = tmp_path / "llm"
    key.write_text("fake", encoding="utf-8")

    class HangingFinRobot:
        def __init__(self, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> HangingFinRobot:
            await asyncio.sleep(3600)
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    fake = ModuleType("finrobot")
    fake.FinRobot = HangingFinRobot  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "finrobot", fake)
    response = await asyncio.wait_for(
        runner.run_research(
            ticker="AAPL",
            pipeline="research",
            model="anthropic:claude-sonnet-4-6",
            timeout_seconds=0.05,
            llm_key_file=key,
            fmp_key_file=tmp_path / "no-fmp",
        ),
        timeout=5,
    )
    assert response["status"] == "ABSTAIN"
    assert response["reason"] == "UPSTREAM_TIMEOUT"


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [0.0, -1.0, float("nan"), float("inf"), 901.0])
async def test_an_invalid_timeout_abstains_before_reading_secrets(
    monkeypatch: pytest.MonkeyPatch, budget: float
) -> None:
    def inaccessible_secret(*args: object, **kwargs: object) -> str:
        raise AssertionError("no secret may be opened for an invalid request")

    monkeypatch.setattr(runner, "read_secret", inaccessible_secret)
    response = await runner.run_research(
        ticker="AAPL",
        pipeline="research",
        model="anthropic:claude-sonnet-4-6",
        timeout_seconds=budget,
    )
    assert response["status"] == "ABSTAIN"
    assert response["reason"] == "INVALID_REQUEST"
