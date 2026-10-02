"""DDB### checks over DynamoDBProjectModel - access patterns, streams, globals.

All findings are static-risk heuristics: no capacity math, no throttling
claims without runtime metrics. Single-table vs multi-table is reported,
never recommended. DDBGT003 resolves through the capability registry.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from forge_doctor.analyzers.dynamodb_model import (
    DynamoAccess,
    DynamoDBProjectModel,
    dynamodb_model,
)
from forge_doctor.core.capabilities import CapabilityStatus
from forge_doctor.core.models import Confidence, EvidenceKind, Severity
from forge_doctor.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor.core.capabilities import CapabilityResult
    from forge_doctor.core.context import ProjectContext
    from forge_doctor.core.models import CheckResult


def _model(ctx: ProjectContext) -> DynamoDBProjectModel:
    return dynamodb_model(ctx)


class _DdbCheck(CheckBase):
    category = "dynamodb"


_WRITE_OPS = {"put_item", "update_item", "delete_item", "batch_write_item", "transact_write_items"}
_HANDLER_HINT = re.compile(r"handler|route|endpoint|controller|api|view|resolver", re.I)
_LOW_CARD = {
    "type",
    "status",
    "state",
    "kind",
    "env",
    "flag",
    "active",
    "enabled",
    "region",
    "source",
    "count",
    "channel",
    "category",
    "tier",
}
_TIME_SK = re.compile(r"^(ts|timestamp|time|date|epoch|created(_at)?|now|ttl)$", re.I)


def _pk_literals(access: DynamoAccess) -> list[str]:
    """Literal values under the first Key attribute (the partition key)."""
    facts = access.key_literals
    out: list[str] = []
    for i, (kind, _) in enumerate(facts):
        if kind != "key":
            continue
        # values of this key = following facts until next "key" marker
        for kind2, val2 in facts[i + 1 :]:
            if kind2 == "key":
                break
            if kind2 == "lit":
                out.append(val2)
        break
    return out


def _pk_is_constant(access: DynamoAccess) -> bool:
    """First Key attribute holds only literals - no variable/pattern parts."""
    facts = access.key_literals
    seen_value = False
    for kind, _ in facts[1:]:
        if kind == "key":
            break
        if kind in {"var", "pattern"}:
            return False
        seen_value = True
    return bool(facts[:1] and facts[0][0] == "key" and seen_value)


def _sk_has_time_only(access: DynamoAccess) -> bool:
    """Second Key attribute bound only by a time-shaped variable."""
    keys = [i for i, (k, _v) in enumerate(access.key_literals) if k == "key"]
    if len(keys) < 2:
        return False
    for kind, val in access.key_literals[keys[1] + 1 :]:
        if kind == "key":
            break
        if kind == "var" and _TIME_SK.match(val):
            return True
        if kind == "pattern":
            return False  # PREFIX# shaped - composite, not time-only
    return False


class DynamoUsage(_DdbCheck):
    """DDB001 anchor: how much of the project touches DynamoDB."""

    id = "DDB001"
    title = "DynamoDB workload detected"
    why = "Anchor: sizes the DynamoDB surface feeding the other DDB checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_dynamodb:
            return [self.result(Severity.PASS, "no dynamodb workloads detected")]
        ops: dict[str, int] = {}
        for a in model.accesses:
            ops[a.op] = ops.get(a.op, 0) + 1
        op_str = ",".join(f"{k}:{v}" for k, v in sorted(ops.items()))
        return [
            self.result(
                Severity.INFO,
                f"{len(model.tables)} tables ({len(model.global_tables)} "
                f"global), {len(model.streams)} streams, "
                f"{len(model.accesses)} access ops [{op_str}], "
                f"{sum(len(t.indexes) for t in model.tables)} indexes",
            )
        ]


class ScanOnLatencyPath(_DdbCheck):
    """DDB002: scan inside a request/handler-shaped code path."""

    id = "DDB002"
    title = "Scan on a latency-sensitive path"
    why = "A scan inside a handler/route runs a full-table read on a "
    "synchronous request path - static hotspot risk, not a capacity claim."
    when_ok = "Scans live in batch/export paths, or are intentionally "
    "unbounded reads."
    fix = "Move the scan off the request path or bound it with a query."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.WARNING,
                f"scan inside {a.symbol or 'module scope'} - latency-"
                "sensitive path runs a full-table read (static risk)",
                file=a.file,
                line=a.line,
                evidence=a.raw or None,
            )
            for a in _model(ctx).accesses
            if a.op == "scan" and a.symbol and _HANDLER_HINT.search(a.symbol + a.file.as_posix())
        ]


class ScanWithoutProjection(_DdbCheck):
    """DDB003: scan with neither ProjectionExpression nor FilterExpression."""

    id = "DDB003"
    title = "Scan without projection or filter strategy"
    why = "An unfiltered, unprojected scan reads every item in full - the "
    "largest possible working set for the table."
    when_ok = "Exports/migrations legitimately read everything."
    fix = "Add a FilterExpression/ProjectionExpression or a bounded design."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                "scan reads all items in full - no ProjectionExpression or "
                "FilterExpression observed",
                file=a.file,
                line=a.line,
                evidence=a.raw or None,
            )
            for a in _model(ctx).accesses
            if a.op == "scan" and not a.has_projection and not a.has_filter
        ]


class PoorCardinalityPK(_DdbCheck):
    """DDB004: partition-key attribute name suggests low cardinality."""

    id = "DDB004"
    title = "Partition key likely poor cardinality"
    why = "Keys like status/type/env take few distinct values - writes "
    "concentrate on a handful of partitions (static risk)."
    when_ok = "The key is a deliberate coarse bucket with hot-key handling."
    fix = "Revisit the partition key or add a suffixing strategy."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"table {t.name!r} partition key {t.partition_key!r} looks "
                "low-cardinality - write concentration risk",
                file=t.file,
                line=t.line,
            )
            for t in _model(ctx).tables
            if t.partition_key and t.partition_key.lower() in _LOW_CARD
        ]


class HotPartition(_DdbCheck):
    """DDB005: the same static PK literal drives multiple write sites."""

    id = "DDB005"
    title = "Hot-partition candidate"
    why = "A single static partition-key value across writes concentrates "
    "the write load on one partition."
    when_ok = "The value is a real entity shard, not a constant."
    fix = "Spread writes across partition-key values."
    confidence = Confidence.MEDIUM
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        hits: dict[str, list[DynamoAccess]] = {}
        for a in _model(ctx).accesses:
            if a.op not in _WRITE_OPS:
                continue
            for lit in _pk_literals(a):
                hits.setdefault(lit, []).append(a)
        return [
            self.result(
                Severity.WARNING,
                f"partition key literal {lit!r} drives {len(sites)} write "
                "sites - static hot-partition risk",
                file=sites[0].file,
                line=sites[0].line,
                evidence_kind=EvidenceKind.DERIVED,
            )
            for lit, sites in sorted(hits.items())
            if len(sites) >= 2
        ]


class ConstantPKLiteral(_DdbCheck):
    """DDB006: a write whose partition key is a pure constant."""

    id = "DDB006"
    title = "Constant partition-key literal"
    why = "A hardcoded pk value means every item lands in one partition - "
    "usually a modeling slip unless it's a deliberate singleton."
    when_ok = "Singleton rows (config/locks) are intentional constants."
    fix = "Bind the partition key to the entity id."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"{a.op} writes with a constant partition-key value",
                file=a.file,
                line=a.line,
                evidence=a.raw or None,
            )
            for a in _model(ctx).accesses
            if a.op in _WRITE_OPS and _pk_is_constant(a)
        ]


class TimeOnlySortKey(_DdbCheck):
    """DDB007: sort key bound by a time-shaped variable alone."""

    id = "DDB007"
    title = "Time-only sort key write pattern"
    why = "sk=timestamp alone makes items monotonically ordered - range "
    "queries can't address entities and time buckets concentrate writes."
    when_ok = "Event/audit append-only streams are intentionally ordered."
    fix = "Prefix the sort key with the entity (e.g. EVENT#<ts>)."
    confidence = Confidence.LOW

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"{a.op} sort key appears time-only - consider an entity prefix for addressability",
                file=a.file,
                line=a.line,
                evidence=a.raw or None,
            )
            for a in _model(ctx).accesses
            if a.op in _WRITE_OPS and _sk_has_time_only(a)
        ]


class GsiDuplicatesBase(_DdbCheck):
    """DDB008: GSI partition key identical to the table's - duplicate path."""

    id = "DDB008"
    title = "GSI duplicates base-table access"
    why = "A GSI whose partition key equals the table's offers no new "
    "access pattern - it re-indexes the same distribution."
    when_ok = "The GSI differs by sort key/projection on purpose."
    fix = "Drop the index or give it a distinct key."
    confidence = Confidence.MEDIUM

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"gsi {g.name!r} on {t.name!r} uses the table's partition "
                f"key {t.partition_key!r} - duplicate access path",
                file=g.file,
                line=g.line,
            )
            for t in _model(ctx).tables
            for g in t.indexes
            if g.kind == "gsi" and t.partition_key and g.partition_key == t.partition_key
        ]


