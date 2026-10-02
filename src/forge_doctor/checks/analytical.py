"""Analytical-engine checks — ClickHouse CH###, Pinot PIN###, Druid DRU###.

All checks read the shared :class:`AnalyticalEngineModel`. Evidence
planes: static (authored ClickHouse DDL), config (Pinot table configs
and Druid ingestion specs), observed_metadata (exported segment/query
artifacts). PIN002's filter evidence and PIN003's group-by evidence are
deliberately narrow, documented heuristics — they describe gaps, never
verdicts.
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import TYPE_CHECKING

from forge_doctor.analyzers.analytical_model import (
    analytical_model,
    has_dedupe_plan,
)
from forge_doctor.core.models import Confidence, EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


class _AnalyticalCheck(CheckBase):
    category = "analytical"


# ---------------------------------------------------------------------------
# ClickHouse


class ClickHouseSurface(_AnalyticalCheck):
    """CH000: anchor census of the ClickHouse surface."""

    id = "CH000"
    title = "ClickHouse surface"
    why = "Anchor: sizes the ClickHouse estate feeding the CH checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        tables = analytical_model(ctx).tables_of("clickhouse")
        if not tables:
            return []
        engines = sorted({t.table_engine for t in tables})
        return [
            self.result(
                Severity.INFO,
                f"{len(tables)} clickhouse tables/views ({', '.join(engines)})",
            )
        ]


class MergeTreeNoOrderBy(_AnalyticalCheck):
    """CH001: MergeTree-family table without ORDER BY."""

    id = "CH001"
    title = "MergeTree table without ORDER BY"
    why = (
        "A MergeTree table without an ordering key sorts data by an "
        "implicit tuple() — every read is a full scan and dedupe is "
        "impossible."
    )
    when_ok = "Every MergeTree-family table declares ORDER BY."
    fix = "Add ORDER BY <key columns> matching the query access pattern."

    _MERGETREE = re.compile(
        r"^(?:Replicated|Shared)?MergeTree$|^(?:Replacing|Summing|"
        r"Aggregating|Collapsing|VersionedCollapsing|Graphite)MergeTree$",
        re.IGNORECASE,
    )

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for t in analytical_model(ctx).tables_of("clickhouse"):
            if t.kind != "table" or not self._MERGETREE.search(t.table_engine):
                continue
            if not t.order_by:
                out.append(
                    self.result(
                        Severity.WARNING,
                        f"table '{t.name}' uses {t.table_engine} but declares no ORDER BY key",
                        file=t.file,
                        line=t.line,
                        evidence=f"ENGINE={t.table_engine}",
                        evidence_kind=EvidenceKind.STATIC,
                    )
                )
        return out


class ReplicatedNoKeeper(_AnalyticalCheck):
    """CH002: Replicated* engine without keeper/zookeeper config."""

    id = "CH002"
    title = "Replicated engine without keeper config"
    why = (
        "Replicated* engines need ClickHouse Keeper (or ZooKeeper) for "
        "coordination — without config, replication silently never "
        "engages or fails at startup."
    )
    when_ok = "Replicated* tables appear only when keeper config exists."
    fix = "Add a keeper/zookeeper config file or switch to a non-replicated engine."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = analytical_model(ctx)
        if model.keeper_files:
            return []
        return [
            self.result(
                Severity.WARNING,
                f"table '{t.name}' uses {t.table_engine} but no keeper/zookeeper config found",
                file=t.file,
                line=t.line,
                evidence=f"ENGINE={t.table_engine}",
                evidence_kind=EvidenceKind.STATIC,
            )
            for t in model.tables_of("clickhouse")
            if t.table_engine.lower().startswith("replicated")
        ]


class DistributedNoShard(_AnalyticalCheck):
    """CH003: Distributed table without a local shard definition."""

    id = "CH003"
    title = "Distributed table without local shard"
    why = (
        "A Distributed table points at a local shard table — if that "
        "local table isn't defined in the deployment, writes land "
        "nowhere and reads fan out to nothing."
    )
    when_ok = "Every Distributed table's local target is defined."
    fix = "Define the local shard table the Distributed engine references."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = analytical_model(ctx)
        tables = model.tables_of("clickhouse")
        local_names = {
            t.name.split(".")[-1].lower() for t in tables if t.table_engine.lower() != "distributed"
        }
        out: list[CheckResult] = []
        for t in tables:
            if t.table_engine.lower() != "distributed":
                continue
            local = t.prop("distributed_local")
            if not local:
                continue  # can't prove the target — stay quiet
            if local.split(".")[-1].lower() not in local_names:
                out.append(
                    self.result(
                        Severity.WARNING,
                        f"Distributed table '{t.name}' targets local table "
                        f"'{local}' which is not defined in the project",
                        file=t.file,
                        line=t.line,
                        evidence=f"ENGINE=Distributed(..., {local})",
                        evidence_kind=EvidenceKind.STATIC,
                    )
                )
        return out


class KafkaIngestionNoDedupe(_AnalyticalCheck):
    """CH004: Kafka engine ingestion with no dedupe/ordering plan."""

    id = "CH004"
    title = "Kafka ingestion without dedupe plan"
    why = (
        "The Kafka engine re-delivers on consumer restarts — without a "
        "dedupe-family target (ReplacingMergeTree etc.) or a materialized "
        "view sink, duplicates accumulate in the destination."
    )
    when_ok = (
        "Kafka-engine tables land beside a dedupe-family table or a "
        "materialized view that carries them somewhere."
    )
    fix = "Add a ReplacingMergeTree/SummingMergeTree target or a MATERIALIZED VIEW sink."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = analytical_model(ctx)
        kafka_tables = [
            t for t in model.tables_of("clickhouse") if t.table_engine.lower() == "kafka"
        ]
        if not kafka_tables or has_dedupe_plan(model):
            return []
        return [
            self.result(
                Severity.WARNING,
                f"Kafka engine table '{t.name}' ingests with no dedupe/"
                "ordering plan (no Replacing*/Aggregating* engine, no MV)",
                file=t.file,
                line=t.line,
                evidence="ENGINE=Kafka without dedupe-family sink",
                evidence_kind=EvidenceKind.STATIC,
            )
            for t in kafka_tables
        ]


# ---------------------------------------------------------------------------
# Pinot


class PinotSurface(_AnalyticalCheck):
    """PIN000: anchor census of the Pinot surface."""

    id = "PIN000"
    title = "Pinot surface"
    why = "Anchor: sizes the Pinot estate feeding the PIN checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = analytical_model(ctx)
        tables = model.tables_of("pinot")
        if not tables and not model.pinot_schemas:
            return []
        types = sorted({t.table_type for t in tables if t.table_type})
        return [
            self.result(
                Severity.INFO,
                f"{len(tables)} pinot tables ({', '.join(types) or '-'}), "
                f"{len(model.pinot_schemas)} schemas",
            )
        ]


class RealtimeNoRetention(_AnalyticalCheck):
    """PIN001: realtime table without retention config."""

    id = "PIN001"
    title = "Realtime table without retention"
    why = (
        "Realtime segments accumulate forever without retention — "
        "storage grows unboundedly until the cluster fills."
    )
    when_ok = "Every REALTIME table sets segmentsConfig.retentionTimeValue."
    fix = "Set segmentsConfig.retentionTimeValue/retentionTimeUnit."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for t in analytical_model(ctx).tables_of("pinot"):
            if t.table_type != "REALTIME":
                continue
            if not t.prop("segmentsConfig.retentionTimeValue"):
                out.append(
                    self.result(
                        Severity.WARNING,
                        f"realtime table '{t.name}' declares no segmentsConfig.retentionTimeValue",
                        file=t.file,
                        evidence="tableType=REALTIME, no retention",
                        evidence_kind=EvidenceKind.CONFIG,
                    )
                )
        return out


class HighCardNoInvertedIndex(_AnalyticalCheck):
    """PIN002: filtered high-cardinality dim without inverted index."""

    id = "PIN002"
    title = "High-cardinality dim filtered without inverted index"
    why = (
        "A high-cardinality dimension that appears in WHERE clauses but "
        "lacks an inverted index forces full segment scans per query."
    )
    when_ok = (
        "Filtered high-cardinality dims carry an inverted index "
        "(invertedIndexColumns or a fieldConfig index)."
    )
    fix = "Add the dim to tableIndexConfig.invertedIndexColumns or a fieldConfigList entry."

    confidence = Confidence.MEDIUM  # filter evidence is a text heuristic

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = analytical_model(ctx)
        if not model.pinot_schemas or not model.tables_of("pinot"):
            return []
        # dims that are filtered in authored SQL
        from forge_doctor.analyzers.sql_ast import analyze_sql

        sql_text = "\n".join(s.text for s in analyze_sql(ctx).statements)
        out: list[CheckResult] = []
        for schema in model.pinot_schemas:
            tables = [
                t
                for t in model.tables_of("pinot")
                if t.name.split("_")[0].lower() == schema.name.lower()
                or t.name.lower().startswith(schema.name.lower())
            ]
            if not tables:
                tables = model.tables_of("pinot")  # unattributed schema → all tables
            for t in tables:
                indexed: set[str] = set()
                with contextlib.suppress(json.JSONDecodeError):
                    indexed.update(
                        str(d).lower()
                        for d in json.loads(t.prop("tableIndexConfig.invertedIndexColumns") or "[]")
                    )
                with contextlib.suppress(json.JSONDecodeError):
                    for fc in json.loads(t.prop("fieldConfigList") or "[]"):
                        if isinstance(fc, dict) and fc.get("indexes"):
                            indexed.add(str(fc.get("name", "")).lower())
                no_dict: set[str] = set()
                with contextlib.suppress(json.JSONDecodeError):
                    no_dict = {
                        str(d).lower()
                        for d in json.loads(t.prop("tableIndexConfig.noDictionaryColumns") or "[]")
                    }
                for dim in schema.dims:
                    is_high_card = dim in schema.high_card_dims or dim.lower() in no_dict
                    if not is_high_card or dim.lower() in indexed:
                        continue
                    if not re.search(
                        rf"\bWHERE\b[^;]*\b{re.escape(dim)}\b", sql_text, re.IGNORECASE
                    ):
                        continue
                    out.append(
                        self.result(
                            Severity.WARNING,
                            f"high-cardinality dim '{dim}' is filtered in "
                            "authored SQL but has no inverted index",
                            file=t.file,
                            evidence=f"dim={dim} filtered, not indexed",
                            evidence_kind=EvidenceKind.CONFIG,
                        )
                    )
        return out


class NoStarTreeOnGroupBy(_AnalyticalCheck):
    """PIN003: no star-tree index on group-by-heavy observed queries."""

    id = "PIN003"
    title = "No star-tree index on group-by-heavy table"
    why = (
        "Observed query logs show repeated GROUP BY against a table "
        "with no star-tree index — each query re-aggregates raw rows."
    )
    when_ok = "Tables hit by repeated observed GROUP BYs carry starTreeIndexConfigs."
    fix = "Add starTreeIndexConfigs to tableIndexConfig (or verify the export is stale)."

    confidence = Confidence.MEDIUM  # observed-log heuristic

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = analytical_model(ctx)
        pinot_rows = [r for r in model.observed if r.engine == "pinot"]
        if not pinot_rows:
            return []
        out: list[CheckResult] = []
        for t in model.tables_of("pinot"):
            hits = 0
            for row in pinot_rows:
                q = row.get("query", "queries", "sql")
                if "group by" in q.lower() and t.name.split("_")[0].lower() in q.lower():
                    hits += 1
            if hits >= 2 and t.prop("tableIndexConfig.starTree").lower() != "true":
                out.append(
                    self.result(
                        Severity.WARNING,
                        f"table '{t.name}' is GROUP BY-heavy in observed "
                        f"queries ({hits} rows) but has no star-tree index",
                        file=t.file,
                        evidence=f"{hits} group-by query rows, no starTreeIndexConfigs",
                        evidence_kind=EvidenceKind.OBSERVED_METADATA,
                    )
                )
        return out


# ---------------------------------------------------------------------------
# Druid


class DruidSurface(_AnalyticalCheck):
    """DRU000: anchor census of the Druid surface."""

    id = "DRU000"
    title = "Druid surface"
    why = "Anchor: sizes the Druid estate feeding the DRU checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        tables = analytical_model(ctx).tables_of("druid")
        if not tables:
            return []
        return [
            self.result(
                Severity.INFO,
                f"{len(tables)} druid datasources ({', '.join(sorted(t.name for t in tables))})",
            )
        ]


class DatasourceNoPartitioning(_AnalyticalCheck):
    """DRU001: datasource ingestion spec without partitioning config."""

    id = "DRU001"
    title = "Datasource without partitioning config"
    why = (
        "An ingestion spec without partitionsSpec falls back to default "
        "dynamic partitioning — segment sizes drift and query pruning "
        "degrades."
    )
    when_ok = "Every ingestion spec sets tuningConfig.partitionsSpec."
    fix = "Add tuningConfig.partitionsSpec (hashed/numShards/range)."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"datasource '{t.name}' ingestion spec sets no tuningConfig.partitionsSpec",
                file=t.file,
                evidence="no partitionsSpec",
                evidence_kind=EvidenceKind.CONFIG,
            )
            for t in analytical_model(ctx).tables_of("druid")
            if not t.prop("partitionsSpec")
        ]


class RollupDisabledHighCard(_AnalyticalCheck):
    """DRU002: rollup disabled on wide metrics/dimensions."""

    id = "DRU002"
    title = "Rollup disabled on high-cardinality datasource"
    why = (
        "rollup=false stores every raw event — with several dims/"
        "metrics the segment volume multiplies without aggregation "
        "benefit."
    )
    when_ok = "Datasources with ≥3 metrics/dims either roll up or opt out knowingly."
    fix = "Set granularitySpec.rollup=true or trim the declared dims/metrics."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        out: list[CheckResult] = []
        for t in analytical_model(ctx).tables_of("druid"):
            if t.prop("rollup") != "false":
                continue
            try:
                width = int(t.prop("metrics.count") or 0) + int(t.prop("dims.count") or 0)
            except ValueError:
                width = 0
            if width >= 3:
                out.append(
                    self.result(
                        Severity.WARNING,
                        f"datasource '{t.name}' disables rollup with {width} "
                        "declared metrics+dims — raw events stored unaggregated",
                        file=t.file,
                        evidence=f"rollup=false, {width} dims+metrics",
                        evidence_kind=EvidenceKind.CONFIG,
                    )
                )
        return out


CHECKS: tuple[Check, ...] = (
    ClickHouseSurface(),
    MergeTreeNoOrderBy(),
    ReplicatedNoKeeper(),
    DistributedNoShard(),
    KafkaIngestionNoDedupe(),
    PinotSurface(),
    RealtimeNoRetention(),
    HighCardNoInvertedIndex(),
    NoStarTreeOnGroupBy(),
    DruidSurface(),
    DatasourceNoPartitioning(),
    RollupDisabledHighCard(),
)
