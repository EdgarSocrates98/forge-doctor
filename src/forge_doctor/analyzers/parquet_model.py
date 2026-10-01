"""Parquet semantic model - code evidence + on-disk file stats.

Two evidence planes: writers/readers/configs from the AST index (never
executed), and real ``*.parquet`` files under the project (``stat`` sizes
only - no footer parsing; that needs an optional extra).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from forge_doctor.analyzers.index import CallSite
    from forge_doctor.core.context import ProjectContext

_CACHE_ATTR = "_forge_doctor_parquet_model"

_WRITE_HINTS = {"parquet", "save", "writeto", "insertinto", "saveastable"}
_READ_HINTS = {"parquet", "load", "table", "read"}
_WRITE_PATTERN_CALLS = {"repartition", "coalesce"}


@dataclass(frozen=True)
class ParquetEvidence:
    """One observation: kind, name, value, location, source."""

    kind: str  # writer|reader|config|write_pattern|file
    name: str
    value: str
    file: Path
    line: int
    source: str  # "python"|"conf"|"disk"


@dataclass
class ParquetProjectModel:
    """Semantic summary of a project's Parquet surface."""

    evidence: list[ParquetEvidence] = field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0
    median_bytes: int = 0
    p95_bytes: int = 0

    def by_kind(self, kind: str) -> list[ParquetEvidence]:
        return [e for e in self.evidence if e.kind == kind]

    @property
    def has_parquet(self) -> bool:
        return bool(self.evidence)

    @property
    def writers(self) -> list[ParquetEvidence]:
        return self.by_kind("writer")

    @property
    def readers(self) -> list[ParquetEvidence]:
        return self.by_kind("reader")

    @property
    def compression_values(self) -> set[str]:
        return {
            e.value.lower() for e in self.evidence if e.kind == "config" and "compression" in e.name
        }


def _py_evidence(index: object) -> tuple[list[ParquetEvidence], set[Path]]:
    from forge_doctor.analyzers.index import ProjectIndex

    assert isinstance(index, ProjectIndex)
    evidence: list[ParquetEvidence] = []
    parquet_files: set[Path] = set()
    for relative, module in sorted(index.modules.items(), key=lambda kv: kv[0].as_posix()):
        names = [_call_name(s) for s in module.calls]
        file_is_parquet = False
        pending: list[tuple[CallSite, str]] = []
        for site, name in zip(module.calls, names, strict=True):
            args = [str(a) for a in site.args]
            kwvals = [str(v) for _, v in site.kwargs]
            dotted = site.dotted.lower()
            if "parquet" in dotted or any("parquet" in a.lower() for a in (*args, *kwvals)):
                file_is_parquet = True
            if name == "option" and args and args[0].lower() == "compression" and len(args) > 1:
                evidence.append(
                    ParquetEvidence("config", "compression", args[1], relative, site.line, "python")
                )
            if (
                site.dotted.endswith("conf.set")
                and args
                and args[0].startswith(("spark.sql.parquet.", "spark.sql.files."))
            ):
                evidence.append(
                    ParquetEvidence(
                        "config",
                        args[0],
                        args[1] if len(args) > 1 else "",
                        relative,
                        site.line,
                        "python",
                    )
                )
            pending.append((site, name))
        if file_is_parquet:
            parquet_files.add(relative)
        has_write_call = any("write" in s.dotted.lower() or n in _WRITE_HINTS for s, n in pending)
        for site, name in pending:
            dotted = site.dotted.lower()
            is_write_side = "write" in dotted or name in {"save", "writeto"}
            is_read_side = "read" in dotted or name in {"load", "table"}
            if file_is_parquet and name in _WRITE_PATTERN_CALLS and has_write_call:
                evidence.append(
                    ParquetEvidence(
                        "write_pattern", name, site.dotted, relative, site.line, "python"
                    )
                )
            if not file_is_parquet:
                continue
            if name == "parquet" and is_write_side:
                evidence.append(
                    ParquetEvidence("writer", "parquet", site.dotted, relative, site.line, "python")
                )
            elif name == "parquet" and is_read_side:
                evidence.append(
                    ParquetEvidence("reader", "parquet", site.dotted, relative, site.line, "python")
                )
            elif name in {"save", "writeto"} and file_is_parquet:
                evidence.append(
                    ParquetEvidence("writer", name, site.dotted, relative, site.line, "python")
                )
            elif name in {"load", "table"} and file_is_parquet:
                evidence.append(
                    ParquetEvidence("reader", name, site.dotted, relative, site.line, "python")
                )
    return evidence, parquet_files


def _call_name(site: object) -> str:
    """Terminal op name; chained calls arrive inside-out in ``site.dotted``."""
    name: str = getattr(site, "name", "")
    if name.isidentifier():
        return name
    dotted: str = getattr(site, "dotted", "")
    return dotted.split("(", 1)[0].rsplit(".", 1)[-1].lower()


def _conf_evidence(ctx: ProjectContext) -> list[ParquetEvidence]:
    evidence: list[ParquetEvidence] = []
    for relative in sorted(ctx.files):
        if relative.suffix.lower() not in {".conf", ".properties"}:
            continue
        text = ctx.read_text(relative)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if (
                stripped.startswith(("spark.sql.parquet.", "spark.sql.files."))
                or "parquet.compression" in stripped
            ):
                for sep in ("=", " ", ":"):
                    if sep in stripped:
                        key, _, value = stripped.partition(sep)
                        evidence.append(
                            ParquetEvidence(
                                "config",
                                key.strip(),
                                value.strip(),
                                relative,
                                lineno,
                                "conf",
                            )
                        )
                        break
    return evidence


def _disk_files(ctx: ProjectContext) -> list[ParquetEvidence]:
    evidence: list[ParquetEvidence] = []
    for relative in sorted(ctx.files):
        if relative.suffix.lower() != ".parquet":
            continue
        try:
            size = (ctx.root / relative).stat().st_size
        except OSError:
            continue
        evidence.append(
            ParquetEvidence("file", relative.as_posix(), str(size), relative, 0, "disk")
        )
    return evidence


def parquet_model(ctx: ProjectContext) -> ParquetProjectModel:
    """Build (once, memoized on ctx) the project's Parquet evidence model."""
    cached = getattr(ctx, _CACHE_ATTR, None)
    if cached is not None:
        return cast(ParquetProjectModel, cached)

    from forge_doctor.analyzers.index import project_index

    evidence: list[ParquetEvidence] = []
    py_ev, _files = _py_evidence(project_index(ctx))
    evidence.extend(py_ev)
    evidence.extend(_conf_evidence(ctx))
    evidence.extend(_disk_files(ctx))
    evidence.sort(key=lambda e: (e.file.as_posix(), e.line, e.kind, e.name, e.value))

    sizes = sorted(int(e.value) for e in evidence if e.kind == "file" and e.value.isdigit())
    model = ParquetProjectModel(evidence=evidence)
    if sizes:
        model.file_count = len(sizes)
        model.total_bytes = sum(sizes)
        model.median_bytes = int(statistics.median(sizes))
        p95_idx = min(len(sizes) - 1, int(len(sizes) * 0.95))
        model.p95_bytes = sizes[p95_idx]
    setattr(ctx, _CACHE_ATTR, model)
    return model
