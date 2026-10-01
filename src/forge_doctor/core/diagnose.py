"""Deterministic log fingerprinting - known error signatures, no LLM.

Patterns in ``knowledge/errors/<domain>.json`` are matched against log text:
plain strings match as case-insensitive substrings, ``re:`` prefixed entries
compile as regexes. Occurrences are counted so recurring failures surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from forge_doctor.core.knowledge import load_pack

ERROR_DOMAINS = (
    "spark",
    "glue",
    "python",
    "iceberg",
    "lakeformation",
    "databricks",
    "controlm",
    "airflow",
)

_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


@dataclass(frozen=True)
class ErrorSignature:
    """One known-error entry from a knowledge pack."""

    id: str
    title: str
    patterns: tuple[str, ...]
    causes: tuple[str, ...]
    related: tuple[str, ...]
    severity: str
    domain: str


@dataclass
class Diagnosis:
    """A signature matched against log text, with occurrence count."""

    signature: ErrorSignature
    count: int = 0
    samples: list[str] = field(default_factory=list)


def load_signatures() -> list[ErrorSignature]:
    """All signatures across error domains, sorted by id for determinism."""
    signatures: list[ErrorSignature] = []
    for domain in ERROR_DOMAINS:
        pack = load_pack("errors", domain)
        errors = pack.get("errors", [])
        if not isinstance(errors, list):
            continue
        for entry in errors:
            if not isinstance(entry, dict):
                continue
            patterns = [str(p) for p in entry.get("patterns", []) if isinstance(p, str)]
            if not patterns:
                continue
            signatures.append(
                ErrorSignature(
                    id=str(entry.get("id", "?")),
                    title=str(entry.get("title", "")),
                    patterns=tuple(patterns),
                    causes=tuple(str(c) for c in entry.get("causes", [])),
                    related=tuple(str(r) for r in entry.get("related", [])),
                    severity=str(entry.get("severity", "error")),
                    domain=domain,
                )
            )
    return sorted(signatures, key=lambda s: s.id)


def _match_positions(text: str, pattern: str) -> list[int]:
    """All match offsets for one pattern; dedup happens per line upstream."""
    if pattern.startswith("re:"):
        regex = _PATTERN_CACHE.get(pattern)
        if regex is None:
            try:
                regex = re.compile(pattern[3:], re.IGNORECASE)
            except re.error:
                return []
            _PATTERN_CACHE[pattern] = regex
        return [m.start() for m in regex.finditer(text)]
    lowered = text.lower()
    needle = pattern.lower()
    positions: list[int] = []
    start = 0
    while True:
        idx = lowered.find(needle, start)
        if idx < 0:
            break
        positions.append(idx)
        start = idx + max(len(needle), 1)
    return positions


def diagnose_text(text: str) -> list[Diagnosis]:
    """Match every known signature against ``text``; sorted by count desc."""
    diagnoses: list[Diagnosis] = []
    lines = text.splitlines()
    for signature in load_signatures():
        hit_lines: set[int] = set()
        for pattern in signature.patterns:
            for pos in _match_positions(text, pattern):
                hit_lines.add(text.count("\n", 0, pos))
        if hit_lines:
            samples = [lines[i].strip()[:240] for i in sorted(hit_lines)[:3] if lines[i].strip()]
            diagnoses.append(Diagnosis(signature=signature, count=len(hit_lines), samples=samples))
    return sorted(diagnoses, key=lambda d: (-d.count, d.signature.id))


def diagnose_file(path: str) -> list[Diagnosis]:
    """Read a file (or '-' for stdin handled by caller) and diagnose it."""
    from pathlib import Path

    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return diagnose_text(text)
