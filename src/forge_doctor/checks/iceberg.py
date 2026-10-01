"""Iceberg checks (ICE###) over the shared IcebergProjectModel - never re-parse."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor.analyzers.iceberg_model import (
    IcebergProjectModel,
    iceberg_model,
)
from forge_doctor.core.models import EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult

_V2_OPS = {"merge", "update", "delete"}


def _model(ctx: ProjectContext) -> IcebergProjectModel:
    return iceberg_model(ctx)


class _IcebergCheck(CheckBase):
    category = "iceberg"
    # Base is STATIC: all Iceberg evidence is code/SQL/config facts - the
    # model never reads real table metadata. Correlation/absence checks
    # override to DERIVED.


class IcebergUsage(_IcebergCheck):
    """ICE000: how much of the project touches Iceberg."""

    id = "ICE000"
    title = "Iceberg usage"
    why = "Anchor: sizes the Iceberg surface feeding the other ICE checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the evidence counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_iceberg:
            return [self.result(Severity.PASS, "no Iceberg usage detected")]
        kinds: dict[str, int] = {}
        for e in model.evidence:
            kinds[e.kind] = kinds.get(e.kind, 0) + 1
        detail = ", ".join(f"{k}={n}" for k, n in sorted(kinds.items()))
        return [
            self.result(
                Severity.INFO,
                f"{len(model.tables)} Iceberg tables, "
                f"{len(model.operations)} operations, "
                f"{len(model.catalog_names)} catalogs ({detail})",
            )
        ]


class FormatVersionCompat(_IcebergCheck):
    """ICE001: v2-requiring operations on a table with no/explicit v1 format."""

    id = "ICE001"
    title = "Format-version vs operations"
    # Combines operation evidence with the format-version property.
    evidence_kind = EvidenceKind.DERIVED
    why = "MERGE/UPDATE/DELETE need format-version=2; v1 (or unset) breaks or misbehaves."
    when_ok = "Append-only tables, or format-version=2 explicitly set."
    fix = "Set TBLPROPERTIES 'format-version'='2' (or confirm append-only intent)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        v2_ops = [e for e in model.operations if e.name in _V2_OPS]
        if not v2_ops:
            return []
        fmt = model.properties.get("format-version")
        if fmt and fmt[0] not in {"", "1"}:
            return []
        if fmt is None:
            suffix = "format-version is unset (defaults to 1)"
        else:
            at = fmt[1]
            suffix = f"format-version={fmt[0] or '1'} set at {at.file.as_posix()}:{at.line}"
        results = []
        for op in v2_ops:
            results.append(
                self.result(
                    Severity.WARNING,
                    f"{op.name.upper()} requires format-version=2; {suffix}",
                    file=op.file,
                    line=op.line,
                    evidence=self.evidence_at(ctx, op.file, op.line),
                )
            )
        return results


class MergeNoPartition(_IcebergCheck):
    """ICE002: MERGE INTO on a table with no partitioning evidence."""

    id = "ICE002"
    title = "MERGE without partition evidence"
    # MERGE op combined with project-wide partitioning absence.
    evidence_kind = EvidenceKind.DERIVED
    why = "Unpartitioned merge targets read/rewrite far more data than intended."
    when_ok = "Small tables, or partitioning declared outside the scanned code."
    fix = "Partition the target on the merge key domain, or bound the source."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if "partitioned-by" in model.properties:
            return []
        return [
            self.result(
                Severity.INFO,
                f"MERGE INTO {e.value or 'target'} - no PARTITIONED BY evidence",
                file=e.file,
                line=e.line,
                evidence=self.evidence_at(ctx, e.file, e.line),
            )
            for e in model.operations
            if e.name == "merge"
        ]


class _MaintenanceCheck(_IcebergCheck):
    # "Procedure X never observed" asserts a project-wide absence over
    # maintenance evidence - derived, not a single literal fact.
    evidence_kind = EvidenceKind.DERIVED
    """Writes detected but a given maintenance procedure is never called."""

    proc = ""
    detail = ""
    severity = Severity.INFO

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_writes or self.proc in model.maintenance_names:
            return []
        first_write = next((e for e in model.operations if e.name != "read"), None)
        return [
            self.result(
                self.severity,
                f"writes detected but no `{self.proc}` maintenance anywhere - {self.detail}",
                file=first_write.file if first_write else None,
                line=first_write.line if first_write else None,
            )
        ]


class NoExpireSnapshots(_MaintenanceCheck):
    """ICE008: snapshot accumulation makes planning and storage grow forever."""

    id = "ICE008"
    title = "No expire_snapshots"
    why = "Every commit adds a snapshot; unexpired tables slow planning and bloat storage."
    when_ok = "Snapshots expired by an external/compaction service."
    fix = "Schedule `CALL <catalog>.system.expire_snapshots('<table>')`."
    severity = Severity.WARNING
    proc = "expire_snapshots"
    detail = "snapshots accumulate unbounded"


class NoRewriteDataFiles(_MaintenanceCheck):
    """ICE009: without compaction, small files degrade scan performance."""

    id = "ICE009"
    title = "No rewrite_data_files"
    why = "Streaming/upsert writes produce small files that compound query cost."
    when_ok = "Compaction handled externally or write volume is tiny."
    fix = "Schedule `rewrite_data_files` (bin-pack or sort strategy)."
    severity = Severity.INFO
    proc = "rewrite_data_files"
    detail = "small files will accumulate"


class NoRewriteManifests(_MaintenanceCheck):
    """ICE010: manifest sprawl slows down query planning."""

    id = "ICE010"
    title = "No rewrite_manifests"
    why = "Many small manifests make planning crawl on write-heavy tables."
    when_ok = "Low write frequency, or handled externally."
    fix = "Schedule `rewrite_manifests` alongside data compaction."
    severity = Severity.INFO
    proc = "rewrite_manifests"
    detail = "manifest count grows with every commit"


class ConflictingCatalogConfig(_IcebergCheck):
    """ICE012: same catalog name configured with different implementations."""

    id = "ICE012"
    title = "Conflicting catalog config"
    # Compares catalog config facts across files.
    evidence_kind = EvidenceKind.DERIVED
    why = "spark.sql.catalog.<name> set to different impls per file/env drifts silently."
    when_ok = "One impl per catalog name across the whole project."
    fix = "Unify the catalog configuration in a single source of truth."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        seen: dict[str, tuple[str, str, int]] = {}
        results = []
        for e in _model(ctx).by_kind("catalog"):
            if not e.value or e.value.startswith("aws_") or "::" in e.value:
                continue
            key = e.name
            if key in seen and seen[key][0] != e.value:
                first_val, first_file, first_line = seen[key]
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"catalog '{key}': '{e.value}' here vs '{first_val}' "
                        f"at {first_file}:{first_line}",
                        file=e.file,
                        line=e.line,
                        evidence=self.evidence_at(ctx, e.file, e.line),
                    )
                )
            else:
                seen[key] = (e.value, e.file.as_posix(), e.line)
        return results


class RuntimeCompatibility(_IcebergCheck):
    """ICE013: detected Iceberg usage vs a risky Glue runtime pin."""

    id = "ICE013"
    title = "Iceberg runtime compatibility"
    # Combines observed table metadata with runtime pins + pack rules.
    evidence_kind = EvidenceKind.DERIVED
    why = "Glue runtimes bundle different Iceberg versions; some features need newer runtimes."
    when_ok = "Runtime pin meets the knowledge pack's floor for detected operations."
    fix = "Bump glue_version or remove v2-only operations."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_iceberg:
            return []
        runtimes = model.runtimes
        glue = runtimes.get("glue")
        if glue is None:
            return []
        from forge_doctor.core.knowledge import load_pack

        pack = load_pack("iceberg", "compatibility")
        entries = pack.get("runtimes", {}).get("glue", {})
        entry = entries.get(glue.value)
        if entry is None:
            return []
        severity = str(entry.get("severity", "")).upper()
        if severity not in {"WARNING", "HIGH"}:
            return []
        return [
            self.result(
                Severity.WARNING,
                f"Glue {glue.value}: {entry.get('note', 'risky Iceberg runtime')}",
                file=glue.file,
                line=glue.line,
                evidence=self.evidence_at(ctx, glue.file, glue.line),
            )
        ]


class LegacyWriteApi(_IcebergCheck):
    """ICE020: V1 DataFrameWriter APIs used in Iceberg-evidenced files."""

    id = "ICE020"
    title = "Legacy writer API"
    why = "insertInto/saveAsTable/write.save predate Iceberg's table APIs and can "
    "silently write through a different catalog/session resolution path."
    when_ok = "Intentional V1 write on a table whose catalog is the session default."
    fix = "Prefer df.writeTo('<table>').using('iceberg').append()."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"legacy writer API `{e.value}` - prefer writeTo().using('iceberg')",
                file=e.file,
                line=e.line,
                evidence=self.evidence_at(ctx, e.file, e.line),
            )
            for e in _model(ctx).by_kind("write_api")
            if e.name == "legacy"
        ]


class InsertIntoCatalog(_IcebergCheck):
    """ICE021: insertInto against a catalog-qualified Iceberg table."""

    id = "ICE021"
    title = "insertInto on cataloged Iceberg table"
    why = "insertInto resolves through the session catalog path; on a catalog-qualified "
    "Iceberg table the intended catalog can differ from the resolved one."
    when_ok = "insertInto intentionally targets the session catalog."
    fix = "Prefer writeTo().using('iceberg').append() so resolution is explicit."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                "insertInto on a catalog-qualified table - resolution is implicit",
                file=e.file,
                line=e.line,
                evidence=self.evidence_at(ctx, e.file, e.line),
            )
            for e in _model(ctx).by_kind("write_api")
            if e.name == "legacy_catalog"
        ]


class MergeNoExtensions(_IcebergCheck):
    """ICE022: row-level ops without IcebergSparkSessionExtensions configured."""

    id = "ICE022"
    title = "MERGE without Iceberg extensions"
    # Row-op evidence combined with project-wide extensions absence.
    evidence_kind = EvidenceKind.DERIVED
    why = "MERGE/UPDATE/DELETE on Iceberg tables require "
    "spark.sql.extensions=...IcebergSparkSessionExtensions; without it the "
    "statement fails or falls back to the session catalog."
    when_ok = "Extensions are set in the runtime env or the engine bundles Iceberg."
    fix = "Set spark.sql.extensions=org.apache.iceberg.spark.extensions."
    "IcebergSparkSessionExtensions."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        row_ops = [e for e in model.operations if e.name in _V2_OPS]
        if not row_ops:
            return []
        configured = any(
            e.kind == "config" and "extensions" in e.name and "iceberg" in e.value.lower()
            for e in model.evidence
        )
        if configured:
            return []
        return [
            self.result(
                Severity.WARNING,
                f"{e.name.upper()} at this site but IcebergSparkSessionExtensions "
                "is never configured in project sources",
                file=e.file,
                line=e.line,
                evidence=self.evidence_at(ctx, e.file, e.line),
            )
            for e in row_ops
        ]


class MergeNoPartitionPruning(_IcebergCheck):
    """ICE023: MERGE ON-predicate columns don't intersect known partition columns."""

    id = "ICE023"
    title = "MERGE unlikely to prune partitions"
    # ON-predicate columns intersected with declared partition columns.
    evidence_kind = EvidenceKind.DERIVED
    why = "MERGE on a partitioned table whose ON predicate never references a "
    "partition column cannot prune target partitions; the scan covers everything."
    when_ok = "Dynamic partition pruning at runtime still helps, or tables are small."
    fix = "Include a partition column in the ON predicate, or bound the source."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        part_cols = {
            e.name.removeprefix("partition.")
            for e in model.by_kind("property")
            if e.name.startswith("partition.")
        }
        if not part_cols:
            return []
        on_cols: dict[str, set[str]] = {}
        for e in model.by_kind("merge_on"):
            on_cols.setdefault(e.name, set()).add(e.value)
        results = []
        for e in model.by_kind("merge_detail"):
            cols = on_cols.get(e.name, set())
            if cols and not cols & part_cols:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"MERGE INTO {e.name}: ON cols "
                        f"{sorted(cols)} never touch partition cols {sorted(part_cols)}",
                        file=e.file,
                        line=e.line,
                        evidence=self.evidence_at(ctx, e.file, e.line),
                    )
                )
        return results