class GsiHotKey(_DdbCheck):
    """DDB009: GSI partition key name suggests a hot index partition."""

    id = "DDB009"
    title = "GSI partition key likely hot"
    why = "A GSI partitioned on a low-cardinality attribute concentrates "
    "indexed reads/writes on few index partitions."
    when_ok = "The index is sparse/filtered (e.g. only flagged items)."
    fix = "Pick a higher-cardinality index key or confirm the sparse use."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"gsi {g.name!r} partitions on {g.partition_key!r} - "
                "low-cardinality index key (static hotspot risk)",
                file=g.file,
                line=g.line,
            )
            for t in _model(ctx).tables
            for g in t.indexes
            if g.kind == "gsi" and g.partition_key.lower() in _LOW_CARD
        ]


class GsiVsAccess(_DdbCheck):
    """DDB010: declared GSI count vs access patterns that name indexes."""

    id = "DDB010"
    title = "GSI count vs observed access patterns"
    why = "Indexes with no observed IndexName usage may be dead capacity; "
    "accesses naming undeclared indexes fail at runtime."
    when_ok = "Index consumers live outside the repo."
    fix = "Reconcile declared indexes with the code's IndexName usage."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        declared = {g.name for t in model.tables for g in t.indexes if g.kind == "gsi"}
        used = {a.index for a in model.accesses if a.index}
        results = []
        unused = declared - used
        if model.accesses and unused:
            results.append(
                self.result(
                    Severity.INFO,
                    f"{len(unused)} declared GSI(s) never referenced by "
                    f"IndexName in code: {sorted(unused)}",
                )
            )
        for name in sorted(used - declared):
            results.append(
                self.result(
                    Severity.INFO,
                    f"IndexName {name!r} used in code but no matching GSI "
                    "declaration found in the project",
                )
            )
        return results


