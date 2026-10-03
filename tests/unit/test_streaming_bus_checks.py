"""KFK/KIN/FLK/STREAM080 check behavior tests (Phase 9)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor_data.checks.streaming_bus import (
    FlinkKeyedStateNoCheckpoint,
    KafkaNoRateCap,
    KafkaSinglePartitionTopic,
    KinesisShortRetention,
    MskPlaintextTransit,
    StreamingDelivery,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def _run(check: object, ctx: ProjectContext):
    return [r for r in check.run(ctx)]  # type: ignore[attr-defined]


def test_msk_plaintext_warns(tmp_path: Path) -> None:
    tf = """
resource "aws_msk_cluster" "c" {
  cluster_name = "c"
  encryption_info {
    encryption_in_transit {
      client_broker = "PLAINTEXT"
    }
  }
}
"""
    ctx = make_context(tmp_path, {"main.tf": tf})
    results = _run(MskPlaintextTransit(), ctx)
    assert results[0].severity == Severity.WARNING
    assert "plaintext" in results[0].message.lower()


def test_msk_tls_passes(tmp_path: Path) -> None:
    tf = """
resource "aws_msk_cluster" "c" {
  cluster_name = "c"
  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
    }
  }
}
"""
    ctx = make_context(tmp_path, {"main.tf": tf})
    results = _run(MskPlaintextTransit(), ctx)
    assert results[0].severity == Severity.PASS


def test_single_partition_topic_warns(tmp_path: Path) -> None:
    tf = 'resource "aws_msk_topic" "t" {\n  name = "t"\n  partitions = 1\n}\n'
    ctx = make_context(tmp_path, {"main.tf": tf})
    results = _run(KafkaSinglePartitionTopic(), ctx)
    assert results[0].severity == Severity.WARNING


def test_kafka_no_rate_cap(tmp_path: Path) -> None:
    py = (
        'ss = spark.readStream.format("kafka").option("subscribe", "t").load()\n'
        'q = ss.writeStream.format("delta").option("checkpointLocation", "s3://c").start()\n'
    )
    ctx = make_context(tmp_path, {"p.py": py})
    results = _run(KafkaNoRateCap(), ctx)
    assert results[0].severity == Severity.WARNING


def test_kafka_rate_cap_passes(tmp_path: Path) -> None:
    py = (
        'ss = spark.readStream.format("kafka").option("subscribe", "t")'
        '.option("maxOffsetsPerTrigger", "1000").load()\n'
        'q = ss.writeStream.format("delta").option("checkpointLocation", "s3://c").start()\n'
    )
    ctx = make_context(tmp_path, {"p.py": py})
    results = _run(KafkaNoRateCap(), ctx)
    assert results[0].severity == Severity.PASS


def test_kinesis_short_retention(tmp_path: Path) -> None:
    tf = 'resource "aws_kinesis_stream" "s" {\n  name = "s"\n  shard_count = 2\n}\n'
    ctx = make_context(tmp_path, {"main.tf": tf})
    results = _run(KinesisShortRetention(), ctx)
    assert results[0].severity == Severity.WARNING


def test_kinesis_long_retention_passes(tmp_path: Path) -> None:
    tf = (
        'resource "aws_kinesis_stream" "s" {\n  name = "s"\n  shard_count = 2\n'
        "  retention_period = 168\n}\n"
    )
    ctx = make_context(tmp_path, {"main.tf": tf})
    results = _run(KinesisShortRetention(), ctx)
    assert results[0].severity == Severity.PASS


def test_flink_keyed_state_no_checkpoint_errors(tmp_path: Path) -> None:
    py = (
        "from pyflink.datastream import StreamExecutionEnvironment\n"
        "env = StreamExecutionEnvironment.get_execution_environment()\n"
        "ds = env.add_source(s).key_by(lambda x: x.k)\n"
        "v = getRuntimeContext().getState(ValueStateDescriptor('s'))\n"
    )
    ctx = make_context(tmp_path, {"job.py": py})
    results = _run(FlinkKeyedStateNoCheckpoint(), ctx)
    assert results[0].severity == Severity.ERROR


def test_flink_keyed_state_with_checkpoint_passes(tmp_path: Path) -> None:
    py = (
        "from pyflink.datastream import StreamExecutionEnvironment\n"
        "env = StreamExecutionEnvironment.get_execution_environment()\n"
        "env.enable_checkpointing(60000)\n"
        "ds = env.add_source(s).key_by(lambda x: x.k)\n"
    )
    ctx = make_context(tmp_path, {"job.py": py})
    results = _run(FlinkKeyedStateNoCheckpoint(), ctx)
    assert results[0].severity == Severity.PASS


def test_delivery_semantics_effectively_once(tmp_path: Path) -> None:
    py = (
        'ss = spark.readStream.format("kafka").option("subscribe", "t").load()\n'
        'q = (ss.writeStream.format("delta")\n'
        '     .option("checkpointLocation", "s3://c")\n'
        "     .start())\n"
    )
    ctx = make_context(tmp_path, {"p.py": py})
    results = _run(StreamingDelivery(), ctx)
    assert any("effectively-once" in r.message for r in results)


def test_delivery_semantics_no_checkpoint_at_most_once(tmp_path: Path) -> None:
    py = (
        'ss = spark.readStream.format("kafka").option("subscribe", "t").load()\n'
        'q = ss.writeStream.format("console").start()\n'
    )
    ctx = make_context(tmp_path, {"p.py": py})
    results = _run(StreamingDelivery(), ctx)
    assert any("at-most-once" in r.message for r in results)


def test_delivery_semantics_basis_is_explainable(tmp_path: Path) -> None:
    py = (
        'ss = spark.readStream.format("kafka").option("subscribe", "t").load()\n'
        'q = (ss.writeStream.format("delta")\n'
        '     .option("checkpointLocation", "s3://c")\n'
        "     .start())\n"
    )
    ctx = make_context(tmp_path, {"p.py": py})
    results = _run(StreamingDelivery(), ctx)
    r = next(r for r in results if r.severity == Severity.INFO)
    assert r.evidence and "checkpoint:present" in r.evidence and "sink:delta" in r.evidence
