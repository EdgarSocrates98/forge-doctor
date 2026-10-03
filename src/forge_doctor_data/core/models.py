"""Core data model: severities, results, reports."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Severity(Enum):
    """Result severity, ordered low to high."""

    PASS = "pass"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @classmethod
    def parse(cls, value: str) -> Severity:
        return cls(value.strip().lower())


class Confidence(Enum):
    """How sure the detector is - several rules are heuristics by design."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def parse(cls, value: str) -> Confidence:
        return cls(value.strip().lower())


class EvidenceKind(Enum):
    """Where a finding's supporting fact was obtained.

    Distinct from ``Confidence`` (how sure the detector is) and
    ``evidence`` (the free-text snippet): this classifies the *source
    plane* so downstream consumers - the platform graph, the capability
    engine, report readers - know what kind of signal backs a fact.
    """

    STATIC = "static"  # parsed source code / AST
    CONFIG = "config"  # declarative config, manifests, IaC
    OBSERVED_METADATA = "observed_metadata"  # real metadata artifacts
    RUNTIME = "runtime"  # runtime artifacts (progress logs) - reserved
    DERIVED = "derived"  # inferred by combining multiple facts

    @classmethod
    def parse(cls, value: str) -> EvidenceKind:
        return cls(value.strip().lower())


@dataclass(frozen=True)
class CheckResult:
    """One finding emitted by one check.

    ``check_id`` is the stable public identifier (e.g. ``SPARK001``); it never
    depends on message text so users can rely on it for ``--ignore``.
    ``fingerprint`` is the stable per-occurrence identity used by baselines,
    SARIF partialFingerprints, and dedup; auto-derived when not provided.
    """

    check_id: str
    title: str
    severity: Severity
    category: str
    message: str
    file: Path | None = None
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    end_column: int | None = None
    recommendation: str | None = None
    confidence: Confidence | None = None
    evidence: str | None = None
    # Source-plane classification of ``evidence`` - excluded from the
    # fingerprint so tagging a check never changes finding identity.
    evidence_kind: EvidenceKind | None = None
    tags: tuple[str, ...] = ()
    docs_uri: str | None = None
    # Provenance: which plugin distribution produced this finding.
    # None = built-in check.
    source: str | None = None
    fixable: bool | None = None
    fingerprint: str | None = None
    # Dotted enclosing symbol (``Pipeline.run``, ``<module>``) - resolved
    # during the runner's fingerprint pass; feeds output and ``trace``.
    symbol: str | None = None
    # Baseline comparison: None = no baseline in use, True = new finding,
    # False = pre-existing finding.
    is_new: bool | None = None

    def __post_init__(self) -> None:
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", default_fingerprint(self))


def default_fingerprint(result: CheckResult) -> str:
    """Context-free semantic identity of one finding occurrence.

    v3 material: ``check_id|file|symbol|anchor`` - line/column/message are
    deliberately excluded so refactors and reworded messages keep identity.
    The runner re-derives this with full AST context; this fallback exists
    for results created outside a scan.
    """
    anchor = result.evidence or result.message
    anchor = " ".join(anchor.strip().split())
    material = "|".join(
        [
            "v3",
            result.check_id,
            result.file.as_posix() if result.file is not None else "",
            result.symbol or "",
            anchor,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Summary:
    """Aggregate counts for a scan."""

    passed: int = 0
    info: int = 0
    warnings: int = 0
    errors: int = 0

    @classmethod
    def from_results(cls, results: list[CheckResult]) -> Summary:
        counts = {s: 0 for s in Severity}
        for result in results:
            counts[result.severity] += 1
        return cls(
            passed=counts[Severity.PASS],
            info=counts[Severity.INFO],
            warnings=counts[Severity.WARNING],
            errors=counts[Severity.ERROR],
        )


@dataclass(frozen=True)
class BaselineDiff:
    """Outcome of comparing a scan against a saved baseline."""

    new: int
    fixed: int
    existing: int


@dataclass(frozen=True)
class SuppressionRecord:
    """Audit trail entry for a configured suppression."""

    rule: str
    path: str | None
    reason: str
    owner: str
    expires: str | None
    approved_by: str
    status: str  # "active" | "expired" | "unused"
    matched: int


@dataclass(frozen=True)
class ScanReport:
    """Full result of one scan: inputs + results + summary."""

    version: str
    project: Path
    results: list[CheckResult] = field(default_factory=list)
    baseline: BaselineDiff | None = None
    suppressions: tuple[SuppressionRecord, ...] = ()

    @property
    def summary(self) -> Summary:
        return Summary.from_results(self.results)

    @property
    def new_results(self) -> list[CheckResult]:
        """Findings new since the baseline; all results without a baseline."""
        if self.baseline is None:
            return list(self.results)
        return [r for r in self.results if r.is_new is True]
