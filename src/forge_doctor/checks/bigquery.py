"""BigQuery checks (BQ###) over the vendor BigQueryProjectModel.

Grounded in ``bigquery_model`` only — vendor-neutral findings belong to
WARE###. Evidence is declarative (DDL, Terraform) plus observed-metadata
exports; no live GCP calls ever.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from forge_doctor.analyzers.bigquery_model import (
    BigQueryProjectModel,
    bigquery_model,
)
from forge_doctor.core.models import Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


def _model(ctx: ProjectContext) -> BigQueryProjectModel:
    return bigquery_model(ctx)


class _BigQueryCheck(CheckBase):
    category = "bigquery"


def _falsy(value: str) -> bool:
    return value.lower() in {"", "0", "false", "off", "none", "never"}


def _tail(name: str) -> str:
    """Last dotted segment — `proj.ds.t` and `ds.t` both tail to `t`."""
    return name.strip("`").rpartition(".")[2].lower() or name.lower()


def _name_matches(candidate: str, names: set[str]) -> bool:
    tail = _tail(candidate)
    for name in names:
        if (
            candidate == name
            or tail == _tail(name)
            or candidate.endswith(f".{name}")
            or name.endswith(f".{candidate}")
        ):
            return True
    return False


def _partition_column(expr: str) -> str:
    """`DATE(ts)` / `RANGE_BUCKET(id, ...)` / `ts` -> innermost column id."""
    idents = re.findall(r"[A-Za-z_]\w*", expr)
    funcs = {"date", "timestamp", "datetime", "range_bucket", "generate_array", "int64"}
    for ident in idents:
        if ident.lower() not in funcs:
            return str(ident)
    return ""


def _has_partition_filter(query_text: str, column: str) -> bool:
    if re.search(r"_partition(time|date)", query_text, re.IGNORECASE):
        return True
    where = re.search(r"\bwhere\b(.*)", query_text, re.IGNORECASE | re.DOTALL)
    if column:
        return bool(where and re.search(rf"\b{re.escape(column)}\b", where.group(1), re.IGNORECASE))
    return bool(where)  # column unknown: require some WHERE at minimum


class BigQueryUsage(_BigQueryCheck):
    """BQ000: anchor census of the BigQuery surface."""

    id = "BQ000"
    title = "BigQuery surface"
    why = "Anchor: sizes the BigQuery estate feeding the BQ checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_evidence:
            return [self.result(Severity.PASS, "no BigQuery evidence detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(model.datasets)} datasets, {len(model.tables)} tables, "
                f"{len(model.views)} views, {len(model.objects)} vendor objects, "
                f"{len(model.queries)} queries, {len(model.observed)} observed rows",
            )
        ]


class UnpartitionedLargeTable(_BigQueryCheck):
    """BQ001: observed large table with no partitioning."""

    id = "BQ001"
    title = "Large table without partitioning"
    why = (
        "BigQuery bills per bytes scanned; a large table with no "
        "partitioning turns every query into a full scan."
    )
    when_ok = "Large observed tables declare PARTITION BY (or a Terraform partitioning block)."
    fix = "Add PARTITION BY on the dominant filter column (usually the event date)."

    _LARGE_BYTES = 1_000_000_000  # 1 GB proxy — static, documented

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        declared = {t.name for t in model.tables}
        partitioned = model.partitioned_tables()
        results = []
        for row in model.observed_tables():
            raw = row.get("size_bytes", "logical_bytes", "bytes", "row_count")
            try:
                size = float(raw) if raw else 0.0
            except ValueError:
                size = 0.0
            name = row.get("table_name", "name")
            if (
                size >= self._LARGE_BYTES
                and not _name_matches(name, partitioned)
                and (not declared or _name_matches(name, declared))
            ):
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"table '{name}' is {size:,.0f} bytes on observed "
                        "metadata with no partitioning evidence",
                        file=row.file,
                        evidence=f"observed metadata row in {row.file.as_posix()}",
                    )
                )
        return results


class PartitionWithoutFilter(_BigQueryCheck):
    """BQ002: partitioned table queried without a partition filter."""

    id = "BQ002"
    title = "Partitioned table queried without partition filter"
    why = (
        "Queries on partitioned tables without a partition filter scan "
        "every partition — the dominant BigQuery cost driver."
    )
    when_ok = "Queries on partitioned tables filter on the partition column or _PARTITIONTIME."
    fix = "Add a WHERE clause on the partition column (or _PARTITIONTIME/_PARTITIONDATE)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        partitioned = model.partitioned_tables()
        if not partitioned:
            return []
        # Partition column per table (best-effort from partition_by expr).
        columns: dict[str, str] = {}
        for t in model.tables:
            if _name_matches(t.name, partitioned):
                col = _partition_column(t.attr("partition_by"))
                if col:
                    columns[t.name] = col
        results = []
        for q in model.queries:
            hit = next(
                (name for name in partitioned if _name_matches(name, set(q.tables_read))),
                None,
            )
            if hit is None:
                continue
            col = columns.get(hit) or next(
                (c for n, c in columns.items() if _name_matches(n, {hit})), ""
            )
            if _has_partition_filter(q.text, col):
                continue
            severity = Severity.ERROR if q.source == "job" else Severity.WARNING
            src = "observed job history" if q.source == "job" else "authored SQL"
            results.append(
                self.result(
                    severity,
                    f"{src} reads partitioned table '{hit}' without a "
                    "partition filter" + (f" (partition column: {col})" if col else ""),
                    file=q.file,
                    line=q.line,
                    evidence=self.evidence_at(ctx, q.file, q.line)
                    if q.source == "file"
                    else f"job row in {q.file.as_posix()}",
                )
            )
        return results


class WildcardQueryCost(_BigQueryCheck):
    """BQ003: ``SELECT *`` authored query — cost-risky on columnar billing."""

    id = "BQ003"
    title = "SELECT * on columnar-billed engine"
    why = (
        "BigQuery charges by bytes scanned; SELECT * reads every column "
        "of every scanned partition regardless of what is used."
    )
    when_ok = "Authored queries name their column list explicitly."
    fix = "Replace SELECT * with the columns the query actually needs."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        results = []
        for q in model.queries:
            if q.source != "file":
                continue
            if re.search(r"\bselect\s+\*", q.text, re.IGNORECASE):
                results.append(
                    self.result(
                        Severity.WARNING,
                        "authored query uses SELECT * - columnar billing "
                        "reads all columns of all scanned partitions",
                        file=q.file,
                        line=q.line,
                        evidence=self.evidence_at(ctx, q.file, q.line),
                    )
                )
        return results


class PublicOrUndocAuthorizedView(_BigQueryCheck):
    """BQ004: public dataset or authorized view without documentation."""

    id = "BQ004"
    title = "Public/external dataset or undocumented authorized view"
    why = (
        "allUsers/allAuthorizedUsers access grants and cross-dataset "
        "authorized views are sharing surfaces; undocumented ones are "
        "unreviewed exfiltration paths."
    )
    when_ok = "No public access grants; every authorized view carries a description."
    fix = "Remove public grants or document the authorized view (description attr)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        results = []
        for ds in model.datasets:
            if ds.attr("public_access"):
                results.append(
                    self.result(
                        Severity.ERROR,
                        f"dataset '{ds.name}' grants public access (allUsers/allAuthorizedUsers)",
                        file=ds.file,
                        line=ds.line,
                        evidence=self.evidence_at(ctx, ds.file, ds.line)
                        if ds.file is not None
                        else None,
                    )
                )
        for access in model.by_kind("dataset_access"):
            if access.attr("public_access"):
                results.append(
                    self.result(
                        Severity.ERROR,
                        f"dataset access '{access.name}' grants public access",
                        file=access.file,
                        line=access.line,
                        evidence=self.evidence_at(ctx, access.file, access.line)
                        if access.file is not None
                        else None,
                    )
                )
            if access.attr("authorized_view") and _falsy(access.attr("description")):
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"authorized view '{access.name}' has no description — "
                        "sharing intent is undocumented",
                        file=access.file,
                        line=access.line,
                        evidence=self.evidence_at(ctx, access.file, access.line)
                        if access.file is not None
                        else None,
                    )
                )
        return results


class MutableBaseMaterializedView(_BigQueryCheck):
    """BQ005: materialized view over a mutable base without staleness policy."""

    id = "BQ005"
    title = "Materialized view over mutable base without staleness policy"
    why = (
        "A materialized view over a table that receives writes silently "
        "serves stale data unless max_staleness (or refresh) is declared."
    )
    when_ok = "Materialized views over mutable bases declare OPTIONS(max_staleness=...)."
    fix = "Add OPTIONS(max_staleness = INTERVAL ...) to the materialized view."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        written = {w for q in model.queries for w in q.tables_written}
        written |= {
            row.get("table_name", "name")
            for row in model.observed
            if row.shape == "jobs"
            and row.get("statement_type", "job_type").upper()
            in {"INSERT", "UPDATE", "DELETE", "MERGE", "LOAD"}
        }
        if not written:
            return []
        results = []
        for mv in model.views:
            if mv.kind != "materialized_view":
                continue
            reads = {r for r in mv.attr("tables_read").split(",") if r}
            base = next((r for r in reads if _name_matches(r, written)), None)
            if base is None or mv.attr("max_staleness") or mv.attr("refresh"):
                continue
            results.append(
                self.result(
                    Severity.WARNING,
                    f"materialized view '{mv.name}' reads mutable base "
                    f"'{base}' with no staleness policy",
                    file=mv.file,
                    line=mv.line,
                    evidence=self.evidence_at(ctx, mv.file, mv.line)
                    if mv.file is not None
                    else None,
                )
            )
        return results


CHECKS: tuple[Check, ...] = (
    BigQueryUsage(),
    UnpartitionedLargeTable(),
    PartitionWithoutFilter(),
    WildcardQueryCost(),
    PublicOrUndocAuthorizedView(),
    MutableBaseMaterializedView(),
)