# --- streams ----------------------------------------------------------------


class StreamNoConsumer(_DdbCheck):
    """DDBSTR001: stream enabled with no detected consumer."""

    id = "DDBSTR001"
    title = "Stream enabled, no consumer detected"
    why = "An unconsumed stream suggests abandoned CDC intent or a "
    "consumer living outside the repo."
    when_ok = "Consumers are wired in another stack (Kinesis/PIPEs)."
    fix = "Confirm a consumer exists or disable the stream."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"table {s.table!r} has a stream ({s.view_type or 'enabled'}) "
                "but no consumer was detected in the project",
                file=s.file,
                line=s.line,
            )
            for s in _model(ctx).streams
            if not s.consumers
        ]


class StreamNoIdempotency(_DdbCheck):
    """DDBSTR002: consumer without a partial-batch/idempotency signal."""

    id = "DDBSTR002"
    title = "Stream consumer lacks idempotency signal"
    why = "Without ReportBatchItemFailures / batchItemFailures a failed "
    "record replays the whole batch - downstream effects re-run."
    when_ok = "Consumers are naturally idempotent (upserts by key)."
    fix = "Return batchItemFailures or make handlers idempotent."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"stream on {s.table or 'undeclared table'!r} has consumers "
                "but no partial-batch/idempotency signal was observed",
                file=s.file,
                line=s.line,
            )
            for s in _model(ctx).streams
            if s.consumers and not s.idempotency_signal
        ]


