"""Streaming checks (STREAM###) over the shared StreamingProjectModel."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from forge_doctor_data.analyzers.streaming_model import (
    _TEMP_PATH,
    StreamingProjectModel,
    streaming_model,
)
from forge_doctor_data.core.models import EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext
    from forge_doctor_data.core.models import CheckResult


def _model(ctx: ProjectContext) -> StreamingProjectModel:
    return streaming_model(ctx)


class _StreamCheck(CheckBase):
    category = "streaming"


class StreamingUsage(_StreamCheck):
    """STREAM001: how many streaming queries the project defines."""

    id = "STREAM001"
    title = "Streaming workload detected"
    why = "Anchor: sizes the streaming surface feeding the STREAM checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        model = _model(ctx)
        if not model.has_streaming:
            return [self.result(Severity.PASS, "no streaming workloads detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(model.queries)} streaming queries "
                f"(sources: {sorted(model.sources) or ['unknown']}, "
                f"sinks: {sorted(model.sinks) or ['unknown']})",
            )
        ]


class MissingCheckpoint(_StreamCheck):
    """STREAM002: writeStream without an observable checkpointLocation."""

    id = "STREAM002"
    title = "Streaming query without checkpoint"
    why = (
        "checkpointLocation holds offsets, commits and state - without it a "
        "restart cannot recover progress (no checkpointLocation observed "
        "statically)."
    )
    when_ok = "Every writeStream sets a stable checkpointLocation."
    fix = "Set option('checkpointLocation', '<durable path>') on the writer."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for q in _model(ctx).queries:
            if q.sink and not q.checkpoint and not q.checkpoint_dynamic:
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"query '{q.name}' writes via {q.sink} with no checkpointLocation observed",
                        file=q.file,
                        line=q.line or None,
                    )
                )
        return results


class TempCheckpoint(_StreamCheck):
    """STREAM003: checkpoint under a temporary/volatile path."""

    id = "STREAM003"
    title = "Checkpoint under temporary path"
    why = "Checkpoints under /tmp-style paths are wiped between runs - recovery state is lost."
    when_ok = "Checkpoint lives on durable storage (s3/abfs/gcs/warehouse)."
    fix = "Move checkpointLocation to a durable, versioned prefix."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for q in _model(ctx).queries:
            if q.checkpoint and _TEMP_PATH.search(q.checkpoint):
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"query '{q.name}' checkpoints to '{q.checkpoint}' - "
                        "a temporary/volatile path",
                        file=q.file,
                        line=q.line or None,
                    )
                )
        return results


class SharedCheckpoint(_StreamCheck):
    """STREAM013: multiple queries sharing one checkpointLocation."""

    id = "STREAM013"
    title = "Shared checkpoint location"
    # Correlates checkpoint facts across queries - derived, not a literal read.
    evidence_kind = EvidenceKind.DERIVED
    why = (
        "A checkpoint directory is bound to one query's plan - two queries "
        "sharing it corrupt each other's offsets and state."
    )
    when_ok = "Each query owns a unique checkpointLocation."
    fix = "Give every query its own checkpoint path."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        by_path: dict[str, list[str]] = defaultdict(list)
        for q in _model(ctx).queries:
            if q.checkpoint:
                by_path[q.checkpoint].append(q.name)
        return [
            self.result(
                Severity.WARNING,
                f"checkpointLocation '{path}' shared by queries {sorted(set(names))}",
            )
            for path, names in sorted(by_path.items())
            if len(set(names)) > 1
        ]


class DynamicCheckpoint(_StreamCheck):
    """STREAM014: checkpointLocation built from timestamp/uuid/etc."""

    id = "STREAM014"
    title = "Dynamic checkpoint location"
    why = (
        "A checkpoint path composed at runtime (f-string, datetime, uuid, "
        "env var) changes between restarts - recovery state is abandoned."
    )
    when_ok = "checkpointLocation is a stable literal."
    fix = "Use a fixed path; parameterize the prefix, not the directory."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for q in _model(ctx).queries:
            if q.checkpoint_dynamic:
                detail = f"'{q.checkpoint}'" if q.checkpoint else "non-literal expression"
                results.append(
                    self.result(
                        Severity.WARNING,
                        f"query '{q.name}' checkpointLocation is {detail} - built dynamically",
                        file=q.file,
                        line=q.line or None,
                    )
                )
        return results


class StatefulNoWatermark(_StreamCheck):
    """STREAM020: stateful ops without withWatermark."""

    id = "STREAM020"
    title = "Stateful operation without watermark"
    why = (
        "Aggregations/joins/dedup on a stream keep state per key - without "
        "a watermark there is no eviction boundary and state grows forever."
    )
    when_ok = "Stateful queries set withWatermark (or intentional state timeouts)."
    fix = "Add withWatermark('<event_time>', '<delay>') or explicit state timeouts."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        from forge_doctor_data.core.knowledge import load_pack

        ops = load_pack("streaming/spark", "stateful_ops").get("stateful_ops", {})
        needs_wm = {
            op for op, meta in ops.items() if isinstance(meta, dict) and meta.get("needs_watermark")
        } or {"groupby", "window", "join", "dropduplicates", "deduplicate"}

        results = []
        for q in _model(ctx).queries:
            flagged = [op for op in q.stateful_ops if op in needs_wm]
            if flagged and not q.watermark:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"query '{q.name}' has stateful ops "
                        f"{flagged} but no withWatermark observed",
                        file=q.file,
                        line=q.line or None,
                    )
                )
        return results


class ForeachBatchDetected(_StreamCheck):
    """STREAM070: foreachBatch sink detected."""

    id = "STREAM070"
    title = "foreachBatch sink"
    why = (
        "foreachBatch runs arbitrary code per micro-batch - idempotency, "
        "batch_id usage and side effects need manual verification."
    )
    when_ok = "Sink is natively transactional/idempotent."
    fix = "Verify the handler uses batch_id and writes idempotently."

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        results = []
        for q in _model(ctx).queries:
            if q.foreach_batch:
                results.append(
                    self.result(
                        Severity.INFO,
                        f"query '{q.name}' sinks via foreachBatch "
                        f"({q.foreach_batch}) - verify idempotency",
                        file=q.file,
                        line=q.line or None,
                    )
                )
        return results


CHECKS: list[Check] = [
    StreamingUsage(),
    MissingCheckpoint(),
    TempCheckpoint(),
    SharedCheckpoint(),
    DynamicCheckpoint(),
    StatefulNoWatermark(),
    ForeachBatchDetected(),
]
