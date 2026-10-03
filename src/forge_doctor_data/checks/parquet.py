"""Parquet checks (PARQ###) over the shared ParquetProjectModel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from forge_doctor_data.analyzers.parquet_model import ParquetProjectModel, parquet_model
from forge_doctor_data.core.models import EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult


def _model(ctx: ProjectContext) -> ParquetProjectModel:
    return parquet_model(ctx)


def _pack() -> dict[str, Any]:
    from forge_doctor_data.core.knowledge import load_pack

    return load_pack("parquet", "format")


class _ParquetCheck(CheckBase):
    category = "parquet"
    # Base is STATIC: most evidence is write/config calls in code. On-disk
    # file-stats checks override to OBSERVED_METADATA.


class ParquetUsage(_ParquetCheck):
    """PARQ000: how much of the project touches Parquet."""

    id = "PARQ000"
    title = "Parquet usage"
    why = "Anchor: sizes the Parquet surface feeding the other PARQ checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the evidence counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_parquet:
            return [self.result(Severity.PASS, "no Parquet usage detected")]
        kinds: dict[str, int] = {}
        for e in model.evidence:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        detail = ", ".join(f"{k}={n}" for k, n in sorted(kinds.items()))
        return [
            self.result(
                Severity.INFO,
                f"{len(model.writers)} writers, {len(model.readers)} readers, "
                f"{model.file_count} files ({detail})",
            )
        ]


class SinglePartitionWrite(_ParquetCheck):
    """PARQ010: repartition/coalesce immediately around a parquet write."""

    id = "PARQ010"
    title = "Possible small-file write pattern"
    why = "repartition(1)/coalesce() before a parquet write serializes output "
    "into few files - the classic small-file trap."
    when_ok = "Bounded single-file output is intended."
    fix = "Drop repartition/coalesce or repartition by the partition key."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"`{e.name}` in a file that also writes parquet - possible "
                "small-file/single-partition pattern",
                file=e.file,
                line=e.line,
                evidence=self.evidence_at(ctx, e.file, e.line),
            )
            for e in _model(ctx).by_kind("write_pattern")
        ]


class UncompressedWrite(_ParquetCheck):
    """PARQ020: compression explicitly set to none/uncompressed."""

    id = "PARQ020"
    title = "Uncompressed analytical dataset"
    why = "Uncompressed parquet trades CPU for storage and network - "
    "usually the wrong trade for analytical tables."
    when_ok = "Tiny ephemeral intermediates or CPU-bound edge cases."
    fix = "Use snappy (default) or zstd for compression."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"parquet compression set to '{e.value}'",
                file=e.file,
                line=e.line,
                evidence=self.evidence_at(ctx, e.file, e.line),
            )
            for e in _model(ctx).evidence
            if e.kind == "config"
            and ("compression" in e.name or e.name.endswith("compression.codec"))
            and e.value.lower() in {"none", "uncompressed"}
        ]


class MixedCodecs(_ParquetCheck):
    """PARQ021: different codecs configured across the project."""

    id = "PARQ021"
    title = "Inconsistent parquet codecs"
    # Correlates compression config facts across the project.
    evidence_kind = EvidenceKind.DERIVED
    why = "Different compression codecs per file/path fragment the dataset "
    "and confuse tuning; usually accidental config drift."
    when_ok = "One codec across the project (or a deliberate per-table choice)."
    fix = "Standardize spark.sql.parquet.compression.codec."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        values = {
            e.value.lower()
            for e in model.evidence
            if e.kind == "config" and "compression" in e.name and e.value
        }
        if len(values) <= 1:
            return []
        first = next(e for e in model.evidence if e.kind == "config" and "compression" in e.name)
        return [
            self.result(
                Severity.INFO,
                f"parquet compression configured inconsistently: {sorted(values)}",
                file=first.file,
                line=first.line,
            )
        ]


class SmallFileDataset(_ParquetCheck):
    """PARQ040: median on-disk parquet file size below the pack floor."""

    id = "PARQ040"
    title = "Small-file proliferation"
    evidence_kind = EvidenceKind.OBSERVED_METADATA
    why = "Median file size below the floor means planning overhead dominates "
    "scan work - files should be in the 128MB-1GB range for analytics."
    when_ok = "Median at/above the knowledge-pack floor."
    fix = "Compact (rewrite_data_files/OPTIMIZE) or reduce partition count."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if model.file_count == 0:
            return []
        floor_mb = int(_pack().get("thresholds", {}).get("small_file_median_mb", 16))
        if model.median_bytes >= floor_mb * 1_000_000:
            return []
        return [
            self.result(
                Severity.INFO,
                f"median parquet file is {model.median_bytes // 1_000_000}MB "
                f"over {model.file_count} files (floor {floor_mb}MB)",
            )
        ]


class ExcessiveFileCount(_ParquetCheck):
    """PARQ041: on-disk file count above the pack threshold."""

    id = "PARQ041"
    title = "Excessive file count"
    evidence_kind = EvidenceKind.OBSERVED_METADATA
    why = "Tens of thousands of files crush driver-side listing and "
    "planning regardless of individual file size."
    when_ok = "File count below the pack threshold."
    fix = "Compact the dataset or coarsen the partition layout."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        limit = int(_pack().get("thresholds", {}).get("excessive_files", 10_000))
        if model.file_count <= limit:
            return []
        return [
            self.result(
                Severity.INFO,
                f"{model.file_count} parquet files exceeds {limit}",
            )
        ]


class SizeSkew(_ParquetCheck):
    """PARQ042: p95/median file-size ratio over the pack threshold."""

    id = "PARQ042"
    title = "Inconsistent file-size distribution"
    evidence_kind = EvidenceKind.OBSERVED_METADATA
    why = "A heavy right tail (p95 >> median) usually means skewed partition "
    "keys or mixed writer configs producing uneven files."
    when_ok = "p95 within the pack ratio of median."
    fix = "Investigate skewed keys; consider salting or repartitioning."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if model.file_count < 4 or model.median_bytes == 0:
            return []
        ratio = float(_pack().get("thresholds", {}).get("size_skew_ratio", 8))
        if model.p95_bytes <= model.median_bytes * ratio:
            return []
        return [
            self.result(
                Severity.INFO,
                f"file-size skew: p95={model.p95_bytes // 1_000_000}MB vs "
                f"median={model.median_bytes // 1_000_000}MB",
            )
        ]


CHECKS: list[Check] = [
    ParquetUsage(),
    SinglePartitionWrite(),
    UncompressedWrite(),
    MixedCodecs(),
    SmallFileDataset(),
    ExcessiveFileCount(),
    SizeSkew(),
]