class StreamDuplicateProcessing(_DdbCheck):
    """DDBSTR003: one stream feeding multiple consumers."""

    id = "DDBSTR003"
    title = "Duplicate-processing risk"
    why = "Multiple consumers on one stream each replay every record - "
    "side effects must be safe to run once per consumer."
    when_ok = "Each consumer is intentionally independent."
    fix = "Confirm per-consumer independence or split responsibilities."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        return [
            self.result(
                Severity.INFO,
                f"stream on {s.table or 'undeclared table'!r} feeds "
                f"{len(s.consumers)} consumers - each replays every record",
                file=s.file,
                line=s.line,
            )
            for s in _model(ctx).streams
            if len(s.consumers) >= 2
        ]


class StreamRecoveryMismatch(_DdbCheck):
    """DDBSTR004: stream with no PITR/failure-destination recovery story."""

    id = "DDBSTR004"
    title = "Stream retention/recovery mismatch"
    why = "Stream records live at most 24h; without PITR or a failure "
    "destination, a consumer outage beyond that window loses events."
    when_ok = "Events are replayable from an upstream source of truth."
    fix = "Enable PITR or add a failure destination to the mapping."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        pitr_tables = {t.name for t in model.tables if t.pitr}
        return [
            self.result(
                Severity.INFO,
                f"stream on {s.table or 'undeclared table'!r} has no "
                "recovery evidence (no PITR on table, no failure "
                "destination) - records expire after ~24h",
                file=s.file,
                line=s.line,
            )
            for s in model.streams
            if s.table not in pitr_tables
        ]


class StreamGlobalDuplicate(_DdbCheck):
    """DDBSTR005: replicated stream events on a global table."""

    id = "DDBSTR005"
    title = "Replicated stream events on global table"
    why = "Global-table streams emit in every region; a consumer per "
    "region sees the same logical write once per region - downstream "
    "effects duplicate unless deduplicated."
    when_ok = "Consumers deduplicate by event id / region routing."
    fix = "Gate side effects on region or dedupe by eventID."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        global_names = {g.table for g in model.global_tables}
        return [
            self.result(
                Severity.WARNING,
                f"global table {s.table!r} streams into {len(s.consumers)} "
                "consumer(s) - replicated events repeat per region",
                file=s.file,
                line=s.line,
            )
            for s in model.streams
            if s.consumers and s.table in global_names
        ]


# --- global tables ----------------------------------------------------------


def _capability(ctx: ProjectContext, capability: str, variant: str = "") -> CapabilityResult | None:
    caps = cast(Any, getattr(ctx, "capabilities", None))
    if caps is None:
        return None
    return cast(
        "CapabilityResult | None",
        caps.evaluate(
            capability,
            platform="dynamodb_global_table",
            variant=variant or None,
        ),
    )


class GlobalWriteConflict(_DdbCheck):
    """DDBGT001: writes against a multi-region table (LWW risk)."""

    id = "DDBGT001"
    title = "Multi-region write-conflict risk"
    why = "Global tables replicate asynchronously with last-writer-wins "
    "resolution - concurrent writes to the same item across regions lose "
    "data silently."
    when_ok = "Writes are region-pinned or conflict-safe by design."
    fix = "Route writes to one region per key or add conflict handling."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.global_tables:
            return []
        if not any(a.op in _WRITE_OPS for a in model.accesses):
            return []
        return [
            self.result(
                Severity.INFO,
                f"global table {g.table!r} (mode={g.mode}) sees write "
                "operations - multi-region writes resolve last-writer-wins",
                file=g.file,
                line=g.line,
            )
            for g in model.global_tables
        ]


