"""Offline Neptune explain/profile artifact analysis.

Consumes a user-supplied export only - nothing connects to Neptune.
Artifacts are classified by evidence plane:

- STATIC: a plan with no execution data (gremlin ``explain`` text,
  openCypher static explain).
- OBSERVED_METADATA: exported structured plan/metadata without
  execution counters.
- RUNTIME: counters/metrics present (profile output, openCypher
  dynamic explain, ``Time (ms)`` sections).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge_doctor.core.models import EvidenceKind

_LARGE_INTERMEDIATE = 10_000

_STEP_NAME_RE = re.compile(r"\b([A-Za-z][\w]*(?:Step|Operator|Scan|Expand|Join|Filter|Project))\b")
_COUNTER_KEYS = {
    "dur",
    "elapsed",
    "elapsedmillis",
    "time",
    "metrics",
    "percentdur",
    "count",
    "indexops",
    "results",
    "rowcount",
    "executiontime",
}
_CARD_KEYS = {"cardinality", "estimatedcount", "rowcount", "count", "rows"}
_SCANISH_RE = re.compile(r"allnodes|fullscan|\bscan\b|g\.V\s*\(\s*\)", re.I)
_FILTERISH_RE = re.compile(r"hasstep|filter|where|selection|predicate", re.I)
_TRAVERSISH_RE = re.compile(r"expand|out|in|travers|join|path|repeat", re.I)


@dataclass(frozen=True)
class ExplainFlag:
    """One observed risk inside an explain artifact."""

    kind: str  # large_intermediate | broad_start | late_filter
    detail: str


@dataclass
class ExplainReport:
    """Parsed view of one exported explain/profile artifact."""

    file: Path
    language: str = "unknown"  # gremlin | opencypher | sparql | unknown
    evidence_kind: EvidenceKind | None = None
    steps: list[str] = field(default_factory=list)
    max_cardinality: int | None = None
    flags: list[ExplainFlag] = field(default_factory=list)
    parsed: bool = False


def _collect_numbers(node: Any, out: list[int]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key.lower() in _CARD_KEYS and isinstance(value, (int, float)):
                out.append(int(value))
            else:
                _collect_numbers(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_numbers(item, out)


def _collect_keys(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            out.add(key.lower())
            _collect_keys(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_keys(item, out)


def _collect_step_names(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        for key in ("name", "step", "operator", "type"):
            value = node.get(key)
            if isinstance(value, str):
                out.append(value)
        for value in node.values():
            if isinstance(value, (dict, list)):
                _collect_step_names(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_step_names(item, out)


def _json_report(file: Path, data: Any) -> ExplainReport:
    report = ExplainReport(file=file, parsed=True)
    keys: set[str] = set()
    _collect_keys(data, keys)
    runtime = bool(keys & _COUNTER_KEYS) and any(
        k in keys
        for k in {"dur", "elapsed", "elapsedmillis", "metrics", "executiontime", "percentdur"}
    )
    report.evidence_kind = EvidenceKind.RUNTIME if runtime else EvidenceKind.OBSERVED_METADATA
    _collect_step_names(data, report.steps)
    blob = json.dumps(data).lower()
    if "sparql" in blob:
        report.language = "sparql"
    elif "opencypher" in blob or "cypher" in blob:
        report.language = "opencypher"
    elif "gremlin" in blob or "traversal" in blob:
        report.language = "gremlin"
    counts: list[int] = []
    _collect_numbers(data, counts)
    if counts:
        report.max_cardinality = max(counts)
    return report


def _text_report(file: Path, text: str) -> ExplainReport:
    report = ExplainReport(file=file, parsed=True)
    report.steps = list(dict.fromkeys(_STEP_NAME_RE.findall(text)))
    lower = text.lower()
    if "final traversal" in lower or "converted traversal" in lower or "gremlin" in lower:
        report.language = "gremlin"
    elif "opencypher" in lower or "cypher" in lower:
        report.language = "opencypher"
    elif "sparql" in lower:
        report.language = "sparql"
    has_counters = bool(
        re.search(r"time\s*\(ms\)|\bdur\b|elapsed|%\s*dur", lower)
        or re.search(r"\bcount\b\s*[:=]\s*\d", lower)
        or re.search(r"\btraversers?\b\s*[:=]\s*\d", lower)
    )
    report.evidence_kind = EvidenceKind.RUNTIME if has_counters else EvidenceKind.STATIC
    numbers = [
        int(n) for n in re.findall(r"\b(?:count|cardinality|estimated)\D{0,8}(\d{3,})", lower)
    ]
    if numbers:
        report.max_cardinality = max(numbers)
    return report


def analyze_explain(path: Path) -> ExplainReport:
    """Parse one explain/profile artifact; unparsed input stays honest."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    report = _json_report(path, data) if data is not None else _text_report(path, text)
    steps_text = " ".join(report.steps) + " " + text[:2000]
    if report.max_cardinality is not None and report.max_cardinality >= _LARGE_INTERMEDIATE:
        report.flags.append(
            ExplainFlag(
                "large_intermediate",
                f"intermediate cardinality reaches {report.max_cardinality}",
            )
        )
    first_scan = _SCANISH_RE.search(steps_text)
    if first_scan:
        after = steps_text[first_scan.end() :]
        if not _FILTERISH_RE.search(steps_text[: first_scan.end()]):
            report.flags.append(
                ExplainFlag(
                    "broad_start",
                    "plan starts from a full scan/broad start with no leading filter",
                )
            )
        elif _TRAVERSISH_RE.search(after[:200]) or _FILTERISH_RE.search(after):
            last_trav = None
            for m in _TRAVERSISH_RE.finditer(steps_text):
                last_trav = m.end()
            first_filter = _FILTERISH_RE.search(steps_text)
            if last_trav and first_filter and first_filter.start() > first_scan.end():
                report.flags.append(
                    ExplainFlag(
                        "late_filter",
                        "filter appears after traversal expansion in the plan",
                    )
                )
    return report
