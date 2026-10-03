"""Deterministic delivery-semantics classification.

Derives a per-stream delivery claim from the tuple
``source + checkpoint + engine + sink + retry/idempotency`` — never
from the checkpoint flag alone. Missing evidence yields ``unknown`` or
a weaker level, not a fabricated guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LEVELS = ("at-most-once", "at-least-once", "effectively-once", "exactly-once-claim", "unknown")

# Source replayability: can input records be re-read after failure?
_REPLAYABLE = {"kafka", "kinesis", "files", "delta", "iceberg", "rate"}
_NON_REPLAYABLE = {"socket", "custom"}
# Sinks with transactional/idempotent write support (spark-ss micro-batch
# commit protocol) vs plain non-transactional outputs.
_TRANSACTIONAL_SINKS = {"delta", "iceberg", "files"}
_BEST_EFFORT_SINKS = {"console", "memory", "socket"}


@dataclass(frozen=True)
class DeliverySemantics:
    """A derived delivery claim with its full evidence basis."""

    level: str  # one of LEVELS
    certainty: str  # derived|claimed|unknown
    basis: tuple[str, ...] = field(default_factory=tuple)  # ordered fact strings


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else len(LEVELS)


def _cap(level: str, cap: str) -> str:
    return cap if _rank(level) > _rank(cap) else level


def derive_delivery(
    *,
    source: str = "",
    checkpoint: bool = False,
    engine: str = "",
    sink: str = "",
    idempotent_sink: bool = False,
    commit_mode: str = "",  # flink CheckpointingMode / DeliveryGuarantee literal
    auto_commit: bool = False,
    retry_evidence: bool = False,
) -> DeliverySemantics:
    """Classify delivery semantics deterministically.

    ``auto_commit`` flags kafka ``enable.auto.commit`` style consumers
    where offsets advance before processing — an at-most-once risk.
    """
    basis: list[str] = []
    src = source.lower()
    snk = sink.lower()
    eng = engine.lower() or "spark_ss"
    mode = commit_mode.upper()

    if src:
        replayable = src in _REPLAYABLE
        basis.append(f"source:{src} ({'replayable' if replayable else 'not replayable'})")
    else:
        replayable = False
        basis.append("source:unknown")
    basis.append(f"checkpoint:{'present' if checkpoint else 'absent'}")
    basis.append(f"engine:{eng}")
    if snk:
        txn = snk in _TRANSACTIONAL_SINKS or idempotent_sink
        basis.append(f"sink:{snk} ({'transactional' if txn else 'non-transactional'})")
    else:
        txn = idempotent_sink
        basis.append("sink:unknown")
    if mode:
        basis.append(f"commit_mode:{mode.lower()}")
    if auto_commit:
        basis.append("auto_commit:true")
    if retry_evidence:
        basis.append("retry_evidence:present")
    if idempotent_sink:
        basis.append("idempotent_sink:true")

    # ---- rules (evaluated in order; first match wins) ------------------
    if auto_commit and not checkpoint:
        return DeliverySemantics("at-most-once", "derived", tuple(basis))
    if not checkpoint:
        return DeliverySemantics("at-most-once", "derived", tuple(basis))
    if not src or not snk:
        # partial evidence — no fabricated guarantee
        level = "at-least-once" if checkpoint else "unknown"
        return DeliverySemantics(level, "unknown", tuple(basis))
    if not replayable:
        level = "at-least-once" if txn else "at-most-once"
        return DeliverySemantics(level, "derived", tuple(basis))

    if eng == "flink":
        if mode == "EXACTLY_ONCE" and txn:
            return DeliverySemantics("exactly-once-claim", "claimed", tuple(basis))
        if mode == "AT_LEAST_ONCE":
            return DeliverySemantics("at-least-once", "derived", tuple(basis))
        # flink with checkpoint + txn sink but no explicit mode
        if txn:
            return DeliverySemantics("effectively-once", "derived", tuple(basis))
        return DeliverySemantics("at-least-once", "derived", tuple(basis))

    # spark structured streaming (micro-batch commit protocol)
    if snk in _TRANSACTIONAL_SINKS:
        return DeliverySemantics("effectively-once", "derived", tuple(basis))
    if idempotent_sink:  # e.g. foreachBatch w/ batch_id dedup
        return DeliverySemantics("effectively-once", "derived", tuple(basis))
    if snk == "kafka":
        # SS kafka sink writes are at-least-once; exactly-once requires a
        # dedup-capable consumer — not derivable from static evidence.
        return DeliverySemantics("at-least-once", "derived", tuple(basis))
    if snk in _BEST_EFFORT_SINKS:
        return DeliverySemantics("at-least-once", "derived", tuple(basis))
    return DeliverySemantics("at-least-once", "derived", tuple(basis))


def _foreachbatch_idempotent(text: str) -> bool:
    """batch_id used for dedup + an idempotent write op in the handler."""
    import re

    has_batch_dedup = bool(
        re.search(r"batch_?[Ii]d", text)
        and re.search(
            r"batch_?[Ii]d.{0,400}?(if|when|==|merge|insert|filter|dropDuplicates|"
            r"where|join|checkpoint|primary|dedup)",
            text,
            re.DOTALL,
        )
    )
    has_idem_write = bool(
        re.search(
            r"\.merge\(|\.update\(|\.delete\(|overwritePartitions|insertOverwrite|"
            r"CREATE OR REPLACE|MERGE INTO|mode\(\s*[\"']overwrite",
            text,
        )
    )
    return has_batch_dedup and has_idem_write


def per_query(ctx: object) -> list[tuple[str, DeliverySemantics]]:
    """Derive semantics for every detected streaming query.

    Returns ``[(query_name, DeliverySemantics)]`` in deterministic order.
    """
    from forge_doctor_data.analyzers.streaming_model import streaming_model
    from forge_doctor_data.core.context import ProjectContext

    assert isinstance(ctx, ProjectContext)
    sm = streaming_model(ctx)
    out: list[tuple[str, DeliverySemantics]] = []
    for q in sorted(sm.queries, key=lambda q: (q.file.as_posix(), q.line)):
        sink = q.sink
        idem = sink in _TRANSACTIONAL_SINKS
        if sink == "foreachBatch" or q.foreach_batch:
            text = ctx.read_text(q.file) or ""
            idem = _foreachbatch_idempotent(text)
        sem = derive_delivery(
            source=q.source,
            checkpoint=bool(q.checkpoint) and not q.checkpoint_dynamic,
            engine=q.engine or "spark_ss",
            sink=sink,
            idempotent_sink=idem,
        )
        out.append((q.name or f"{q.file}:{q.line}", sem))
    return out
