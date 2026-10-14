"""Drill reports: what was attempted, what the controls did, and a digest anyone can reproduce.

A report holds only deterministic facts (case names, expected and observed outcomes). Random ids,
timings and paths are kept out, so the same drill version and seed always produce the same digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

# Bump when a drill's cases change; a digest is only comparable within one version.
DRILL_VERSION = "6c.1"


@dataclass(frozen=True)
class Case:
    name: str
    expected: str
    observed: str

    @property
    def passed(self) -> bool:
        return self.expected == self.observed


@dataclass
class DrillReport:
    kind: str
    seed: int
    cases: list[Case] = field(default_factory=list)
    version: str = DRILL_VERSION

    def expect(self, name: str, expected: object, observed: object) -> None:
        self.cases.append(Case(name, str(expected), str(observed)))

    def guard(self, name: str, exc: BaseException) -> None:
        """A control that raises instead of refusing fails the drill; it never aborts it."""

        self.expect(f"{name}_completed", "completed", f"raised {type(exc).__name__}")

    @property
    def passed(self) -> bool:
        return bool(self.cases) and all(c.passed for c in self.cases)

    @property
    def failures(self) -> list[Case]:
        return [c for c in self.cases if not c.passed]

    def canonical(self) -> str:
        body = {
            "kind": self.kind,
            "version": self.version,
            "seed": self.seed,
            "cases": [asdict(c) for c in self.cases],
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical().encode()).hexdigest()

    @property
    def reference(self) -> str:
        """What a promotion evidence records: enough to re-run and compare."""

        return f"drill:{self.kind}:v{self.version}:seed={self.seed}:sha256={self.digest[:16]}"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "version": self.version,
            "seed": self.seed,
            "passed": self.passed,
            "digest": self.digest,
            "reference": self.reference,
            "cases": [{**asdict(c), "passed": c.passed} for c in self.cases],
        }
