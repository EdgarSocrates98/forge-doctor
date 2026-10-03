"""Streaming-bus checks (KFK/KIN/FLK/STREAM08x) over Phase-9 models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from forge_doctor_data.analyzers.flink_model import FlinkProjectModel, flink_model
from forge_doctor_data.analyzers.kafka_model import KafkaProjectModel, kafka_model
from forge_doctor_data.analyzers.kinesis_model import KinesisProjectModel, kinesis_model
from forge_doctor_data.core.delivery import per_query
from forge_doctor_data.core.models import CheckResult, EvidenceKind, Severity
from forge_doctor_data.plugins.protocol import Check, CheckBase

if TYPE_CHECKING:
    from forge_doctor_data.core.context import ProjectContext


def _kafka(ctx: ProjectContext) -> KafkaProjectModel:
    return kafka_model(ctx)


def _kinesis(ctx: ProjectContext) -> KinesisProjectModel:
    return kinesis_model(ctx)


def _flink(ctx: ProjectContext) -> FlinkProjectModel:
    return flink_model(ctx)


class _BusCheck(CheckBase):
    category = "streaming-bus"


# --- Kafka -----------------------------------------------------------------


class KafkaUsage(_BusCheck):
    """KFK000: anchor — kafka surface size."""

    id = "KFK000"
    title = "Kafka surface detected"
    why = "Anchor: sizes clusters/topics/options feeding the KFK checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."
    evidence_kind = EvidenceKind.CONFIG

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _kafka(ctx)
        if not m.has_kafka:
            return [self.result(Severity.PASS, "no kafka evidence detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(m.clusters)} clusters, {len(m.topics)} topics, "
                f"{len(m.client_calls)} client calls, "
                f"groups: {sorted(m.consumer_groups) or ['none declared']}",
            )
        ]


class MskPlaintextTransit(_BusCheck):
    """KFK001: MSK cluster with PLAINTEXT/absent client-broker encryption."""

    id = "KFK001"
    title = "MSK cluster without TLS client-broker encryption"
    why = "client_broker=PLAINTEXT leaves broker traffic unencrypted on the wire."
    when_ok = "No MSK cluster declares PLAINTEXT client-broker encryption."
    fix = "Set encryption_in_transit client_broker = TLS (or TLS_PLAINTEXT during migration)."
    evidence_kind = EvidenceKind.CONFIG

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        bad = [
            c
            for c in _kafka(ctx).clusters
            if c.encryption_in_transit in ("PLAINTEXT", "TLS_PLAINTEXT")
            or (not c.encryption_in_transit and not c.serverless)
        ]
        if not bad:
            return [self.result(Severity.PASS, "no plaintext client-broker traffic detected")]
        return [
            self.result(
                Severity.WARNING,
                f"cluster '{c.name}' allows plaintext client-broker traffic",
                file=c.file,
                line=c.line,
                evidence=f"cluster={c.name} transit={c.encryption_in_transit or 'unset'}",
            )
            for c in bad
        ]


class KafkaSinglePartitionTopic(_BusCheck):
    """KFK002: kafka topic with a single partition."""

    id = "KFK002"
    title = "Kafka topic with a single partition"
    why = "Single-partition topics serialize throughput and cap consumer parallelism."
    when_ok = "No topic is declared with partitions = 1."
    fix = "Raise partitions for throughput, or document the ordering requirement."
    evidence_kind = EvidenceKind.CONFIG

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        bad = [t for t in _kafka(ctx).topics if t.partitions == 1]
        if not bad:
            return [self.result(Severity.PASS, "no single-partition topics detected")]
        return [
            self.result(
                Severity.WARNING,
                f"topic '{t.name}' declares partitions=1",
                file=t.file,
                line=t.line,
                evidence=f"topic={t.name} partitions=1",
            )
            for t in bad
        ]


class KafkaNoRateCap(_BusCheck):
    """KFK003: kafka streaming source without maxOffsetsPerTrigger."""

    id = "KFK003"
    title = "Kafka source without maxOffsetsPerTrigger"
    why = (
        "Without maxOffsetsPerTrigger a catch-up burst can pull an unbounded "
        "batch into one trigger, causing memory pressure and long micro-batches."
    )
    when_ok = "Kafka streaming sources set a per-trigger cap."
    fix = "Set .option('maxOffsetsPerTrigger', N) to bound per-trigger catch-up."
    evidence_kind = EvidenceKind.STATIC

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _kafka(ctx)
        subs = m.options_named("subscribe")
        if not subs and not m.subscribed_topics:
            return [self.result(Severity.PASS, "no kafka streaming sources detected")]
        if m.options_named("maxOffsetsPerTrigger") or m.options_named("minOffsetsPerTrigger"):
            return [self.result(Severity.PASS, "kafka sources declare a per-trigger cap")]
        return [
            self.result(
                Severity.WARNING,
                "kafka source has no maxOffsetsPerTrigger/minOffsetsPerTrigger cap",
                file=subs[0].file if subs else None,
                line=subs[0].line if subs else None,
                evidence="topics: " + ", ".join(sorted(m.subscribed_topics) or ["unnamed"]),
            )
        ]


class KafkaNoSchemaRegistry(_BusCheck):
    """KFK004: kafka workloads without a schema registry."""

    id = "KFK004"
    title = "Kafka topics consumed without a schema registry"
    why = (
        "Without a registry, producers/consumers drift on ad-hoc serialization "
        "and incompatible payloads fail at read time."
    )
    when_ok = "Kafka evidence coexists with schema registry evidence."
    fix = "Publish schemas through a registry (confluent/glue) and reference it."
    evidence_kind = EvidenceKind.STATIC

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _kafka(ctx)
        if not m.has_kafka or m.schema_registry:
            return [self.result(Severity.PASS, "schema registry evidence present (or no kafka)")]
        return [
            self.result(
                Severity.WARNING,
                "kafka evidence found without any schema registry reference",
            )
        ]


class KafkaNoConsumerGroup(_BusCheck):
    """KFK005: KafkaConsumer calls without an observable group.id."""

    id = "KFK005"
    title = "Kafka consumer without a consumer group"
    why = "No group.id means no committed offsets — replay and failover degrade."
    when_ok = "Every KafkaConsumer binds a group.id or kafka.group.id option."
    fix = "Set group.id (or kafka.group.id for spark) for every consumer."
    evidence_kind = EvidenceKind.STATIC

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _kafka(ctx)
        consumers = [c for c in m.client_calls if "consumer" in c.api.lower()]
        if not consumers or m.consumer_groups:
            return [self.result(Severity.PASS, "consumer groups declared (or no consumers)")]
        return [
            self.result(
                Severity.WARNING,
                f"KafkaConsumer call without an observable group.id ({c.api})",
                file=c.file,
                line=c.line,
                evidence=f"api={c.api}",
            )
            for c in consumers
        ]


class KafkaInsecureTransport(_BusCheck):
    """KFK006: kafka evidence but no TLS/SASL anywhere."""

    id = "KFK006"
    title = "Kafka traffic without security evidence"
    why = "No TLS/SASL evidence anywhere — brokers and clients may run PLAINTEXT."
    when_ok = "Kafka evidence coexists with security.protocol/sasl evidence."
    fix = "Enable TLS and SASL/IAM on brokers and clients."
    evidence_kind = EvidenceKind.CONFIG

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _kafka(ctx)
        if not m.has_kafka or m.secure_transport:
            return [self.result(Severity.PASS, "kafka security evidence present (or no kafka)")]
        if m.clusters and all(
            c.encryption_in_transit not in ("", "PLAINTEXT", "TLS_PLAINTEXT") for c in m.clusters
        ):
            return [self.result(Severity.PASS, "clusters declare encrypted transit")]
        return [
            self.result(
                Severity.WARNING,
                "kafka workloads detected without TLS/SASL security evidence",
            )
        ]


# --- Kinesis ---------------------------------------------------------------


class KinesisUsage(_BusCheck):
    """KIN000: anchor — kinesis surface size."""

    id = "KIN000"
    title = "Kinesis surface detected"
    why = "Anchor: sizes streams/consumers/options feeding the KIN checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."
    evidence_kind = EvidenceKind.CONFIG

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _kinesis(ctx)
        if not m.has_kinesis:
            return [self.result(Severity.PASS, "no kinesis evidence detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(m.streams)} streams, {len(m.consumers)} consumers, "
                f"{len(m.api_calls)} api calls, efo={m.has_enhanced_fanout}",
            )
        ]


class KinesisSingleShard(_BusCheck):
    """KIN001: provisioned stream with a single shard."""

    id = "KIN001"
    title = "Kinesis stream with a single shard"
    why = "One shard caps throughput at 1 MB/s writes and serializes consumer ordering."
    when_ok = "No stream declares shard_count = 1 (ON_DEMAND streams pass)."
    fix = "Raise shard_count or switch to ON_DEMAND stream_mode."
    evidence_kind = EvidenceKind.CONFIG

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        bad = [s for s in _kinesis(ctx).streams if s.shard_count == 1 and s.mode != "ON_DEMAND"]
        if not bad:
            return [self.result(Severity.PASS, "no single-shard provisioned streams")]
        return [
            self.result(
                Severity.WARNING,
                f"stream '{s.name}' provisioned with one shard",
                file=s.file,
                line=s.line,
                evidence=f"stream={s.name} shard_count=1",
            )
            for s in bad
        ]


class KinesisShortRetention(_BusCheck):
    """KIN002: stream retention at/below the 24h default."""

    id = "KIN002"
    title = "Kinesis stream with default retention"
    why = (
        "24-hour retention means consumers must drain data same-day — lagging "
        "consumers lose records permanently."
    )
    when_ok = "Streams set retention_period above 24 hours."
    fix = "Increase retention_period (up to 8760h) for consumers that may lag."
    evidence_kind = EvidenceKind.CONFIG

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        bad = [s for s in _kinesis(ctx).streams if s.retention_hours <= 24]
        if not bad:
            return [self.result(Severity.PASS, "streams declare extended retention")]
        return [
            self.result(
                Severity.WARNING,
                f"stream '{s.name}' at default (≤24h) retention",
                file=s.file,
                line=s.line,
                evidence=f"stream={s.name} retention={s.retention_hours or 'default'}h",
            )
            for s in bad
        ]


class KinesisFanOut(_BusCheck):
    """KIN003: multiple polling consumers but no enhanced fan-out."""

    id = "KIN003"
    title = "Multiple consumers without enhanced fan-out"
    why = (
        "Polling consumers share each shard's 2 MB/s read budget — multiple "
        "consumers contend without register_stream_consumer/EFO."
    )
    when_ok = "Multiple consumers use enhanced fan-out (or a single consumer exists)."
    fix = "Register EFO consumers (aws_kinesis_stream_consumer / subscribe_to_shard)."
    evidence_kind = EvidenceKind.STATIC

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _kinesis(ctx)
        readers = [c for c in m.api_calls if c.api in ("get_records", "get_shard_iterator")]
        if m.has_enhanced_fanout or len(readers) < 2:
            return [self.result(Severity.PASS, "fan-out is EFO-backed or single-consumer")]
        return [
            self.result(
                Severity.WARNING,
                f"{len(readers)} polling consumer call(s) without enhanced fan-out",
                evidence=", ".join(f"{c.file}:{c.line}" for c in readers[:5]),
            )
        ]


# --- Flink ------------------------------------------------------------------


class FlinkUsage(_BusCheck):
    """FLK000: anchor — flink surface size."""

    id = "FLK000"
    title = "Flink surface detected"
    why = "Anchor: sizes jobs/operators/state feeding the FLK checks."
    when_ok = "Anchor check - always reports."
    fix = "Nothing to fix; use the counts to size the surface."
    evidence_kind = EvidenceKind.STATIC

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _flink(ctx)
        if not m.has_flink:
            return [self.result(Severity.PASS, "no flink evidence detected")]
        return [
            self.result(
                Severity.INFO,
                f"{len(m.jobs)} job(s), {len(m.evidence)} evidence items, "
                f"checkpoint_mode={m.checkpoint_mode or 'unset'}",
            )
        ]


class FlinkNoCheckpoint(_BusCheck):
    """FLK001: flink job without checkpointing."""

    id = "FLK001"
    title = "Flink job without checkpointing"
    why = "No checkpointing means no failure recovery — state rebuilds from scratch."
    when_ok = "Every flink job enables checkpointing."
    fix = "Call env.enable_checkpointing(interval_ms) (or set execution.checkpointing)."
    evidence_kind = EvidenceKind.STATIC

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _flink(ctx)
        if not m.has_flink or m.has_checkpoint:
            return [self.result(Severity.PASS, "flink checkpointing configured (or no flink)")]
        return [
            self.result(
                Severity.WARNING,
                f"{len(m.jobs) or 1} flink job(s) without checkpointing evidence",
            )
        ]


class FlinkKeyedStateNoCheckpoint(_BusCheck):
    """FLK002: keyed state without checkpointing (state loss on failure)."""

    id = "FLK002"
    title = "Flink keyed state without checkpointing"
    why = "Keyed state without checkpoints is lost on restart — windows and dedup reset."
    when_ok = "Keyed state coexists with checkpoint configuration."
    fix = "Enable checkpointing before relying on keyed state."
    evidence_kind = EvidenceKind.STATIC

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _flink(ctx)
        if not (m.has_keyed_state and not m.has_checkpoint):
            return [self.result(Severity.PASS, "keyed state is checkpointed (or absent)")]
        e = m.evidence_of("keyed_op")[0]
        return [
            self.result(
                Severity.ERROR,
                "keyed state detected without checkpoint configuration — state loss on restart",
                file=e.file,
                line=e.line,
                evidence=f"op={e.detail}",
            )
        ]


class FlinkManagedNoAutoscaling(_BusCheck):
    """FLK003: managed flink app without autoscaling or explicit parallelism."""

    id = "FLK003"
    title = "Managed Flink app without autoscaling"
    why = "KinesisAnalyticsV2 apps without auto_scaling_enabled scale manually only."
    when_ok = "Managed apps enable autoscaling or set explicit parallelism."
    fix = "Set auto_scaling_enabled = true or a deliberate parallelism."
    evidence_kind = EvidenceKind.CONFIG

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _flink(ctx)
        managed = [j for j in m.jobs if j.source != "code"]
        bad = [j for j in managed if not j.autoscaling and j.parallelism == 0]
        if not bad:
            return [self.result(Severity.PASS, "managed apps autoscale or set parallelism")]
        return [
            self.result(
                Severity.WARNING,
                f"managed app '{j.name}' without autoscaling/parallelism",
                file=j.file,
                line=j.line,
                evidence=f"app={j.name}",
            )
            for j in bad
        ]


class FlinkAtLeastOnce(_BusCheck):
    """FLK004: flink declared AT_LEAST_ONCE checkpointing mode."""

    id = "FLK004"
    title = "Flink checkpointing at AT_LEAST_ONCE"
    why = "AT_LEAST_ONCE trades exactly-once for throughput — duplicates reach sinks."
    when_ok = "No flink job declares AT_LEAST_ONCE (or it's a deliberate choice)."
    fix = "Use CheckpointingMode.EXACTLY_ONCE + transactional sinks for stronger guarantees."
    evidence_kind = EvidenceKind.STATIC

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        m = _flink(ctx)
        if m.checkpoint_mode != "AT_LEAST_ONCE":
            return [self.result(Severity.PASS, "no AT_LEAST_ONCE declaration")]
        ev = [e for e in m.evidence_of("delivery_mode") if "AT_LEAST_ONCE" in e.detail]
        e = ev[0] if ev else None
        return [
            self.result(
                Severity.WARNING,
                "flink declares AT_LEAST_ONCE — duplicate delivery to sinks is possible",
                file=e.file if e else None,
                line=e.line if e else None,
                evidence="mode=AT_LEAST_ONCE",
            )
        ]


# --- Delivery semantics -----------------------------------------------------


class StreamingDelivery(_BusCheck):
    """STREAM080: derived delivery semantics per streaming query."""

    id = "STREAM080"
    title = "Streaming delivery semantics"
    why = (
        "Delivery guarantees derive from source+checkpoint+engine+sink+"
        "idempotency — never from the checkpoint flag alone."
    )
    when_ok = "Queries derive to at-least-once or stronger with known certainty."
    fix = "Add a checkpoint + transactional/idempotent sink for effectively-once."
    evidence_kind = EvidenceKind.DERIVED

    def run(self, ctx: ProjectContext) -> list[CheckResult]:
        pairs = per_query(ctx)
        if not pairs:
            return [self.result(Severity.PASS, "no streaming queries detected")]
        return [
            self.result(
                Severity.WARNING if sem.level == "at-most-once" else Severity.INFO,
                f"{name}: {sem.level} ({sem.certainty})",
                evidence="; ".join(sem.basis),
            )
            for name, sem in pairs
        ]


CHECKS: tuple[Check, ...] = (
    KafkaUsage(),
    MskPlaintextTransit(),
    KafkaSinglePartitionTopic(),
    KafkaNoRateCap(),
    KafkaNoSchemaRegistry(),
    KafkaNoConsumerGroup(),
    KafkaInsecureTransport(),
    KinesisUsage(),
    KinesisSingleShard(),
    KinesisShortRetention(),
    KinesisFanOut(),
    FlinkUsage(),
    FlinkNoCheckpoint(),
    FlinkKeyedStateNoCheckpoint(),
    FlinkManagedNoAutoscaling(),
    FlinkAtLeastOnce(),
    StreamingDelivery(),
)