class SmallFilePattern(_IcebergCheck):
    """ICE024: repartition/coalesce calls in files with write evidence."""

    id = "ICE024"
    title = "Possible small-file write pattern"
    why = "repartition(1)/coalesce() before a write serializes the write into a "
    "handful of partitions - the classic small-file (or single-writer) trap."
    when_ok = "Explicit bounded output (e.g. one manifest file) is intended."
    fix = "Remove repartition/coalesce before write, or repartition by the table's "
    "partition key to spread output."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"`{e.name}` in a file that also writes - possible "
                "small-file/single-partition write pattern",
                file=e.file,
                line=e.line,
                evidence=self.evidence_at(ctx, e.file, e.line),
            )
            for e in _model(ctx).by_kind("write_pattern")
        ]


class FeatureFormatFloor(_IcebergCheck):
    """ICE025: feature properties that require a higher format-version."""

    id = "ICE025"
    title = "Feature vs format-version"
    # Property value combined with format-version and pack floor rules.
    evidence_kind = EvidenceKind.DERIVED
    why = "merge-on-read deletes need format-version>=2; deletion vectors and row "
    "lineage need >=3 - setting them on a lower-format table is invalid."
    when_ok = "format-version meets the feature floor from the knowledge pack."
    fix = "Raise 'format-version' or drop the newer-mode property."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_iceberg:
            return []
        from forge_doctor.core.knowledge import load_pack

        floors = load_pack("iceberg", "spec").get("feature_floors", {})
        fmt = model.properties.get("format-version")
        fmt_v = int(fmt[0]) if fmt and fmt[0].isdigit() else 1
        results = []
        for e in model.by_kind("property"):
            for feature, rule in floors.items():
                if (
                    e.name in rule.get("props", [])
                    and e.value == rule.get("value")
                    and fmt_v < int(rule.get("min_format", 1))
                ):
                    results.append(
                        self.result(
                            Severity.WARNING,
                            f"'{e.name}'='{e.value}' ({feature}) needs "
                            f"format-version>={rule['min_format']} but table is "
                            f"{'v' + str(fmt_v) if fmt else 'unset (v1 default)'} - "
                            f"{rule.get('fix', '')}",
                            file=e.file,
                            line=e.line,
                            evidence=self.evidence_at(ctx, e.file, e.line),
                        )
                    )
        return results


CHECKS: list[Check] = [
    IcebergUsage(),
    FormatVersionCompat(),
    MergeNoPartition(),
    NoExpireSnapshots(),
    NoRewriteDataFiles(),
    NoRewriteManifests(),
    ConflictingCatalogConfig(),
    RuntimeCompatibility(),
    LegacyWriteApi(),
    InsertIntoCatalog(),
    MergeNoExtensions(),
    MergeNoPartitionPruning(),
    SmallFilePattern(),
    FeatureFormatFloor(),
]
