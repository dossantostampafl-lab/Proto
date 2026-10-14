"""Run or verify a drill from the command line (exit code 0 only when it passes / reproduces)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from services.promotion import EvidenceKind

from . import DRILL_VERSION, DRILLS, run_drill


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.drills")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a drill and print its report")
    run.add_argument("kind", choices=[k.value for k in DRILLS])
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--out", type=Path)
    verify = sub.add_parser("verify", help="re-run a saved report and compare digests")
    verify.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    logging.disable(logging.WARNING)  # the drills provoke the failures they test

    if args.command == "run":
        report = asyncio.run(run_drill(EvidenceKind(args.kind), args.seed))
        text = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if report.passed else 1

    saved = json.loads(args.report.read_text(encoding="utf-8"))
    if saved.get("version") != DRILL_VERSION:
        print(f"report is drill version {saved.get('version')}, this code is {DRILL_VERSION}")
        return 2
    again = asyncio.run(run_drill(EvidenceKind(saved["kind"]), int(saved["seed"])))
    same = again.digest == saved.get("digest")
    print(
        f"{saved['kind']} seed={saved['seed']}: "
        f"{'reproduced' if same else 'DIFFERENT'} {again.digest}"
    )
    return 0 if same and again.passed else 1


if __name__ == "__main__":
    sys.exit(main())