class GlobalMrecTransactions(_DdbCheck):
    """DDBGT002: transactions on MREC - region-local atomicity only."""

    id = "DDBGT002"
    title = "MREC transaction semantics are region-local"
    why = "On MREC global tables a transaction is atomic only in the "
    "invoking region; replication can leave regions divergent mid-txn."
    when_ok = "Region-local atomicity is sufficient for the workload."
    fix = "Confirm the region-local guarantee suffices."
    confidence = Confidence.MEDIUM
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.transactions_used:
            return []
        cap = _capability(ctx, "DYNAMODB_TRANSACTIONS", variant="MREC")
        reason = cap.reason if cap else ""
        return [
            self.result(
                Severity.INFO,
                f"transactions on MREC global table {g.table!r}: {reason}",
                file=g.file,
                line=g.line,
            )
            for g in model.global_tables
            if g.mode == "mrec"
        ]


class GlobalMrscTransactions(_DdbCheck):
    """DDBGT003: transaction ops against an MRSC table (unsupported)."""

    id = "DDBGT003"
    title = "Transactions on MRSC global table"
    why = "MRSC global tables do not support TransactGetItems/"
    "TransactWriteItems - the calls will fail at the service."
    when_ok = "Never - the registry marks this UNSUPPORTED."
    fix = "Remove the transaction ops or use an MREC table."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.transactions_used:
            return []
        out = []
        for g in model.global_tables:
            if g.mode != "mrsc":
                continue
            cap = _capability(ctx, "DYNAMODB_TRANSACTIONS", variant="MRSC")
            if cap is not None and cap.status is CapabilityStatus.UNSUPPORTED:
                out.append(
                    self.result(
                        Severity.ERROR,
                        f"transactions used against MRSC global table {g.table!r} - {cap.reason}",
                        file=g.file,
                        line=g.line,
                        evidence_kind=EvidenceKind.DERIVED,
                    )
                )
        return out


class GlobalRegionRouting(_DdbCheck):
    """DDBGT005: global table but no region-routing signal in code/config."""

    id = "DDBGT005"
    title = "Region routing strategy unclear"
    why = "A multi-region table without visible region pinning in code or "
    "providers leaves write/read routing implicit."
    when_ok = "Routing is handled by infrastructure outside the repo."
    fix = "Make the region strategy explicit (provider region, session "
    "region, routing layer)."
    confidence = Confidence.LOW
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.global_tables:
            return []
        from forge_doctor.analyzers.terraform_model import terraform_model

        tf = terraform_model(ctx)
        region_signal = any(
            isinstance(p.attrs.get("region"), str) and p.attrs["region"] for p in tf.providers
        )
        if not region_signal:
            for rel in ctx.files:
                if rel.suffix != ".py":
                    continue
                text = ctx.read_text(rel) or ""
                if re.search(r"region_name\s*=|AWS_REGION|Session\(", text):
                    region_signal = True
                    break
        if region_signal:
            return []
        return [
            self.result(
                Severity.INFO,
                f"global table {g.table!r} (mode={g.mode}) has no visible "
                "region-routing signal in code or providers",
                file=g.file,
                line=g.line,
            )
            for g in model.global_tables
        ]


CHECKS: list[Check] = [
    DynamoUsage(),
    ScanOnLatencyPath(),
    ScanWithoutProjection(),
    PoorCardinalityPK(),
    HotPartition(),
    ConstantPKLiteral(),
    TimeOnlySortKey(),
    GsiDuplicatesBase(),
    GsiHotKey(),
    GsiVsAccess(),
    StreamNoConsumer(),
    StreamNoIdempotency(),
    StreamDuplicateProcessing(),
    StreamRecoveryMismatch(),
    StreamGlobalDuplicate(),
    GlobalWriteConflict(),
    GlobalMrecTransactions(),
    GlobalMrscTransactions(),
    GlobalRegionRouting(),
]
