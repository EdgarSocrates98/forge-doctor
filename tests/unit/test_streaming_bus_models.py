"""Kafka/Kinesis/Flink model extraction tests (Phase 9)."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.flink_model import flink_model
from forge_doctor.analyzers.kafka_model import kafka_model
from forge_doctor.analyzers.kinesis_model import kinesis_model
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


TF_MSK = """
resource "aws_msk_cluster" "events" {
  cluster_name           = "events-msk"
  kafka_version          = "3.5.1"
  number_of_broker_nodes = 3
  broker_node_group_info {
    instance_type  = "kafka.m5.large"
    client_subnets = ["s-1"]
  }
  encryption_info {
    encryption_in_transit {
      client_broker = "PLAINTEXT"
    }
  }
  client_authentication {
    sasl {
      iam = true
    }
  }
}

resource "aws_msk_topic" "orders" {
  name               = "orders"
  partitions         = 6
  replication_factor = 3
}
"""

PY_KAFKA = """
from kafka import KafkaConsumer

consumer = KafkaConsumer(
    "orders",
    bootstrap_servers="b1:9092",
    group_id="analytics",
)
"""

PY_SPARK_KAFKA = """
ss = (spark.readStream.format("kafka")
      .option("subscribe", "orders")
      .option("kafka.bootstrap.servers", "b1:9092")
      .option("maxOffsetsPerTrigger", "10000")
      .option("failOnDataLoss", "true")
      .load())
q = (ss.writeStream.format("delta")
     .option("checkpointLocation", "s3://ck/orders")
     .start())
"""

TF_KINESIS = """
resource "aws_kinesis_stream" "clicks" {
  name             = "clicks"
  shard_count      = 4
  retention_period = 168
}

resource "aws_kinesis_stream_consumer" "analyzer" {
  name       = "analyzer"
  stream_arn = aws_kinesis_stream.clicks.arn
}

resource "aws_kinesisanalyticsv2_application" "enrich" {
  name                = "enrich"
  runtime_environment = "FLINK-1_18"
}
"""

PY_KINESIS = """
import boto3

client = boto3.client("kinesis")
client.put_record(StreamName="clicks", Data=b"x", PartitionKey="p")
"""

PY_FLINK = """
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.common.state import ValueStateDescriptor
from pyflink.datastream.window import TumblingEventTimeWindows

env = StreamExecutionEnvironment.get_execution_environment()
env.enable_checkpointing(30000)
ds = env.add_source(kafka_source)
ds.key_by(lambda x: x.user).window(TumblingEventTimeWindows.of(60))
ds.add_sink(delta_sink)
"""


def test_kafka_terraform(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": TF_MSK})
    m = kafka_model(ctx)
    assert m.has_kafka
    assert len(m.clusters) == 1
    c = m.clusters[0]
    assert c.name == "events-msk"
    assert c.broker_nodes == 3
    assert c.encryption_in_transit == "PLAINTEXT"
    assert c.kafka_version == "3.5.1"
    assert len(m.topics) == 1 and m.topics[0].partitions == 6


def test_kafka_python_clients_and_groups(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.py": PY_KAFKA})
    m = kafka_model(ctx)
    assert any(c.api == "KafkaConsumer" for c in m.client_calls)
    assert "analytics" in m.consumer_groups


def test_kafka_spark_options(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"pipeline.py": PY_SPARK_KAFKA})
    m = kafka_model(ctx)
    assert "orders" in m.subscribed_topics
    keys = {o.key for o in m.options}
    assert "subscribe" in keys or "kafka.bootstrap.servers" in keys


def test_kafka_schema_registry(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"cfg.py": 'SCHEMA_URL = {"schema.registry.url": "https://reg:8081"}'},
    )
    m = kafka_model(ctx)
    assert m.schema_registry


def test_kafka_empty(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"x.py": "print('hi')"})
    m = kafka_model(ctx)
    assert not m.has_kafka
    assert not m.clusters and not m.topics


def test_kinesis_terraform(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"main.tf": TF_KINESIS})
    m = kinesis_model(ctx)
    assert m.has_kinesis
    assert len(m.streams) == 1
    s = m.streams[0]
    assert s.name == "clicks" and s.shard_count == 4 and s.retention_hours == 168
    assert m.has_enhanced_fanout
    assert m.flink_apps[0].runtime_env == "FLINK-1_18"


def test_kinesis_boto3_calls(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"writer.py": PY_KINESIS})
    m = kinesis_model(ctx)
    apis = {c.api for c in m.api_calls}
    assert "put_record" in apis
    call = next(c for c in m.api_calls if c.api == "put_record")
    assert call.stream == "clicks"


def test_flink_model(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": PY_FLINK})
    m = flink_model(ctx)
    assert m.has_flink
    assert m.has_checkpoint and m.checkpoint_interval_ms == 30000
    assert m.has_keyed_state and m.has_window
    assert m.jobs  # StreamExecutionEnvironment entrypoint
    kinds = {e.kind for e in m.evidence}
    assert {"env", "source", "keyed_op", "window", "sink", "checkpoint"} <= kinds


def test_flink_managed_app(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"app.tf": TF_KINESIS})
    m = flink_model(ctx)
    managed = [j for j in m.jobs if j.source == "terraform"]
    assert managed and managed[0].runtime_env == "FLINK-1_18"


def test_models_deterministic(tmp_path: Path) -> None:
    files = {"main.tf": TF_MSK + TF_KINESIS, "job.py": PY_FLINK, "app.py": PY_KAFKA}
    ctx = make_context(tmp_path, files)
    k1 = kafka_model(ctx)
    ctx2 = make_context(tmp_path / "dup" / ".." if False else tmp_path, files)
    # rebuild a second context (model calls are pure w.r.t. files)
    k2 = kafka_model(ctx2)
    assert [(c.name, c.encryption_in_transit) for c in k1.clusters] == [
        (c.name, c.encryption_in_transit) for c in k2.clusters
    ]
    assert [o.key for o in k1.options] == [o.key for o in k2.options]
