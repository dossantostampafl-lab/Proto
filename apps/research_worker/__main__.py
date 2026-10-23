"""One-shot read-only research task using the ORIGINAL pinned FinRobot SDK.

Adapter code belongs to PROTO; it is not represented as upstream FinRobot code.
Run only inside the dedicated research container or independently secured sandbox.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

ORIGINAL_FINROBOT_COMMIT = "2717499b8e30f242640af08c4ad9afd1113c2d45"
MODEL_PATTERN = re.compile(r"(?:anthropic|openai):[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SYMBOL_PATTERN = re.compile(r"[A-Z][A-Z0-9._-]{0,15}\Z")
PIPELINES = {
    "research": "aresearch",
    "dcf": "adcf",
    "comps": "acomps",
    "earnings": "aearnings",
    "ic-memo": "aic_memo",
    "lbo": "albo",
}
LLM_KEY_FILE = Path("/run/secrets/finrobot_llm_key")
FMP_KEY_FILE = Path("/run/secrets/finrobot_fmp_key")


def validate_request(ticker: str, pipeline: str, model: str) -> str:
    """Validate before loading secrets, imports or making provider requests."""
    ticker = ticker.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(ticker):
        raise ValueError("invalid instrument ticker")
    if pipeline not in PIPELINES:
        raise ValueError("unsupported original FinRobot pipeline")
    if not MODEL_PATTERN.fullmatch(model):
        raise ValueError("model must be a built-in anthropic:<id> or openai:<id>")
    return ticker


def read_secret(path: Path, *, optional: bool = False) -> str:
    """Read Docker secret files; never put provider keys in process environment."""
    if optional and not path.exists():
        return ""
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("required provider credential is unavailable")
    return value


def abstain(ticker: str, pipeline: str, reason: str) -> dict[str, object]:
    """Never substitute fabricated analysis for failed upstream evidence."""
    return {
        "status": "ABSTAIN",
        "reason": reason,
        "ticker": ticker,
        "pipeline": pipeline,
        "finrobot_commit": ORIGINAL_FINROBOT_COMMIT,
        "research_only": True,
        "execution_authority": False,
        "report": None,
    }


async def run_research(
    *,
    ticker: str,
    pipeline: str,
    model: str,
    timeout_seconds: float,
    llm_key_file: Path = LLM_KEY_FILE,
    fmp_key_file: Path = FMP_KEY_FILE,
) -> dict[str, object]:
    """Call FinRobot's original typed pipeline (no alternative research engine)."""
    ticker = validate_request(ticker, pipeline, model)
    try:
        llm_key = read_secret(llm_key_file)
        fmp_key = read_secret(fmp_key_file, optional=True)
    except (OSError, ValueError):
        return abstain(ticker, pipeline, "CREDENTIAL_UNAVAILABLE")

    # Deliberately lazy import: the PROTO runtime never imports or initializes
    # the third-party model, data providers or remote services.
    from finrobot import FinRobot  # type: ignore[import-not-found]

    provider = model.split(":", 1)[0]
    try:
        async with FinRobot(
            model=model,
            provider_keys={provider: llm_key},
            fmp_api_key=fmp_key,
            log_to_file=False,
            cache_db_path="/work/finrobot_cache.sqlite3",
        ) as original:
            method = getattr(original, PIPELINES[pipeline])
            result: Any = await asyncio.wait_for(
                method(ticker), timeout=timeout_seconds
            )
        report = result.format_summary()
        warnings = result.collect_warnings()
        if result.has_failures or warnings or not result.steps or not report.strip():
            return abstain(ticker, pipeline, "UPSTREAM_EVIDENCE_INCOMPLETE")
        if len(report) > 200_000:
            return abstain(ticker, pipeline, "REPORT_TOO_LARGE")
    except TimeoutError:
        return abstain(ticker, pipeline, "UPSTREAM_TIMEOUT")
    except Exception:  # noqa: BLE001
        # Do not surface arbitrary upstream exceptions: they may contain
        # endpoint URLs, account metadata, prompt data or credentials.
        return abstain(ticker, pipeline, "UPSTREAM_FAILURE")

    return {
        "status": "RESEARCH_REPORT",
        "ticker": ticker,
        "pipeline": pipeline,
        "finrobot_commit": ORIGINAL_FINROBOT_COMMIT,
        "research_only": True,
        "execution_authority": False,
        "data_quality": "verify publication timestamps and provider terms",
        "report": report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PROTO isolated FinRobot research")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--pipeline", choices=tuple(PIPELINES), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        ticker = validate_request(args.ticker, args.pipeline, args.model)
        if not 10 <= args.timeout <= 900:
            raise ValueError("timeout outside 10..900 seconds")
    except ValueError:
        print(json.dumps(abstain(args.ticker, args.pipeline, "INVALID_REQUEST")))
        return 2

    if args.validate_only:
        print(json.dumps({
            "status": "VALIDATED_ONLY",
            "ticker": ticker,
            "pipeline": args.pipeline,
            "finrobot_commit": ORIGINAL_FINROBOT_COMMIT,
            "execution_authority": False,
            "research_only": True,
        }))
        return 0

    result = asyncio.run(run_research(
        ticker=ticker,
        pipeline=args.pipeline,
        model=args.model,
        timeout_seconds=args.timeout,
    ))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "RESEARCH_REPORT" else 2


if __name__ == "__main__":
    sys.exit(main())
