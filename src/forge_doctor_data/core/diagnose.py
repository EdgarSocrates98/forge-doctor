"""Deterministic log fingerprinting - known error signatures, no LLM.

Patterns in ``knowledge/errors/<domain>.json`` are matched against log text:
plain strings match as case-insensitive substrings, ``re:`` prefixed entries
compile as regexes. Occurrences are counted so recurring failures surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from forge_doctor_data.core.knowledge import load_pack

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext

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
    fixes: tuple[str, ...]
    related: tuple[str, ...]
    families: tuple[str, ...]
    severity: str
    domain: str


@dataclass
class Diagnosis:
    """A signature matched against log text, with occurrence count."""

    signature: ErrorSignature
    count: int = 0
    samples: list[str] = field(default_factory=list)


def load_signatures() -> list[ErrorSignature]:
    """All signatures across error domains, sorted by id for determinism.

    Packs live in two layouts: the legacy flat ``errors/<domain>.json`` and
    the per-domain ``<domain>/errors.json``. Both are merged; first-seen
    ``(domain, id)`` wins so a domain can migrate packs without dupes.
    """
    signatures: list[ErrorSignature] = []
    seen: set[tuple[str, str]] = set()
    for domain in ERROR_DOMAINS:
        for pack in (load_pack("errors", domain), load_pack(domain, "errors")):
            errors = pack.get("errors", [])
            if not isinstance(errors, list):
                continue
            for entry in errors:
                if not isinstance(entry, dict):
                    continue
                patterns = [str(p) for p in entry.get("patterns", []) if isinstance(p, str)]
                if not patterns:
                    continue
                sig_id = str(entry.get("id", "?"))
                if (domain, sig_id) in seen:
                    continue
                seen.add((domain, sig_id))
                signatures.append(
                    ErrorSignature(
                        id=sig_id,
                        title=str(entry.get("title", "")),
                        patterns=tuple(patterns),
                        causes=tuple(str(c) for c in entry.get("causes", [])),
                        fixes=tuple(str(f) for f in entry.get("fixes", [])),
                        related=tuple(str(r) for r in entry.get("related", [])),
                        families=tuple(str(f) for f in entry.get("families", [])),
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


# ---------------------------------------------------------------------------
# Evidence-gated correlations (repo facts + matched signatures).
# A correlation only fires when every leg has observable evidence - never
# inferred from the log line alone.
# ---------------------------------------------------------------------------

_VENDING_FAMILY = "credential-vending"
_LF_MARKER_RE = re.compile(r"lake[\s_-]?formation|fgac", re.IGNORECASE)
_LF_SCAN_SUFFIXES = {".tf", ".json", ".yml", ".yaml", ".conf", ".properties", ".cfg"}
_GLUE_JOB_TYPES = {"aws_glue_job", "AWS::Glue::Job"}
_GLUE_VERSION_KEYS = ("glue_version", "GlueVersion")
_WRITE_CALLS = {"writeto", "insertinto", "saveastable", "insertoverwrite", "save"}


def _glue_v5_plus(ctx: ProjectContext) -> bool:
    from forge_doctor_data.analyzers.hcl_lite import project_iac

    for resource in project_iac(ctx.files, ctx.root):
        if resource.type not in _GLUE_JOB_TYPES:
            continue
        for key in _GLUE_VERSION_KEYS:
            raw = str(resource.attrs.get(key, ""))
            digits = "".join(ch for ch in raw.split(".", 1)[0] if ch.isdigit())
            if digits and int(digits) >= 5:
                return True
    return False


def _lf_config_evidence(ctx: ProjectContext) -> bool:
    for relative in sorted(ctx.files):
        if relative.suffix.lower() not in _LF_SCAN_SUFFIXES:
            continue
        text = ctx.read_text(relative)
        if text is not None and _LF_MARKER_RE.search(text):
            return True
    return False


def _write_op_evidence(ctx: ProjectContext) -> bool:
    from forge_doctor_data.analyzers.iceberg_model import iceberg_model
    from forge_doctor_data.analyzers.index import project_index

    if iceberg_model(ctx).operation_names:
        return True
    for module in project_index(ctx).modules.values():
        for site in module.calls:
            name = (
                site.name.lower()
                if site.name.isidentifier()
                else site.dotted.rsplit(".", 1)[-1].split("(", 1)[0].lower()
            )
            if name in _WRITE_CALLS:
                return True
    try:
        from forge_doctor_data.analyzers.sql_ast import analyze_sql

        for statement in analyze_sql(ctx).statements:
            if statement.tables_written or statement.kind in {
                "insert",
                "merge",
                "update",
                "delete",
            }:
                return True
    except ImportError:
        pass
    return False


def project_correlations(ctx: ProjectContext, diagnoses: list[Diagnosis]) -> list[str]:
    """Repo-evidence correlation lines for matched diagnoses.

    Currently: a credential-vending LF diagnosis + Glue >=5.x + LF/FGAC
    config evidence + a write op in code -> the vending/write-path conflict
    line. Missing any leg -> no claim (evidence-gated by design).
    """
    if not any(
        _VENDING_FAMILY in d.signature.families or d.signature.id in {"LAKE-E003", "LAKE-E004"}
        for d in diagnoses
    ):
        return []
    if _glue_v5_plus(ctx) and _lf_config_evidence(ctx) and _write_op_evidence(ctx):
        return [
            "possible credential-vending/write-path conflict: Glue >=5.x plus "
            "Lake Formation/FGAC config plus a write operation in code - LF "
            "vends scoped credentials differently for governed writes than reads"
        ]
    return []
